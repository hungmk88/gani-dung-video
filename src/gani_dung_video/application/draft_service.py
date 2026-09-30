"""Use case: plan + candidates + note → Claude → V1–V9 → snap to beats → edit/drafts.json (4c).

Loop (spec §7.4, risk R2): one call for all drafts; findings Claude must fix go back with the
rejected answer, and only the drafts with such findings are asked again; at most
`ai.max_attempts` rounds. Before every call the worst possible cost of that call is added to
what was already spent; if that could break the cap, nothing is sent (decision 29/09/2026).
edit/ai_log.jsonl records tokens and cost per round — never the prompt or the key.
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

from gani_dung_video.application.verify_recorder import VerifyRecorder
from gani_dung_video.domain.beats import (
    DraftSegment,
    SceneRef,
    SnapError,
    snap_to_beats,
)
from gani_dung_video.domain.drafts import (
    AiDraft,
    AiResponse,
    SnappedDraft,
    build_user_prompt,
    cost_vnd,
    estimate_tokens,
    response_schema,
    system_prompt_for,
)
from gani_dung_video.domain.feasibility import check_feasible
from gani_dung_video.domain.pick import PickFile
from gani_dung_video.domain.plan import Plan, beats_of
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.verify import Issue, VerifyContext, parse_response, verify_drafts
from gani_dung_video.domain.verify_report import make_round
from gani_dung_video.infrastructure.claude_client import AiError, AiReply, Caller, make_caller
from gani_dung_video.infrastructure.config_files import (
    ConfigError,
    GlossaryFile,
    load_glossary,
    load_rules,
    read_options_file,
)
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.settings import Settings, load_settings, require_api_key

logger = logging.getLogger(__name__)
PLAN_FILE, PICK_FILE, OUT_FILE, LOG_FILE = (
    "plan.json",
    "candidates.json",
    "drafts.json",
    "ai_log.jsonl",
)


class DraftError(RuntimeError):
    """Claude did not pass the checks within the allowed rounds; nothing is rendered."""

    def __init__(self, msg: str, issues: Sequence[Issue] = ()) -> None:
        super().__init__(msg)
        self.issues = list(issues)


class InfeasibleError(DraftError):
    """The picked footage cannot fill the plan (checked before any call; nothing was spent)."""


class CostCapReached(DraftError):
    """The next call could push the spend over the cap, so it was not sent."""


class DraftsFile(BaseModel):
    """Content of edit/drafts.json: drafts laid on the beat grid, ready for render (phase 5)."""

    model: str
    attempts: int
    input_tokens: int
    output_tokens: int
    cost_vnd: float
    cap_vnd: float
    drafts: list[SnappedDraft]
    notes: list[str]  # auto-fixes (V3) and warnings (V6) kept for the editor


@dataclass(frozen=True)
class DryRun:
    model: str
    input_tokens: int  # estimate
    input_only_vnd: float
    worst_call_vnd: float  # estimated input + max_tokens of output
    cap_vnd: float
    max_attempts: int


@dataclass
class _Job:
    plan: Plan
    pick: PickFile
    ctx: VerifyContext
    note: str | None
    cfg: Mapping[str, Any]
    cap: float
    duration: int
    folder: Path


def _read(path: Path, model: type[Plan] | type[PickFile], missing: str) -> Any:
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


def _job(options_file: Path, folder: Path, rules: Rules, glossary: GlossaryFile) -> _Job:
    msgs = rules["input_msgs"]
    plan: Plan = _read(folder / EDIT_DIR / PLAN_FILE, Plan, msgs["PICK_NO_PLAN"])
    pick: PickFile = _read(folder / EDIT_DIR / PICK_FILE, PickFile, msgs["DRAFT_NO_PICK"])
    raw = read_options_file(options_file.expanduser().resolve())
    opts: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    if opts.get("phong_cach") != plan.options["style"] or pick.style != plan.options["style"]:
        raise ConfigError(msgs["PICK_STALE_PLAN"].format(plan=plan.options["style"],
                                                         opt=opts.get("phong_cach")))  # fmt: skip
    promo = opts.get("khuyen_mai") if plan.options["purpose"] == "khuyen_mai" else None
    deadline = str(promo.get("thoi_han", "")).strip() if isinstance(promo, Mapping) else ""
    ctx = VerifyContext(plan=plan, pick=pick, rules=rules, banned=glossary.banned_claims,
                        promo_deadline=deadline or None)  # fmt: skip
    duration = int(plan.options["duration"])
    return _Job(plan=plan, pick=pick, ctx=ctx, note=opts.get("ghi_chu"), cfg=rules["ai"],
                cap=_cap(rules["ai"], duration), duration=duration, folder=folder)  # fmt: skip


def _log(folder: Path, **entry: Any) -> None:
    # "mode": speech drafts (5c-2) log to this same file, tagged "speech" (decision 29/09/2026).
    line = {"time": datetime.now(UTC).isoformat(timespec="seconds"), "mode": "music", **entry}
    with (folder / EDIT_DIR / LOG_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _snap(d: AiDraft, job: _Job) -> tuple[SnappedDraft | None, Issue | None]:
    refs = {c.id: SceneRef(clip=c.clip, start=c.start, end=c.end, peak=c.peak)
            for b in job.pick.blocks for c in b.candidates}  # fmt: skip
    try:
        cuts = [DraftSegment(**s.model_dump()) for s in d.segments]
        segs = snap_to_beats(cuts, beats_of(job.plan), refs)
    except SnapError as e:
        return None, Issue("SNAP", "ai", str(e), d.draft)
    return SnappedDraft(draft=d.draft, label=d.label, segments=segs,
                        missing_shots=d.missing_shots, warnings=d.warnings,
                        chapter_titles=d.chapter_titles), None  # fmt: skip


def _judge(
    reply: AiReply, letters: Sequence[str], kept: Mapping[str, AiDraft], job: _Job, rules: Rules
) -> tuple[dict[str, AiDraft], dict[str, SnappedDraft], list[Issue], AiResponse | None]:
    """Parse + V1–V9 + snap. Returns (all drafts, snapped good ones, findings, parsed answer)."""
    if reply.stop_reason == "max_tokens":
        return dict(kept), {}, [Issue("V1", "ai", rules["input_msgs"]["DRAFT_TRUNCATED"])], None
    part = VerifyContext(plan=job.plan, pick=job.pick, rules=rules, banned=job.ctx.banned,
                         promo_deadline=job.ctx.promo_deadline, letters=list(letters))  # fmt: skip
    resp, issues = parse_response(reply.text, part)
    if resp is None:
        return dict(kept), {}, issues, None
    merged = {**kept, **{d.draft: d for d in resp.drafts}}
    fixed, issues = verify_drafts(AiResponse(drafts=list(merged.values())), job.ctx)
    drafts = {d.draft: d for d in fixed.drafts}
    snapped: dict[str, SnappedDraft] = {}
    for letter, d in drafts.items():
        s, err = _snap(d, job)
        if err is not None:
            issues.append(err)
        elif s is not None:
            snapped[letter] = s
    return drafts, snapped, issues, resp


def _retry_errors(issues: Sequence[Issue], kept: Mapping[str, AiDraft]) -> list[str]:
    lines = [str(i) for i in issues if i.level == "ai"]
    for letter, d in sorted(kept.items()):
        ids = ", ".join(s.scene_id for s in d.segments)
        lines.append(
            f"Bản {letter} đã đạt, giữ nguyên (hook {d.segments[0].scene_id}; cảnh: {ids})."
        )
    return lines


def _check_budget(job: _Job, spent: float, prompt: str, rules: Rules) -> None:
    est_in = estimate_tokens(
        system_prompt_for(job.plan) + prompt, float(job.cfg["chars_per_token"])
    )
    worst = cost_vnd(est_in, int(job.cfg["max_tokens"]), job.cfg)
    if spent + worst > job.cap:
        _log(job.folder, stopped="cost_cap", spent_vnd=spent, next_worst_vnd=worst, cap_vnd=job.cap)
        raise CostCapReached(rules["input_msgs"]["DRAFT_COST_CAP"].format(
            spent=spent, next=worst, cap=job.cap, duration=job.duration))  # fmt: skip


def _loop(job: _Job, caller: Caller, model: str, rules: Rules, rec: VerifyRecorder) -> DraftsFile:
    letters_all = job.ctx.expected
    letters: list[str] = list(letters_all)
    kept: dict[str, AiDraft] = {}
    errors: list[str] = []
    previous: AiResponse | None = None
    spent, tok_in, tok_out = 0.0, 0, 0
    issues: list[Issue] = []
    for attempt in range(1, int(job.cfg["max_attempts"]) + 1):
        prompt = build_user_prompt(job.plan, job.pick, job.note, job.ctx.banned, letters,
                                   errors=errors, previous=previous)  # fmt: skip
        _check_budget(job, spent, prompt, rules)
        try:
            reply = caller(
                system_prompt_for(job.plan),
                prompt,
                response_schema(letters, [c.id for c in job.plan.chapters]),
            )
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
        drafts, snapped, issues, resp = _judge(reply, letters, kept, job, rules)
        bad = {i.draft for i in issues if i.level == "ai"}
        _log(job.folder, attempt=attempt, model=reply.model, drafts=letters,
             input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost_vnd=cost,
             spent_vnd=round(spent, 2), stop_reason=reply.stop_reason,
             issues=sorted({i.code for i in issues if i.level == "ai"}))  # fmt: skip
        rec.add(make_round(attempt, letters, resp is not None, cost, reply.input_tokens,
                           reply.output_tokens, reply.stop_reason, issues))  # fmt: skip
        if not bad:
            return DraftsFile(model=reply.model, attempts=attempt, input_tokens=tok_in,
                              output_tokens=tok_out, cost_vnd=round(spent, 2), cap_vnd=job.cap,
                              drafts=[snapped[x] for x in sorted(snapped)],
                              notes=[str(i) for i in issues])  # fmt: skip
        if resp is None:  # unreadable answer: ask the same drafts again, keep the good ones
            redo = letters
        else:
            redo = sorted(letters_all) if None in bad else sorted(x for x in bad if x)
            kept = {x: d for x, d in drafts.items() if x not in redo}
        letters, previous = redo, resp
        errors = _retry_errors(issues, kept)
    left = "; ".join(str(i) for i in issues if i.level == "ai")
    raise DraftError(rules["input_msgs"]["DRAFT_FAILED"].format(
        attempts=int(job.cfg["max_attempts"]), issues=left),
        [i for i in issues if i.level == "ai"])  # fmt: skip


def _recorded(job: _Job, caller: Caller, model: str, rules: Rules) -> DraftsFile:
    """Run the loop and leave edit/verify_report.json saying how it ended (6i)."""
    extra = ["V10"] if job.plan.chapters else []  # the title check only exists with chapters
    rec = VerifyRecorder(job.folder, "music", extra)
    try:
        result = _loop(job, caller, model, rules, rec)
    except CostCapReached:
        rec.finish("cost_cap")
        raise
    except AiError:
        rec.finish("api_error")
        raise
    except DraftError:
        rec.finish("failed")
        raise
    rec.finish("passed")
    return result


def build_drafts(
    options_file: Path, folder: Path, rules: Rules | None = None,
    glossary: GlossaryFile | None = None, caller: Caller | None = None,
    settings: Settings | None = None, dry_run: bool = False, max_vnd: float | None = None,
) -> tuple[DraftsFile | DryRun, Path | None]:  # fmt: skip
    """Ask Claude for the drafts of <folder>/edit/plan.json and write edit/drafts.json.

    Raises:
        ConfigError: plan/candidates/options missing, corrupt or out of date.
        InfeasibleError: the picked scenes cannot fill the plan (before any estimate or call).
        SettingsError: no API key (only when a real call is needed).
        CostCapReached / DraftError: budget or check rounds exhausted (nothing written).
        AiError: the API call itself failed.

    `max_vnd` (what a person approved for a queued job) can only lower the cost cap.
    """
    rules = rules if rules is not None else load_rules()
    glossary = glossary if glossary is not None else load_glossary()
    folder = folder.expanduser().resolve()
    job = _job(options_file, folder, rules, glossary)
    if rules["pick"].get("feasibility_gate", True):  # free: before the estimate and any call
        problem = check_feasible(job.plan, job.pick, rules, len(job.ctx.expected))
        if problem is not None:
            raise InfeasibleError(problem.message)
    if max_vnd is not None and not dry_run:
        job.cap = min(job.cap, max_vnd)
    settings = settings if settings is not None else load_settings()
    model = settings.gani_dv_claude_model or str(job.cfg["model"])
    if dry_run:
        prompt = build_user_prompt(job.plan, job.pick, job.note, job.ctx.banned, job.ctx.expected)
        est = estimate_tokens(
            system_prompt_for(job.plan) + prompt, float(job.cfg["chars_per_token"])
        )
        return DryRun(model=model, input_tokens=est, input_only_vnd=cost_vnd(est, 0, job.cfg),
                      worst_call_vnd=cost_vnd(est, int(job.cfg["max_tokens"]), job.cfg),
                      cap_vnd=job.cap, max_attempts=int(job.cfg["max_attempts"])), None  # fmt: skip
    if caller is None:
        caller = make_caller(require_api_key(settings), model, job.cfg)
    result = _recorded(job, caller, model, rules)
    path = folder / EDIT_DIR / OUT_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return result, path
