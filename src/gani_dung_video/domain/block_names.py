"""Vietnamese names of plan blocks and chapters for text a person reads (17/10/2026).

Ids such as 'san_pham' or 'quy_trinh#2' stay the keys of the plan, the drafts and the prompts
(Claude needs the ids); messages for people go through `block_name`. The names live in
rules.json → block_names, mirrored in frontend/src/data/rules.ts. Pure code.
"""

from __future__ import annotations

from gani_dung_video.domain.preflight import Rules

__all__ = ["block_name"]


def block_name(rules: Rules, role: str) -> str:
    """'san_pham' → 'Sản phẩm'; a sub-block 'quy_trinh#2' → 'Quy trình (đoạn 2)'; an id with no
    name reads as its words ('khoi_moi' → 'khoi moi')."""
    base, _, part = role.partition("#")
    names = rules.get("block_names", {})
    name = str(names.get(base, base.replace("_", " ")))
    return f"{name} (đoạn {part})" if part else name
