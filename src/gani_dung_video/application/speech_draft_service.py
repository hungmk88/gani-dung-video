"""Use case: phrases + candidates + note → Claude → S1–S6 → edit/speech_drafts.json (5c-2).

A parallel retry loop to draft_service.py's (4c), not a shared one: phase 5c-2 chose to keep
this module independent so 4c's already-shipped, already-tested loop stays untouched (decision
29/09/2026, "loại trừ 100% rủi ro hồi quy"). The shapes match closely on purpose — same R2
retry policy, same cost-cap-before-call check, same ai_log.jsonl (tagged "mode": "speech" so
the two pipelines share one audit trail without mixing up which is which).

The AI only picks phrase ids, assigns them to blocks, and picks a B-roll candidate id per slot;
every second and cut point stays in code: domain.speech_edit.aroll_timeline chains the chosen
phrases, default_overlays places each B-roll window on the beat grid exactly as 5c-1 did.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gani_dung_video.application.draft_service import DryRun
from gani_dung_video.application.render_service import SpeechDraftsFile
from gani_dung_video.domain.drafts import cost_vnd, estimate_tokens
from gani_dung_video.domain.edl import BrollPlacement, SpeechDraft
from gani_dung_video.domain.pick import PickFile
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.speech_ai import (
    SYSTEM_PROMPT_SPEECH,
    AiSpeechDraft,
    AiSpeechResponse,
    build_speech_user_prompt,
    response_schema,
)
from gani_dung_video.domain.speech_edit import PhrasesFile, aroll_timeline, default_overlays
from gani_dung_video.domain.speech_verify import (
    Issue,
    VerifyContext,
    parse_speech_response,
    verify_speech_drafts,
)
from gani_dung_video.infrastructure.claude_client import AiError, AiReply, Caller, make_caller
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules, read_options_file
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.settings import Settings, load_settings, require_api_key

logger = logging.getLogger(__name__)
PLAN_FILE, PHRASES_FILE, PICK_FILE, OUT_FILE, LOG_FILE = (
    "plan.json", "phrases.json", "candidates.json", "speech_drafts.json", "ai_log.jsonl",
)  # fmt: skip


class SpeechDraftError(RuntimeError):
    """Claude did not pass the checks within the allowed rounds; nothing is rendered."""

    def __init__(self, msg: str, issues: Sequence[Issue] = ()) -> None:
        super().__init__(msg)
        self.issues = list(issues)


class SpeechCostCapReached(SpeechDraftError):
    """The next call could push the spend over the cap, so it was not sent."""


@dataclass(frozen=True)
class SpeechDraftsResult:
    """In-memory report of a `draft` run (speech mode); edit/speech_drafts.json holds less
    (model, cost_vnd, drafts only — the format render_service.py already reads)."""

    model: str
    attempts: int
    input_tokens: int
    output_tokens: int
    cost_vnd: float
    cap_vnd: float
    drafts: list[SpeechDraft]
    notes: list[str]  # warnings (S5) kept for the editor


@dataclass
class _Job:
    plan: Plan
    phrases: PhrasesFile
    pick: PickFile
    ctx: VerifyContext
    note: str | None
    cfg: Mapping[str, Any]
    cap: float
    duration: int
    folder: Path
    fps: int
    se_cfg: Mapping[str, Any]


def _read(path: Path, model: type[BaseModel], missing: str) -> Any:
    if not path.is_file():
        raise ConfigError(missing)
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{path.name} hỏng — chạy lại lệnh tạo ra nó.") from e


def _cap(cfg: Mapping[str, Any], duration: int) -> float:
    """Cap for the smallest duration bracket that covers the video (the largest otherwise)."""
    caps = sorted((int(k), float(v)) for k, v in cfg["cost_cap_vnd"].items())
    return next((v for k, v in caps if duration <= k), caps[-1][1])


def _job(options_file: Path, folder: Path, rules: Rules) -> _Job:
    msgs = rules["input_msgs"]
    plan: Plan = _read(folder / EDIT_DIR / PLAN_FILE, Plan, msgs["PICK_NO_PLAN"])
    if plan.driver != "speech":
        raise ConfigError(msgs["PHRASES_NOT_SPEECH_STYLE"].format(style=plan.options["style"]))
    phrases: PhrasesFile = _read(folder / EDIT_DIR / PHRASES_FILE, PhrasesFile,
                                 msgs["SPEECH_DRAFT_NO_PHRASES"])  # fmt: skip
    pick: PickFile = _read(folder / EDIT_DIR / PICK_FILE, PickFile, msgs["DRAFT_NO_PICK"])
    raw = read_options_file(options_file.expanduser().resolve())
    opts: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    style = plan.options["style"]
    if opts.get("phong_cach") != style or pick.style != style or phrases.style != style:
        raise ConfigError(msgs["PICK_STALE_PLAN"].format(plan=style, opt=opts.get("phong_cach")))
    ctx = VerifyContext(plan=plan, phrases=phrases, pick=pick, rules=rules)
    duration = int(plan.options["duration"])
    return _Job(plan=plan, phrases=phrases, pick=pick, ctx=ctx, note=opts.get("ghi_chu"),
               cfg=rules["ai"], cap=_cap(rules["ai"], duration), duration=duration,
               folder=folder, fps=int(rules["render"]["fps"]),
               se_cfg=rules["speech_edit"])  # fmt: skip


def _log(folder: Path, **entry: Any) -> None:
    line = {"time": datetime.now(UTC).isoformat(timespec="seconds"), "mode": "speech", **entry}
    with (folder / EDIT_DIR / LOG_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _resolve(d: AiSpeechDraft, job: _Job) -> tuple[SpeechDraft | None, Issue | None]:
    """Turn a checked answer into a renderable SpeechDraft: chain the chosen phrases, then
    place each requested B-roll on the beat window default_overlays computes for its phrase
    (5c-1's rule-based placement, now filled in only where the AI asked for a B-roll)."""
    phrases = [job.ctx.phrase_by_id[s.phrase_id] for s in d.segments]
    cuts = aroll_timeline(phrases, job.fps)
    slots = {o.phrase_id: o for o in default_overlays(
        cuts, job.plan.beat_sec, job.plan.beats_per_scene_allowed,
        float(job.se_cfg["broll_max_shift_sec"]))}  # fmt: skip
    broll: list[BrollPlacement] = []
    for s, c in zip(d.segments, cuts, strict=True):
        if s.broll is None:
            continue
        slot = slots.get(c.phrase_id)
        if slot is None:
            return None, Issue("SNAP", "ai", f"Câu '{s.phrase_id}' quá ngắn, không đủ chỗ cho "
                               "B-roll theo phách.", d.draft)  # fmt: skip
        cand = job.ctx.cand_by_id[s.broll]
        broll.append(BrollPlacement(overlay=slot, clip=cand.clip, start=cand.start,
                                    end=cand.end, peak=cand.peak))  # fmt: skip
    return SpeechDraft(draft=d.draft, label=d.label, cuts=cuts, broll=broll,
                       missing_shots=d.missing_shots, warnings=d.warnings), None  # fmt: skip


def _judge(
    reply: AiReply,
    letters: Sequence[str],
    kept: Mapping[str, AiSpeechDraft],
    job: _Job,
    rules: Rules,
) -> tuple[dict[str, AiSpeechDraft], dict[str, SpeechDraft], list[Issue], AiSpeechResponse | None]:
    """Parse + S1–S6 + resolve. Returns (all drafts, resolved good ones, findings, answer)."""
    if reply.stop_reason == "max_tokens":
        truncated = Issue("S1", "ai", rules["input_msgs"]["SPEECH_DRAFT_TRUNCATED"])
        return dict(kept), {}, [truncated], None
    part = VerifyContext(plan=job.plan, phrases=job.phrases, pick=job.pick, rules=rules,
                         letters=list(letters))  # fmt: skip
    resp, issues = parse_speech_response(reply.text, part)
    if resp is None:
        return dict(kept), {}, issues, None
    merged = {**kept, **{d.draft: d for d in resp.drafts}}
    fixed, issues = verify_speech_drafts(AiSpeechResponse(drafts=list(merged.values())), job.ctx)
    drafts = {d.draft: d for d in fixed.drafts}
    resolved: dict[str, SpeechDraft] = {}
    for letter, d in drafts.items():
        s, err = _resolve(d, job)
        if err is not None:
            issues.append(err)
        elif s is not None:
            resolved[letter] = s
    return drafts, resolved, issues, resp


def _retry_errors(issues: Sequence[Issue], kept: Mapping[str, AiSpeechDraft]) -> list[str]:
    lines = [str(i) for i in issues if i.level == "ai"]
    for letter, d in sorted(kept.items()):
        ids = ", ".join(s.phrase_id for s in d.segments)
        lines.append(f"Bản {letter} đã đạt, giữ nguyên (câu mở {d.segments[0].phrase_id}; "
                     f"câu: {ids}).")  # fmt: skip
    return lines


def _check_budget(job: _Job, spent: float, prompt: str, rules: Rules) -> None:
    est_in = estimate_tokens(SYSTEM_PROMPT_SPEECH + prompt, float(job.cfg["chars_per_token"]))
    worst = cost_vnd(est_in, int(job.cfg["max_tokens"]), job.cfg)
    if spent + worst > job.cap:
        _log(job.folder, stopped="cost_cap", spent_vnd=spent, next_worst_vnd=worst, cap_vnd=job.cap)
        raise SpeechCostCapReached(rules["input_msgs"]["SPEECH_DRAFT_COST_CAP"].format(
            spent=spent, next=worst, cap=job.cap, duration=job.duration))  # fmt: skip


def _loop(job: _Job, caller: Caller, model: str, rules: Rules) -> SpeechDraftsResult:
    letters_all = job.ctx.expected
    letters: list[str] = list(letters_all)
    kept: dict[str, AiSpeechDraft] = {}
    errors: list[str] = []
    previous: AiSpeechResponse | None = None
    spent, tok_in, tok_out = 0.0, 0, 0
    issues: list[Issue] = []
    tol = float(job.se_cfg["duration_tolerance_pct"])
    for attempt in range(1, int(job.cfg["max_attempts"]) + 1):
        prompt = build_speech_user_prompt(job.plan, job.phrases, job.pick, job.note, letters, tol,
                                          errors=errors, previous=previous)  # fmt: skip
        _check_budget(job, spent, prompt, rules)
        try:
            reply = caller(SYSTEM_PROMPT_SPEECH, prompt, response_schema(letters))
        except AiError as e:  # nothing billed, but the incident belongs in the log
            _log(job.folder, attempt=attempt, model=model, drafts=letters, cost_vnd=0,
                spent_vnd=round(spent, 2), error=str(e))  # fmt: skip
            raise
        cost = cost_vnd(reply.input_tokens, reply.output_tokens, job.cfg)
        spent, tok_in, tok_out = (
            spent + cost,
            tok_in + reply.input_tokens,
            tok_out + reply.output_tokens,
        )
        drafts, resolved, issues, resp = _judge(reply, letters, kept, job, rules)
        bad = {i.draft for i in issues if i.level == "ai"}
        _log(job.folder, attempt=attempt, model=reply.model, drafts=letters,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost_vnd=cost,
            spent_vnd=round(spent, 2), stop_reason=reply.stop_reason,
            issues=sorted({i.code for i in issues if i.level == "ai"}))  # fmt: skip
        if not bad:
            return SpeechDraftsResult(model=reply.model, attempts=attempt, input_tokens=tok_in,
                                      output_tokens=tok_out, cost_vnd=round(spent, 2),
                                      cap_vnd=job.cap,
                                      drafts=[resolved[x] for x in sorted(resolved)],
                                      notes=[str(i) for i in issues])  # fmt: skip
        if resp is None:  # unreadable answer: ask the same drafts again, keep the good ones
            redo = letters
        else:
            redo = sorted(letters_all) if None in bad else sorted(x for x in bad if x)
            kept = {x: d for x, d in drafts.items() if x not in redo}
        letters, previous = redo, resp
        errors = _retry_errors(issues, kept)
    left = "; ".join(str(i) for i in issues if i.level == "ai")
    raise SpeechDraftError(rules["input_msgs"]["SPEECH_DRAFT_FAILED"].format(
        attempts=int(job.cfg["max_attempts"]), issues=left),
        [i for i in issues if i.level == "ai"])  # fmt: skip


def build_speech_drafts(
    options_file: Path, folder: Path, rules: Rules | None = None, caller: Caller | None = None,
    settings: Settings | None = None, dry_run: bool = False, max_vnd: float | None = None,
) -> tuple[SpeechDraftsResult | DryRun, Path | None]:  # fmt: skip
    """Ask Claude for the speech-mode drafts of <folder>/edit/plan.json and write
    edit/speech_drafts.json.

    Raises:
        ConfigError: plan/phrases/candidates/options missing, corrupt, out of date, or the
            plan's style is not speech-driven.
        SettingsError: no API key (only when a real call is needed).
        SpeechCostCapReached / SpeechDraftError: budget or check rounds exhausted (nothing
            written).
        AiError: the API call itself failed.

    `max_vnd` (what a person approved for a queued job) can only lower the cost cap.
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    job = _job(options_file, folder, rules)
    if max_vnd is not None and not dry_run:
        job.cap = min(job.cap, max_vnd)
    settings = settings if settings is not None else load_settings()
    model = settings.gani_dv_claude_model or str(job.cfg["model"])
    if dry_run:
        tol = float(job.se_cfg["duration_tolerance_pct"])
        prompt = build_speech_user_prompt(job.plan, job.phrases, job.pick, job.note,
                                          job.ctx.expected, tol)  # fmt: skip
        est = estimate_tokens(SYSTEM_PROMPT_SPEECH + prompt, float(job.cfg["chars_per_token"]))
        return DryRun(model=model, input_tokens=est, input_only_vnd=cost_vnd(est, 0, job.cfg),
                      worst_call_vnd=cost_vnd(est, int(job.cfg["max_tokens"]), job.cfg),
                      cap_vnd=job.cap, max_attempts=int(job.cfg["max_attempts"])), None  # fmt: skip
    if caller is None:
        caller = make_caller(require_api_key(settings), model, job.cfg)
    result = _loop(job, caller, model, rules)
    path = folder / EDIT_DIR / OUT_FILE
    tmp = path.with_suffix(".json.tmp")
    out = SpeechDraftsFile(model=result.model, cost_vnd=result.cost_vnd, drafts=result.drafts)
    tmp.write_text(out.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return result, path
