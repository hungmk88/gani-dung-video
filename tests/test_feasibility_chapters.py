"""Unit tests: domain/feasibility.py on plans with chapters (phase 7b, GD7 §1 and §4.2).

With ~18 blocks the old search tried only single blocks and the whole plan (too many subsets),
so a chapter starved of scenes could go unnoticed until the money was spent. The search now groups
by chapter: every subset inside a chapter, then every subset of chapters. Still necessary
conditions only — it must never refuse a plan that can be built.
"""

from __future__ import annotations

import copy
import random
import time
from collections.abc import Sequence
from typing import Any

from mock_footage import STYLE, mock_scenes
from test_feasibility import buildable, scene

from gani_dung_video.domain.feasibility import Shortfall, check_feasible, footage_fit
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.pick import (
    BlockCandidates,
    Candidate,
    PickFile,
    eligible_candidates,
    rank_plan,
)
from gani_dung_video.domain.plan import MusicInfo, Plan, PlanBlock, PlanChapter, compile_plan
from gani_dung_video.infrastructure.config_files import load_rules

SHIPPED = load_rules()
ALLOWED = [4, 6]
ALL_TAGS = {"nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "khu_vuc", "mon_an"}


def rules_on() -> Any:
    r = copy.deepcopy(dict(SHIPPED))
    r["durations"]["180"]["enabled"] = True
    return r


RULES = rules_on()


def plan180(drafts: int = 2) -> Plan:
    m = MusicInfo(track="t.mp3", bpm=84.0, first_beat_sec=0.5, duration_sec=200.0, source="library")
    o = VideoOptions(phong_cach=STYLE, muc_dich="gioi_thieu_xuong", do_dai_giay=180,
                     so_ban_nhap=drafts, nhac="thu_vien", nhac_file="t.mp3")  # fmt: skip
    return compile_plan(o, RULES, music=m, available_tags=ALL_TAGS)


def picked(plan: Plan, clips: int) -> PickFile:
    """What the pick step would write for `clips` mock clips."""
    pool = eligible_candidates(
        mock_scenes(clips), STYLE, plan.beat_sec, plan.beats_per_scene_allowed
    )
    blocks, missing = rank_plan(
        plan.blocks, pool, set(), plan.beats_per_scene_allowed, RULES["pick"]
    )
    return PickFile(rules_version="2.0", style=STYLE, beat_sec=plan.beat_sec,
                    beats_per_scene_allowed=plan.beats_per_scene_allowed, dishes=[],
                    blocks=blocks, missing_shots=missing, similar_pairs=[])  # fmt: skip


def gate(plan: Plan, pick: PickFile) -> Shortfall | None:
    return check_feasible(plan, pick, RULES, int(plan.options["drafts"]))


# ─────────────── the mock footage of decision Q5 ───────────────


def test_28_mock_clips_fill_a_three_minute_plan_for_two_drafts() -> None:
    p = plan180()
    assert gate(p, picked(p, 28)) is None


def test_nine_clips_cannot_fill_three_minutes() -> None:
    p = plan180()
    verdict = gate(p, picked(p, 9))
    assert verdict is not None
    assert "Dừng trước khi gọi Claude (0đ)" in verdict.message


def test_the_search_is_fast_with_eighteen_blocks() -> None:
    p = plan180()
    pick = picked(p, 28)
    assert len(p.blocks) >= 12
    t0 = time.perf_counter()
    gate(p, pick)
    assert time.perf_counter() - t0 < 2.0


def test_footage_fit_counts_scenes_over_the_whole_plan() -> None:
    p = plan180()
    fit = footage_fit(p, picked(p, 28), RULES)
    assert fit is not None
    assert fit.scenes_available <= 28 * 3  # at most 3 per clip
    assert fit.scenes_needed_min <= fit.scenes_needed_max
    starved = footage_fit(p, picked(p, 9), RULES)
    assert starved is not None and starved.tight is True


def test_footage_fit_reports_one_row_per_chapter_not_per_sub_block() -> None:
    p = plan180()
    fit = footage_fit(p, picked(p, 28), RULES)
    assert fit is not None
    hook, cta = p.blocks[0].role, p.blocks[-1].role
    assert [r.role for r in fit.blocks] == [hook, *(c.id for c in p.chapters), cta]
    for c in p.chapters:
        row = next(r for r in fit.blocks if r.role == c.id)
        subs = [b for b in p.blocks if b.chapter == c.id]
        assert row.beats_needed == sum(b.beats for b in subs)
    assert fit.scenes_needed_min == sum(r.scenes_min for r in fit.blocks)  # totals unchanged
    assert fit.scenes_needed_max == sum(r.scenes_max for r in fit.blocks)


def test_a_starved_chapter_shows_up_as_that_chapter() -> None:
    p = plan180()
    mid = next(c for c in p.chapters if c.id == "quy_trinh")
    fit = footage_fit(p, starve(picked(p, 28), mid.blocks, 1), RULES)
    assert fit is not None
    assert [r.role for r in fit.blocks if r.tight] == ["quy_trinh"]


# ─────────────── a starved chapter is found and named ───────────────


def starve(pick: PickFile, chapter_blocks: Sequence[str], keep: int) -> PickFile:
    blocks = [b.model_copy(update={"candidates": b.candidates[:keep]})
              if b.role in chapter_blocks else b for b in pick.blocks]  # fmt: skip
    return pick.model_copy(update={"blocks": blocks})


def test_a_chapter_whose_blocks_lack_scenes_is_named_by_its_chapter() -> None:
    p = plan180()
    mid = next(c for c in p.chapters if c.id == "quy_trinh")
    pick = starve(picked(p, 28), mid.blocks, 1)  # one scene for every sub-block of the chapter
    verdict = gate(p, pick)
    assert verdict is not None and verdict.kind in {"block", "group"}
    assert any(r.startswith("quy_trinh") for r in verdict.blocks)


def test_scenes_shared_by_every_chapter_are_not_enough_for_the_whole_plan() -> None:
    """Each block alone and each chapter alone look fine; only the pooled plan is short."""
    pool = [scene(i) for i in range(3)]
    blocks = [(f"{ch}#{k}", ch, 8, pool) for ch in ("c1", "c2") for k in (1, 2)]
    plan, pick = chapter_plan(blocks)
    verdict = check_feasible(plan, pick, RULES, 1)
    assert verdict is not None and verdict.kind == "group"
    assert "chương" in verdict.message and "#" not in verdict.message


def test_a_shortage_inside_one_chapter_is_found_before_the_whole_plan() -> None:
    thin, rich = [scene(i) for i in range(2)], [scene(i) for i in range(2, 30)]
    plan, pick = chapter_plan([("c1#1", "c1", 12, thin), ("c1#2", "c1", 12, thin),
                               ("c1#3", "c1", 12, thin), ("c2#1", "c2", 12, rich)])  # fmt: skip
    verdict = check_feasible(plan, pick, RULES, 1)
    assert verdict is not None and verdict.kind == "group"
    assert set(verdict.blocks) == {"c1#1", "c1#2", "c1#3"}  # the chapter, not the whole plan
    assert "chương 'c1'" in verdict.message


# ─────────────── property: never refuse what could be built (plans with chapters) ───────────


def chapter_plan(
    blocks: Sequence[tuple[str, str, int, Sequence[Candidate]]],
) -> tuple[Plan, PickFile]:
    """blocks = (role, chapter, beats, candidates); consecutive blocks of a chapter form it."""
    total = sum(b for _, _, b, _ in blocks)
    ids: list[str] = list(dict.fromkeys(ch for _, ch, _, _ in blocks))
    chapters, cursor = [], 0
    for cid in ids:
        mine = [(r, b) for r, ch, b, _ in blocks if ch == cid]
        size = sum(b for _, b in mine)
        chapters.append(PlanChapter(id=cid, role=cid, start_beat=cursor, beats=size,
                                    title_beats=1, blocks=[r for r, _ in mine]))  # fmt: skip
        cursor += sum(b for _, b in mine)
    plan = Plan(rules_version="2.0", options={"drafts": 1}, driver="music", music=None,
                beat_sec=0.75, target_beats=total, planned_beats=total,
                beats_per_scene_allowed=list(ALLOWED),
                blocks=[PlanBlock(role=r, beats=b, chapter=ch) for r, ch, b, _ in blocks],
                chapters=chapters, text_rules={}, draft_strategy=["toi_uu"])  # fmt: skip
    pick = PickFile(rules_version="2.0", style=STYLE, beat_sec=0.75,
                    beats_per_scene_allowed=list(ALLOWED), dishes=[],
                    blocks=[BlockCandidates(role=r, beats=b, required_tags=[], missing_tags=[],
                                            candidates=list(c)) for r, _, b, c in blocks],
                    missing_shots=[], similar_pairs=[])  # fmt: skip
    return plan, pick


def random_chapter_instance(rng: random.Random) -> tuple[Plan, PickFile]:
    pool = [scene(i, clip=rng.choice(["x.mp4", "y.mp4", "z.mp4"]),
                  fits=rng.choice([(4,), (4, 6)])) for i in range(rng.randint(3, 6))]  # fmt: skip
    blocks = []
    for ch in ("c1", "c2"):
        for k in (1, 2):
            cands = rng.sample(pool, rng.randint(1, len(pool)))
            blocks.append((f"{ch}#{k}", ch, rng.choice([4, 8]), cands))
    return chapter_plan(blocks)


def test_the_chapter_search_never_refuses_a_plan_that_can_be_built() -> None:
    rng = random.Random(20261001)
    per_clip = int(RULES["vision"]["max_segments_per_clip"])
    overlap = float(RULES["drafts"]["max_scene_overlap"])
    refused_impossible = 0
    for _ in range(80):
        plan, pick = random_chapter_instance(rng)
        verdict = check_feasible(plan, pick, RULES, 1)
        if buildable(plan, pick, 1, per_clip, overlap):
            assert verdict is None, verdict.message if verdict else ""
        elif verdict is not None:
            refused_impossible += 1
    assert refused_impossible > 0  # not vacuous


def test_a_plan_without_chapters_is_searched_as_before() -> None:
    blocks = [(f"b{i}", 4, [scene(i)]) for i in range(20)]
    from test_feasibility import make

    plan, pick = make(blocks)
    verdict = check_feasible(plan, pick, RULES, 1)
    assert verdict is None or verdict.kind != "group"
