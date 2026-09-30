"""Unit tests: domain/feasibility.py — can the picked footage possibly fill the plan? (phase 6e)

The gate runs on the free side of a draft job, before any token estimate. It only ever uses
NECESSARY conditions: it refuses a plan only when V2 (beats), V4 (a scene once, ≤ 3 per clip)
and V5 (a block uses its own candidates) and V9 (several drafts differ) cannot all be met, and
never refuses one that could be met. The property tests at the bottom check exactly that
against an exhaustive search.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from gani_dung_video.domain.feasibility import INFEASIBLE_EXIT_CODE, Shortfall, check_feasible
from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
from gani_dung_video.domain.plan import Plan, PlanBlock
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
ALLOWED = [4, 6]
TOL = max(ALLOWED)
COST_LINE = "Dừng trước khi gọi Claude (0đ)"


def scene(i: int, clip: str | None = None, fits: Sequence[int] = (4, 6)) -> Candidate:
    return Candidate(id=f"s{i}", clip=clip or f"c{i}.mp4", start=10.0 * i, end=10.0 * i + 5.0,
                     peak=10.0 * i + 2.0, duration=5.0, fits_beats=list(fits), labels=[],
                     aesthetic=5.0)  # fmt: skip


def make(
    blocks: Sequence[tuple[str, int, Sequence[Candidate]]],
    drafts: int = 1,
    tags: dict[str, tuple[list[str], list[str]]] | None = None,
) -> tuple[Plan, PickFile]:
    """A plan of (role, beats, candidates) blocks. `tags[role]` = (required, missing)."""
    tags = tags or {}
    total = sum(b for _, b, _ in blocks)
    plan = Plan(rules_version="2.0", options={"drafts": drafts}, driver="music", music=None,
                beat_sec=0.75, target_beats=total, planned_beats=total,
                beats_per_scene_allowed=list(ALLOWED),
                blocks=[PlanBlock(role=r, beats=b, required_tags=tags.get(r, ([], []))[0])
                        for r, b, _ in blocks],
                text_rules={}, draft_strategy=["toi_uu"] * drafts)  # fmt: skip
    pick = PickFile(rules_version="2.0", style="ke_chuyen_nghe", beat_sec=0.75,
                    beats_per_scene_allowed=list(ALLOWED), dishes=[],
                    blocks=[BlockCandidates(role=r, beats=b, required_tags=tags.get(r, ([], []))[0],
                                            missing_tags=tags.get(r, ([], []))[1],
                                            candidates=list(c)) for r, b, c in blocks],
                    missing_shots=[], similar_pairs=[])  # fmt: skip
    return plan, pick


def gate(plan: Plan, pick: PickFile) -> Shortfall | None:
    return check_feasible(plan, pick, RULES, int(plan.options["drafts"]))


# ─────────────── step 1: a block's capacity ───────────────


def test_the_exit_code_for_an_impossible_plan_is_3() -> None:
    assert INFEASIBLE_EXIT_CODE == 3


def test_two_short_scenes_cannot_fill_a_24_beat_block() -> None:
    """The real case: 2 candidates of at most 4 beats each cannot make a 24-beat block."""
    two = [scene(0, fits=(4,)), scene(1, fits=(4,))]
    plan, pick = make([("hook", 4, [scene(2), scene(3)]), ("san_pham", 24, two),
                       ("cta", 4, [scene(4), scene(5)])])  # fmt: skip
    got = gate(plan, pick)
    assert got is not None and got.kind == "block" and got.blocks == ("san_pham",)
    assert (got.need, got.have) == (24 - TOL, 8)  # V2 lets a block be one scene short
    msg = got.message
    assert msg.startswith(COST_LINE) and "'san_pham'" in msg and "24 phách" in msg
    assert "2 cảnh" in msg and "8 phách" in msg and "Quay thêm" in msg


def test_the_required_tags_are_named_in_the_hint() -> None:
    plan, pick = make([("san_pham", 24, [scene(0, fits=(4,))])],
                      tags={"san_pham": (["mon_an"], ["mon_an"])})  # fmt: skip
    got = gate(plan, pick)
    assert got is not None and "mon_an" in got.message


def test_a_block_exactly_at_its_tolerance_passes_and_one_beat_less_does_not() -> None:
    two = [scene(0), scene(1)]  # 2 × 6 = 12 beats
    assert gate(*make([("main", 12, two)])) is None
    one = [scene(0)]
    got = gate(*make([("main", 12, one)]))
    assert got is not None and got.kind == "block" and got.have == 6


def test_a_scene_counts_for_the_most_beats_it_is_long_enough_for() -> None:
    short = [scene(0, fits=(4,)), scene(1, fits=(4,))]  # 8 beats in all
    assert gate(*make([("main", 12, short)])) is not None
    assert gate(*make([("main", 12, [scene(0), scene(1)])])) is None  # each fits 6: 12


def test_missing_tags_alone_never_block_the_plan() -> None:
    """X8 stays soft: no scene carries the tag, but there are enough scenes: build nearest."""
    plan, pick = make([("main", 12, [scene(0), scene(1)])],
                      tags={"main": (["mon_an"], ["mon_an"])})  # fmt: skip
    assert gate(plan, pick) is None


def test_a_block_missing_from_the_pick_has_no_capacity() -> None:
    plan, pick = make([("hook", 12, [scene(0), scene(1)]), ("main", 12, [scene(2), scene(3)])])
    pick = pick.model_copy(update={"blocks": pick.blocks[:1]})
    got = gate(plan, pick)
    assert got is not None and got.blocks == ("main",) and got.have == 0


# ─────────────── step 2: scenes cannot be shared between blocks ───────────────


def test_blocks_sharing_the_same_few_scenes_cannot_all_be_filled() -> None:
    shared = [scene(0), scene(1), scene(2)]  # 18 beats of footage for 24 beats of plan
    plan, pick = make([("hook", 12, shared), ("main", 12, shared)])
    got = gate(plan, pick)
    assert got is not None and got.kind == "group"
    assert got.blocks == ("hook", "main") and got.have == 18
    assert got.message.startswith(COST_LINE) and "3 cảnh" in got.message
    assert "mỗi cảnh chỉ dùng một lần" in got.message


def test_disjoint_scenes_for_the_same_blocks_are_fine() -> None:
    plan, pick = make([("hook", 12, [scene(0), scene(1)]), ("main", 12, [scene(2), scene(3)])])
    assert gate(plan, pick) is None


def test_scenes_of_one_clip_count_only_up_to_the_per_clip_limit() -> None:
    five_of_one_clip = [scene(i, clip="same.mp4") for i in range(5)]  # only 3 may be used
    got = gate(*make([("main", 24, five_of_one_clip)]))
    assert got is not None and got.have == 18 and "3 đoạn" in got.message
    five_of_five = [scene(i) for i in range(5)]
    assert gate(*make([("main", 24, five_of_five)])) is None


def test_a_group_failure_names_the_smallest_group_that_fails() -> None:
    plan, pick = make(
        [("hook", 12, [scene(0), scene(1)]), ("main", 12, [scene(2)]), ("cta", 12, [scene(2)])]
    )  # main and cta both need the one scene s2
    got = gate(plan, pick)
    assert got is not None and got.kind == "group" and got.blocks == ("main", "cta")
    assert got.have == 6


# ─────────────── step 3: several drafts must differ (V9) ───────────────


def overlapping(n: int) -> list[tuple[str, int, list[Candidate]]]:
    """Five blocks (42 beats) whose candidates are windows over the same n scenes."""
    pool = [scene(i) for i in range(n)]
    windows = [pool[0:4], pool[1:6], pool[2:8], pool[3:9], pool[5:9]]
    beats = [4, 12, 12, 10, 4]
    return [(f"b{k}", beats[k], [c for c in windows[k] if int(c.id[1:]) < n]) for k in range(5)]


def test_two_drafts_need_more_distinct_scenes_than_one() -> None:
    """9 scenes fill 42 beats once (7 scenes of 6), but a second draft may share only 60%: the
    two drafts together need 7 + 3 = 10 different scenes."""
    blocks = overlapping(9)
    assert gate(*make(blocks, drafts=1)) is None
    got = gate(*make(blocks, drafts=2))
    assert got is not None and got.kind == "variety"
    assert (got.need, got.have) == (10, 9)
    msg = got.message
    assert msg.startswith(COST_LINE) and "2 bản nháp" in msg and "60%" in msg and "9" in msg


def test_enough_distinct_scenes_let_two_drafts_pass() -> None:
    blocks = [("hook", 12, [scene(i) for i in range(0, 6)]),
              ("main", 12, [scene(i) for i in range(6, 12)]),
              ("cta", 12, [scene(i) for i in range(12, 18)])]  # fmt: skip
    assert gate(*make(blocks, drafts=2)) is None


def test_two_drafts_need_two_different_hooks() -> None:
    blocks = [("hook", 4, [scene(0)]), ("main", 20, [scene(i) for i in range(1, 9)])]
    assert gate(*make(blocks, drafts=1)) is None
    got = gate(*make(blocks, drafts=2))
    assert got is not None and got.kind == "hook" and got.blocks == ("hook",)
    assert got.message.startswith(COST_LINE) and "2 bản nháp" in got.message


# ─────────────── switches ───────────────


def test_a_speech_plan_has_no_scene_blocks_to_check() -> None:
    plan, pick = make([("main", 24, [scene(0)])])
    assert check_feasible(plan.model_copy(update={"driver": "speech"}), pick, RULES, 1) is None


def test_a_plan_with_many_blocks_skips_the_group_search_instead_of_exploding() -> None:
    blocks = [(f"b{i}", 4, [scene(i)]) for i in range(20)]
    plan, pick = make(blocks)
    assert gate(plan, pick) is None or gate(plan, pick).kind != "group"  # type: ignore[union-attr]


# ─────────────── property: never refuse what could be built ───────────────


def solutions(
    plan: Plan, pick: PickFile, per_clip: int
) -> set[tuple[frozenset[str], frozenset[str]]]:
    """Every (scenes, possible first scenes) of ONE draft that V2, V4 and V5 accept."""
    by_role = {b.role: b for b in pick.blocks}
    tol, allowed = max(plan.beats_per_scene_allowed), plan.beats_per_scene_allowed
    found: set[tuple[frozenset[str], frozenset[str]]] = set()

    def clips_ok(ids: Sequence[str]) -> bool:
        clip_of = {c.id: c.clip for b in pick.blocks for c in b.candidates}
        counts: dict[str, int] = {}
        for i in ids:
            counts[clip_of[i]] = counts.get(clip_of[i], 0) + 1
        return all(n <= per_clip for n in counts.values())

    def block(bi: int, used: tuple[str, ...], total: int, first: frozenset[str]) -> None:
        if bi == len(plan.blocks):
            if total == plan.planned_beats and clips_ok(used):
                found.add((frozenset(used), first))
            return
        b = plan.blocks[bi]
        cands = [c for c in by_role[b.role].candidates if c.id not in used]

        def choose(ci: int, took: tuple[str, ...], beats: int) -> None:
            if ci == len(cands):
                if abs(beats - b.beats) <= tol:
                    block(bi + 1, used + took, total + beats,
                          first if first or not took else frozenset(took))  # fmt: skip
                return
            choose(ci + 1, took, beats)
            for n in allowed:
                if n in cands[ci].fits_beats:
                    choose(ci + 1, (*took, cands[ci].id), beats + n)

        choose(0, (), 0)

    block(0, (), 0, frozenset())
    return found


def buildable(plan: Plan, pick: PickFile, drafts: int, per_clip: int, overlap: float) -> bool:
    sols = solutions(plan, pick, per_clip)
    if drafts == 1:
        return bool(sols)
    for a_ids, a_first in sols:
        for b_ids, b_first in sols:
            hooks_differ = not (len(a_first) == 1 and a_first == b_first)
            if hooks_differ and len(a_ids & b_ids) / max(len(b_ids), 1) <= overlap + 1e-9:
                return True
    return False


def random_instance(rng: random.Random, drafts: int) -> tuple[Plan, PickFile]:
    pool = [scene(i, clip=rng.choice(["x.mp4", "y.mp4", "z.mp4"]),
                  fits=rng.choice([(4,), (4, 6)])) for i in range(rng.randint(2, 6))]  # fmt: skip
    blocks = []
    for k in range(rng.randint(1, 3)):
        cands = rng.sample(pool, rng.randint(1, len(pool)))
        blocks.append((f"b{k}", rng.choice([4, 8, 12]), cands))
    return make(blocks, drafts=drafts)


@pytest.mark.parametrize("drafts", [1, 2])
def test_the_gate_never_refuses_a_plan_that_can_be_built(drafts: int) -> None:
    rng = random.Random(20260930 + drafts)
    per_clip = int(RULES["vision"]["max_segments_per_clip"])
    overlap = float(RULES["drafts"]["max_scene_overlap"])
    refused_impossible = 0
    for _ in range(120):
        plan, pick = random_instance(rng, drafts)
        verdict = gate(plan, pick)
        if buildable(plan, pick, drafts, per_clip, overlap):
            assert verdict is None, verdict.message if verdict else ""
        elif verdict is not None:
            refused_impossible += 1
    assert refused_impossible > 0  # the gate does catch impossible plans: the test is not vacuous
