"""Unit tests: domain/finish.py — the two optional finishing filters (Dev Package 03, T-A and T-D).

T-A: afftdn denoise on the camera-sound layer. T-D: a warm food tone through eq + lutyuv
(colorbalance does the same job but made a 37 s 1080p render 35 % slower; lutyuv costs ~0).
Both are off in the shipped rules.json; a missing section also means off, so older rules files
keep working.
"""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.finish import FinishError, denoise_filter, grade_filter
from gani_dung_video.infrastructure.config_files import load_rules

SHIPPED = load_rules()["render"]


def render_with(**sections: dict[str, Any]) -> dict[str, Any]:
    return {**SHIPPED, **sections}


# ─────────────── T-A: denoise ───────────────


def test_denoise_is_off_in_the_shipped_rules_and_when_the_section_is_missing() -> None:
    assert SHIPPED["denoise"]["enabled"] is False
    assert denoise_filter(SHIPPED) is None
    assert denoise_filter({}) is None


def test_denoise_enabled_gives_afftdn_with_the_configured_numbers() -> None:
    r = render_with(denoise={**SHIPPED["denoise"], "enabled": True})
    assert denoise_filter(r) == "afftdn=nr=12:nf=-40"
    r = render_with(denoise={"enabled": True, "nr": 9.5, "nf": -50})
    assert denoise_filter(r) == "afftdn=nr=9.5:nf=-50"


@pytest.mark.parametrize(
    ("nr", "nf"), [(0, -40), (98, -40), (12, -10), (12, -90)]
)  # fmt: skip
def test_denoise_numbers_outside_the_filter_range_are_refused(nr: float, nf: float) -> None:
    r = render_with(denoise={"enabled": True, "nr": nr, "nf": nf})
    with pytest.raises(FinishError, match="denoise"):
        denoise_filter(r)


def test_denoise_numbers_are_not_checked_while_it_is_off() -> None:
    assert denoise_filter(render_with(denoise={"enabled": False, "nr": 0, "nf": 0})) is None


# ─────────────── T-D: warm food tone ───────────────


def test_grade_is_off_in_the_shipped_rules_and_when_the_section_is_missing() -> None:
    assert SHIPPED["grade"]["enabled"] is False
    assert grade_filter(SHIPPED) is None
    assert grade_filter({}) is None


def grade(**over: Any) -> dict[str, Any]:
    return render_with(grade={**SHIPPED["grade"], "enabled": True, **over})


def test_grade_scales_every_adjustment_by_the_strength() -> None:
    full = grade_filter(grade(strength=1.0, saturation=0.15, contrast=0.04, warmth=4))
    assert full == "eq=saturation=1.15:contrast=1.04,lutyuv=u='val-4':v='val+4'"
    part = grade_filter(grade(strength=0.4, saturation=0.15, contrast=0.04, warmth=4))
    assert part == "eq=saturation=1.06:contrast=1.016,lutyuv=u='val-1.6':v='val+1.6'"


def test_grade_with_zero_strength_is_no_filter_at_all() -> None:
    assert grade_filter(grade(strength=0)) is None


@pytest.mark.parametrize("strength", [-0.1, 1.01, 5])
def test_grade_strength_outside_zero_to_one_is_refused(strength: float) -> None:
    with pytest.raises(FinishError, match="grade"):
        grade_filter(grade(strength=strength))


def test_the_shipped_grade_defaults_are_gentle() -> None:
    g = SHIPPED["grade"]
    assert 0 < g["strength"] <= 0.5
    assert g["saturation"] <= 0.2 and g["contrast"] <= 0.1
    assert g["warmth"] <= 6  # chroma code levels (of 255): a pink roll must stay pink
