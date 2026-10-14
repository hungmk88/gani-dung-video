"""Unit tests: domain/beats.py — BPM folding, beat grid, loops, snap_to_beats (4a)."""

from __future__ import annotations

import itertools

import pytest

from gani_dung_video.domain.beats import (
    DraftSegment,
    SceneRef,
    SnapError,
    beat_grid,
    beats_that_fit,
    fold_bpm,
    loop_info,
    segment_refs,
    snap_to_beats,
)
from gani_dung_video.domain.scenes import Scene, Segment

FPS = 30


def ref(start: float, end: float, peak: float) -> SceneRef:
    return SceneRef(clip="a.mp4", start=start, end=end, peak=peak)


# ─────────────── fold_bpm (X12) ───────────────


def test_bpm_inside_range_is_kept() -> None:
    assert fold_bpm(92.0, (70.0, 95.0)) == (92.0, "giu_nguyen")


def test_bpm_too_fast_is_halved_and_too_slow_is_doubled() -> None:
    assert fold_bpm(128.0, (60.0, 85.0)) == (64.0, "dem_2_thanh_1")  # count 2 beats as 1
    assert fold_bpm(40.0, (60.0, 85.0)) == (80.0, "tach_1_thanh_2")  # split 1 beat in 2


def test_bpm_without_range_or_hopeless_is_kept() -> None:
    assert fold_bpm(150.0, None) == (150.0, "giu_nguyen")
    assert fold_bpm(150.0, (60.0, 85.0))[1] == "dem_2_thanh_1"  # 75 fits
    assert fold_bpm(97.0, (60.0, 85.0)) == (97.0, "giu_nguyen")  # 48.5 / 194 are worse
    assert fold_bpm(300.0, (60.0, 85.0)) == (300.0, "giu_nguyen")  # 150 is still > 20% away


# ─────────────── beat grid, fitting, loops ───────────────


def test_beat_grid_has_count_plus_one_boundaries() -> None:
    g = beat_grid(0.5, 4)
    assert g == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])


def test_beats_that_fit_a_segment() -> None:
    # 0.8333 s per beat (72 BPM), a 5.5 s segment: 6 beats = 5.0 s fits, 8 beats = 6.67 s not.
    assert beats_that_fit(5.5, 60 / 72, [6, 8]) == [6]
    assert beats_that_fit(2.0, 0.5, [1, 2]) == [1, 2]
    assert beats_that_fit(0.4, 0.5, [1, 2]) == []


# ─────────────── audit 1: a window that "fits" survives the snap's frame rounding ───────────────


def _snaps_with_a_long_scene_first(window: float, beat: float, n: int, start_boundary: int) -> None:
    """Lay one n-beat cut that must live in a `window`-second scene on boundary `start_boundary`."""
    grid = beat_grid(beat, start_boundary + n)
    long_scene = ref(0.0, 20.0, 10.0)
    ids = ["long"] * start_boundary + ["s"]
    segs = [DraftSegment(block="b", scene_id=ids[k], beats=1 if k < start_boundary else n)
            for k in range(start_boundary + 1)]  # fmt: skip
    snap_to_beats(segs, grid, {"s": ref(0.0, window, window * 0.4), "long": long_scene}, FPS)


def test_a_window_one_frame_too_short_for_the_rounded_cut_is_not_offered() -> None:
    """112 BPM, a 1.0724 s window: 2 beats are 1.0714 s, but the cut that starts on boundary 6
    is rounded to 1.1 s. The fit test used to say yes and the snap said "quá ngắn"."""
    beat = 60 / 112
    assert beats_that_fit(1.0724, beat, [2, 3, 4]) == []
    with pytest.raises(SnapError, match="quá ngắn"):
        _snaps_with_a_long_scene_first(1.0724, beat, 2, 6)  # why the filter must refuse it


def test_a_window_with_a_frame_to_spare_still_fits() -> None:
    beat = 60 / 112
    window = 2 * beat + 1 / FPS + 1e-6
    assert beats_that_fit(window, beat, [2, 3, 4]) == [2]
    for boundary in range(0, 30):
        _snaps_with_a_long_scene_first(window, beat, 2, boundary)


@pytest.mark.parametrize("bpm", range(55, 181, 3))
def test_no_window_that_fits_can_be_refused_by_the_snap_at_any_position(bpm: int) -> None:
    beat = 60.0 / bpm
    for n in (2, 3, 4, 6):
        for extra in (
            0.0,
            0.2,
            0.5,
            0.8,
            0.999,
        ):  # the window sits this far above n beats, in frames
            window = n * beat + extra / FPS
            if n not in beats_that_fit(window, beat, [n]):
                continue
            for boundary in range(0, 12):
                _snaps_with_a_long_scene_first(window, beat, n, boundary)


def test_loop_info_only_when_track_is_shorter_than_video() -> None:
    assert loop_info(40.0, 0.5, 92.0, 30, 4) is None
    got = loop_info(20.0, 0.5, 92.0, 30, 4)
    assert got is not None
    loop_sec, repeats = got
    bar = 4 * 60 / 92
    assert loop_sec == pytest.approx(7 * bar)  # whole bars only, so the beat grid stays intact
    assert repeats == 2


