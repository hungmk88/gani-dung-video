"""Skip marks for tests that need ffmpeg, shared by the test modules.

`needs_render_ffmpeg` is for tests that run the real render graph: it needs ffmpeg >= 7
(`-/filter_complex`, see infrastructure/ffmpeg_render.py), so on an older ffmpeg (Ubuntu 24.04
ships 6.1) those tests are skipped with the reason instead of failing on the version check.
On CI (the CI variable is set) they are never skipped for the version: CI installs a pinned
ffmpeg 9, and a wrong ffmpeg there must turn the run red, not hide the render tests.
"""

from __future__ import annotations

import os
import shutil

import pytest

from gani_dung_video.infrastructure.ffmpeg_render import ffmpeg_problem

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _render_skip_reason() -> str | None:
    if not HAS_FFMPEG:
        return "cần ffmpeg"
    if os.environ.get("CI"):
        return None
    problem = ffmpeg_problem()
    return f"cần ffmpeg >= 7 để dựng thật: {problem}" if problem is not None else None


_REASON = _render_skip_reason()
needs_render_ffmpeg = pytest.mark.skipif(_REASON is not None, reason=_REASON or "")
