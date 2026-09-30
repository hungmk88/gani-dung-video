"""Unit tests: domain/pick.py — candidates for the sub-blocks of a chapter (phase 7b, GD7 §4.2).

The sub-blocks of one chapter share a source pool. Giving each the same list wastes the prompt and
invites repeats inside the chapter; instead the chapter is ranked once and the ranked scenes are
dealt out, so every sub-block owns different candidates. Hook and CTA, and all the blocks of a
15–60 s video, are ranked exactly as before.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

from mock_footage import STYLE, mock_scenes, write_mock_folder, write_options

from gani_dung_video.application.pick_service import build_pick
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.pick import (
    Candidate,
    candidate_limit,
    deal,
    eligible_candidates,
    limits_for,
    rank_block,
    rank_plan,
)
from gani_dung_video.domain.plan import MusicInfo, Plan, compile_plan
from gani_dung_video.infrastructure.config_files import load_glossary, load_rules

SHIPPED = load_rules()
GLOSSARY = load_glossary()
ALLOWED = [4, 6]
CFG = SHIPPED["pick"]


def rules_on() -> Any:
    r = copy.deepcopy(dict(SHIPPED))
    r["durations"]["180"]["enabled"] = True
    return r


RULES = rules_on()


def plan_of(**kw: Any) -> Plan:
    o = {"phong_cach": STYLE, "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 180,
         "so_ban_nhap": 2, "nhac": "thu_vien", "nhac_file": "am_ap_84.mp3", **kw}  # fmt: skip
    m = MusicInfo(track="am_ap_84.mp3", bpm=84.0, first_beat_sec=0.5, duration_sec=200.0,
                  source="library")  # fmt: skip
    tags = {"nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "khu_vuc", "mon_an"}
    return compile_plan(VideoOptions(**o), RULES, music=m, available_tags=tags)


def pool_of(plan: Plan, clips: int = 28) -> list[Candidate]:
    return eligible_candidates(mock_scenes(clips), STYLE, plan.beat_sec,
                               plan.beats_per_scene_allowed)  # fmt: skip


def cand(i: int, labels: list[str], aes: float) -> Candidate:
    return Candidate(id=f"s{i}", clip=f"c{i}.mp4", start=0.0, end=5.0, peak=1.0, duration=5.0,
                     fits_beats=[4, 6], labels=labels, aesthetic=aes)  # fmt: skip


# ─────────────── rank_block(top_up=True) ───────────────


def test_top_up_keeps_tagged_first_then_fills_with_the_best_of_the_rest() -> None:
    pool = [
        cand(0, ["thiet_bi"], 4.0),
        cand(1, [], 9.0),
        cand(2, [], 8.0),
        cand(3, ["thiet_bi"], 5.0),
    ]
    plain, _ = rank_block(pool, ["thiet_bi"], set(), 4)
    assert [c.id for c in plain] == ["s3", "s0"]  # the old behaviour: tagged only
    full, missing = rank_block(pool, ["thiet_bi"], set(), 4, top_up=True)
    assert [c.id for c in full] == ["s3", "s0", "s1", "s2"] and missing == []


def test_top_up_changes_nothing_when_nobody_has_the_tag() -> None:
    pool = [cand(0, [], 4.0), cand(1, [], 9.0)]
    a, miss_a = rank_block(pool, ["thiet_bi"], set(), 2)
    b, miss_b = rank_block(pool, ["thiet_bi"], set(), 2, top_up=True)
    assert a == b and miss_a == miss_b == ["thiet_bi"]


# ─────────────── limits and dealing ───────────────


def test_limits_follow_candidate_limit_for_each_block() -> None:
    p = plan_of()
    got = limits_for(p.blocks, ALLOWED, 6, 20, 0)
    assert got == [candidate_limit(b.beats, ALLOWED, 6, 20) for b in p.blocks]
    assert all(6 <= n <= 20 for n in got)


def test_a_total_cap_scales_the_limits_down_but_never_below_the_scenes_a_block_needs() -> None:
    p = plan_of()
    free = limits_for(p.blocks, ALLOWED, 6, 20, 0)
    capped = limits_for(p.blocks, ALLOWED, 6, 20, sum(free) - 30)
    assert sum(capped) <= sum(free) - 30 + len(free)  # rounding down may leave a little room
    assert sum(capped) < sum(free)
    for b, n in zip(p.blocks, capped, strict=True):
        assert n >= -(-b.beats // max(ALLOWED))  # at least as many as the fewest-scene layout


def test_an_impossible_cap_is_ignored_instead_of_starving_the_blocks() -> None:
    p = plan_of()
    floors = limits_for(p.blocks, ALLOWED, 6, 20, 1)
    assert all(n >= -(-b.beats // max(ALLOWED)) for b, n in zip(p.blocks, floors, strict=True))


def test_deal_gives_every_candidate_to_one_block_in_rank_order() -> None:
    ranked = [cand(i, [], 9.0 - i) for i in range(10)]
    got = deal(ranked, [4, 4, 2])
    assert [len(g) for g in got] == [4, 4, 2]
    ids = [c.id for g in got for c in g]
    assert sorted(ids) == sorted(c.id for c in ranked) and len(set(ids)) == 10
    assert got[0][0].id == "s0" and got[1][0].id == "s1"


def test_deal_stops_when_the_limits_are_full() -> None:
    ranked = [cand(i, [], 1.0) for i in range(10)]
    got = deal(ranked, [2, 2])
    assert sum(len(g) for g in got) == 4


def test_deal_with_fewer_candidates_than_room_still_spreads_them() -> None:
    ranked = [cand(i, [], 1.0) for i in range(3)]
    got = deal(ranked, [5, 5, 5])
    assert [len(g) for g in got] == [1, 1, 1]


# ─────────────── rank_plan on the mock footage ───────────────


def test_a_short_plan_is_ranked_exactly_as_before() -> None:
    p = compile_plan(VideoOptions(phong_cach=STYLE, muc_dich="gioi_thieu_xuong", do_dai_giay=30,
                                  so_ban_nhap=2, nhac="khong_nhac"),
                     SHIPPED, music=None, available_tags=set())  # fmt: skip
    pool = eligible_candidates(mock_scenes(10), STYLE, p.beat_sec, p.beats_per_scene_allowed)
    got, missing = rank_plan(p.blocks, pool, set(), p.beats_per_scene_allowed, CFG)
    for b, plan_block in zip(got, p.blocks, strict=True):
        limit = candidate_limit(plan_block.beats, ALLOWED, int(CFG["min_candidates"]),
                                int(CFG["max_candidates"]))  # fmt: skip
        want, _ = rank_block(pool, plan_block.required_tags, set(), limit)
        assert [c.id for c in b.candidates] == [c.id for c in want]
        assert b.role == plan_block.role and b.beats == plan_block.beats
    assert isinstance(missing, list)


def test_sub_blocks_of_a_chapter_get_different_candidates() -> None:
    p = plan_of()
    got, _ = rank_plan(p.blocks, pool_of(p), set(), p.beats_per_scene_allowed, CFG)
    by_chapter: dict[str, list[set[str]]] = {}
    for blk, pb in zip(got, p.blocks, strict=True):
        if pb.chapter:
            by_chapter.setdefault(pb.chapter, []).append({c.id for c in blk.candidates})
    assert set(by_chapter) == {"ve_sinh", "quy_trinh", "bang_chung"}
    for sets in by_chapter.values():
        assert len(sets) >= 2
        assert sum(len(s) for s in sets) == len(set().union(*sets))  # no scene twice in a chapter


def test_every_sub_block_has_enough_scenes_to_be_filled() -> None:
    p = plan_of()
    got, _ = rank_plan(p.blocks, pool_of(p), set(), p.beats_per_scene_allowed, CFG)
    for blk in got:
        assert len(blk.candidates) >= -(-blk.beats // max(ALLOWED))


def test_each_sub_block_of_a_tagged_chapter_gets_a_tagged_scene_when_there_are_some() -> None:
    p = plan_of()
    got, _ = rank_plan(p.blocks, pool_of(p), set(), p.beats_per_scene_allowed, CFG)
    for blk, pb in zip(got, p.blocks, strict=True):
        if pb.chapter == "ve_sinh":
            assert any("nguoi_thao_tac" in c.labels for c in blk.candidates), pb.role
        if pb.chapter == "bang_chung":
            assert any({"chung_nhan", "thiet_bi"} & set(c.labels) for c in blk.candidates)


def test_the_total_number_of_candidates_respects_the_cap() -> None:
    p = plan_of()
    cfg = {**CFG, "max_total_candidates": 60}
    got, _ = rank_plan(p.blocks, pool_of(p), set(), p.beats_per_scene_allowed, cfg)
    assert sum(len(b.candidates) for b in got) <= 60 + len(got)


def test_missing_tags_are_reported_on_every_sub_block_and_once_overall() -> None:
    p = plan_of()
    scenes = mock_scenes(28, tags=("nguoi_thao_tac", "thiet_bi", "thanh_pham"))  # no certificates
    pool = eligible_candidates(scenes, STYLE, p.beat_sec, p.beats_per_scene_allowed)
    got, missing = rank_plan(p.blocks, pool, set(), p.beats_per_scene_allowed, CFG)
    assert missing == ["chung_nhan"]
    proof = [b for b, pb in zip(got, p.blocks, strict=True) if pb.chapter == "bang_chung"]
    assert proof and all(b.missing_tags == ["chung_nhan"] for b in proof)


def test_ranking_28_clips_is_fast() -> None:
    p = plan_of()
    pool = pool_of(p)
    t0 = time.perf_counter()
    rank_plan(p.blocks, pool, set(), p.beats_per_scene_allowed, CFG)
    assert time.perf_counter() - t0 < 1.0


# ─────────────── build_pick end to end (no video, no CLIP) ───────────────


def test_build_pick_writes_candidates_for_sub_blocks(tmp_path: Path) -> None:
    write_mock_folder(tmp_path)
    p = plan_of(nhac="khong_nhac", nhac_file=None)
    (tmp_path / "edit" / "plan.json").write_text(p.model_dump_json(), encoding="utf-8")
    opt = write_options(tmp_path)
    result, out = build_pick(opt, tmp_path, RULES, GLOSSARY)
    assert out.is_file()
    assert [b.role for b in result.blocks] == [b.role for b in p.blocks]
    chapter = [b for b in result.blocks if b.role.startswith("quy_trinh#")]
    ids = [c.id for b in chapter for c in b.candidates]
    assert len(chapter) >= 2 and len(ids) == len(set(ids))
    assert sum(len(b.candidates) for b in result.blocks) <= int(CFG["max_total_candidates"])


def test_the_total_cap_is_a_documented_rule() -> None:
    assert CFG["max_total_candidates"] >= 100
