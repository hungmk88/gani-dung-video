"""How long a render job may take, from the length of the video it renders (phase 7d)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gani_dung_video.domain.jobs import render_limits
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR


def video_seconds(folder: Path) -> float | None:
    """Length in seconds of the video the project's plan.json describes, or None when the plan
    is missing or unreadable (the fixed limits then apply)."""
    try:
        data = json.loads((folder / EDIT_DIR / "plan.json").read_text(encoding="utf-8"))
        seconds = float(data["options"]["duration"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return seconds if seconds > 0 else None


def limits_of(cfg: Mapping[str, Any], folder: Path, final: bool) -> tuple[float, float, float]:
    """(expected, stall, timeout) seconds of a render job in this project folder."""
    return render_limits(cfg, final, video_seconds(folder))
