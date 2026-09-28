"""Unit tests: domain/pick.py — candidate filtering and ranking per block (4b, spec §7.1)."""

from __future__ import annotations

import numpy as np
import pytest

from gani_dung_video.domain.pick import (
    Candidate,
    candidate_limit,
    dish_matches,
    eligible_candidates,
    find_dishes,
    rank_block,
    similar_id_pairs,
)
from gani_dung_video.domain.scenes import Scene, Segment, SimilarPair

STYLE = "ke_chuyen_nghe"
ALLOWED = [4, 6]
BEAT = 0.75  # 4 beats = 3.0 s, 6 beats = 4.5 s


def seg(start: float, dur: float, labels: list[str] | None = None, aes: float = 5.0) -> Segment:
    return Segment(start=start, end=start + dur, peak=start + dur / 3, aesthetic=aes,
                   labels=labels or [])  # fmt: skip


def scene(clip: str, segs: list[Segment], blurry: bool = False, index: int = 0) -> Scene:
    return Scene(clip=clip, index=index, start=0.0, end=100.0, sharpness=100, motion="tinh",
                 jitter=0, consistency=1, blurry=blurry, segments={STYLE: segs})  # fmt: skip


def cand(
    cid: str, labels: list[str], aes: float, probs: dict[str, float] | None = None
) -> Candidate:
    return Candidate(id=cid, clip="a.mp4", start=0.0, end=5.0, peak=1.0, duration=5.0,
                     fits_beats=[4, 6], labels=labels, aesthetic=aes,
                     tag_probs=probs or {})  # fmt: skip


# ─────────────── eligibility (step 1: flags, "khac", too short) ───────────────


def test_blurry_khac_and_too_short_segments_are_dropped() -> None:
    scenes = [
        scene("a.mp4", [seg(0, 5, ["thiet_bi"]), seg(10, 2.0, ["thiet_bi"]),  # 2 s < 3 s
                        seg(20, 5, ["khac", "thiet_bi"]), seg(30, 5, ["thiet_bi", "khac"])]),
        scene("b.mp4", [seg(0, 5, ["thiet_bi"])], blurry=True),
    ]  # fmt: skip
    got = eligible_candidates(scenes, STYLE, BEAT, ALLOWED)
    assert [c.id for c in got] == ["a_s0_0", "a_s0_3"]
    assert got[0].fits_beats == [4, 6] and got[0].duration == 5.0


def test_fits_beats_lists_only_counts_the_segment_can_hold() -> None:
    got = eligible_candidates([scene("a.mp4", [seg(0, 3.5)])], STYLE, BEAT, ALLOWED)
    assert got[0].fits_beats == [4]


def test_candidate_ids_match_the_snap_ids_of_phase_4a() -> None:
    from gani_dung_video.domain.beats import segment_refs

    scenes = [scene("VID_1.mp4", [seg(0, 5), seg(10, 5)], index=2)]
    assert [c.id for c in eligible_candidates(scenes, STYLE, BEAT, ALLOWED)] == list(
        segment_refs(scenes, STYLE)
    )


# ─────────────── ranking (step 2: required tags, step 3: dish, step 4: aesthetic) ───────────────


def test_required_tag_beats_a_prettier_scene_without_it() -> None:
    pool = [cand("pretty", ["thiet_bi"], 6.0), cand("hands", ["nguoi_thao_tac"], 4.0)]
    got, missing = rank_block(pool, ["nguoi_thao_tac"], set(), 10)
    assert [c.id for c in got] == ["hands"] and missing == []


def test_without_required_tags_all_are_ranked_by_aesthetic() -> None:
    pool = [cand("a", [], 4.0), cand("b", [], 6.0), cand("c", [], 5.0)]
    got, missing = rank_block(pool, [], set(), 10)
    assert [c.id for c in got] == ["b", "c", "a"] and missing == []


