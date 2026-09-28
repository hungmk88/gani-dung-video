"""Read the option block at the top of yeu-cau.md (spec v2.1 Appendix D).

Format (a small YAML subset, parsed with the stdlib on purpose — no PyYAML dependency):

    ---
    phong_cach: ke_chuyen_nghe      # comment
    do_dai_giay: 30
    nhac_quyen_su_dung: true
    khuyen_mai:                     # one level of nesting
      uu_dai: "Giảm 20%"
    ---
    ## Ghi chú cho AI
    Free text for the AI...

Why a strict subset: this file is hand-written by non-developers; anything ambiguous
(tabs, deeper nesting, missing ':') is rejected with a line number instead of guessed.
"""

from __future__ import annotations

import re
from typing import Any

NOTE_HEADING = "## ghi chú cho ai"
_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+\.\d+")


class YeuCauFormatError(ValueError):
    """yeu-cau.md does not follow Appendix D; message is Vietnamese and names the line."""


def _strip_comment(value: str) -> str:
    """Drop a trailing `# comment` from a value, but keep '#' inside a quoted string."""
    v = value.strip()
    if v.startswith("#"):
        return ""
    if v[:1] in {'"', "'"}:
        end = v.find(v[0], 1)
        return v if end == -1 else v[: end + 1]
    return v.split(" #", 1)[0].split("\t#", 1)[0].strip()


def parse_scalar(v: str) -> Any:  # Any: YAML-like scalar (str/int/float/bool/None)
    """Convert one scalar the way Appendix D uses it.

    Example:
        >>> [parse_scalar(x) for x in ["30", "2.0", "true", '"Giảm 20%"', "", "thu_vien"]]
        [30, 2.0, True, 'Giảm 20%', None, 'thu_vien']
    """
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
        return v[1:-1]
    low = v.lower()
    if low in {"true", "false"}:
        return low == "true"
    if low in {"", "null", "~"}:
        return None
    if _INT.fullmatch(v):
        return int(v)
    if _FLOAT.fullmatch(v):
        return float(v)
    return v


def _split_block(lines: list[str]) -> tuple[list[tuple[int, str]], list[str]]:
    """Return (numbered lines inside the --- block, lines after it)."""
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or lines[first].strip() != "---":
        raise YeuCauFormatError(
            "yeu-cau.md phải bắt đầu bằng dòng '---' (khối tùy chọn, Phụ lục D)."
        )
    for j in range(first + 1, len(lines)):
        if lines[j].strip() == "---":
            return [(k + 1, lines[k]) for k in range(first + 1, j)], lines[j + 1 :]
    raise YeuCauFormatError("Thiếu dòng '---' đóng khối tùy chọn.")


def _parse_block(numbered: list[tuple[int, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    parent: str | None = None
    for no, raw in numbered:
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YeuCauFormatError(f"Dòng {no}: dùng dấu cách để thụt lề, không dùng Tab.")
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, rest = line.partition(":")
        key = key.strip()
        if not sep or not key or " " in key:
            raise YeuCauFormatError(
                f"Dòng {no}: cần dạng 'ten_truong: gia_tri' — nhận được '{line}'."
            )
        value = _strip_comment(rest)  # only the value: a quoted value may contain ' #'
        if raw[:1] == " ":
            if parent is None:
                raise YeuCauFormatError(f"Dòng {no}: dòng thụt lề nhưng không nằm dưới trường nào.")
            out[parent][key] = parse_scalar(value)
            continue
        if key in out:
            raise YeuCauFormatError(f"Dòng {no}: trường '{key}' bị ghi hai lần.")
        parent = key if value == "" else None
        out[key] = {} if value == "" else parse_scalar(value)
    # "key:" with nothing nested under it means "left blank", not an empty object.
    return {k: (None if v == {} else v) for k, v in out.items()}


def _note(after: list[str]) -> str | None:
    """Text under '## Ghi chú cho AI' until the next '## ' heading."""
    start = next((i for i, ln in enumerate(after) if ln.strip().lower() == NOTE_HEADING), None)
    if start is None:
        return None
    body: list[str] = []
    for ln in after[start + 1 :]:
        if ln.startswith("## "):
            break
        body.append(ln)
    text = "\n".join(body).strip()
    return text or None


def parse_yeu_cau(text: str) -> dict[str, Any]:  # Any: raw values, validated by VideoOptions
    """Parse yeu-cau.md into a raw option dict (validated later by preflight).

    Args:
        text: Full content of yeu-cau.md.

    Returns:
        The option block as a dict; the '## Ghi chú cho AI' section becomes ``ghi_chu``
        unless the block already sets ``ghi_chu``.

    Raises:
        YeuCauFormatError: the block is missing or malformed (message says which line).
    """
    numbered, after = _split_block(text.lstrip("﻿").splitlines())
    opts = _parse_block(numbered)
    if opts.get("ghi_chu") is None:
        note = _note(after)
        if note is not None:
            opts["ghi_chu"] = note
    return opts
