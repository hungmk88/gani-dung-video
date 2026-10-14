"""Option-compliance checks V1–V10 on AI drafts (phase 4c, spec §7.4). Pure functions.

Each finding says who fixes it: "ai" → sent back to Claude on the next round (max 3 rounds,
risk R2), "auto" → code fixed it already (V3 rounding), "warn" → kept as a warning (V6).
V10 (phase 7c) only exists for 3-minute plans: one short, distinct, claim-free title per chapter.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from gani_dung_video.domain.drafts import AiDraft, AiResponse, AiSegment
from gani_dung_video.domain.glossary import find_banned_claims, tokens
from gani_dung_video.domain.pick import Candidate, PickFile
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules

LETTERS = "ABC"


@dataclass(frozen=True)
class Issue:
    code: str  # V1..V10 or SNAP
    level: str  # ai | auto | warn
    msg: str  # Vietnamese; also what Claude reads on a retry
    draft: str | None = None

    def __str__(self) -> str:
        where = f" [{self.draft}]" if self.draft else ""
        return f"{self.code}{where}: {self.msg}"


@dataclass(frozen=True)
class VerifyContext:
    plan: Plan
    pick: PickFile
    rules: Rules
    banned: Sequence[str]
    promo_deadline: str | None = None
    letters: Sequence[str] | None = None  # drafts expected in this answer (default: all)
    cand_by_id: dict[str, Candidate] = field(init=False)

    def __post_init__(self) -> None:
        by_id = {c.id: c for b in self.pick.blocks for c in b.candidates}
        object.__setattr__(self, "cand_by_id", by_id)

    @property
    def expected(self) -> list[str]:
        return list(self.letters or LETTERS[: int(self.plan.options["drafts"])])


def _has(text: str, phrase: str) -> bool:
    t, p = tokens(text), tokens(phrase)
    return bool(p) and any(t[i : i + len(p)] == p for i in range(len(t)))


# ─────────────── V1 ───────────────


def parse_response(raw: object, ctx: VerifyContext) -> tuple[AiResponse | None, list[Issue]]:
    """V1: valid JSON of the schema, the expected drafts, known blocks and candidate ids."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        resp = AiResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        return None, [Issue("V1", "ai", f"JSON không đúng cấu trúc yêu cầu ({type(e).__name__}).")]
    issues: list[Issue] = []
    got, want = sorted(d.draft for d in resp.drafts), sorted(ctx.expected)
    if got != want:
        msg = f"Cần đúng {len(want)} bản: {', '.join(want)}; nhận được {', '.join(got) or 'không'}."
        issues.append(Issue("V1", "ai", msg))
    roles = {b.role for b in ctx.plan.blocks}
    for d in resp.drafts:
        issues += _title_structure(d, ctx)
        for s in d.segments:
            if s.block not in roles:
                issues.append(Issue("V1", "ai", f"Khối '{s.block}' không có trong plan.", d.draft))
            elif s.scene_id not in ctx.cand_by_id:
                issues.append(Issue("V1", "ai", f"Cảnh '{s.scene_id}' không có trong danh "
                                                "sách ứng viên.", d.draft))  # fmt: skip
    return (None, issues) if issues else (resp, [])


