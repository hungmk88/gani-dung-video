"""Unit tests: domain/subtitles.py — ASS for on-screen text, SRT sidecar (5a, spec §7.5)."""

from __future__ import annotations

import re

from gani_dung_video.domain.edl import Caption, Edl, MusicTrack, Shot
from gani_dung_video.domain.subtitles import ass_document, ass_time, escape_ass, srt_document
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
R = RULES["render"]


def edl(font: str = "Be Vietnam Pro ExtraBold", captions: list[Caption] | None = None,
        width: int = 1080) -> Edl:  # fmt: skip
    shot = Shot(source="a.mp4", src_in=0, src_out=3, t_start=0, t_end=3, block="hook",
                hdr=False, dissolve_out_sec=0, pad_sec=0)  # fmt: skip
    return Edl(style="ke_chuyen_nghe", width=width, height=width * 16 // 9, fps=30,
               video_kbps=9000, shots=[shot], content_end=3.0, end_card_sec=2.0, duration=5.0,
               music=MusicTrack(track="t.m4a", audio_in_sec=0.5, loop_sec=None, fade_out_sec=1.5),
               ambient_db=-20, lufs=-14.0, true_peak_db=-1.5, font=font,
               captions=captions if captions is not None else
               [Caption(start=0.0, end=3.0, text="Một ngày ở xưởng", kind="text")])  # fmt: skip


def section(doc: str, name: str) -> str:
    return doc.split(f"[{name}]")[1].split("\n[")[0]


def style_line(doc: str, name: str) -> dict[str, str]:
    styles = section(doc, "V4+ Styles")
    fmt = [x.strip() for x in styles.split("Format:")[1].splitlines()[0].split(",")]
    for ln in styles.splitlines():
        if ln.startswith(f"Style: {name},"):
            return dict(zip(fmt, [x.strip() for x in ln[7:].split(",")], strict=True))
    raise AssertionError(name)


# ─────────────── times and escaping ───────────────


def test_ass_time_is_h_mm_ss_cc() -> None:
    assert ass_time(0) == "0:00:00.00" and ass_time(3.0) == "0:00:03.00"
    assert ass_time(61.233) == "0:01:01.23" and ass_time(3725.5) == "1:02:05.50"


def test_ai_text_cannot_inject_ass_override_tags() -> None:
    text = r"Giảm {\b1\pos(0,0)}50% \N ngay"
    out = escape_ass(text)
    assert "{" not in out and "}" not in out and r"\N" not in out and r"\p" not in out
    assert "Giảm" in out and "50%" in out


# ─────────────── layout (safe zones, fonts, size) ───────────────


def test_play_resolution_is_always_the_final_frame_so_preview_scales() -> None:
    for w in (1080, 540):
        info = section(ass_document(edl(width=w), RULES), "Script Info")
        assert "PlayResX: 1080" in info and "PlayResY: 1920" in info


def test_caption_style_respects_safe_zones_and_minimum_size() -> None:
    s = style_line(ass_document(edl(), RULES), "Caption")
    assert s["Fontname"] == "Be Vietnam Pro ExtraBold" and int(s["Fontsize"]) >= 64
    assert int(s["MarginR"]) >= R["safe"]["right"] and int(s["MarginV"]) >= R["safe"]["bottom"]
    assert s["Alignment"] == "2"  # bottom centre, above the 420 px app UI


def test_sang_trong_uses_the_light_font() -> None:
    assert style_line(ass_document(edl("Be Vietnam Pro Light"), RULES), "Caption")["Fontname"] == (
        "Be Vietnam Pro Light")  # fmt: skip


def test_logo_is_top_left_at_80_percent_and_marked_placeholder() -> None:
    doc = ass_document(edl(), RULES)
    s = style_line(doc, "Logo")
    assert s["Alignment"] == "7" and int(s["MarginV"]) >= R["safe"]["top"]
    assert s["PrimaryColour"].startswith("&H33")  # alpha 0x33 = 20 % transparent = 80 % opaque
    assert "placeholder" in section(doc, "Script Info").lower()
    logo = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:") and ",Logo," in ln]
    assert len(logo) == 1 and ass_time(0) in logo[0] and ass_time(3.0) in logo[0]


# ─────────────── events ───────────────


def test_captions_and_end_card_become_dialogue_lines() -> None:
    doc = ass_document(edl(), RULES)
    events = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]
    cap = [e for e in events if ",Caption," in e]
    assert len(cap) == 1 and "0:00:00.00,0:00:03.00" in cap[0] and "Một ngày ở xưởng" in cap[0]
    card = [e for e in events if ",EndCard" in e]
    joined = "\n".join(card)
    assert "0:00:03.00,0:00:05.00" in card[0]
    assert R["brand"]["zalo"] in joined and R["brand"]["footer"] in joined
    assert R["brand"]["logo_text"] in joined


def test_cta_caption_uses_the_cta_style() -> None:
    caps = [Caption(start=0, end=3, text="Nhắn tin nhận báo giá sỉ", kind="cta")]
    doc = ass_document(edl(captions=caps), RULES)
    assert any(",CTA," in ln for ln in doc.splitlines() if ln.startswith("Dialogue:"))


def test_srt_holds_the_captions_with_comma_milliseconds() -> None:
    caps = [Caption(start=0, end=3, text="Một ngày", kind="text"),
            Caption(start=3.033, end=5.5, text="Nhắn tin", kind="cta")]  # fmt: skip
    srt = srt_document(edl(captions=caps))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:03,000\nMột ngày\n\n2\n")
    assert "00:00:03,033 --> 00:00:05,500\nNhắn tin" in srt
    assert re.search(r"\n\n$", srt)