def test_loop_info_judges_what_can_be_heard_from_the_first_beat() -> None:
    """Audit 4: the track was compared whole, but the music starts at its first beat."""
    assert loop_info(16.0, 0.8, 100.0, 15.2, 4) is None  # exactly enough: 16.0 - 0.8 = 15.2
    got = loop_info(16.0, 0.8, 100.0, 15.3, 4)  # a tenth short: it must loop
    assert got is not None and got[1] == 2


def test_loop_info_track_shorter_than_one_bar_cannot_loop() -> None:
    assert loop_info(1.0, 0.5, 92.0, 30, 4) is None


# ─────────────── segment ids for the AI ───────────────


def test_segment_refs_use_clip_stem_scene_and_pick_index() -> None:
    def seg(a: float, b: float, p: float) -> Segment:
        return Segment(start=a, end=b, peak=p)

    sc = Scene(clip="VID_1.mp4", index=2, start=0.0, end=20.0, sharpness=100, motion="tinh",
               jitter=0, consistency=1,
               segments={"sang_trong": [seg(1, 5, 2), seg(9, 13, 10)],
                         "tvc_cat_nhanh": []})  # fmt: skip
    got = segment_refs([sc], "sang_trong")
    assert list(got) == ["VID_1_s2_0", "VID_1_s2_1"]
    assert got["VID_1_s2_1"] == SceneRef(clip="VID_1.mp4", start=9, end=13, peak=10)


# ─────────────── snap_to_beats (spec §7.3, X17/X18) ───────────────


def test_cuts_land_on_beats_and_are_contiguous() -> None:
    beats = beat_grid(0.5, 10)
    scenes = {"s1": ref(0, 10, 3), "s2": ref(20, 30, 24)}
    segs = [DraftSegment(block="hook", scene_id="s1", beats=2, text="a"),
            DraftSegment(block="x", scene_id="s2", beats=4)]  # fmt: skip
    out = snap_to_beats(segs, beats, scenes)
    assert [(o.t_start, o.t_end) for o in out] == [(0.0, 1.0), (1.0, 3.0)]
    assert out[0].text == "a" and out[1].block == "x"
    assert [o.clip for o in out] == ["a.mp4", "a.mp4"]  # the render needs the source file


def test_durations_are_whole_frames_without_drift() -> None:
    beats = beat_grid(60 / 124, 60)  # 0.48387 s per beat: not a whole number of frames
    scenes = {"s": ref(0, 100, 50)}
    segs = [DraftSegment(block="b", scene_id="s", beats=1) for _ in range(50)]
    out = snap_to_beats(segs, beats, scenes)
    for o in out:
        assert round((o.t_end - o.t_start) * FPS) == pytest.approx((o.t_end - o.t_start) * FPS)
    assert all(a.t_end == b.t_start for a, b in itertools.pairwise(out))
    assert abs(out[-1].t_end - beats[50]) <= 0.5 / FPS  # error never accumulates


def test_action_peak_sits_at_35_percent_of_the_shot() -> None:
    out = snap_to_beats([DraftSegment(block="b", scene_id="s", beats=4)], beat_grid(0.5, 8),
                        {"s": ref(0, 20, 10)})  # 2 s shot  # fmt: skip
    assert out[0].src_in == pytest.approx(10 - 0.35 * 2.0)
    assert out[0].src_out - out[0].src_in == pytest.approx(2.0)
    assert out[0].peak_ratio == 0.35


def test_peak_near_the_end_of_the_segment_moves_back_up_to_50_percent() -> None:
    # 2 s shot, segment 0–6 s, peak at 4.9 s: at 35% the shot would end at 6.2 s (> 6), at 45%
    # it ends exactly at 6.0 s. X18: keep the beat, let the peak sit later in the shot.
    scenes = {"s": ref(0, 6, 4.9)}
    out = snap_to_beats([DraftSegment(block="b", scene_id="s", beats=4)], beat_grid(0.5, 8), scenes)
    assert out[0].peak_ratio == 0.45 and out[0].src_in == pytest.approx(4.0)


def test_peak_too_close_to_scene_start_is_clamped_inside_the_segment() -> None:
    out = snap_to_beats([DraftSegment(block="b", scene_id="s", beats=4)], beat_grid(0.5, 8),
                        {"s": ref(5, 9, 5.1)})  # fmt: skip
    assert out[0].src_in == 5.0 and out[0].peak_ratio is None


def test_scene_too_short_for_the_beats_is_refused() -> None:
    with pytest.raises(SnapError, match="quá ngắn"):
        snap_to_beats([DraftSegment(block="b", scene_id="s", beats=6)], beat_grid(0.5, 8),
                      {"s": ref(0, 2.5, 1)})  # fmt: skip


def test_running_past_the_beat_grid_is_refused() -> None:
    with pytest.raises(SnapError, match="độ dài nhạc"):
        snap_to_beats([DraftSegment(block="b", scene_id="s", beats=5)], beat_grid(0.5, 4),
                      {"s": ref(0, 20, 5)})  # fmt: skip


def test_unknown_scene_id_is_refused() -> None:
    with pytest.raises(SnapError, match="không có trong danh sách"):
        snap_to_beats([DraftSegment(block="b", scene_id="zz", beats=1)], beat_grid(0.5, 4), {})


def test_nonpositive_beats_are_refused() -> None:
    with pytest.raises(SnapError, match="phách"):
        snap_to_beats([DraftSegment(block="b", scene_id="s", beats=0)], beat_grid(0.5, 4),
                      {"s": ref(0, 20, 5)})  # fmt: skip
