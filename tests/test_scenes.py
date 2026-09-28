"""Unit tests: domain/scenes.py on synthetic metric series (no video, no OpenCV)."""

from __future__ import annotations

import itertools
import math

import pytest

from gani_dung_video.domain.scenes import (
    FrameStats,
    Scene,
    Segment,
    Window,
    blur_flags,
    classify_motion,
    motion_windows,
    pick_clip_segments,
    pick_segment,
    split_scenes,
    style_segment_range,
    usable_by_style,
    usable_spans,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
CFG = RULES["vision"]
STYLES = RULES["styles"]


def fs(t: float, cut: float = 0.0, dx: float = 0.0, dy: float = 0.0, act: float = 1.0,
       sharp: float = 100.0) -> FrameStats:  # fmt: skip
    return FrameStats(t=t, sharpness=sharp, cut_score=cut, dx=dx, dy=dy, activity=act)


def series(n: int, **kw: float) -> list[FrameStats]:
    return [fs(i * 0.2, **kw) for i in range(n)]


# ─────────────── split_scenes ───────────────


def test_one_cut_gives_two_scenes() -> None:
    st = series(20)
    st[10] = fs(2.0, cut=0.9)
    assert split_scenes(st, 4.0, 0.5, 0.6) == [(0, 10), (10, 20)]


def test_no_cut_is_one_scene_and_empty_is_none() -> None:
    assert split_scenes(series(20), 4.0, 0.5, 0.6) == [(0, 20)]
    assert split_scenes([], 0.0, 0.5, 0.6) == []


def test_flash_shorter_than_min_scene_is_ignored() -> None:
    st = series(20)
    st[10], st[11] = fs(2.0, cut=0.9), fs(2.2, cut=0.9)  # two cuts 0.2 s apart
    assert split_scenes(st, 4.0, 0.5, 0.6) == [(0, 10), (10, 20)]


def test_short_tail_scene_is_merged_back() -> None:
    st = series(20)
    st[19] = fs(3.8, cut=0.9)  # cut 0.2 s before the end of a 4 s clip
    assert split_scenes(st, 4.0, 0.5, 0.6) == [(0, 20)]


# ─────────────── classify_motion (spec §3.2-6: pan ≠ shake) ───────────────


def test_static_tripod_shot() -> None:
    assert classify_motion(series(20, dx=0.1), CFG)[0] == "tinh"


def test_steady_pan_is_not_shake() -> None:
    motion, jitter, consistency = classify_motion(series(20, dx=6.0), CFG)
    assert (motion, jitter, consistency) == ("lia", 0.0, 1.0)


def test_back_and_forth_is_shake() -> None:
    st = [fs(i * 0.2, dx=8.0 * (-1) ** i, dy=5.0 * math.sin(i * 2.1)) for i in range(20)]
    motion, jitter, consistency = classify_motion(st, CFG)
    assert motion == "rung" and jitter > CFG["shake_jitter_px"] and consistency < 0.6


def test_jerky_drift_in_one_direction_is_shake_not_pan() -> None:
    # Short windows: hand shake can drift one way (consistency ≥ 0.6) but the motion vector
    # jumps by about its own size every sample; a real pan changes smoothly.
    st = [fs(i * 0.2, dx=20.0 if i % 2 else 60.0, dy=10.0 * (-1) ** i) for i in range(10)]
    motion, _, consistency = classify_motion(st, CFG)
    assert consistency >= CFG["pan_min_consistency"] and motion == "rung"


def test_pan_with_mild_wobble_stays_pan() -> None:
    st = [fs(i * 0.2, dx=6.0 + (-1) ** i, dy=0.5) for i in range(10)]
    assert classify_motion(st, CFG)[0] == "lia"


def test_single_sample_is_static() -> None:
    assert classify_motion([fs(0.0)], CFG) == ("tinh", 0.0, 1.0)


# ─────────────── blur_flags (absolute floor only — decision 28/09/2026, option B) ───────────────


def test_blur_only_below_floor_even_in_least_sharp_20_percent() -> None:
    sharp = [500, 400, 300, 250, 200, 180, 160, 150, 140, 75]
    assert blur_flags(sharp, floor=30.0) == [False] * 10  # all above floor: nobody flagged


def test_blur_flags_every_scene_under_floor() -> None:
    sharp = [10.0, 12.0, 15.0, 20.0, 25.0, 300.0]  # a bad shooting day: 5 of 6 out of focus
    assert blur_flags(sharp, floor=30.0) == [True, True, True, True, True, False]
    assert blur_flags([], 30.0) == []


# ─────────────── pick_segment / styles ───────────────


def test_segment_puts_action_peak_at_35_percent() -> None:
    st = [fs(i * 0.2, act=10.0 if i == 20 else 1.0) for i in range(50)]  # peak at 4.0 s
    seg = pick_segment(st, 0.0, 10.0, 1.2, 2.5)
    assert seg is not None and seg.peak == 4.0
    assert seg.start == pytest.approx(3.125) and seg.end == pytest.approx(5.625)


def test_segment_clamped_inside_scene_and_short_scene_rejected() -> None:
    st = [fs(i * 0.2, act=10.0 if i == 0 else 1.0) for i in range(10)]  # peak at scene start
    seg = pick_segment(st, 0.0, 2.0, 1.2, 2.5)
    assert seg is not None and (seg.start, seg.end) == (0.0, 2.0)  # whole 2 s scene
    assert pick_segment(st, 0.0, 1.0, 1.2, 2.5) is None  # shorter than the style minimum


def test_style_ranges_follow_rules() -> None:
    assert style_segment_range(STYLES["tvc_cat_nhanh"]) == (1.2, 2.5)
    assert style_segment_range(STYLES["sang_trong"]) == (3.0, 5.5)
    assert style_segment_range(STYLES["theo_mo_ta"]) == (2.5, 4.0)  # falls back to scene_sec


def test_usable_by_style_counts_segments_of_clean_scenes() -> None:
    seg = pick_segment(series(40), 0.0, 6.0, 1.2, 2.5)
    assert seg is not None

    def scene(**kw: object) -> Scene:
        return Scene(clip="a", index=0, start=0.0, end=6.0, sharpness=100, motion="tinh",
                     jitter=0, consistency=1, **kw)  # fmt: skip

    scenes = [
        scene(segments={"tvc_cat_nhanh": [seg, seg], "sang_trong": []}),
        scene(segments={"tvc_cat_nhanh": [seg], "sang_trong": [seg]}),
        scene(blurry=True, segments={"tvc_cat_nhanh": [seg], "sang_trong": [seg]}),
    ]
    counts = usable_by_style(scenes, {"tvc_cat_nhanh": {}, "sang_trong": {}})
    assert counts == {"tvc_cat_nhanh": 3, "sang_trong": 1}  # blurry scene never counts


def test_segments_labelled_khac_are_never_usable() -> None:
    # Decision 29/09/2026: text cards / outros / logos (label "khac") are not footage.
    def seg(*labels: str) -> Segment:
        return Segment(start=0.0, end=2.0, peak=1.0, labels=list(labels))

    segs = [seg("khac"), seg("thiet_bi", "khac"), seg(), seg("khac", "thiet_bi")]
    sc = Scene(clip="a", index=0, start=0.0, end=6.0, sharpness=100, motion="tinh",
               jitter=0, consistency=1, segments={"tvc_cat_nhanh": segs})  # fmt: skip
    # Only a segment whose *leading* label is khac is dropped; unlabelled ones still count.
    assert usable_by_style([sc], {"tvc_cat_nhanh": {}}) == {"tvc_cat_nhanh": 2}


# ─────────────── 2 s motion windows → usable spans (decision 28/09/2026) ───────────────


def shaky_then_static(n_shaky: int, n: int) -> list[FrameStats]:
    """0.2 s samples: the first n_shaky samples jump back and forth, the rest are still."""
    return [fs(i * 0.2, dx=8.0 * (-1) ** i, dy=5.0 * math.sin(i * 2.1)) if i < n_shaky
            else fs(i * 0.2) for i in range(n)]  # fmt: skip


def test_windows_flag_only_the_shaky_part() -> None:
    wins = motion_windows(shaky_then_static(20, 50), 0.0, 10.0, 2.0, CFG)
    assert [(w.start, w.end) for w in wins] == [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10)]
    assert [w.motion for w in wins] == ["rung", "rung", "tinh", "tinh", "tinh"]
    assert [w.shaky for w in wins] == [True, True, False, False, False]


