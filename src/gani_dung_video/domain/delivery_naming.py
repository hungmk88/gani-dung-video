"""E3 — file names and folder tree of a delivered video (Dev Package 03, T-E). Pure code.

GANI_{san-pham}_{phong-cach}_{muc-dich}_{do-dai}s_{ty-le}_{nen-tang}_{nhac}_v{n}_{ngay}.mp4, every
field lower case, hyphen-separated and without Vietnamese marks. The template and the allowed
products live in rules.json → delivery.naming. A video rendered without a platform profile has no
{nen-tang} part (and no separator around it), and {nhac} is there only for a video exported
without music (no_music_tag, 16/10/2026): a plain render keeps the name from before 14/10/2026.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from gani_dung_video.domain.preflight import Rules

_FIELDS = ("san-pham", "phong-cach", "muc-dich", "do-dai", "ty-le", "nen-tang", "nhac", "n", "ngay")
_OPTIONAL = ("nen-tang", "nhac")  # an empty one is dropped with one of its separators


def slug(text: str) -> str:
    """Lower case ASCII words joined by single hyphens: 'Giò Lụa Đặc Biệt' → 'gio-lua-dac-biet'.

    Returns an empty string when nothing readable is left (the caller decides what to do).
    """
    folded = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    ascii_text = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def product_of(product: str | None, rules: Rules) -> tuple[str, str | None]:
    """The product slug and a warning when it is outside the approved list (or None).

    Nothing given (or nothing readable) → rules.delivery.naming.default_product.
    """
    cfg = rules["delivery"]["naming"]
    s = slug(product or "") or str(cfg["default_product"])
    if s in cfg["products"]:
        return s, None
    return s, (f"Sản phẩm '{s}' chưa nằm trong danh mục đã chốt (rules.json → "
               f"delivery.naming.products): {', '.join(cfg['products'])}.")  # fmt: skip


@dataclass(frozen=True)
class NameParts:
    """What a delivered name says about the video. `product`, `style` and `purpose` are slugged
    when the name is built, so rule ids such as 'tvc_cat_nhanh' become 'tvc-cat-nhanh'."""

    product: str
    style: str
    purpose: str
    duration_sec: int
    day: date
    platform: str = ""  # tiktok | facebook; "" = no platform part in the name
    no_music: bool = False  # exported without music: the {nhac} part says so


def file_name(parts: NameParts, version: int, rules: Rules) -> str:
    """The delivered file name for one version."""
    cfg = rules["delivery"]["naming"]
    values = {
        "san-pham": slug(parts.product),
        "phong-cach": slug(parts.style),
        "muc-dich": slug(parts.purpose),
        "do-dai": str(parts.duration_sec),
        "ty-le": str(cfg["ratio"]),
        "nen-tang": slug(parts.platform),
        "nhac": str(cfg["no_music_tag"]) if parts.no_music else "",
        "n": str(version),
        "ngay": parts.day.strftime(str(cfg["date_format"])),
    }
    name = str(cfg["template"])
    for key in _OPTIONAL:
        if not values[key]:
            name = name.replace("_{" + key + "}", "").replace("{" + key + "}_", "")
    for key in _FIELDS:
        name = name.replace("{" + key + "}", values[key])
    return name


def next_version_name(parts: NameParts, taken: Collection[str], rules: Rules) -> tuple[str, int]:
    """The first name (v1, v2, …) that is not in `taken`, and its version number."""
    version = 1
    while (name := file_name(parts, version, rules)) in taken:
        version += 1
    return name, version
