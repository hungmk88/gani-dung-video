"""Unit tests: domain/qc.py — technical checks T1–T7 on the rendered file (5b). Pure code."""

from __future__ import annotations

import math

import pytest

from gani_dung_video.domain.edl import Caption, Edl, Shot
from gani_dung_video.domain.qc import (
    Finding,
    Measures,
    MediaFacts,
    check_captions,
    check_measures,
    check_media,
    fit_font_px,
    parse_measures,
    wrap_lines,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
QC = RULES["qc"]

# Real ffmpeg 9 output (blackdetect + freezedetect + ebur128=peak=true, 29/09/2026)
LOG = """\
[Parsed_blackdetect_0 @ 00000261f08d8f40] black_start:2 black_end:2.5 black_duration:0.5
[Parsed_freezedetect_1 @ 00000261f088f380] lavfi.freezedetect.freeze_start: 2.533333
[Parsed_freezedetect_1 @ 00000261f088f380] lavfi.freezedetect.freeze_duration: 1.533333
[Parsed_freezedetect_1 @ 00000261f088f380] lavfi.freezedetect.freeze_end: 4.066667
[Parsed_ebur128_2 @ 00000261f09646c0] Summary:

  Integrated loudness:
    I:         -21.8 LUFS
    Threshold: -31.8 LUFS

  Loudness range:
    LRA:         0.0 LU

  True peak:
    Peak:      -17.6 dBFS
"""


def shot(t0: float, t1: float, pad: float = 0.0) -> Shot:
    return Shot(source="a.mp4", src_in=0, src_out=t1 - t0, t_start=t0, t_end=t1, block="b",
                hdr=False, dissolve_out_sec=0, pad_sec=pad)  # fmt: skip


def edl(captions: list[Caption] | None = None, shots: list[Shot] | None = None,
        width: int = 540) -> Edl:  # fmt: skip
    shots = shots or [shot(0, 3), shot(3, 6)]
    end = shots[-1].t_end
    return Edl(style="ke_chuyen_nghe", width=width, height=width * 16 // 9, fps=30,
               video_kbps=2500, shots=shots, content_end=end, end_card_sec=2.0,
               duration=end + 2.0, music=None, ambient_db=-20, lufs=-14.0, true_peak_db=-1.5,
               font="Be Vietnam Pro ExtraBold", captions=captions or [])  # fmt: skip


def facts(**kw: object) -> MediaFacts:
    base: dict[str, object] = {"width": 540, "height": 960, "fps": 30.0, "duration": 8.0,
                               "frames": 240, "video_codec": "h264", "pix_fmt": "yuv420p",
                               "audio_codec": "aac", "sample_rate": 48000,
                               "channels": 2}  # fmt: skip
    return MediaFacts.model_validate({**base, **kw})


def codes(findings: list[Finding], level: str | None = None) -> list[str]:
    return [f.code for f in findings if level is None or f.level == level]


# ─────────────── parsing the one-pass ffmpeg log ───────────────


def test_log_is_parsed_into_measures() -> None:
    m = parse_measures(LOG, duration=5.0)
    assert (m.lufs, m.true_peak_db) == (-21.8, -17.6)
    assert m.blacks == [(2.0, 2.5)]
    [(start, end)] = m.freezes
    assert start == pytest.approx(2.533333) and end == pytest.approx(4.066667)


def test_a_freeze_still_running_at_the_end_closes_at_the_duration() -> None:
    log = (
        "[Parsed_freezedetect_1 @ 0] lavfi.freezedetect.freeze_start: 6.5\n" + LOG.split("\n", 4)[4]
    )
    assert parse_measures(log, duration=8.0).freezes == [(6.5, 8.0)]


def test_missing_loudness_summary_is_an_error() -> None:
    with pytest.raises(ValueError, match="ebur128"):
        parse_measures("nothing here", duration=5.0)


# ─────────────── T1, T2: container and frame count ───────────────


def test_a_file_matching_the_edl_passes() -> None:
    assert check_media(facts(), edl(), QC) == []


@pytest.mark.parametrize(
    ("kw", "code"),
    [({"duration": 8.2}, "T1"), ({"width": 1080}, "T1"), ({"fps": 29.97}, "T1"),
     ({"pix_fmt": "yuv444p"}, "T1"), ({"sample_rate": 44100}, "T1"), ({"channels": 1}, "T1"),
     ({"audio_codec": None}, "T1"), ({"frames": 232}, "T2")],
)  # fmt: skip
def test_container_mismatches_are_errors(kw: dict[str, object], code: str) -> None:
    found = check_media(facts(**kw), edl(), QC)
    assert codes(found) == [code] and found[0].level == "error"


def test_one_frame_of_slack_is_allowed() -> None:
    assert check_media(facts(frames=239, duration=8.0 + 1 / 30), edl(), QC) == []


# T2 slack is max(frame_slack, round-half-up(fps × frame_slack_sec)): B-frame reordering can drop
# 2–3 trailing frames when -shortest cuts mid-GOP (theo_mo_ta: 507 of 510, 07/10/2026).


def edl_at(fps: int) -> Edl:
    return edl().model_copy(update={"fps": fps})


def test_a_three_frame_b_frame_drop_at_30fps_passes_t2() -> None:
    assert check_media(facts(frames=237), edl(), QC) == []


def test_the_real_theo_mo_ta_case_507_of_510_passes_t2() -> None:
    e = edl().model_copy(update={"duration": 17.0})
    assert check_media(facts(frames=507, duration=17.0), e, QC) == []


def test_slack_at_30fps_is_five_frames_not_four() -> None:
    assert check_media(facts(frames=235), edl(), QC) == []
    assert codes(check_media(facts(frames=234), edl(), QC)) == ["T2"]


def test_a_five_frame_drop_at_60fps_passes_t2() -> None:
    assert check_media(facts(fps=60.0, frames=475), edl_at(60), QC) == []


def test_slack_at_60fps_is_nine_frames() -> None:
    assert check_media(facts(fps=60.0, frames=471), edl_at(60), QC) == []
    assert codes(check_media(facts(fps=60.0, frames=470), edl_at(60), QC)) == ["T2"]


def test_a_ten_frame_loss_at_30fps_still_fails_t2() -> None:
    found = check_media(facts(frames=230), edl(), QC)
    assert codes(found) == ["T2"] and found[0].level == "error"
    assert "230 khung hình, cần 240" in found[0].msg


def test_frame_slack_is_the_floor_at_very_low_fps() -> None:
    # 5 fps × 0.15 s = 0.75 → 1 frame: the floor and the seconds rule agree on 1.
    assert check_media(facts(fps=5.0, frames=39), edl_at(5), QC) == []
    assert codes(check_media(facts(fps=5.0, frames=38), edl_at(5), QC)) == ["T2"]


def test_frame_slack_floor_wins_when_the_seconds_rule_rounds_to_zero() -> None:
    cfg = {**QC, "frame_slack": 2, "frame_slack_sec": 0.01}
    assert check_media(facts(frames=238), edl(), cfg) == []
    assert codes(check_media(facts(frames=237), edl(), cfg)) == ["T2"]


def test_the_seconds_rule_is_configured_in_rules_json() -> None:
    assert QC["frame_slack"] == 1 and QC["frame_slack_sec"] == 0.15


# ─────────────── T3, T4, T5: loudness, black, freeze ───────────────


def m(lufs: float = -14.1, tp: float = -1.3, blacks: list[tuple[float, float]] | None = None,
      freezes: list[tuple[float, float]] | None = None) -> Measures:  # fmt: skip
    return Measures(lufs=lufs, true_peak_db=tp, blacks=blacks or [], freezes=freezes or [])


def test_loudness_within_one_lu_and_peak_under_minus_one_passes() -> None:
    assert check_measures(m(), edl(), QC) == []


@pytest.mark.parametrize(("lufs", "tp"), [(-15.2, -2.0), (-12.8, -2.0), (-15.5, -0.5)])
def test_loudness_off_asks_for_the_two_pass_fix(lufs: float, tp: float) -> None:
    found = check_measures(m(lufs=lufs, tp=tp), edl(), QC)
    assert codes(found) == ["T3"] and found[0].level == "fix" and found[0].fix == "loudness"


def test_only_the_peak_too_high_asks_for_a_lower_ceiling_not_a_second_loudness_pass() -> None:
    # Found 29/09/2026: the mix was -1.8 dBTP, the AAC encoder pushed it to +0.6; three
    # two-pass re-renders changed nothing. Loudness right + peak high = encoder problem, so the
    # fix (phase 7d) is to lower the ceiling the mix is limited to by what the encoder added.
    found = check_measures(m(lufs=-14.2, tp=0.6), edl(), QC)
    assert codes(found) == ["T3"] and found[0].level == "fix" and found[0].fix == "peak"
    assert "đỉnh" in found[0].msg


def test_a_peak_just_inside_the_limit_needs_nothing() -> None:
    assert check_measures(m(lufs=-14.2, tp=-1.0), edl(), QC) == []


def test_a_video_planned_silent_skips_loudness() -> None:
    # Found 29/09/2026: TVC without music mutes camera sound (ambient_db null) → -inf LUFS;
    # the two-pass "fix" then fed -inf to loudnorm and ffmpeg failed.
    silent = edl().model_copy(update={"ambient_db": None, "music": None})
    assert check_measures(m(lufs=-math.inf, tp=-math.inf), silent, QC) == []
    assert check_measures(m(lufs=-70.0, tp=-math.inf), silent, QC) == []  # ebur128's floor


def test_silence_where_sound_was_planned_is_an_error_not_a_fix() -> None:
    for lufs in (-math.inf, -70.0):
        found = check_measures(m(lufs=lufs, tp=-math.inf), edl(), QC)
        assert codes(found) == ["T3"] and found[0].level == "error" and found[0].fix is None


def test_black_inside_the_content_is_an_error_naming_the_shot() -> None:
    found = check_measures(m(blacks=[(3.4, 3.7)]), edl(), QC)
    assert codes(found) == ["T4"] and found[0].level == "error"
    assert found[0].at_sec == 3.4 and "cảnh 2" in found[0].msg


def test_black_end_card_and_short_blips_are_ignored() -> None:
    assert check_measures(m(blacks=[(6.0, 8.0), (1.0, 1.1)]), edl(), QC) == []


def test_the_planned_fade_out_before_the_end_card_is_not_a_black_frame_bug() -> None:
    # Content ends at 6.0 s; the cut-to-length fade (0.5 s) makes dark footage go black early.
    faded = edl().model_copy(update={"trim_fade_sec": 0.5})
    assert check_measures(m(blacks=[(5.5, 8.0)]), faded, QC) == []
    # …but black before the fade starts is still a defect, and without the fade 5.5 is one too.
    assert codes(check_measures(m(blacks=[(5.0, 8.0)]), faded, QC)) == ["T4"]
    assert codes(check_measures(m(blacks=[(5.5, 8.0)]), edl(), QC)) == ["T4"]


def test_the_closing_end_wipe_is_not_a_black_frame_bug() -> None:
    # An iris closing on black (transitions, 26/10/2026) darkens the last 0.5 s of the content.
    iris = edl().model_copy(update={"end_wipe": "circleclose", "end_wipe_sec": 0.5})
    assert check_measures(m(blacks=[(5.6, 8.0)]), iris, QC) == []
    assert codes(check_measures(m(blacks=[(5.0, 8.0)]), iris, QC)) == ["T4"]


def test_freeze_is_only_a_warning_and_planned_pads_are_ignored() -> None:
    e = edl(shots=[shot(0, 3), shot(3, 6, pad=1.2)])
    found = check_measures(m(freezes=[(0.5, 2.0), (4.7, 6.0), (6.0, 8.0)]), e, QC)
    assert codes(found) == ["T5"] and found[0].level == "warn" and found[0].at_sec == 0.5


# ─────────────── T6, T7: captions ───────────────


# 800 px usable: 72 px → ~20 characters per line (3 lines), 68 px → ~21 (2 lines)
LONG = "Bò viên GANI tròn đều dai giòn làm mỗi sáng"


def width_of(text: str, font: str, px: int) -> float:
    return len(text) * px * 0.55  # stand-in: ~0.55 em per character


def test_wrap_lines_breaks_on_words_greedily() -> None:
    assert wrap_lines("một hai ba", 90.0, lambda t: len(t) * 10.0) == ["một hai", "ba"]
    assert wrap_lines("một hai ba", 100.0, lambda t: len(t) * 10.0) == ["một hai ba"]  # exact fit
    assert wrap_lines("một", 5.0, lambda t: len(t) * 10.0) == ["một"]  # one long word: one line


def test_overlapping_captions_are_an_error() -> None:
    caps = [Caption(start=0, end=3, text="A", kind="text"), Caption(start=2.5, end=4, text="B",
            kind="text")]  # fmt: skip
    found = check_captions(edl(caps), RULES, {"caption": 72, "cta": 80}, width_of)
    assert codes(found) == ["T6"] and found[0].at_sec == 2.5


def test_short_captions_fit() -> None:
    caps = [Caption(start=0, end=3, text="Một ngày ở xưởng", kind="text")]
    assert check_captions(edl(caps), RULES, {"caption": 72, "cta": 80}, width_of) == []


def test_too_long_caption_asks_for_a_smaller_font() -> None:
    caps = [Caption(start=0, end=3, text=LONG, kind="text")]
    found = check_captions(edl(caps), RULES, {"caption": 72, "cta": 80}, width_of)
    assert codes(found) == ["T7"] and found[0].level == "fix" and found[0].fix == "font"


def test_fit_font_px_steps_down_to_the_floor_then_gives_up() -> None:
    caps = [Caption(start=0, end=3, text=LONG, kind="text")]
    px = fit_font_px(edl(caps), RULES, {"caption": 72, "cta": 80}, width_of)
    assert px == {"caption": 68, "cta": 80}  # 3 lines at 72 px, 2 lines at 68 px
    huge = [Caption(start=0, end=3, text=f"{LONG} {LONG}", kind="text")]
    assert fit_font_px(edl(huge), RULES, {"caption": 72, "cta": 80}, width_of) is None


def test_split_screen_labels_have_their_own_lanes() -> None:
    caps = [Caption(start=0, end=3, text="Nhanh hơn", kind="text"),
            Caption(start=0, end=3, text="Tự làm", kind="split_top"),
            Caption(start=0, end=3, text="Dùng GANI", kind="split_bottom")]  # fmt: skip
    px = {"caption": 72, "cta": 80}
    assert "T6" not in codes(check_captions(edl(caps), RULES, px, width_of))
    two = [*caps, Caption(start=1, end=2, text="Lại", kind="split_top")]
    assert "T6" in codes(check_captions(edl(two), RULES, px, width_of))
