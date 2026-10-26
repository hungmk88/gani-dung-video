"""Tests (T-E, E1): domain/delivery_checks.py — duration, safe zone, glyphs, claims, report."""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.delivery_checks import (
    E1Item,
    build_e1,
    check_duration,
    check_duration_gap,
    check_glyphs,
    check_onscreen_claims,
    check_platform_words,
    check_safe_zone,
    duration_deviations,
    screen_texts,
)
from gani_dung_video.domain.edl import Caption, Edl, Shot
from gani_dung_video.domain.qc import Finding
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.subtitles import ass_document
from gani_dung_video.infrastructure.config_files import load_glossary, load_rules
from gani_dung_video.infrastructure.media_probe import FontMetrics

RULES = load_rules()
FONT = "Be Vietnam Pro ExtraBold"
PX = {"caption": 72, "cta": 80, "chapter_title": 88}


def edl(captions: list[Caption] | None = None, **update: Any) -> Edl:
    shot = Shot(source="a.mp4", src_in=0, src_out=3, t_start=0, t_end=3, block="b", hdr=False,
                dissolve_out_sec=0, pad_sec=0)  # fmt: skip
    plain = [Caption(start=0, end=3, text="Một ngày", kind="text")]
    base = Edl(style="ke_chuyen_nghe", width=1080, height=1920, fps=30, video_kbps=9000,
               shots=[shot], content_end=3.0, end_card_sec=2.0, duration=5.0, music=None,
               ambient_db=None, lufs=-14.0, true_peak_db=-2.0, font=FONT,
               captions=captions or plain)  # fmt: skip
    return base.model_copy(update=update)


def width_of(text: str, font: str, px: int) -> float:
    return len(text) * px * 0.55


def with_qc(**over: Any) -> dict[str, Any]:
    return {**RULES, "qc": {**RULES["qc"], **over}}


# ─────────────── E1-DUR ───────────────


def test_the_shipped_rules_measure_against_the_chosen_length() -> None:
    assert RULES["qc"]["duration_reference"] == "chosen"  # since 13/10/2026 (hướng B)
    item, stop = check_duration(15.0 + 2.0, edl(), 15, 15.0, RULES)
    assert item.passed and stop == [] and item.value == pytest.approx(0.0)


def test_in_plan_mode_a_render_on_its_plan_passes_even_off_the_chosen_length() -> None:
    # What the old TVC plan did (12 beats × 1.3 s = 15.6 s); "plan" stays for whoever wants it.
    by_plan = with_qc(duration_reference="plan")
    item, stop = check_duration(17.6, edl(), 15, 15.6, by_plan)
    assert item.passed and stop == [] and item.value == pytest.approx(0.0)
    assert "kế hoạch 15.60s" in item.detail and "đã chọn 15s" in item.detail
    assert "so với đã chọn +0.60s, so với kế hoạch +0.00s" in item.detail  # both gaps shown


def test_measured_against_the_chosen_length_the_same_video_is_rejected() -> None:
    literal = with_qc(duration_reference="chosen")
    item, stop = check_duration(17.6, edl(), 15, 15.6, literal)
    assert not item.passed and item.value == pytest.approx(0.6) and item.limit == 0.5
    assert [(f.code, f.level) for f in stop] == [("E1-DUR", "error")]


def test_a_render_far_from_its_plan_is_blocked_and_the_message_has_the_numbers() -> None:
    item, stop = check_duration(23.0, edl(), 15, 15.6, with_qc(duration_reference="plan"))
    assert not item.passed and item.value == pytest.approx(5.4)
    assert "21.00s" in stop[0].msg and "kế hoạch 15.60s" in stop[0].msg and stop[0].level == "error"


def test_the_gap_at_exactly_the_limit_passes() -> None:
    item, _ = check_duration(15.5 + 2.0, edl(), 15, 15.0, RULES)
    assert item.passed and item.value == pytest.approx(0.5)


def test_three_minutes_have_a_wider_tolerance() -> None:
    ok, _ = check_duration(182.0 + 0.9, edl(), 180, 180.0, RULES)  # 0.9 ≤ 1
    bad, _ = check_duration(182.0 + 1.1, edl(), 180, 180.0, RULES)
    assert ok.passed and not bad.passed


# Measured 25–30/09 on "30 s" speech videos: content 34.93 s, 31.4 s and 17.27 s.
VOICE = edl(mode="speech")