def _title_structure(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V1 for chapters: exactly one title for each chapter of the plan, none for another."""
    want = [c.id for c in ctx.plan.chapters]
    if not want:
        return []
    got = [t.chapter for t in d.chapter_titles]
    if sorted(got) == sorted(want):
        return []
    msg = (f"Cần đúng {len(want)} tiêu đề chương ({', '.join(want)}), mỗi chương một tiêu đề; "
           f"nhận được {', '.join(got) or 'không có'}.")  # fmt: skip
    return [Issue("V1", "ai", msg, d.draft)]


# ─────────────── V2, V3 ───────────────


def _round_beats(d: AiDraft, allowed: Sequence[int]) -> tuple[AiDraft, list[Issue]]:
    """V3: beats outside the allowed set are rounded to the nearest value (ties go down)."""
    issues, segs = [], []
    for s in d.segments:
        if s.beats in allowed:
            segs.append(s)
            continue
        n = min(allowed, key=lambda a: (abs(a - s.beats), a))
        issues.append(Issue("V3", "auto", f"Cảnh '{s.scene_id}' {s.beats} phách → {n} phách.",
                            d.draft))  # fmt: skip
        segs.append(s.model_copy(update={"beats": n}))
    return d.model_copy(update={"segments": segs}), issues


def _check_beats(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V2: total = plan exactly; each block within one scene of the plan."""
    plan, tol = ctx.plan, max(ctx.plan.beats_per_scene_allowed)
    out = []
    for b in plan.blocks:
        n = sum(s.beats for s in d.segments if s.block == b.role)
        if abs(n - b.beats) > tol:
            out.append(Issue("V2", "ai", f"Khối '{b.role}' có {n} phách, plan là {b.beats}.",
                             d.draft))  # fmt: skip
    total = sum(s.beats for s in d.segments)
    if total != plan.planned_beats:
        out.append(Issue("V2", "ai", f"Tổng {total} phách, phải đúng {plan.planned_beats}.",
                         d.draft))  # fmt: skip
    out += _check_block_order(d, ctx)
    return out


def _check_block_order(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V2: the blocks follow the plan's order and each is one unbroken run of cuts.

    Counting beats per block does not see the order, yet everything downstream assumes it: a
    block's caption is held from its first cut to its last, and a chapter title sits on its
    first cut. A draft that goes main, cta, main, hook has the right counts and overlapping
    captions (T6, found only after the paid call). Roles the plan does not have are V1's
    business and left out here."""
    order = [b.role for b in ctx.plan.blocks]
    rank = {r: i for i, r in enumerate(order)}
    runs = [r for r, _ in itertools.groupby(s.block for s in d.segments if s.block in rank)]
    ranks = [rank[r] for r in runs]
    if ranks == sorted(ranks) and len(set(runs)) == len(runs):
        return []
    return [Issue("V2", "ai", f"Thứ tự khối sai: plan đi {' → '.join(order)}, bản nháp đang đi "
                              f"{' → '.join(runs)}. Mỗi khối là một mạch liền, đúng thứ tự "
                              "plan.", d.draft)]  # fmt: skip


# ─────────────── V4, V5, V6 ───────────────


def _check_reuse(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V4: no repeated segment, ≤ N segments per clip at least `gap` s apart, no twin pair."""
    vis = ctx.rules["vision"]
    cap, gap = int(vis["max_segments_per_clip"]), float(vis["segment_gap_sec"])
    ids = [s.scene_id for s in d.segments]
    out = [Issue("V4", "ai", f"Cảnh '{i}' dùng {ids.count(i)} lần.", d.draft)
           for i in dict.fromkeys(ids) if ids.count(i) > 1]  # fmt: skip
    by_clip: dict[str, list[Candidate]] = {}
    for i in dict.fromkeys(ids):
        c = ctx.cand_by_id[i]
        by_clip.setdefault(c.clip, []).append(c)
    for clip, cs in by_clip.items():
        if len(cs) > cap:
            out.append(Issue("V4", "ai", f"Dùng {len(cs)} đoạn từ {clip}, tối đa {cap}.", d.draft))
        cs = sorted(cs, key=lambda c: c.start)
        for first, second in itertools.pairwise(cs):
            if second.start - first.end < gap - 1e-6:
                out.append(Issue("V4", "ai", f"'{first.id}' và '{second.id}' cùng {clip} cách "
                                             f"nhau dưới {gap:g} giây.", d.draft))  # fmt: skip
    used = set(ids)
    for x, y in ctx.pick.similar_pairs:
        if x in used and y in used:
            out.append(Issue("V4", "ai", f"'{x}' và '{y}' giống nhau, chỉ dùng một.", d.draft))
    return out


def _check_candidates(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V5: every segment comes from its own block's candidates (flag-free by phase 4b)."""
    allowed = {b.role: {c.id for c in b.candidates} for b in ctx.pick.blocks}
    return [Issue("V5", "ai", f"Cảnh '{s.scene_id}' không nằm trong ứng viên khối '{s.block}'.",
                  d.draft)
            for s in d.segments if s.scene_id not in allowed.get(s.block, set())]  # fmt: skip


def _check_required(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V6: a block with required tags shows one of them, or the draft lists it as missing."""
    names: Mapping[str, str] = ctx.rules["pick"]["tag_names"]
    listed = set(d.missing_shots)
    out = []
    for b in ctx.plan.blocks:
        if not b.required_tags:
            continue
        labels = {lb for s in d.segments if s.block == b.role
                  for lb in ctx.cand_by_id[s.scene_id].labels}  # fmt: skip
        if labels & set(b.required_tags) or any(names.get(t, t) in listed for t in b.required_tags):
            continue
        want = ", ".join(names.get(t, t) for t in b.required_tags)
        out.append(Issue("V6", "warn", f"Khối '{b.role}' thiếu cảnh bắt buộc: {want}.", d.draft))
    return out


# ─────────────── V7, V8 ───────────────


def _check_text(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V7: words per line, texts per block / every n scenes, banned claims."""
    rules: Mapping[str, Any] = ctx.plan.text_rules
    cta_role = ctx.plan.blocks[-1].role
    texts = [s for s in d.segments if s.text.strip()]
    out = []
    for s in texts:
        n = len(s.text.split())
        if s.block != cta_role and "max_words" in rules and n > int(rules["max_words"]):
            out.append(Issue("V7", "ai", f"Chữ '{s.text}' có {n} từ, tối đa {rules['max_words']}.",
                             d.draft))  # fmt: skip
        if s.block != cta_role and "min_words" in rules and n < int(rules["min_words"]):
            out.append(Issue("V7", "ai", f"Chữ '{s.text}' có {n} từ, tối thiểu "
                                         f"{rules['min_words']}.", d.draft))  # fmt: skip
        for phrase in find_banned_claims(s.text, ctx.banned):
            out.append(Issue("V7", "ai", f"Chữ '{s.text}' chứa cụm cấm '{phrase}'.", d.draft))
    if "per_block" in rules:
        for b in ctx.plan.blocks:
            k = sum(1 for s in texts if s.block == b.role)
            if k > int(rules["per_block"]):
                out.append(Issue("V7", "ai", f"Khối '{b.role}' có {k} dòng chữ, tối đa "
                                             f"{rules['per_block']}.", d.draft))  # fmt: skip
    if "per_chapter" in rules:
        lo, hi = (int(x) for x in rules["per_chapter"])
        for c in ctx.plan.chapters:
            k = sum(1 for s in texts if s.block in c.blocks)
            if not lo <= k <= hi:
                msg = f"Chương '{c.id}' có {k} dòng chữ, cần từ {lo} đến {hi}."
                out.append(Issue("V7", "ai", msg, d.draft))
    if "every_n_scenes" in rules:
        limit = math.ceil(len(d.segments) / int(rules["every_n_scenes"]))
        if len(texts) > limit:
            out.append(Issue("V7", "ai", f"{len(texts)} dòng chữ cho {len(d.segments)} cảnh, tối "
                                         f"đa {limit} (1 chữ/{rules['every_n_scenes']} cảnh).",
                             d.draft))  # fmt: skip
    return out


def _check_cta(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V8: the last block shows the purpose's CTA; a promotion shows its deadline."""
    last = ctx.plan.blocks[-1]
    out = []
    if last.text and not any(_has(s.text, last.text) for s in d.segments if s.block == last.role):
        out.append(Issue("V8", "ai", f"Khối '{last.role}' phải có chữ '{last.text}'.", d.draft))
    if ctx.promo_deadline and not any(_has(s.text, ctx.promo_deadline) for s in d.segments):
        out.append(Issue("V8", "ai", f"Khuyến mãi phải hiện thời hạn '{ctx.promo_deadline}' "
                                     "trên hình.", d.draft))  # fmt: skip
    return out


# ─────────────── V10 ───────────────


def _check_titles(d: AiDraft, ctx: VerifyContext) -> list[Issue]:
    """V10: each chapter title is filled in, short, free of banned claims and unlike the others."""
    if not ctx.plan.chapters:
        return []
    limit = ctx.plan.text_rules.get("title_max_words")
    out: list[Issue] = []
    seen: dict[tuple[str, ...], str] = {}
    for t in d.chapter_titles:
        words = t.title.split()
        if not words:
            out.append(Issue("V10", "ai", f"Chương '{t.chapter}' chưa có tiêu đề.", d.draft))
            continue
        if limit is not None and len(words) > int(limit):
            msg = (
                f"Tiêu đề '{t.title}' của chương '{t.chapter}' có {len(words)} từ, tối đa {limit}."
            )
            out.append(Issue("V10", "ai", msg, d.draft))
        for phrase in find_banned_claims(t.title, ctx.banned):
            out.append(Issue("V10", "ai", f"Tiêu đề '{t.title}' của chương '{t.chapter}' chứa "
                                          f"cụm cấm '{phrase}'.", d.draft))  # fmt: skip
        key = tuple(tokens(t.title))
        if key in seen:
            out.append(Issue("V10", "ai", f"Chương '{t.chapter}' trùng tiêu đề với chương "
                                          f"'{seen[key]}'.", d.draft))  # fmt: skip
        seen.setdefault(key, t.chapter)
    return out


# ─────────────── V9 ───────────────


def _check_variety(drafts: Sequence[AiDraft], ctx: VerifyContext) -> list[Issue]:
    """V9: later drafts need a different hook and ≤ max_scene_overlap shared scenes."""
    limit = float(ctx.rules["drafts"]["max_scene_overlap"])
    out = []
    for i, later in enumerate(drafts):
        mine = {s.scene_id for s in later.segments}
        for earlier in drafts[:i]:
            if (
                later.segments
                and earlier.segments
                and (later.segments[0].scene_id == earlier.segments[0].scene_id)
            ):
                out.append(Issue("V9", "ai", f"Hook trùng bản {earlier.draft}.", later.draft))
            shared = len(mine & {s.scene_id for s in earlier.segments}) / max(len(mine), 1)
            if shared > limit:
                out.append(Issue("V9", "ai", f"Trùng {shared:.0%} cảnh với bản {earlier.draft}, "
                                             f"tối đa {limit:.0%}.", later.draft))  # fmt: skip
    return out


def verify_drafts(resp: AiResponse, ctx: VerifyContext) -> tuple[AiResponse, list[Issue]]:
    """Run V2–V9 on a parsed answer; returns the V3-fixed answer and all findings."""
    allowed = ctx.plan.beats_per_scene_allowed
    fixed: list[AiDraft] = []
    issues: list[Issue] = []
    for d in sorted(resp.drafts, key=lambda x: x.draft):
        d2, auto = _round_beats(d, allowed)
        fixed.append(d2)
        issues += auto
        for check in (_check_beats, _check_reuse, _check_candidates, _check_required,
                      _check_text, _check_cta, _check_titles):  # fmt: skip
            issues += check(d2, ctx)
    issues += _check_variety(fixed, ctx)
    return AiResponse(drafts=fixed), issues


def segments_of(resp: AiResponse, letter: str) -> list[AiSegment]:
    return next((d.segments for d in resp.drafts if d.draft == letter), [])