def test_short_tail_window_is_merged() -> None:
    wins = motion_windows(series(26), 0.0, 5.2, 2.0, CFG)  # 2 + 2 + 1.2 → 1.2 ≥ half: kept
    assert [(w.start, w.end) for w in wins] == [(0, 2), (2, 4), (4, 5.2)]
    wins = motion_windows(series(22), 0.0, 4.4, 2.0, CFG)  # 0.4 s tail → merged back
    assert [(w.start, w.end) for w in wins] == [(0, 2), (2, 4.4)]
    assert motion_windows([], 0.0, 0.0, 2.0, CFG) == []


def test_spans_join_adjacent_clean_windows() -> None:
    def w(a: float, b: float, shaky: bool) -> Window:
        return Window(start=a, end=b, motion="rung" if shaky else "tinh", jitter=0,
                      consistency=1, shaky=shaky)  # fmt: skip

    wins = [w(0, 2, False), w(2, 4, True), w(4, 6, False), w(6, 8, False), w(8, 9, True)]
    assert usable_spans(wins) == [(0, 2), (4, 8)]
    assert usable_spans([w(0, 2, True)]) == []


def test_long_clean_take_gives_up_to_3_spaced_segments() -> None:
    st = series(460)  # 92 s, all still
    segs = pick_clip_segments(st, [(0.0, 92.0)], 1.2, 2.5, gap=3.0, max_n=3)
    assert len(segs) == 3
    assert all(s.start >= 0.0 and s.end <= 92.0 for s in segs)
    assert all(b.start - a.end >= 3.0 for a, b in itertools.pairwise(segs))  # sorted by time