@pytest.mark.parametrize(("content", "level"), [(34.93, "warn"), (31.4, "warn"), (30.2, None)])
def test_speech_inside_the_voice_limit_passes_and_only_warns_beyond_the_normal_tolerance(
    content: float, level: str | None
) -> None:
    item, found = check_duration(content + 2.0, VOICE, 30, 30.0, RULES)
    assert item.passed and item.limit == pytest.approx(7.5)  # max(25 % of 30, 7)
    assert [f.level for f in found] == ([level] if level else [])


def test_speech_outside_the_voice_limit_is_blocked_with_the_numbers() -> None:
    item, found = check_duration(17.27 + 2.0, VOICE, 30, 30.0, RULES)  # the 17.3 s video
    assert not item.passed and item.value == pytest.approx(-12.73)
    assert [(f.code, f.level) for f in found] == [("E1-DUR", "error")]
    assert "-12.73s" in found[0].msg and "±7.5s" in found[0].msg


@pytest.mark.parametrize(
    ("chosen", "limit"), [(15, 7.0), (30, 7.5), (45, 11.25), (60, 15.0), (180, 36.0)]
)
def test_the_voice_limit_is_the_larger_of_the_percentage_and_the_seconds(
    chosen: int, limit: float
) -> None:
    item, _ = check_duration(chosen + 2.0, VOICE, chosen, float(chosen), RULES)
    assert item.limit == pytest.approx(limit)
    over, _ = check_duration(chosen + limit + 0.1 + 2.0, VOICE, chosen, float(chosen), RULES)
    assert not over.passed


def test_the_voice_limit_does_not_loosen_beat_driven_videos() -> None:
    item, found = check_duration(17.0 + 2.0, edl(mode="grid"), 15, 16.0, RULES)  # 3 s over plan
    assert not item.passed and item.limit == 0.5 and found[0].level == "error"


# ─────────────── E1-DUR-GAP ───────────────


@pytest.mark.parametrize(
    ("chosen", "planned"), [(15, 15.0), (15, 16.0), (15, 15.6), (15, 16.5), (180, 198.0)]
)
def test_a_plan_within_ten_percent_of_the_chosen_length_passes(chosen: int, planned: float) -> None:
    item, stop = check_duration_gap(chosen, planned, RULES)
    assert item.passed and stop == [] and item.limit == pytest.approx(chosen * 0.1)


@pytest.mark.parametrize(
    ("chosen", "planned"), [(15, 16.6), (15, 13.4), (180, 198.1), (180, 161.0)]
)
def test_a_plan_that_drifted_beyond_ten_percent_is_blocked(chosen: int, planned: float) -> None:
    item, stop = check_duration_gap(chosen, planned, RULES)
    assert not item.passed and item.code == "E1-DUR-GAP"
    assert [(f.code, f.level) for f in stop] == [("E1-DUR-GAP", "error")]
    assert f"đã chọn {chosen}s" in stop[0].msg and "±10%" in stop[0].msg


def test_the_two_deviations_are_signed_and_independent_of_the_reference() -> None:
    assert duration_deviations(17.6, edl(), 15, 15.6) == (0.6, 0.0)
    assert duration_deviations(15.0, edl(), 15, 16.0) == (-2.0, -3.0)


# ─────────────── E1-SAFE ───────────────


def test_ordinary_captions_sit_inside_the_safe_zone_with_numbers() -> None:
    caps = [Caption(start=0, end=2, text="Một ngày", kind="text"),
            Caption(start=0, end=2, text="Quy trình", kind="title"),
            Caption(start=2, end=3, text="Nhắn tin đặt hàng", kind="cta")]  # fmt: skip
    item, stop = check_safe_zone(edl(caps), RULES, PX, width_of)
    assert item.passed and stop == [] and item.limit == 0.0 and item.unit == "px"
    assert item.value is not None and item.value > 0
    assert "trên" in item.detail and "(250px)" in item.detail and "(400px)" in item.detail


def test_a_word_wider_than_the_zone_is_blocked_with_the_distance() -> None:
    long_word = "X" * 40  # 40 × 72 × 0.55 = 1584 px: wider than the 1080 px frame
    caps = [Caption(start=1.5, end=3, text=long_word, kind="text")]
    item, stop = check_safe_zone(edl(caps), RULES, PX, width_of)
    assert not item.passed and item.value is not None and item.value < 0
    assert {f.code for f in stop} == {"E1-SAFE"} and stop[0].at_sec == 1.5
    assert "trái" in stop[0].msg and "≥ 60px" in stop[0].msg


