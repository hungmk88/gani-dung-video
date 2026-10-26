"""Tests: domain/platforms.py — one draft rendered for TikTok and Facebook Reels (16/10/2026)."""

from __future__ import annotations

from datetime import date

import pytest

from gani_dung_video.domain.delivery_naming import NameParts, file_name
from gani_dung_video.domain.edl import Caption, Edl, MusicTrack, Shot
from gani_dung_video.domain.glossary import find_banned_claims
from gani_dung_video.domain.platforms import (
    PlatformError,
    apply_platform,
    platform_ids,
    profile,
    render_dir_name,
)
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.subtitles import ass_document
from gani_dung_video.infrastructure.config_files import load_glossary, load_rules

RULES = load_rules()
BRAND = RULES["render"]["brand"]


def edl(captions: list[Caption]) -> Edl:
    shot = Shot(source="a.mp4", src_in=0, src_out=6, t_start=0, t_end=6, block="hook", hdr=False,
                dissolve_out_sec=0, pad_sec=0)  # fmt: skip
    return Edl(style="ke_chuyen_nghe", width=1080, height=1920, fps=30, video_kbps=9000,
               shots=[shot], content_end=6.0, end_card_sec=2.0, duration=8.0,
               music=MusicTrack(track="t.m4a", audio_in_sec=0.5, loop_sec=None, fade_out_sec=1.5),
               ambient_db=-20, lufs=-14.0, true_peak_db=-1.5, font="Be Vietnam Pro ExtraBold",
               captions=captions)  # fmt: skip


CAPS = [
    Caption(start=0.0, end=3.0, text="Giò lụa làm mỗi sáng", kind="text"),
    Caption(start=3.0, end=4.5, text="Nhắn tin đặt hàng", kind="cta"),
    Caption(start=4.5, end=6.0, text="ngay hôm nay", kind="cta"),
]


def dialogues(doc: str, style: str) -> list[str]:
    return [ln for ln in doc.splitlines() if ln.startswith("Dialogue:") and f",{style}," in ln]


# ─────────────── rules.json ───────────────


def test_rules_offer_tiktok_and_reels_both_ticked_by_default() -> None:
    assert platform_ids(RULES) == ["tiktok", "facebook"]
    assert RULES["platforms"]["default"] == ["tiktok", "facebook"]
    tiktok, facebook = profile(RULES, "tiktok"), profile(RULES, "facebook")
    assert tiktok is not None and tiktok["zalo_on_end_card"] is False  # owner, 16/10/2026
    assert facebook is not None and facebook["zalo_on_end_card"] is True


def test_tiktok_calls_to_action_avoid_words_that_send_people_off_the_app() -> None:
    glossary = load_glossary()
    for purpose, text in RULES["platforms"]["profiles"]["tiktok"]["cta"].items():
        assert purpose in RULES["purposes"]
        low = text.lower()
        assert not any(w in low for w in ("nhắn tin", "inbox", "zalo", "sỉ", "link")), text
        assert find_banned_claims(text, glossary.banned_claims) == []


def test_an_unknown_platform_is_refused_with_the_known_ones() -> None:
    with pytest.raises(PlatformError, match="tiktok, facebook"):
        profile(RULES, "youtube")
    assert profile(RULES, "") is None


# ─────────────── render folder ───────────────


def test_each_platform_renders_into_its_own_folder() -> None:
    assert render_dir_name("A", "") == "A"  # no profile: the folder from before
    assert render_dir_name("A", "tiktok") == "A-tiktok"
    assert render_dir_name("B", "facebook") == "B-facebook"


# ─────────────── CTA and end card ───────────────


def test_no_platform_leaves_the_edl_untouched() -> None:
    e = edl(CAPS)
    assert apply_platform(e, RULES, "ban_hang", "") is e


def test_tiktok_replaces_the_whole_cta_and_drops_the_zalo_line() -> None:
    e = apply_platform(edl(CAPS), RULES, "ban_hang", "tiktok")
    cta = [c for c in e.captions if c.kind == "cta"]
    want = RULES["platforms"]["profiles"]["tiktok"]["cta"]["ban_hang"]
    assert [(c.start, c.end, c.text) for c in cta] == [(3.0, 6.0, want)]  # one line, same time
    assert e.captions[0].text == "Giò lụa làm mỗi sáng" and e.platform == "tiktok"
    doc = ass_document(e, RULES)
    end = "\n".join(dialogues(doc, "EndCardText"))
    assert BRAND["zalo"] not in end and BRAND["footer"] in end
    assert "Nhắn tin" not in doc


def test_facebook_keeps_the_ai_cta_and_the_zalo_line() -> None:
    e = apply_platform(edl(CAPS), RULES, "ban_hang", "facebook")
    assert [c.text for c in e.captions if c.kind == "cta"] == ["Nhắn tin đặt hàng", "ngay hôm nay"]
    assert BRAND["zalo"] in "\n".join(dialogues(ass_document(e, RULES), "EndCardText"))


def test_a_purpose_without_a_platform_cta_keeps_the_plan_text() -> None:
    caps = [Caption(start=3.0, end=6.0, text="Theo dõi GANI", kind="cta")]
    e = apply_platform(edl(caps), RULES, "ke_chuyen_thuong_hieu", "tiktok")
    assert [c.text for c in e.captions if c.kind == "cta"] == ["Theo dõi GANI"]
    assert e.end_card_zalo is False  # the end card still follows the platform


def test_a_platform_cta_with_a_placeholder_is_refused() -> None:
    rules: Rules = {**RULES, "platforms": {**RULES["platforms"], "profiles": {
        "tiktok": {"name": "TikTok", "zalo_on_end_card": False,
                   "cta": {"khuyen_mai": "Ưu đãi đến [ngày]"}}}}}  # fmt: skip
    with pytest.raises(PlatformError, match=r"\[ngày\]"):
        apply_platform(edl(CAPS), rules, "khuyen_mai", "tiktok")


# ─────────────── delivered file name ───────────────


def parts(platform: str) -> NameParts:
    return NameParts(product="gio-lua", style="tvc_cat_nhanh", purpose="ban_hang",
                     duration_sec=15, day=date(2026, 10, 16), platform=platform)  # fmt: skip


def test_the_delivered_name_carries_the_platform() -> None:
    assert file_name(parts("tiktok"), 1, RULES) == (
        "GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_tiktok_v1_20261016.mp4")  # fmt: skip
    assert file_name(parts("facebook"), 2, RULES) == (
        "GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_facebook_v2_20261016.mp4")  # fmt: skip


def test_without_a_platform_the_name_is_the_one_from_before() -> None:
    assert file_name(parts(""), 1, RULES) == (
        "GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_v1_20261016.mp4")  # fmt: skip


def test_a_video_without_music_says_so_in_its_name() -> None:
    # 16/10/2026: the music is added in the app; the file must not pass for the full mix.
    quiet = NameParts(product="gio-lua", style="tvc_cat_nhanh", purpose="ban_hang",
                      duration_sec=15, day=date(2026, 10, 16), platform="tiktok",
                      no_music=True)  # fmt: skip
    want = "GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_tiktok_khong-nhac_v1_20261016.mp4"
    assert file_name(quiet, 1, RULES) == want
    bare = NameParts(product="gio-lua", style="tvc_cat_nhanh", purpose="ban_hang",
                     duration_sec=15, day=date(2026, 10, 16), no_music=True)  # fmt: skip
    assert file_name(bare, 1, RULES) == (
        "GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_khong-nhac_v1_20261016.mp4")  # fmt: skip
