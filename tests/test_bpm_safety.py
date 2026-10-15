"""Unit tests: domain/bpm_safety.py + bpm_measure.py + rules.music.bpm_safe_by_style_duration.

The table in rules.json is a measurement, so the first tests re-measure it with the real planner:
if styles, lengths or the beat rules change and nobody regenerates the table, they fail.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from gani_dung_video.domain.bpm_measure import blocked_text, level_of_plan, levels_at, sweep_entry
from gani_dung_video.domain.bpm_safety import (
    BPM_BLOCKED_EXIT_CODE,
    check_bpm_safe,
    complement,
    format_ranges,
    message,
)
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import MusicInfo, compile_plan
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
LO, HI = (int(x) for x in RULES["music"]["bpm_safe_domain"])
TABLE = RULES["music"]["bpm_safe_by_style_duration"]
BEAT_STYLES = [k for k, v in RULES["styles"].items() if v.get("driver") != "speech"]
FAST = [15, 30, 45, 60]  # measured at every whole BPM; 3-minute plans are slow, they are sampled


def plan_at(style: str, duration: int, bpm: float):  # type: ignore[no-untyped-def]
    o = VideoOptions(phong_cach=style, muc_dich="gioi_thieu_xuong", do_dai_giay=duration,
                     so_ban_nhap=1, nhac="thu_vien", nhac_file="x.mp3")  # fmt: skip
    m = MusicInfo(track="x.mp3", bpm=bpm, first_beat_sec=0.4, duration_sec=900.0, source="library")
    return compile_plan(o, RULES, music=m, available_tags=set())


# ─────────────── the table is what the planner says ───────────────


@pytest.mark.parametrize("duration", FAST)
@pytest.mark.parametrize("style", BEAT_STYLES)
def test_the_table_equals_a_fresh_sweep_of_every_whole_bpm(style: str, duration: int) -> None:
    fresh = sweep_entry(RULES, style, duration, LO, HI)
    assert fresh == TABLE[style][str(duration)], f"{style} {duration}s: regenerate the table"


@pytest.mark.parametrize("style", BEAT_STYLES)
def test_three_minute_entries_match_a_sample_of_the_planner(style: str) -> None:
    sample = list(range(LO, HI + 1, 9))
    got = levels_at(RULES, style, 180, sample)
    want = [check_bpm_safe(RULES, style, 180, b).level for b in sample]
    assert got == want


def test_the_table_has_every_beat_style_and_length_and_no_speech_style() -> None:
    assert set(TABLE) == set(BEAT_STYLES) and "ban_hang_co_loi" not in TABLE
    for style in BEAT_STYLES:
        assert set(TABLE[style]) == {"15", "30", "45", "60", "180"}


def test_ranges_are_ordered_inside_the_domain_and_never_overlap() -> None:
    for style, by_len in TABLE.items():
        for duration, entry in by_len.items():
            seen: list[tuple[int, int]] = []
            for key in ("blocked", "warning"):
                rs = [tuple(r) for r in entry[key]]
                assert rs == sorted(rs), (style, duration, key)
                assert all(LO <= a <= b <= HI for a, b in rs), (style, duration, key)
                seen += rs
            seen.sort()
            assert all(x[1] < y[0] for x, y in pairwise(seen)), (style, duration)


# ─────────────── levels ───────────────


@pytest.mark.parametrize(
    ("style", "duration", "bpm", "level"),
    [
        ("sang_trong", 15, 104, "BLOCKING"),  # 20.8 s for 15 s: +38 %
        ("sang_trong", 15, 100, "WARNING"),  # counted at 100 instead of 50 since 14/10/2026
        ("sang_trong", 15, 88, "WARNING"),  # +1.36 s: cut, within 10 %
        ("sang_trong", 15, 87, "BLOCKING"),  # inside the style's own 60–85 range: still blocked
        ("ke_chuyen_nghe", 15, 80, "OK"),  # 15.0 s exactly
        ("ke_chuyen_nghe", 15, 100, "WARNING"),  # +0.6 s
        ("tvc_cat_nhanh", 15, 120, "OK"),
        ("ban_hang_co_loi", 15, 100, "OK"),  # speech style: the table says nothing
    ],
)
def test_levels_of_known_cases(style: str, duration: int, bpm: float, level: str) -> None:
    assert check_bpm_safe(RULES, style, duration, bpm).level == level


@pytest.mark.parametrize(
    ("style", "duration", "bpm"),
    [("sang_trong", 15, 100), ("sang_trong", 15, 88), ("ke_chuyen_nghe", 15, 80),
     ("sang_trong", 30, 105), ("review_mon_an", 30, 100)],
)  # fmt: skip
def test_a_compiled_plan_gets_the_same_level_as_the_table(
    style: str, duration: int, bpm: int
) -> None:
    assert level_of_plan(plan_at(style, duration, float(bpm)), RULES) == (
        check_bpm_safe(RULES, style, duration, bpm).level
    )


def test_outside_the_measured_domain_the_table_stays_silent() -> None:
    assert check_bpm_safe(RULES, "sang_trong", 15, LO - 10).level == "OK"
    assert check_bpm_safe(RULES, "sang_trong", 15, HI + 10).level == "OK"


def test_a_decimal_bpm_is_looked_up_at_the_nearest_whole_one() -> None:
    assert check_bpm_safe(RULES, "sang_trong", 15, 104.4).level == "BLOCKING"
    assert check_bpm_safe(RULES, "sang_trong", 15, 103.6).level == "BLOCKING"
    assert check_bpm_safe(RULES, "sang_trong", 15, 100.4).level == "WARNING"


def test_buildable_ranges_are_what_blocked_leaves_free() -> None:
    chk = check_bpm_safe(RULES, "sang_trong", 15, 104)
    blocked = [tuple(r) for r in TABLE["sang_trong"]["15"]["blocked"]]
    assert chk.buildable == complement(blocked, LO, HI)
    assert all(not (a <= 104 <= b) for a, b in chk.buildable)


def test_complement_and_format() -> None:
    assert complement([(55, 65), (83, 87)], 55, 100) == [(66, 82), (88, 100)]
    assert complement([], 55, 60) == [(55, 60)]
    assert complement([(55, 60)], 55, 60) == []
    assert format_ranges([(66, 82), (90, 90)]) == "66–82, 90"


# ─────────────── the words ───────────────


def test_the_block_message_names_the_track_the_style_the_length_and_the_way_out() -> None:
    chk = check_bpm_safe(RULES, "sang_trong", 15, 104)
    text = message(RULES, "W_BPM_BLOCK", "sang_trong", 15, 104, chk.buildable)
    assert "BPM 104" in text and "Sang trọng" in text and "15 giây" in text
    assert "±10%" in text and "66–82" in text and "Chọn bài khác" in text


def test_the_stop_message_has_the_numbers_and_the_override() -> None:
    plan = plan_at("sang_trong", 15, 104.0)
    text = blocked_text(RULES, plan, 104.0)
    assert "20.8s" in text and "+5.8s" in text and "+38%" in text and "--bpm-override" in text


def test_a_count_that_cannot_reach_the_length_falls_back_on_one_that_can() -> None:
    # 14/10/2026: 100 BPM is counted at 50 for Sang trọng (closest to 60–85), which makes every
    # scene 7.2 s and 15 s impossible (21.6 s). Counted at 100 (still within 20 %) it fits.
    plan = plan_at("sang_trong", 15, 100.0)
    assert plan.music is not None and plan.music.fold == "giu_nguyen"
    assert plan.music.effective_bpm == 100.0 and plan.beat_sec == pytest.approx(0.6)
    assert plan.planned_beats * plan.beat_sec == pytest.approx(15.6)
    assert any("thay vì 50 BPM" in a for a in plan.adjustments)
    # a count that already fits is never changed, even if another one would fit too
    fits = plan_at("sang_trong", 15, 80.0)
    assert fits.music is not None and fits.music.fold == "giu_nguyen"
    assert not any("thay vì" in a for a in fits.adjustments)
    assert BPM_BLOCKED_EXIT_CODE == 4  # 0 ok, 1 failed, 2 bad input, 3 infeasible
