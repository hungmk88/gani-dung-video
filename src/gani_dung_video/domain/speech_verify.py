"""Option-compliance checks S1–S6 on AI speech drafts (phase 5c-2). Pure functions.

Adapts phase 4c's verify.py (V1–V9) to phrase selection. Speech mode already dropped
banned-claim and over-length phrases at 5c-1 (phrases.json), and the CTA line is added by code
(domain/edl.py's _cta), not written by the AI — so there is no V7 (banned claims) or V8 (CTA)
equivalent here; S1 additionally refuses B-roll on the opening or closing phrase (the AI
chooses B-roll placement itself in 5c-2, unlike the rule-based default_overlays of 5c-1).

Each finding says who fixes it: "ai" → sent back to Claude on the next round (max 3 rounds,
same risk R2 as 4c), "warn" → kept as a warning (S5).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from gani_dung_video.domain.pick import Candidate, PickFile
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.speech_ai import AiSpeechDraft, AiSpeechResponse
from gani_dung_video.domain.speech_edit import Phrase, PhrasesFile, cut_length

LETTERS = "ABC"


@dataclass(frozen=True)
class Issue:
    code: str  # S1..S6 or SNAP
    level: str  # ai | warn
    msg: str  # Vietnamese; also what Claude reads on a retry
    draft: str | None = None

    def __str__(self) -> str:
        where = f" [{self.draft}]" if self.draft else ""
        return f"{self.code}{where}: {self.msg}"


@dataclass(frozen=True)
class VerifyContext:
    plan: Plan
    phrases: PhrasesFile
    pick: PickFile
    rules: Rules
    letters: Sequence[str] | None = None  # drafts expected in this answer (default: all)
    phrase_by_id: dict[str, Phrase] = field(init=False)
    cand_by_id: dict[str, Candidate] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "phrase_by_id", {p.id: p for p in self.phrases.phrases})
        by_id = {c.id: c for b in self.pick.blocks for c in b.candidates}
        object.__setattr__(self, "cand_by_id", by_id)

    @property
    def expected(self) -> list[str]:
        return list(self.letters or LETTERS[: int(self.plan.options["drafts"])])


# ─────────────── S1 ───────────────


def parse_speech_response(raw: object, ctx: VerifyContext
                          ) -> tuple[AiSpeechResponse | None, list[Issue]]:  # fmt: skip
    """S1: valid JSON of the schema, the expected drafts, known blocks/phrases/B-roll ids."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        resp = AiSpeechResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        return None, [Issue("S1", "ai", f"JSON không đúng cấu trúc yêu cầu ({type(e).__name__}).")]
    issues: list[Issue] = []
    got, want = sorted(d.draft for d in resp.drafts), sorted(ctx.expected)
    if got != want:
        msg = f"Cần đúng {len(want)} bản: {', '.join(want)}; nhận được {', '.join(got) or 'không'}."
        issues.append(Issue("S1", "ai", msg))
    roles = {b.role for b in ctx.plan.blocks}
    for d in resp.drafts:
        if not d.segments:
            issues.append(Issue("S1", "ai", "Bản nháp không có câu nào.", d.draft))
        for s in d.segments:
            if s.phrase_id not in ctx.phrase_by_id:
                issues.append(Issue("S1", "ai", f"Câu '{s.phrase_id}' không có trong danh sách "
                                                "câu dùng được.", d.draft))  # fmt: skip
            if s.block not in roles:
                issues.append(Issue("S1", "ai", f"Khối '{s.block}' không có trong plan.", d.draft))
            if s.broll is not None and s.broll not in ctx.cand_by_id:
                issues.append(Issue("S1", "ai", f"B-roll '{s.broll}' không có trong danh sách "
                                                "ứng viên.", d.draft))  # fmt: skip
    return (None, issues) if issues else (resp, [])


def _check_edges(d: AiSpeechDraft) -> list[Issue]:
    """S1: the opening phrase (hook) and the closing phrase (CTA) show the speaker, no B-roll."""
    out = []
    if d.segments and d.segments[0].broll is not None:
        out.append(Issue("S1", "ai", f"Câu mở đầu '{d.segments[0].phrase_id}' không được có "
                                     "B-roll.", d.draft))  # fmt: skip
    if d.segments and d.segments[-1].broll is not None:
        out.append(Issue("S1", "ai", f"Câu cuối (CTA) '{d.segments[-1].phrase_id}' không được "
                                     "có B-roll.", d.draft))  # fmt: skip
    return out


# ─────────────── S2 ───────────────


def _check_duration(d: AiSpeechDraft, ctx: VerifyContext) -> list[Issue]:
    """S2: the total length of the cuts stays inside the tolerance of the requested length.

    Judged on the padded, frame-accurate cut windows that are rendered, not on the spoken
    words: with 0.2 s of padding per phrase the two differ by seconds over a whole video, and
    E1-DUR measures the cuts."""
    fps = int(ctx.rules["render"]["fps"])
    total = sum(cut_length(ctx.phrase_by_id[s.phrase_id], fps) for s in d.segments
               if s.phrase_id in ctx.phrase_by_id)  # fmt: skip
    target = float(ctx.plan.options["duration"])
    tol = float(ctx.rules["speech_edit"]["duration_tolerance_pct"])
    lo, hi = target * (1 - tol), target * (1 + tol)
    if not (lo - 1e-6 <= total <= hi + 1e-6):
        msg = (
            f"Tổng độ dài các câu đã chọn {total:.1f}s, phải trong khoảng {lo:.1f}–{hi:.1f}s "
            f"(mục tiêu {target:g}s ±{tol:.0%})."
        )
        return [Issue("S2", "ai", msg, d.draft)]
    return []


