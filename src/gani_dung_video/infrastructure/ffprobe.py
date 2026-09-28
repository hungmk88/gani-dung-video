"""Run ffprobe on one file and return its JSON (streams + format)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class FfprobeMissingError(RuntimeError):
    """ffprobe is not installed / not on PATH."""


class ProbeError(RuntimeError):
    """ffprobe could not read the file (corrupt, not media, timed out)."""


def require_ffprobe() -> str:
    """Return the ffprobe path or raise with an actionable message."""
    exe = shutil.which("ffprobe")
    if exe is None:
        raise FfprobeMissingError(
            "Không tìm thấy ffprobe. Cài ffmpeg (Mac: `brew install ffmpeg`, "
            "Windows: gói ffmpeg của gyan.dev) rồi mở lại cửa sổ dòng lệnh."
        )
    return exe


def probe(path: Path, timeout_sec: float, exe: str | None = None) -> dict[str, Any]:  # Any: JSON
    """ffprobe -show_streams -show_format as a dict.

    Raises:
        ProbeError: non-zero exit, timeout, or unreadable JSON.
    """
    cmd = [exe or require_ffprobe(), "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", str(path)]  # fmt: skip
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"ffprobe quá {timeout_sec:g}s không trả lời") from e
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ProbeError(err[-1] if err else f"ffprobe lỗi (mã {r.returncode})")
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise ProbeError("ffprobe trả về dữ liệu không đọc được") from e
    if not isinstance(data, dict):
        raise ProbeError("ffprobe trả về dữ liệu không đúng dạng")
    return data