def test_segments_stay_inside_spans_and_respect_style_minimum() -> None:
    st = shaky_then_static(20, 50)
    segs = pick_clip_segments(st, [(4.0, 10.0)], 3.0, 5.5, gap=3.0, max_n=3)
    assert len(segs) == 1 and segs[0].start >= 4.0 and segs[0].end <= 10.0
    assert pick_clip_segments(st, [(0.0, 2.0)], 3.0, 5.5, gap=3.0, max_n=3) == []


def test_gap_applies_across_spans() -> None:
    st = series(60)
    segs = pick_clip_segments(st, [(0.0, 2.5), (4.0, 6.5)], 1.2, 2.5, gap=3.0, max_n=3)
    assert len(segs) == 1  # the two 2.5 s spans are only 1.5 s apart


def test_room_left_before_the_best_segment_is_used() -> None:
    # Bug found in 3c (12 s clean take gave 1 segment): the first slot's segment was pushed
    # next to the best one and dropped for the 3 s gap, although 0–3.5 s was still free.
    acts = {37: 9.0, 23: 5.0}  # best peak at 7.4 s, a smaller one at 4.6 s
    st = [fs(i * 0.2, act=acts.get(i, 1.0)) for i in range(60)]
    segs = pick_clip_segments(st, [(0.0, 12.0)], 1.2, 2.5, gap=3.0, max_n=3)
    assert len(segs) == 2 and segs[1].peak == pytest.approx(7.4)
    assert segs[1].start - segs[0].end >= 3.0


def test_busiest_moments_win_when_capped() -> None:
    st = [fs(i * 0.2, act=9.0 if i in (100, 300) else 1.0) for i in range(460)]
    segs = pick_clip_segments(st, [(0.0, 92.0)], 1.2, 2.5, gap=3.0, max_n=2)
    assert sorted(s.peak for s in segs) == [20.0, 60.0]
