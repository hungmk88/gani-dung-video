"""Unit tests: domain/plan.py — the plan's length is the chosen length (hướng B, 13/10/2026).

No music: the virtual grid is stretched so the planned beats add up to exactly the chosen
seconds. Music: the tempo is fixed, so a plan more than the threshold longer is cut at the
chosen length (with a fade), one more than the threshold shorter is rebuilt with more beats.
"""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import (
    MusicInfo,
    PlanBlock,
    PlanError,
    _pick_music_plan,
    compile_plan,
    natural_sec,
    planned_sec,
)
from gani_dung_video.infrastructure.config_files import load_rules

BASE = load_rules()
RULES = {**BASE, "durations": {**BASE["durations"],
                               "180": {**BASE["durations"]["180"], "enabled": True}}}  # fmt: skip
TAGS = {"nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "mon_an", "nguoi_noi"}
STYLES = ["ke_chuyen_nghe", "tvc_cat_nhanh", "review_mon_an", "sang_trong"]
DURATIONS = [15, 30, 45, 60, 180]
THRESHOLD = 0.5


def with_qc(**qc: Any) -> dict[str, Any]:
    return {**RULES, "qc": {**RULES["qc"], **qc}}


def opts(style: str, duration: int, music: bool) -> VideoOptions:
    kw: dict[str, Any] = {"phong_cach": style, "muc_dich": "gioi_thieu_xuong",
                          "do_dai_giay": duration, "so_ban_nhap": 1}  # fmt: skip
    if music:
        return VideoOptions(nhac="thu_vien", nhac_file="x.mp3", **kw)
    return VideoOptions(nhac="khong_nhac", **kw)


def track(bpm: float) -> MusicInfo:
    return MusicInfo(track="x.mp3", bpm=bpm, first_beat_sec=0.4, duration_sec=400.0,
                     source="library")  # fmt: skip


def bpms(style: str) -> list[float]:
    lo, hi = RULES["styles"][style]["bpm_range"]
    return [float(lo), (lo + hi) / 2, float(hi)]


# ─────────────── no music: exact length ───────────────


@pytest.mark.parametrize("duration", DURATIONS)
@pytest.mark.parametrize("style", STYLES)
def test_without_music_the_plan_is_exactly_the_chosen_length(style: str, duration: int) -> None:
    p = compile_plan(opts(style, duration, music=False), RULES, music=None, available_tags=TAGS)
    assert p.driver == "grid" and p.trim_to_sec is None and p.trim_fade_sec == 0.0
    assert p.planned_beats * p.beat_sec == pytest.approx(duration, abs=1e-9)
    assert planned_sec(p) == pytest.approx(duration, abs=1e-9)


@pytest.mark.parametrize("duration", DURATIONS)
@pytest.mark.parametrize("style", STYLES)
def test_the_stretched_beat_stays_within_seven_percent_of_the_styles_grid(
    style: str, duration: int
) -> None:
    cfg = RULES["styles"][style]
    nominal = float(cfg["scene_avg_sec"]) / min(cfg["beats_allowed"])
    p = compile_plan(opts(style, duration, music=False), RULES, music=None, available_tags=TAGS)
    assert abs(p.beat_sec / nominal - 1) <= 0.07


def test_stretching_keeps_the_beat_count_and_says_what_it_did() -> None:
    p = compile_plan(opts("tvc_cat_nhanh", 15, music=False), RULES, music=None, available_tags=TAGS)
    assert p.planned_beats == 12 and p.beat_sec == pytest.approx(1.25)  # 12 × 1.3 = 15.6 before
    assert any("Co giãn lưới ảo" in a and "1.300s → 1.250s" in a for a in p.adjustments)


def test_a_plan_that_was_already_exact_is_left_alone() -> None:
    p = compile_plan(opts("ke_chuyen_nghe", 15, music=False), RULES, music=None,
                     available_tags=TAGS)  # fmt: skip
    assert p.beat_sec == pytest.approx(0.75) and p.adjustments == []


# ─────────────── music: cut + fade, or rebuild longer ───────────────


@pytest.mark.parametrize("duration", DURATIONS)
@pytest.mark.parametrize("style", STYLES)
def test_with_music_the_length_is_the_chosen_one_or_cut_to_it(style: str, duration: int) -> None:
    for bpm in bpms(style):
        p = compile_plan(opts(style, duration, music=True), RULES, music=track(bpm),
                         available_tags=TAGS)  # fmt: skip
        beats_sec = p.planned_beats * p.beat_sec
        assert p.driver == "music"
        if p.trim_to_sec is None:
            assert abs(beats_sec - duration) <= THRESHOLD + 1e-9, (style, duration, bpm)
            assert p.trim_fade_sec == 0.0
        else:
            assert beats_sec > duration + THRESHOLD, (style, duration, bpm)
            assert p.trim_to_sec == duration and p.trim_fade_sec == pytest.approx(0.5)
        assert abs(planned_sec(p) - duration) <= THRESHOLD + 1e-9


def test_music_plan_longer_than_the_threshold_is_cut_at_the_chosen_length() -> None:
    # Sang trọng at 100 BPM: scenes of 6 or 8 beats of 0.6 s, the plan comes out at 21.6 s.
    p = compile_plan(opts("sang_trong", 15, music=True), RULES, music=track(100.0),
                     available_tags=TAGS)  # fmt: skip
    assert p.planned_beats * p.beat_sec > 15.5
    assert p.trim_to_sec == 15.0 and planned_sec(p) == 15.0
    assert any("cắt ở 15s" in a and "0.5s" in a for a in p.adjustments)


def test_music_plan_within_the_threshold_is_kept_as_it_is() -> None:
    p = compile_plan(opts("ke_chuyen_nghe", 15, music=True), RULES, music=track(70.0),
                     available_tags=TAGS)  # fmt: skip
    assert p.planned_beats * p.beat_sec == pytest.approx(15.428, abs=1e-3)  # +0.43 s
    assert p.trim_to_sec is None and p.adjustments == []


def test_music_plan_shorter_than_the_threshold_is_rebuilt_with_more_beats() -> None:
    # Sang trọng at 85 BPM used to plan 20 beats = 14.1 s (0.9 s short of 15).
    p = compile_plan(opts("sang_trong", 15, music=True), RULES, music=track(85.0),
                     available_tags=TAGS)  # fmt: skip
    assert p.planned_beats > 20
    assert planned_sec(p) >= 15 - THRESHOLD - 1e-9
    assert any("lập lại kế hoạch" in a for a in p.adjustments)


# ─────────────── policy in rules.json ───────────────


def test_policy_none_turns_the_music_trim_off_but_not_the_grid_stretch() -> None:
    rules = with_qc(duration_trim_policy="none")
    p = compile_plan(opts("sang_trong", 15, music=True), rules, music=track(100.0),
                     available_tags=TAGS)  # fmt: skip
    assert p.trim_to_sec is None and p.planned_beats * p.beat_sec > 20  # as before
    g = compile_plan(opts("tvc_cat_nhanh", 15, music=False), rules, music=None, available_tags=TAGS)
    assert g.planned_beats * g.beat_sec == pytest.approx(15.0)


def test_a_wider_threshold_lets_a_longer_plan_through() -> None:
    p = compile_plan(opts("sang_trong", 15, music=True), with_qc(duration_trim_threshold_sec=7.0),
                     music=track(100.0), available_tags=TAGS)  # fmt: skip
    assert p.trim_to_sec is None


def test_the_fade_length_comes_from_the_policy_text() -> None:
    p = compile_plan(opts("sang_trong", 15, music=True), with_qc(duration_trim_policy="fade_1.25s"),
                     music=track(100.0), available_tags=TAGS)  # fmt: skip
    assert p.trim_fade_sec == pytest.approx(1.25)


@pytest.mark.parametrize("policy", ["fade", "fade_0s", "fade_-1s", "cut", "fade_0.5", ""])
def test_a_policy_text_that_is_not_understood_stops_the_plan(policy: str) -> None:
    with pytest.raises(PlanError, match="duration_trim_policy") as err:
        compile_plan(opts("ke_chuyen_nghe", 15, music=True), with_qc(duration_trim_policy=policy),
                     music=track(80.0), available_tags=TAGS)  # fmt: skip
    assert err.value.code == "PLAN_RULES"


def test_a_negative_threshold_stops_the_plan() -> None:
    with pytest.raises(PlanError, match="duration_trim_threshold_sec"):
        compile_plan(opts("ke_chuyen_nghe", 15, music=True),
                     with_qc(duration_trim_threshold_sec=-0.1), music=track(80.0),
                     available_tags=TAGS)  # fmt: skip


def test_speech_style_is_not_stretched_or_cut() -> None:
    o = VideoOptions(phong_cach="ban_hang_co_loi", muc_dich="ban_hang", do_dai_giay=30,
                     so_ban_nhap=1, nhac="khong_nhac")  # fmt: skip
    p = compile_plan(o, RULES, music=None, available_tags=TAGS)
    assert p.driver == "speech" and p.trim_to_sec is None
    cfg = RULES["styles"]["ban_hang_co_loi"]
    assert p.beat_sec == pytest.approx(float(cfg["scene_avg_sec"]) / min(cfg["broll_beats"]))


@pytest.mark.parametrize("duration", DURATIONS)
@pytest.mark.parametrize("style", STYLES)
def test_frame_rounding_cannot_push_a_kept_plan_past_the_tolerance(
    style: str, duration: int
) -> None:
    """E1-DUR measures the rendered content length against the chosen one, so a plan the trim
    keeps (within 0.5 s) must still be within it after its end is put on a whole frame."""
    fps = int(RULES["render"]["fps"])
    tol = float(RULES["qc"]["duration_tolerance_sec"][str(duration)])
    for bpm in range(55, 146, 3):
        p = compile_plan(opts(style, duration, music=True), RULES, music=track(float(bpm)),
                         available_tags=TAGS)  # fmt: skip
        end = round(planned_sec(p) * fps) / fps
        assert abs(end - duration) <= tol + 1e-9, (style, duration, bpm, end)


# ─────────────── picking the plan that needs the least cutting ───────────────


def pick(beat: float, first_total: int, chosen: float = 15.0, thr: float = 0.5) -> int:
    """Total beats picked when every total around the target can be built."""

    def build(total: int) -> tuple[list[PlanBlock], list[Any]]:
        return [PlanBlock(role="main", beats=total)], []

    picked, _ = _pick_music_plan(chosen, beat, [4, 6], first_total, build, build(first_total), thr)
    return int(picked[0].beats)


def test_a_total_within_the_threshold_beats_one_that_has_to_be_cut() -> None:
    assert pick(0.6, 26) == 25  # 26 beats = 15.6 s must be cut; 25 beats = 15.0 s do not


def test_the_closest_total_within_the_threshold_wins() -> None:
    assert pick(0.7, 22) == 21  # 15.4 s (+0.4) or 14.7 s (-0.3): the shorter miss is closer


def test_when_nothing_is_within_the_threshold_the_smallest_cut_wins_over_a_short_plan() -> None:
    assert pick(1.2, 12) == 13  # 14.4 s is 0.6 short and cannot be fixed; 15.6 s is cut to 15


def test_a_plan_beyond_the_gap_limit_is_warned_about_at_plan_time() -> None:
    bad = compile_plan(opts("sang_trong", 15, music=True), RULES, music=track(100.0),
                       available_tags=TAGS)  # fmt: skip
    assert natural_sec(bad) > 15 * 1.1 and any(w.startswith("W_DUR_GAP") for w in bad.warnings)
    fine = compile_plan(opts("ke_chuyen_nghe", 15, music=True), RULES, music=track(100.0),
                        available_tags=TAGS)  # fmt: skip
    assert not any(w.startswith("W_DUR_GAP") for w in fine.warnings)  # 15.6 s: cut, but ≤ 10%
