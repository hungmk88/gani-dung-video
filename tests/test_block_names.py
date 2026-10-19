"""Tests: domain/block_names.py — Vietnamese names of blocks and chapters (17/10/2026)."""

from __future__ import annotations

from gani_dung_video.domain.block_names import block_name
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()


def test_every_block_and_chapter_in_the_rules_has_a_vietnamese_name() -> None:
    ids = {role for p in RULES["purposes"].values() for role, _ in p["blocks"]}
    ids |= {c[0] for layout in RULES["durations"]["180"]["layouts"].values()
            for c in layout["chapters"]}  # fmt: skip
    missing = sorted(i for i in ids if i not in RULES["block_names"])
    assert missing == [], f"thêm tên vào rules.json → block_names: {missing}"


def test_names_read_like_the_interface() -> None:
    assert block_name(RULES, "san_pham") == "Sản phẩm"
    assert block_name(RULES, "hook") == "Mở đầu"
    assert block_name(RULES, "quy_trinh#2") == "Quy trình (đoạn 2)"
    assert block_name(RULES, "khoi_moi") == "khoi moi"  # no name yet: its words
    assert block_name({}, "san_pham") == "san pham"  # rules without the table