# ─────────────── S3, S4, S5 ───────────────


def _check_reuse(d: AiSpeechDraft, ctx: VerifyContext) -> list[Issue]:
    """S3: no repeated phrase or B-roll; ≤ N B-roll per clip ≥ gap s apart; no twin pair."""
    pids = [s.phrase_id for s in d.segments]
    out = [Issue("S3", "ai", f"Câu '{i}' dùng {pids.count(i)} lần.", d.draft)
           for i in dict.fromkeys(pids) if pids.count(i) > 1]  # fmt: skip
    broll_ids = [s.broll for s in d.segments if s.broll]
    out += [Issue("S3", "ai", f"B-roll '{i}' dùng {broll_ids.count(i)} lần.", d.draft)
            for i in dict.fromkeys(broll_ids) if broll_ids.count(i) > 1]  # fmt: skip
    vis = ctx.rules["vision"]
    cap, gap = int(vis["max_segments_per_clip"]), float(vis["segment_gap_sec"])
    by_clip: dict[str, list[Candidate]] = {}
    for i in dict.fromkeys(broll_ids):
        c = ctx.cand_by_id[i]
        by_clip.setdefault(c.clip, []).append(c)
    for clip, cs in by_clip.items():
        if len(cs) > cap:
            out.append(Issue("S3", "ai", f"Dùng {len(cs)} đoạn B-roll từ {clip}, tối đa "
                                         f"{cap}.", d.draft))  # fmt: skip
        cs = sorted(cs, key=lambda c: c.start)
        for first, second in itertools.pairwise(cs):
            if second.start - first.end < gap - 1e-6:
                out.append(Issue("S3", "ai", f"'{first.id}' và '{second.id}' cùng {clip} cách "
                                             f"nhau dưới {gap:g} giây.", d.draft))  # fmt: skip
    used = set(broll_ids)
    for x, y in ctx.pick.similar_pairs:
        if x in used and y in used:
            out.append(Issue("S3", "ai", f"'{x}' và '{y}' giống nhau, chỉ dùng một.", d.draft))
    return out


def _check_candidates(d: AiSpeechDraft, ctx: VerifyContext) -> list[Issue]:
    """S4: every B-roll comes from its own block's candidates (flag-free by phase 4b)."""
    allowed = {b.role: {c.id for c in b.candidates} for b in ctx.pick.blocks}
    bad = (s for s in d.segments if s.broll and s.broll not in allowed.get(s.block, set()))
    return [Issue("S4", "ai", f"B-roll '{s.broll}' không nằm trong ứng viên khối '{s.block}'.",
                  d.draft) for s in bad]  # fmt: skip


def _check_required(d: AiSpeechDraft, ctx: VerifyContext) -> list[Issue]:
    """S5: a block with required tags shows one of them (via its B-roll), or lists it missing."""
    names: dict[str, str] = ctx.rules["pick"]["tag_names"]
    listed = set(d.missing_shots)
    out = []
    for b in ctx.plan.blocks:
        if not b.required_tags:
            continue
        labels = {lb for s in d.segments if s.block == b.role and s.broll
                  for lb in ctx.cand_by_id[s.broll].labels}  # fmt: skip
        if labels & set(b.required_tags) or any(names.get(t, t) in listed for t in b.required_tags):
            continue
        want = ", ".join(names.get(t, t) for t in b.required_tags)
        out.append(Issue("S5", "warn", f"Khối '{b.role}' thiếu cảnh bắt buộc: {want}.", d.draft))
    return out


# ─────────────── S6 ───────────────


def _check_variety(drafts: Sequence[AiSpeechDraft], ctx: VerifyContext) -> list[Issue]:
    """S6: later drafts need a different opening phrase and ≤ max_scene_overlap shared phrases."""
    limit = float(ctx.rules["drafts"]["max_scene_overlap"])
    out = []
    for i, later in enumerate(drafts):
        mine = {s.phrase_id for s in later.segments}
        for earlier in drafts[:i]:
            if (
                later.segments
                and earlier.segments
                and (later.segments[0].phrase_id == earlier.segments[0].phrase_id)
            ):
                out.append(Issue("S6", "ai", f"Câu mở đầu trùng bản {earlier.draft}.", later.draft))
            shared = len(mine & {s.phrase_id for s in earlier.segments}) / max(len(mine), 1)
            if shared > limit:
                out.append(Issue("S6", "ai", f"Trùng {shared:.0%} câu với bản {earlier.draft}, "
                                             f"tối đa {limit:.0%}.", later.draft))  # fmt: skip
    return out


def verify_speech_drafts(resp: AiSpeechResponse, ctx: VerifyContext
                         ) -> tuple[AiSpeechResponse, list[Issue]]:  # fmt: skip
    """Run S1(edges)–S5 on a parsed answer; returns the answer unchanged and all findings."""
    drafts = sorted(resp.drafts, key=lambda x: x.draft)
    issues: list[Issue] = []
    for d in drafts:
        issues += _check_edges(d)
        for check in (_check_duration, _check_reuse, _check_candidates, _check_required):
            issues += check(d, ctx)
    issues += _check_variety(drafts, ctx)
    return AiSpeechResponse(drafts=drafts), issues
