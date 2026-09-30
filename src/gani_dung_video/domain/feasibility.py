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
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

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


def _names(blocks: Sequence[str]) -> str:
    return ", ".join(f"'{b}'" for b in blocks)


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
                block=b.role, beats=plan.planned_beats if lone else b.beats, tol=tol,
                count=count, have=have, per_clip=per_clip, tags=_tags(b))  # fmt: skip
            return Shortfall("block", roles, want, have, text)
        text = msgs["FEASIBLE_GROUP"].format(blocks=_names(roles), need=want, count=count,
                                             have=have, per_clip=per_clip)  # fmt: skip
        return Shortfall("group", roles, want, have, text)

    k = len(blocks)
    for i in range(k):  # step 1: each block alone
        if (found := group((i,))) is not None:
            return found
    sizes = range(2, k + 1) if k <= MAX_GROUP_BLOCKS else [k]
    for size in sizes:  # step 2: scenes cannot be shared between blocks
        for idx in itertools.combinations(range(k), size):
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
        text = msgs["FEASIBLE_HOOK"].format(drafts=drafts, block=first.role, count=hooks)
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
