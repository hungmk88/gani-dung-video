"""Unit tests: domain/options.py — VideoOptions accepts san_pham and chien_dich."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.yeu_cau import parse_yeu_cau


def _base(**kw: object) -> dict[str, object]:
    return {
        "phong_cach": "ke_chuyen_nghe",
        "muc_dich": "gioi_thieu_xuong",
        "do_dai_giay": 30,
        "so_ban_nhap": 1,
        "nhac": "khong_nhac",
        **kw,
    }


def test_san_pham_and_chien_dich_are_optional() -> None:
    o = VideoOptions(**_base())
    assert o.san_pham is None and o.chien_dich is None


def test_san_pham_and_chien_dich_accept_strings() -> None:
    o = VideoOptions(**_base(san_pham="bo-vien", chien_dich="tet-2027"))
    assert o.san_pham == "bo-vien" and o.chien_dich == "tet-2027"


def test_san_pham_rejects_non_string() -> None:
    with pytest.raises(ValidationError):
        VideoOptions(**_base(san_pham=123))


def test_unknown_field_still_rejected() -> None:
    with pytest.raises(ValidationError):
        VideoOptions(**_base(san_pham_typo="x"))


def test_yeu_cau_md_with_san_pham_and_chien_dich_validates() -> None:
    text = (
        "---\n"
        "phong_cach: ke_chuyen_nghe\n"
        "muc_dich: gioi_thieu_xuong\n"
        "do_dai_giay: 30\n"
        "so_ban_nhap: 1\n"
        "nhac: khong_nhac\n"
        "san_pham: bo-vien\n"
        "chien_dich: tet-2027\n"
        "---\n"
    )
    v = VideoOptions(**parse_yeu_cau(text))
    assert v.san_pham == "bo-vien" and v.chien_dich == "tet-2027"
