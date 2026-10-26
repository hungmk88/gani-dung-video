"""Unit tests: domain/slowmo.py — which shots play slowly, and what part of the clip they read."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.slowmo import (
    reserve_for_slowmo,
    slowed_window,
    slowmo_candidates,
    slowmo_picks,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
REVIEW = RULES["styles"]["review_mon_an"]


def test_only_clips_filmed_fast_enough_are_candidates() -> None:
    fps = [30.0, 59.94, 60.0, 120.0, 60.0, 25.0]
    usable = [True, True, True, True, False, True]
    assert slowmo_candidates(fps, usable, REVIEW) == [1, 2, 3]  # 59.94 counts, unusable does not
    assert slowmo_candidates(fps, usable, RULES["styles"]["tvc_cat_nhanh"]) == []  # no opt-in


def test_picks_spread_within_what_max_effects_leaves() -> None:
    cands = [1, 3, 4, 6, 8]
    assert slowmo_picks(cands, REVIEW, used=0) == [1, 4, 8]  # max_effects 3, spread
    assert slowmo_picks(cands, REVIEW, used=2) == [1]
    assert slowmo_picks(cands, REVIEW, used=3) == []


def test_half_of_max_effects_is_kept_for_slow_motion_when_there_is_some() -> None:
    assert reserve_for_slowmo(REVIEW, candidates=5) == 1  # 3 // 2
    assert reserve_for_slowmo(REVIEW, candidates=0) == 0
    assert reserve_for_slowmo({"max_effects": None}, candidates=5) == 0


def test_slowed_window_is_half_as_long_around_the_same_peak() -> None:
    a, b = slowed_window(10.0, 12.0, 0.5, 0.35, 30)
    assert b - a == pytest.approx(1.0)
    assert a == pytest.approx(10.0 + 0.35 * 1.0, abs=1 / 30)  # the peak keeps its place
    assert a * 30 == pytest.approx(round(a * 30))  # starts on a frame
