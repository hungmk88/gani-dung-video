"""Optional finishing filters (Dev Package 03: T-A denoise, T-D warm food tone). Pure functions.

Both read their numbers from rules.json → render and return an ffmpeg filter string, or None
when switched off. A missing section counts as off, so older rules files keep working.

- denoise: afftdn on the camera-sound layer only (never on the music or the final mix).
- grade: eq (freshness) + lutyuv (warmth: blue chroma down, red chroma up) on the picture only,
  never on captions or the end card. `strength` (0–1) scales every adjustment, so 0 is "no
  change" and 1 is the full configured look. It is a tone, not a colour match between clips,
  and it is meant to stay gentle: a pink roll must stay pink. lutyuv rather than colorbalance:
  colorbalance converts every frame to RGB and made a 37 s 1080p render 35 % slower.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

NR_RANGE = (0.01, 97.0)  # afftdn noise reduction, dB
NF_RANGE = (-80.0, -20.0)  # afftdn noise floor, dB


class FinishError(ValueError):
    """A finishing setting in rules.json is outside what the filter accepts (Vietnamese text)."""


def _num(x: float) -> str:
    return f"{x:.6g}"


def denoise_filter(render: Mapping[str, Any]) -> str | None:
    """The afftdn filter for the camera-sound layer, or None when denoise is off.

    Raises:
        FinishError: nr or nf is outside afftdn's range (only checked when enabled).
    """
    cfg = render.get("denoise")
    if not cfg or not cfg.get("enabled"):
        return None
    nr, nf = float(cfg["nr"]), float(cfg["nf"])
    if not NR_RANGE[0] <= nr <= NR_RANGE[1]:
        raise FinishError(f"rules.json render.denoise.nr = {nr:g} ngoài khoảng "
                          f"{NR_RANGE[0]:g}–{NR_RANGE[1]:g} dB của afftdn.")  # fmt: skip
    if not NF_RANGE[0] <= nf <= NF_RANGE[1]:
        raise FinishError(f"rules.json render.denoise.nf = {nf:g} ngoài khoảng "
                          f"{NF_RANGE[0]:g} đến {NF_RANGE[1]:g} dB của afftdn.")  # fmt: skip
    return f"afftdn=nr={_num(nr)}:nf={_num(nf)}"


def grade_filter(render: Mapping[str, Any]) -> str | None:
    """The eq + colorbalance filter for the picture, or None when off (or strength is 0).

    Raises:
        FinishError: strength is outside 0–1 (only checked when enabled).
    """
    cfg = render.get("grade")
    if not cfg or not cfg.get("enabled"):
        return None
    k = float(cfg["strength"])
    if not 0.0 <= k <= 1.0:
        raise FinishError(f"rules.json render.grade.strength = {k:g} phải nằm trong 0–1.")
    if k == 0.0:
        return None
    sat, con = float(cfg["saturation"]) * k, float(cfg["contrast"]) * k
    warm = float(cfg["warmth"]) * k
    return (
        f"eq=saturation={_num(1 + sat)}:contrast={_num(1 + con)},"
        f"lutyuv=u='val-{_num(warm)}':v='val+{_num(warm)}'"
    )
