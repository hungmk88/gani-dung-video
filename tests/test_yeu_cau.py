"""Unit tests: domain/yeu_cau.py — reading the Appendix D option block."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.yeu_cau import YeuCauFormatError, parse_scalar, parse_yeu_cau

BLOCK = """---
phong_cach: tvc_cat_nhanh   # comment
do_dai_giay: 30
nhac_quyen_su_dung: true
nhac_file: "bai #2.mp3"     # hash inside quotes is kept
rules_version: 2.0
khuyen_mai:
  uu_dai: "Giảm 20%"
  thoi_han: ''
---
## Ghi chú cho AI
Dòng một.
Dòng hai.

## Mục khác
không lấy
"""


def test_parse_full_block() -> None:
    assert parse_yeu_cau(BLOCK) == {
        "phong_cach": "tvc_cat_nhanh",
        "do_dai_giay": 30,
        "nhac_quyen_su_dung": True,
        "nhac_file": "bai #2.mp3",
        "rules_version": 2.0,
        "khuyen_mai": {"uu_dai": "Giảm 20%", "thoi_han": ""},
        "ghi_chu": "Dòng một.\nDòng hai.",
    }


@pytest.mark.parametrize(
    ("raw", "value"),
    [("30", 30), ("-2", -2), ("2.5", 2.5), ("TRUE", True), ("false", False), ("", None),
     ("null", None), ("~", None), ('"30"', "30"), ("'x y'", "x y"), ("thu_vien", "thu_vien")],
)  # fmt: skip
def test_parse_scalar(raw: str, value: object) -> None:
    assert parse_scalar(raw) == value


def test_bom_crlf_and_leading_blank_lines() -> None:
    text = "﻿\r\n---\r\nphong_cach: sang_trong\r\n---\r\n"
    assert parse_yeu_cau(text) == {"phong_cach": "sang_trong"}


def test_empty_parent_becomes_none() -> None:
    assert parse_yeu_cau("---\nkhuyen_mai:\nnhac: thu_vien\n---\n")["khuyen_mai"] is None


def test_block_ghi_chu_wins_over_section() -> None:
    text = "---\nghi_chu: trong khối\n---\n## Ghi chú cho AI\nngoài khối\n"
    assert parse_yeu_cau(text)["ghi_chu"] == "trong khối"


def test_no_note_section_means_no_ghi_chu() -> None:
    assert "ghi_chu" not in parse_yeu_cau("---\nnhac: thu_vien\n---\nChữ tự do\n")


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("phong_cach: x\n", "bắt đầu bằng dòng '---'"),
        ("", "bắt đầu bằng dòng '---'"),
        ("---\nphong_cach: x\n", "đóng khối"),
        ("---\nphong_cach x\n---\n", "Dòng 2"),
        ("---\n  uu_dai: x\n---\n", "không nằm dưới trường nào"),
        ("---\nnhac: a\nnhac: b\n---\n", "hai lần"),
        ("---\nkhuyen_mai:\n\tuu_dai: x\n---\n", "Tab"),
        ("---\nten truong: x\n---\n", "Dòng 2"),
    ],
)
def test_malformed_blocks_name_the_problem(text: str, fragment: str) -> None:
    with pytest.raises(YeuCauFormatError, match=fragment):
        parse_yeu_cau(text)
