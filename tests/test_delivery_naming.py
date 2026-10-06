"""Tests (T-E, E3): domain/delivery_naming.py — slugs, the file name, versions."""

from __future__ import annotations

import re
from datetime import date

import pytest

from gani_dung_video.domain.delivery_naming import (
    NameParts,
    file_name,
    next_version_name,
    product_of,
    slug,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
DAY = date(2026, 10, 6)
PATTERN = re.compile(r"^GANI_[a-z0-9-]+_[a-z0-9-]+_[a-z0-9-]+_\d+s_9x16_v\d+_\d{8}\.mp4$")


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Giò Lụa Đặc Biệt", "gio-lua-dac-biet"),
        ("  Chả   bò!! ", "cha-bo"),
        ("tvc_cat_nhanh", "tvc-cat-nhanh"),
        ("đ Đ", "d-d"),
        ("???", ""),
    ],
)
def test_slug_is_lowercase_ascii_with_single_hyphens(text: str, want: str) -> None:
    assert slug(text) == want


def test_the_name_follows_the_template_from_the_owner() -> None:
    parts = NameParts("gio-lua", "tvc_cat_nhanh", "gioi_thieu_xuong", 15, DAY)
    want = "GANI_gio-lua_tvc-cat-nhanh_gioi-thieu-xuong_15s_9x16_v1_20261006.mp4"
    assert file_name(parts, 1, RULES) == want


def test_a_product_with_marks_is_written_without_them() -> None:
    parts = NameParts("Chả Lụa", "sang_trong", "ban_hang", 30, DAY)
    assert file_name(parts, 3, RULES).startswith("GANI_cha-lua_sang-trong_ban-hang_30s_9x16_v3_")


def test_versions_count_up_until_a_free_name() -> None:
    parts = NameParts("chung", "sang_trong", "ban_hang", 30, DAY)
    taken = {file_name(parts, 1, RULES), file_name(parts, 2, RULES)}
    name, version = next_version_name(parts, taken, RULES)
    assert version == 3 and name == file_name(parts, 3, RULES)
    assert next_version_name(parts, set(), RULES)[1] == 1


def test_a_new_day_starts_again_at_v1() -> None:
    today = NameParts("chung", "sang_trong", "ban_hang", 30, DAY)
    tomorrow = NameParts("chung", "sang_trong", "ban_hang", 30, date(2026, 10, 7))
    taken = {file_name(today, 1, RULES)}
    assert next_version_name(tomorrow, taken, RULES)[1] == 1


def test_the_product_defaults_and_warns_outside_the_approved_list() -> None:
    assert product_of(None, RULES) == ("chung", None)
    assert product_of("Giò Lụa", RULES) == ("gio-lua", None)
    slugged, warning = product_of("Xúc xích", RULES)
    assert slugged == "xuc-xich" and warning is not None and "gio-lua" in warning


def test_every_style_purpose_and_length_gives_a_name_that_obeys_the_convention() -> None:
    """100% of the names the rules can produce match the pattern: lower case, ASCII, hyphens."""
    count = 0
    for style in RULES["styles"]:
        for purpose in RULES["purposes"]:
            for dur in RULES["durations"]:
                name = file_name(NameParts("chung", style, purpose, int(dur), DAY), 1, RULES)
                assert PATTERN.match(name), name
                body = name.removeprefix("GANI_")
                assert name.isascii() and body == body.lower()
                count += 1
    assert count == 6 * 4 * 5