def test_partly_missing_tags_keep_the_ones_found_and_report_the_rest() -> None:
    pool = [cand("m", ["thiet_bi"], 5.0), cand("x", ["mon_an"], 6.0)]
    got, missing = rank_block(pool, ["chung_nhan", "thiet_bi"], set(), 10)
    assert [c.id for c in got] == ["m"] and missing == ["chung_nhan"]


def test_no_tagged_scene_falls_back_to_the_closest_by_clip_probability() -> None:
    pool = [cand("far", ["thiet_bi"], 6.0, {"chung_nhan": 0.01}),
            cand("near", ["thiet_bi"], 4.0, {"chung_nhan": 0.20})]  # fmt: skip
    got, missing = rank_block(pool, ["chung_nhan"], set(), 10)
    assert [c.id for c in got] == ["near", "far"] and missing == ["chung_nhan"]


def test_dish_named_in_the_note_comes_before_aesthetic() -> None:
    pool = [cand("pretty", ["thanh_pham"], 6.0), cand("bo_vien", ["thanh_pham"], 4.5)]
    got, _ = rank_block(pool, ["thanh_pham"], {"bo_vien"}, 10)
    assert [c.id for c in got] == ["bo_vien", "pretty"]


def test_ranking_is_cut_to_the_limit() -> None:
    pool = [cand(str(i), [], float(i)) for i in range(30)]
    got, _ = rank_block(pool, [], set(), 6)
    assert [c.id for c in got] == ["29", "28", "27", "26", "25", "24"]


LIMITS = [(4, 6), (12, 6), (16, 8), (40, 20), (22, 20)]


@pytest.mark.parametrize(("beats", "expected"), LIMITS)
def test_candidate_limit_is_twice_the_scenes_needed_within_6_to_20(
    beats: int, expected: int
) -> None:
    assert candidate_limit(beats, [4, 6] if beats != 22 else [1, 2]) == expected


# ─────────────── dishes in the user's note (visual dictionary) ───────────────


def test_dishes_are_found_as_whole_syllable_phrases() -> None:
    terms = ["giò lụa", "giò chả", "bò viên", "chả quế"]
    note = "Video về Giò Lụa và bò viên; nhấn mạnh chả quế nướng."
    assert find_dishes(note, terms) == ["giò lụa", "bò viên", "chả quế"]
    assert find_dishes("bánh giò chảo", terms) == []  # "giò chả" is not inside "giò chảo"
    assert find_dishes(None, terms) == []


def test_dish_matches_by_text_to_image_similarity_threshold() -> None:
    v = {"a": np.array([1.0, 0.0], dtype=np.float32), "b": np.array([0.0, 1.0], dtype=np.float32)}
    cents = {"bò viên": np.array([0.96, 0.28], dtype=np.float32)}
    assert dish_matches(v, cents, 0.5) == {"a": ["bò viên"]}
    assert dish_matches(v, cents, 0.99) == {}
    frames = {"c": np.stack([v["b"], v["a"]])}  # best of several frames counts
    assert dish_matches(frames, cents, 0.5) == {"c": ["bò viên"]}


# ─────────────── near-duplicates from 3c, mapped to candidate ids ───────────────


def test_similar_pairs_become_candidate_id_pairs() -> None:
    peaks = (("x", 2.0), ("y", 9.0), ("z", 20.0))
    cands = [Candidate(id=i, clip="a.mp4", start=0, end=5, peak=p, duration=5, fits_beats=[4],
                       labels=[], aesthetic=5.0) for i, p in peaks]  # fmt: skip
    pairs = [
        SimilarPair(clip="a.mp4", peak_a=2.0, peak_b=9.0, similarity=0.97, styles=[STYLE]),
        SimilarPair(clip="a.mp4", peak_a=2.0, peak_b=50.0, similarity=0.96, styles=[STYLE]),
    ]
    assert similar_id_pairs(cands, pairs) == [("x", "y")]
