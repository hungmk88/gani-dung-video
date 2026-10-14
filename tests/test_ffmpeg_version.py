"""Tests: infrastructure/ffmpeg_render.py version gate (audit C2).

The render graph is loaded with `-/filter_complex <file>`, which ffmpeg only understands from
7.0. On 6.x every render died with "Unrecognized option '/filter_complex'" after the paid call.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from gani_dung_video.infrastructure import ffmpeg_render
from gani_dung_video.infrastructure.ffmpeg_render import (
    MIN_FFMPEG_MAJOR,
    RenderError,
    ensure_ffmpeg_version,
    ffmpeg_problem,
    parse_ffmpeg_version,
)


@pytest.mark.parametrize(
    ("banner", "expected"),
    [
        ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers", (6, 1)),
        ("ffmpeg version 7.0 Copyright (c) 2000-2024", (7, 0)),
        ("ffmpeg version 9.0-full_build-www.gyan.dev Copyright (c) 2000-2026", (9, 0)),
        ("ffmpeg version n7.1.2 Copyright", (7, 1)),
        ("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright", (4, 4)),
        # git / dated builds carry no release number: unknown, never guessed
        ("ffmpeg version N-117000-gabc123def Copyright", None),
        ("ffmpeg version 2025-01-01-git-abc-full_build-www.gyan.dev Copyright", None),
        ("", None),
        ("not ffmpeg at all", None),
    ],
)
def test_the_release_number_is_read_from_the_banner(
    banner: str, expected: tuple[int, int] | None
) -> None:
    assert parse_ffmpeg_version(banner) == expected


def _banner(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(ffmpeg_render, "_ffmpeg_banner", lambda: text)


def test_an_old_ffmpeg_is_refused_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _banner(monkeypatch, "ffmpeg version 6.1.1-3ubuntu5 Copyright")
    problem = ffmpeg_problem()
    assert problem is not None
    assert "6.1" in problem and f"{MIN_FFMPEG_MAJOR}.0" in problem
    with pytest.raises(RenderError, match="quá cũ"):
        ensure_ffmpeg_version()


@pytest.mark.parametrize(
    "banner",
    [
        f"ffmpeg version {MIN_FFMPEG_MAJOR}.0 Copyright",  # exactly the minimum
        "ffmpeg version 9.0-full_build-www.gyan.dev Copyright",
        "ffmpeg version N-117000-gabc123def Copyright",  # unknown: not blocked on a guess
        "",  # not installed: the render reports that itself, with its own message
    ],
)
def test_a_supported_or_unknown_ffmpeg_is_not_a_problem(
    monkeypatch: pytest.MonkeyPatch, banner: str
) -> None:
    _banner(monkeypatch, banner)
    assert ffmpeg_problem() is None
    ensure_ffmpeg_version()  # does not raise


@pytest.mark.parametrize("entry", ["render", "measure_loudnorm", "render_voice_only"])
def test_every_entry_that_uses_the_graph_file_checks_the_version_first(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    # None for every argument: if the check did not come first, this would die on an
    # AttributeError while building inputs instead of raising the version error.
    _banner(monkeypatch, "ffmpeg version 6.1.1 Copyright")
    fn = cast(Any, getattr(ffmpeg_render, entry))
    n_args = {"render": 6, "measure_loudnorm": 5, "render_voice_only": 4}[entry]
    with pytest.raises(RenderError, match="quá cũ"):
        fn(*([None] * n_args))
