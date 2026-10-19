"""Can the picked footage possibly fill the plan? (phase 6e, decided 30/09/2026). Pure functions.

Asking Claude for drafts costs money, and some plans can be shown to be impossible before that:
two candidates of at most 4 beats cannot fill a 24-beat block; nine scenes cannot give two drafts
that share at most 60% of their scenes and open differently. The gate runs on the free side of a
draft job and stops there (0đ).

It uses NECESSARY conditions only, each a relaxation of the rules the drafts are checked by, so
it refuses a plan only when V2, V4, V5 and V9 cannot all be met and never refuses one that could
be met (the property tests search exhaustively to prove it). Passing does not mean the AI will
succeed. What it relaxes on purpose: the gap between scenes of one clip, twin scenes, and the
order of scenes.

- V2: every block within one scene (`max(beats_per_scene_allowed)`) of its planned beats, and
  the total exactly the plan's;
- V4: a scene once, at most N scenes per clip;
- V5: a block uses only its own candidates;
- V9: with several drafts, the hooks differ and a later draft shares at most a set fraction of
  its scenes with an earlier one.

Missing tags alone never block (X8: "build with the nearest scene"): only a lack of scenes does.

`footage_fit` (phase 6h) is the softer question asked before a person approves the money: the plan
CAN be filled, but with how much room to spare? Nine different scenes for a plan that needs at most
nine pass the gate, yet Claude has nothing to swap when it repeats one (V4) and three rounds go
by. It never blocks; it measures, so the UI can warn.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from gani_dung_video.domain.block_names import block_name
from gani_dung_video.domain.pick import Candidate, PickFile
from gani_dung_video.domain.plan import Plan, PlanBlock
from gani_dung_video.domain.preflight import Rules

INFEASIBLE_EXIT_CODE = 3  # `draft` exits with this when the plan cannot be filled (0đ spent)
MAX_GROUP_BLOCKS = 12  # above this only single blocks and the whole plan are tried (2^k subsets)


@dataclass(frozen=True)
class Shortfall:
    """Why a plan cannot be filled: `need` beats/scenes are required, the footage has `have`."""

    kind: Literal["block", "group", "variety", "hook"]
    blocks: tuple[str, ...]
    need: int
    have: int
    message: str  # Vietnamese, ready to show


def _cap(c: Candidate) -> int:
    """The most beats a scene can carry: the longest count it is long enough for."""
    return max(c.fits_beats) if c.fits_beats else 0


def _distinct(cands: Iterable[Candidate]) -> dict[str, Candidate]:
    return {c.id: c for c in cands}  # a scene offered to several blocks is still one scene


def _supply(cands: Iterable[Candidate], per_clip: int) -> int:
    """Most beats these scenes can carry in one draft: each scene once, at most `per_clip` of
    any one clip (the gap between scenes of a clip is ignored: a relaxation)."""
    by_clip: dict[str, list[int]] = {}
    for c in _distinct(cands).values():
        by_clip.setdefault(c.clip, []).append(_cap(c))
    return sum(sum(sorted(caps, reverse=True)[:per_clip]) for caps in by_clip.values())


def _clusters(plan: Plan) -> list[list[int]]:
    """Blocks that belong together: the sub-blocks of a chapter form one cluster, every other
    block (and every block of a short video) is a cluster of its own."""
    out: list[list[int]] = []
    where: dict[str, int] = {}
    for i, b in enumerate(plan.blocks):
        if b.chapter is not None and b.chapter in where:
            out[where[b.chapter]].append(i)
            continue
        if b.chapter is not None:
            where[b.chapter] = len(out)
        out.append([i])
    return out


def _group_label(plan: Plan, idx: Sequence[int], rules: Rules) -> str:
    """Names for a failing group: a whole chapter reads as "chương 'Quy trình'", other blocks by
    their Vietnamese name (rules.json → block_names)."""
    chosen = set(idx)
    parts: list[str] = []
    for cluster in _clusters(plan):
        blocks = [plan.blocks[i] for i in cluster]
        if blocks[0].chapter is not None and chosen.issuperset(cluster):
            parts.append(f"chương '{block_name(rules, blocks[0].chapter)}'")
        else:
            parts += [f"'{block_name(rules, plan.blocks[i].role)}'" for i in cluster if i in chosen]
    return ", ".join(parts)


def _group_search(plan: Plan) -> Iterable[tuple[int, ...]]:
    """Block groups to test, smallest first. Every block alone; then every subset inside a
    cluster (a chapter has ~5 sub-blocks, so at most a few hundred); then every subset of
    clusters pooled (only the whole plan when there are more than MAX_GROUP_BLOCKS clusters,
    as before: 2^k subsets do not scale)."""
    clusters = _clusters(plan)
    for i in range(len(plan.blocks)):
        yield (i,)
    for cluster in clusters:
        for size in range(2, len(cluster) + 1):
            yield from itertools.combinations(cluster, size)
    k = len(clusters)
    for size in range(2, k + 1) if k <= MAX_GROUP_BLOCKS else [k]:
        for chosen in itertools.combinations(range(k), size):
            yield tuple(i for c in chosen for i in clusters[c])


def _tags(block: PlanBlock) -> str:
    return f" (nhãn: {', '.join(block.required_tags)})" if block.required_tags else ""


def check_feasible(plan: Plan, pick: PickFile, rules: Rules, drafts: int) -> Shortfall | None:
    """The first reason the plan cannot be filled by the picked scenes, or None.

    Checks, in order: each block alone; groups of blocks that share scenes (smallest first, the
    whole plan last); then, for several drafts, the hooks and the number of different scenes.
    """
    if plan.driver == "speech":  # speech mode has no scene blocks (cuts follow the voice)
        return None
    msgs = rules["input_msgs"]
    per_clip = int(rules["vision"]["max_segments_per_clip"])
    tol = max(plan.beats_per_scene_allowed)
    by_role = {b.role: b for b in pick.blocks}
    blocks = plan.blocks
    cands = {b.role: list(by_role[b.role].candidates) if b.role in by_role else [] for b in blocks}
    lone = len(blocks) == 1
    need = {b.role: (plan.planned_beats if lone else max(0, b.beats - tol)) for b in blocks}

    def group(idx: tuple[int, ...]) -> Shortfall | None:
        chosen = [blocks[i] for i in idx]
        pool = [c for b in chosen for c in cands[b.role]]
        want = sum(need[b.role] for b in chosen)
        if len(idx) == len(blocks):
            want = max(want, plan.planned_beats)  # V2: the total is exact
        have = _supply(pool, per_clip)
        if have >= want:
            return None
        count = len(_distinct(pool))
        roles = tuple(b.role for b in chosen)
        if len(idx) == 1:
            b = chosen[0]
            text = msgs["FEASIBLE_BLOCK"].format(
                block=block_name(rules, b.role), beats=plan.planned_beats if lone else b.beats,
                tol=tol,
                count=count, have=have, per_clip=per_clip, tags=_tags(b))  # fmt: skip
            return Shortfall("block", roles, want, have, text)
        text = msgs["FEASIBLE_GROUP"].format(blocks=_group_label(plan, idx, rules), need=want,
                                             count=count, have=have, per_clip=per_clip)  # fmt: skip
        return Shortfall("group", roles, want, have, text)

    for idx in _group_search(plan):  # single blocks, inside chapters, then pooled chapters
        if (found := group(idx)) is not None:
            return found
    if drafts >= 2 and blocks:
        return _variety(plan, cands, rules, drafts)
    return None


def _variety(plan: Plan, cands: dict[str, list[Candidate]], rules: Rules, drafts: int
             ) -> Shortfall | None:  # fmt: skip
    """Step 3 (V9): different hooks, and enough different scenes for the drafts to differ."""
    msgs: dict[str, Any] = rules["input_msgs"]
    first = plan.blocks[0]
    hooks = len(_distinct(cands[first.role]))
    if hooks < drafts:
        text = msgs["FEASIBLE_HOOK"].format(drafts=drafts, block=block_name(rules, first.role),
                                            count=hooks)  # fmt: skip
        return Shortfall("hook", (first.role,), drafts, hooks, text)
    every = _distinct(c for b in plan.blocks for c in cands[b.role])
    # Fewest scenes one draft can be made of: the highest-capacity ones until the total is met.
    beats, per_draft = 0, 0
    for cap in sorted((_cap(c) for c in every.values()), reverse=True):
        if beats >= plan.planned_beats:
            break
        beats, per_draft = beats + cap, per_draft + 1
    limit = float(rules["drafts"]["max_scene_overlap"])
    new_ones = math.ceil((1.0 - limit) * per_draft - 1e-9)  # a later draft: scenes not in the first
    need, have = per_draft + new_ones, len(every)
    if have >= need:
        return None
    text = msgs["FEASIBLE_VARIETY"].format(drafts=drafts, need=need, per=per_draft,
                                           overlap=f"{limit:.0%}", have=have)  # fmt: skip
    return Shortfall("variety", (), need, have, text)


# ─────────────── 6h: how much room is there? ───────────────


@dataclass(frozen=True)
class BlockFit:
    """One block: what it can draw on, what it needs, and whether that is barely enough."""

    role: str  # the block's role, or the chapter id for the sub-blocks of a chapter (180 s)
    candidates: int  # different scenes offered to this block
    beats_needed: int
    max_fill: int  # most beats those scenes can carry (each once, at most N per clip)
    scenes_min: int  # fewest scenes that make exactly `beats_needed`
    scenes_max: int  # most scenes that make exactly `beats_needed`
    tight: bool  # candidates < ceil(scenes_max x ratio)


@dataclass(frozen=True)
class FootageFit:
    """The whole plan: usable different scenes against the scenes it may need."""

    scenes_available: int  # different scenes over all blocks, at most N per clip
    scenes_needed_min: int
    scenes_needed_max: int
    warn_below: int  # ceil(scenes_needed_max x ratio)
    tight: bool  # scenes_available < warn_below
    blocks: tuple[BlockFit, ...]


def scene_counts(beats: int, allowed: Sequence[int]) -> tuple[int, int] | None:
    """(fewest, most) scenes that add up to exactly `beats`, each scene one of `allowed` beats.

    None when `beats` is not a sum of allowed sizes (a plan built by `compile_plan` never is).

    Example:
        >>> scene_counts(16, [4, 6])  # 4+6+6, or 4+4+4+4
        (3, 4)
    """
    if beats <= 0:
        return (0, 0)
    fewest: list[int | None] = [0] + [None] * beats
    most: list[int | None] = [0] + [None] * beats
    for n in range(1, beats + 1):
        options = [(fewest[n - a], most[n - a]) for a in allowed if a > 0 and n >= a]
        lows = [lo + 1 for lo, _ in options if lo is not None]
        highs = [hi + 1 for _, hi in options if hi is not None]
        fewest[n], most[n] = (min(lows), max(highs)) if lows and highs else (None, None)
    lo, hi = fewest[beats], most[beats]
    return (lo, hi) if lo is not None and hi is not None else None


def _warn_level(needed: int, ratio: float) -> int:
    """ceil(needed x ratio), ignoring float noise (10 x 1.3 is 13, not 14)."""
    return math.ceil(needed * ratio - 1e-9)


def _usable_scenes(cands: Iterable[Candidate], per_clip: int) -> int:
    """Different scenes one draft can use: each once, at most `per_clip` of any one clip."""
    counts: dict[str, int] = {}
    for c in _distinct(cands).values():
        counts[c.clip] = counts.get(c.clip, 0) + 1
    return sum(min(n, per_clip) for n in counts.values())


def footage_fit(plan: Plan, pick: PickFile, rules: Rules) -> FootageFit | None:
    """Room to spare between the picked scenes and what the plan may need; None in speech mode
    (cuts follow the voice, there are no scene blocks).

    A block needs between `scenes_min` and `scenes_max` scenes (sums of the allowed scene sizes
    that make its planned beats); the plan needs the sums over its blocks. The footage is `tight`
    when its usable different scenes fall below `scenes_needed_max x pick.footage_warn_ratio`.

    The sub-blocks of a chapter (180 s) draw on one ranking of the chapter's scenes, dealt out
    without repeats, so they are reported as one row named by the chapter id, like the
    feasibility gate names "chương 'x'".
    """
    if plan.driver == "speech":
        return None
    ratio = float(rules["pick"]["footage_warn_ratio"])
    per_clip = int(rules["vision"]["max_segments_per_clip"])
    allowed = plan.beats_per_scene_allowed
    by_role = {b.role: b for b in pick.blocks}
    groups: dict[str, list[PlanBlock]] = {}  # row name -> its blocks, in plan order
    for block in plan.blocks:
        groups.setdefault(block.chapter or block.role, []).append(block)
    rows: list[BlockFit] = []
    for name, blocks in groups.items():
        cands = [c for b in blocks if b.role in by_role for c in by_role[b.role].candidates]
        lo = hi = 0
        for b in blocks:
            counts = scene_counts(b.beats, allowed)
            if counts is None:  # not a sum of allowed sizes: bound it by the extremes instead
                counts = (math.ceil(b.beats / max(allowed)), b.beats // min(allowed))
            lo, hi = lo + counts[0], hi + counts[1]
        have = len(_distinct(cands))
        rows.append(BlockFit(
            role=name, candidates=have, beats_needed=sum(b.beats for b in blocks),
            max_fill=_supply(cands, per_clip), scenes_min=lo, scenes_max=hi,
            tight=have < _warn_level(hi, ratio),
        ))  # fmt: skip
    need_min = sum(r.scenes_min for r in rows)
    need_max = sum(r.scenes_max for r in rows)
    warn_below = _warn_level(need_max, ratio)
    pool = [c for b in plan.blocks if b.role in by_role for c in by_role[b.role].candidates]
    available = _usable_scenes(pool, per_clip)
    return FootageFit(scenes_available=available, scenes_needed_min=need_min,
                      scenes_needed_max=need_max, warn_below=warn_below,
                      tight=available < warn_below, blocks=tuple(rows))  # fmt: skip
