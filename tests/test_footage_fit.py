"""Unit tests: domain/feasibility.py `footage_fit` — is the footage only just enough? (phase 6h)

The feasibility gate (6e) refuses plans that CANNOT be filled. This is the softer question the UI
asks before a person approves money: the plan can be filled, but with almost no spare scenes, so
Claude may repeat a scene (V4) and burn its rounds. It never blocks; it only measures.

The tight case comes from a real run (30/09/2026, ke_chuyen_nghe x gioi_thieu_xuong x 30s x
84 BPM): 42 beats in blocks of 4/10/16/6/6, scenes of 4 or 6 beats, and only 9 different scenes
in the footage. Three rounds of V4 later: 2.992đ, no video.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from gani_dung_video.domain.feasibility import FootageFit, footage_fit, scene_counts
from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
from gani_dung_video.domain.plan import Plan, PlanBlock
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
ALLOWED = [4, 6]
BLOCKS = [("hook", 4), ("ve_sinh", 10), ("quy_trinh", 16), ("bang_chung", 6), ("cta", 6)]


def scene(i: int, clip: str | None = None, fits: Sequence[int] = (4, 6)) -> Candidate:
    return Candidate(id=f"s{i}", clip=clip or f"c{i}.mp4", start=10.0 * i, end=10.0 * i + 5.0,
                     peak=10.0 * i + 2.0, duration=5.0, fits_beats=list(fits), labels=[],
                     aesthetic=5.0)  # fmt: skip


def make(blocks: Sequence[tuple[str, int, Sequence[Candidate]]], driver: str = "music"
         ) -> tuple[Plan, PickFile]:  # fmt: skip
    total = sum(b for _, b, _ in blocks)
    plan = Plan(rules_version="2.0", options={"drafts": 1}, driver=driver, music=None,
                beat_sec=0.7143, target_beats=total, planned_beats=total,
                beats_per_scene_allowed=list(ALLOWED),
                blocks=[PlanBlock(role=r, beats=b) for r, b, _ in blocks],
                text_rules={}, draft_strategy=["toi_uu"])  # fmt: skip
    pick = PickFile(rules_version="2.0", style="ke_chuyen_nghe", beat_sec=0.7143,
                    beats_per_scene_allowed=list(ALLOWED), dishes=[],
                    blocks=[BlockCandidates(role=r, beats=b, required_tags=[], missing_tags=[],
                                            candidates=list(c)) for r, b, c in blocks],
                    missing_shots=[], similar_pairs=[])  # fmt: skip
    return plan, pick


def overlapping(n_distinct: int) -> tuple[Plan, PickFile]:
    """The five blocks of the real run, all drawing on the same `n_distinct` scenes (each in its
    own clip), so most scenes are offered to several blocks — as in the real candidates.json."""
    pool = [scene(i) for i in range(n_distinct)]
    return make([(role, beats, pool) for role, beats in BLOCKS])


def fit(plan: Plan, pick: PickFile, ratio: float | None = None) -> FootageFit:
    rules = RULES
    if ratio is not None:
        rules = {**RULES, "pick": {**RULES["pick"], "footage_warn_ratio": ratio}}
    got = footage_fit(plan, pick, rules)
    assert got is not None
    return got


# ─────────────── how many scenes a block needs ───────────────


@pytest.mark.parametrize(
    "beats,expected",
    [(4, (1, 1)), (6, (1, 1)), (10, (2, 2)), (12, (2, 3)), (16, (3, 4)), (42, (7, 10)),
     (5, None), (2, None), (0, (0, 0))],
)  # fmt: skip
def test_scene_counts_are_sums_of_the_allowed_scene_sizes(
    beats: int, expected: tuple[int, int] | None
) -> None:
    assert scene_counts(beats, ALLOWED) == expected


def test_scene_counts_with_a_single_size() -> None:
    assert scene_counts(12, [4]) == (3, 3) and scene_counts(13, [4]) is None


# ─────────────── the real tight case ───────────────


def test_nine_scenes_for_a_plan_that_needs_eight_to_nine_is_tight() -> None:
    """The case of the run that failed: it passes the 6e gate but leaves no room to spare."""
    got = fit(*overlapping(9))
    assert (got.scenes_needed_min, got.scenes_needed_max) == (8, 9)
    assert got.scenes_available == 9
    assert got.warn_below == 12  # ceil(9 x 1.3)
    assert got.tight is True


def test_plenty_of_scenes_is_not_tight() -> None:
    got = fit(*overlapping(20))
    assert got.scenes_available == 20 and got.tight is False


def test_the_threshold_itself_is_not_tight_one_below_is() -> None:
    assert fit(*overlapping(12)).tight is False  # available == warn_below (12)
    assert fit(*overlapping(11)).tight is True


def test_the_ratio_comes_from_the_rules() -> None:
    plan, pick = overlapping(9)
    assert fit(plan, pick, ratio=1.0).warn_below == 9
    assert fit(plan, pick, ratio=1.0).tight is False  # 9 is enough for a ratio of 1
    assert fit(plan, pick, ratio=2.0).warn_below == 18 and fit(plan, pick, ratio=2.0).tight


def test_the_default_ratio_is_1_3() -> None:
    assert RULES["pick"]["footage_warn_ratio"] == 1.3


# ─────────────── counting the scenes ───────────────


def test_a_scene_offered_to_several_blocks_counts_once() -> None:
    same = [scene(0), scene(1)]
    plan, pick = make([("a", 4, same), ("b", 4, same), ("c", 4, same)])
    assert fit(plan, pick).scenes_available == 2


def test_at_most_three_scenes_of_one_clip_can_be_used() -> None:
    """vision.max_segments_per_clip = 3: nine scenes cut from ONE clip are only three usable."""
    one_clip = [scene(i, clip="only.mp4") for i in range(9)]
    plan, pick = make([("a", 4, one_clip), ("b", 6, one_clip)])
    assert fit(plan, pick).scenes_available == 3


def test_scenes_of_different_clips_all_count() -> None:
    plan, pick = make([("a", 4, [scene(i) for i in range(5)])])
    assert fit(plan, pick).scenes_available == 5


# ─────────────── per block ───────────────


def test_each_block_reports_its_candidates_capacity_and_scene_range() -> None:
    plan, pick = make(
        [("hook", 4, [scene(0), scene(1)]), ("main", 16, [scene(i) for i in range(2, 9)])]
    )
    hook, main = fit(plan, pick).blocks
    assert (hook.role, hook.candidates, hook.beats_needed, hook.max_fill) == ("hook", 2, 4, 12)
    assert (hook.scenes_min, hook.scenes_max) == (1, 1)
    assert (main.role, main.candidates, main.beats_needed) == ("main", 7, 16)
    assert (main.scenes_min, main.scenes_max) == (3, 4)
    assert main.max_fill == 42  # seven scenes of one clip each, 6 beats at most per scene


def test_the_warn_threshold_survives_floating_point_noise() -> None:
    """10 scenes needed at most x 1.3 is exactly 13, not 14 (10 * 1.3 = 13.000000000000002)."""
    plan, pick = make([("only", 40, [scene(i) for i in range(13)])])  # 40 = 4x10 -> up to 10 scenes
    got = fit(plan, pick)
    assert got.scenes_needed_max == 10 and got.warn_below == 13 and got.tight is False


def test_a_block_is_tight_when_its_own_candidates_are_barely_enough() -> None:
    plan, pick = make([("hook", 4, [scene(0)]), ("main", 16, [scene(i) for i in range(1, 10)])])
    hook, main = fit(plan, pick).blocks
    assert hook.tight is True  # 1 candidate, warn below ceil(1 x 1.3) = 2
    assert main.tight is False


def test_a_block_with_no_candidates_is_reported_and_tight() -> None:
    plan, pick = make([("hook", 4, []), ("main", 6, [scene(0), scene(1)])])
    hook, _main = fit(plan, pick).blocks
    assert (hook.candidates, hook.max_fill, hook.tight) == (0, 0, True)


# ─────────────── where it does not apply ───────────────


def test_speech_mode_has_no_scene_blocks() -> None:
    plan, pick = overlapping(9)
    assert footage_fit(plan.model_copy(update={"driver": "speech"}), pick, RULES) is None


def test_an_empty_plan_needs_nothing_and_is_not_tight() -> None:
    plan, pick = make([])
    got = fit(plan, pick)
    assert (got.scenes_needed_min, got.scenes_needed_max, got.scenes_available) == (0, 0, 0)
    assert got.tight is False and got.blocks == ()


def test_a_block_that_is_not_a_sum_of_scene_sizes_is_bounded_by_the_extremes() -> None:
    """compile_plan never builds one, but a hand-edited plan.json might: 5 beats with scenes of 4 or
    6 beats. It must not crash; it is bounded by the fewest (longest scenes) and most scenes."""
    plan, pick = make([("odd", 5, [scene(0), scene(1), scene(2)])])
    (block,) = fit(plan, pick).blocks
    assert (block.scenes_min, block.scenes_max) == (1, 1)  # ceil(5/6), 5//4