def test_a_wider_safe_zone_in_the_rules_catches_a_title_that_sat_inside_before() -> None:
    caps = [Caption(start=0, end=2, text="Quy trình", kind="title")]
    ok, _ = check_safe_zone(edl(caps), RULES, PX, width_of)
    wide = with_qc(safe_zone={"top": 500, "bottom": 400, "left": 60, "right": 60})
    bad, stop = check_safe_zone(edl(caps), wide, PX, width_of)
    assert ok.passed and not bad.passed and "trên" in stop[0].msg


def test_no_text_means_nothing_to_check() -> None:
    item, stop = check_safe_zone(edl().model_copy(update={"captions": []}), RULES, PX, width_of)
    assert item.passed and stop == [] and item.value is None


def test_karaoke_lines_are_measured_too() -> None:
    words = [[SpokenWord(text="Giò", start=0.0, end=0.5, prob=0.9),
              SpokenWord(text="lụa", start=0.5, end=1.0, prob=0.9)]]  # fmt: skip
    texts = screen_texts(edl(mode="speech", karaoke=words))
    assert [t.kind for t in texts] == ["text", "karaoke"] and texts[1].text == "Giò lụa"


@pytest.mark.parametrize("mode", ["music", "speech"])
def test_the_layout_matches_the_real_ass_styles(mode: str) -> None:
    """E1-SAFE re-derives the margins subtitles.py writes; this keeps them from drifting."""
    from gani_dung_video.domain.delivery_checks import _layout  # private on purpose: the guard

    caps = [Caption(start=0, end=1, text="a", kind="text"),
            Caption(start=1, end=2, text="b", kind="title"),
            Caption(start=2, end=3, text="c", kind="cta"),
            Caption(start=3, end=4, text="d", kind="split_top"),
            Caption(start=3, end=4, text="e", kind="split_bottom")]  # fmt: skip
    e = edl(caps, mode=mode)
    styles = {}
    for line in ass_document(e, RULES).splitlines():
        if line.startswith("Style: "):
            f = line.removeprefix("Style: ").split(",")
            styles[f[0]] = (int(f[18]), int(f[21]))  # Alignment, MarginV
    for kind, style in (("text", "Caption"), ("title", "ChapterTitle"), ("cta", "CTA"),
                        ("karaoke", "Karaoke"), ("split_top", "SplitTop"),
                        ("split_bottom", "SplitBottom")):  # fmt: skip
        _, margin_v, from_top = _layout(kind, e, RULES)
        align, mv = styles[style]
        assert margin_v == mv and from_top == (align == 8), kind


# ─────────────── E1-GLYPH ───────────────


def test_vietnamese_marks_all_have_a_glyph_in_the_bundled_font() -> None:
    caps = [Caption(start=0, end=2, text="Giò lụa Đặc biệt – ngon ơi", kind="text")]
    brand = [("GANI", FONT), ("GANI FOODS – Đạt chuẩn HACCP Codex 2022", FONT)]
    item, stop = check_glyphs(edl(caps), brand, FontMetrics().has_glyph)
    assert item.passed and stop == [] and item.value == 0.0
    assert "chữ có dấu tiếng Việt" in item.detail and "đủ glyph" in item.detail


def test_a_character_the_font_lacks_is_named_and_blocks() -> None:
    caps = [Caption(start=0, end=2, text="Giò lụa 你好", kind="text")]
    item, stop = check_glyphs(edl(caps), [], FontMetrics().has_glyph)
    assert not item.passed and item.value == 2.0
    assert stop[0].code == "E1-GLYPH" and "U+4F60" in stop[0].msg


def test_spaces_and_control_characters_are_not_glyphs_to_check() -> None:
    caps = [Caption(start=0, end=2, text="a b​", kind="text")]
    item, _ = check_glyphs(edl(caps), [], lambda ch, font: ch in "ab")
    assert item.passed


# ─────────────── C1 on screen ───────────────


def test_a_banned_claim_on_screen_is_a_blocking_c1_finding() -> None:
    caps = [Caption(start=0, end=2, text="Không hàn the", kind="text")]
    item, stop = check_onscreen_claims(edl(caps), ["hàn the"])
    assert not item.passed and item.value == 1.0 and item.code == "C1"
    assert stop[0].code == "C1" and "hàn the" in stop[0].msg and "chữ 1" in stop[0].msg


def test_clean_text_passes_and_the_count_of_texts_is_reported() -> None:
    item, stop = check_onscreen_claims(edl(), ["hàn the"])
    assert item.passed and stop == [] and "Đã soát 1 đoạn chữ" in item.detail


# ─────────────── report ───────────────


def _checks(**failed: bool) -> dict[str, E1Item]:
    codes = ("E1-DUR", "E1-DUR-GAP", "E1-SAFE", "E1-GLYPH", "C1")
    return {c: E1Item(code=c, name=c, passed=not failed.get(c.replace("-", "_"), False),
                      detail="x") for c in codes}  # fmt: skip


def test_the_report_lists_every_check_in_the_owners_order_with_numbers() -> None:
    rep = build_e1(edl(), RULES, [], -14.1, -1.98, _checks())
    assert [i.code for i in rep.items] == ["E1-DUR", "E1-DUR-GAP", "E1-FORMAT", "E1-LOUD",
                                           "E1-PICTURE", "E1-SAFE", "E1-GLYPH", "C1"]  # fmt: skip
    loud = next(i for i in rep.items if i.code == "E1-LOUD")
    assert rep.passed and loud.value == -14.1 and loud.limit == -14.0
    assert "-14.1 LUFS" in loud.detail and "đỉnh thật -2.0 dBTP" in loud.detail


def test_one_failed_item_fails_the_report() -> None:
    assert not build_e1(edl(), RULES, [], -14.0, -2.0, _checks(C1=True)).passed


def test_a_container_error_from_t1_fails_the_format_item() -> None:
    t1 = Finding(code="T1", level="error", msg="File xuất sai chuẩn: khung 540×960, cần 1080×1920")
    rep = build_e1(edl(), RULES, [t1], None, None, _checks())
    fmt = next(i for i in rep.items if i.code == "E1-FORMAT")
    assert not fmt.passed and "540×960" in fmt.detail and not rep.passed


def test_black_frames_fail_the_picture_item_but_a_freeze_only_warns() -> None:
    black = Finding(code="T4", level="error", msg="Khung đen 1–2s ở cảnh 1.", at_sec=1.0)
    freeze = Finding(code="T5", level="warn", msg="Hình đứng yên 1–3s ở cảnh 1.", at_sec=1.0)
    only_freeze = build_e1(edl(), RULES, [freeze], -14.0, -2.0, _checks())
    both = build_e1(edl(), RULES, [black, freeze], -14.0, -2.0, _checks())
    pic = [next(i for i in r.items if i.code == "E1-PICTURE") for r in (only_freeze, both)]
    assert pic[0].passed and not pic[1].passed and pic[1].value == 1.0


# ─────────────── 16/10/2026: platform risk words (warnings only) ───────────────


def test_platform_words_warn_on_screen_and_spoken_text_and_never_block() -> None:
    caps = [Caption(start=0.0, end=2.0, text="Màu đỏ tươi tự nhiên", kind="text"),
            Caption(start=2.0, end=4.0, text="Nhắn tin Zalo ngay", kind="cta")]  # fmt: skip
    spoken = [[SpokenWord(text="giá", start=4.0, end=4.3, prob=0.9),
               SpokenWord(text="sỉ", start=4.3, end=4.6, prob=0.9)]]  # fmt: skip
    e = edl(caps).model_copy(update={"platform": "tiktok", "karaoke": spoken})
    words = load_glossary().platform_risk_words["tiktok"]
    item, found = check_platform_words(e, words)
    assert item.passed and item.value == 3.0  # nhắn tin, zalo (screen), giá sỉ (voice)
    assert {f.level for f in found} == {"warn"}
    assert any("lời nói" in f.msg and "giá sỉ" in f.msg for f in found)
    assert not any("máu" in f.msg for f in found)  # "màu" (colour) is not "máu" (blood)


def test_facebook_allows_nhan_tin_and_a_video_without_platform_is_not_checked() -> None:
    caps = [Caption(start=0.0, end=2.0, text="Nhắn tin Zalo đặt hàng", kind="cta")]
    words = load_glossary().platform_risk_words["facebook"]
    item, found = check_platform_words(edl(caps).model_copy(update={"platform": "facebook"}), words)
    assert item.value == 0.0 and found == []
    item, found = check_platform_words(edl(caps), load_glossary().platform_risk_words["tiktok"])
    assert found == [] and "không soát" in item.detail


def test_every_platform_with_words_is_a_platform_of_rules_json() -> None:
    assert set(load_glossary().platform_risk_words) <= set(RULES["platforms"]["profiles"])
