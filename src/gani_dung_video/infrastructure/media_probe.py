"""Measure a rendered file (phase 5b): ffprobe facts, one decoding pass, caption widths.

The measuring pass runs ebur128 (loudness + true peak), blackdetect and freezedetect in a
single ffmpeg decode; domain/qc.py reads its log. Caption width uses the real glyph advances
of the bundled Be Vietnam Pro fonts, scaled the way libass sizes text (ascent - descent).
"""

from __future__ import annotations

import json
import subprocess
import unicodedata
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont

from gani_dung_video.domain.qc import Measures, MediaFacts, parse_measures
from gani_dung_video.infrastructure.ffmpeg_render import FONTS_DIR


class ProbeFailed(RuntimeError):
    """The file or a font could not be measured (message in Vietnamese)."""


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise ProbeFailed(f"Không tìm thấy {args[0]}.") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeFailed(f"{args[0]} chạy quá {timeout:g}s.") from e


def _rate(text: str) -> float:
    num, _, den = text.partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def probe_media(path: Path, timeout: float) -> MediaFacts:
    """ffprobe with an exact frame count (decodes the video once).

    Raises:
        ProbeFailed: ffprobe missing, timed out, or the file is not a video.
    """
    r = _run(["ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
              "-show_streams", "-show_format", str(path)], timeout)  # fmt: skip
    try:
        data: dict[str, Any] = json.loads(r.stdout.decode("utf-8", "replace"))
        v = next(s for s in data["streams"] if s["codec_type"] == "video")
    except (json.JSONDecodeError, KeyError, StopIteration) as e:
        raise ProbeFailed(f"ffprobe không đọc được {path.name}.") from e
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return MediaFacts(
        width=int(v["width"]), height=int(v["height"]), fps=round(_rate(v["r_frame_rate"]), 3),
        duration=float(data["format"]["duration"]), frames=int(v.get("nb_read_frames", 0)),
        video_codec=str(v["codec_name"]), pix_fmt=str(v.get("pix_fmt", "")),
        audio_codec=str(a["codec_name"]) if a else None,
        sample_rate=int(a["sample_rate"]) if a else None,
        channels=int(a["channels"]) if a else None,
    )  # fmt: skip


def probe_duration(path: Path, timeout: float) -> float:
    """Container duration in seconds (fast: no frame decoding, unlike probe_media).

    Raises:
        ProbeFailed: ffprobe missing, timed out, or the file has no readable duration.
    """
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
              str(path)], timeout)  # fmt: skip
    try:
        return float(r.stdout.decode("utf-8", "replace").strip())
    except ValueError as e:
        raise ProbeFailed(f"ffprobe không đọc được độ dài của {path.name}.") from e


def measure(path: Path, cfg: Mapping[str, Any], duration: float, timeout: float) -> Measures:
    """One decode: loudness, true peak, black gaps, freezes.

    Raises:
        ProbeFailed: ffmpeg failed or its log had no loudness summary.
    """
    graph = (f"[0:v]blackdetect=d={cfg['black_min_sec']}:pix_th={cfg['black_pix_th']},"
             f"freezedetect=n={cfg['freeze_noise_db']}dB:d={cfg['freeze_min_sec']}[v];"
             "[0:a]ebur128=peak=true:framelog=quiet[a]")  # fmt: skip
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-filter_complex", graph,
              "-map", "[v]", "-map", "[a]", "-f", "null", "-"], timeout)  # fmt: skip
    log = r.stderr.decode("utf-8", "replace")
    try:
        return parse_measures(log, duration)
    except ValueError as e:
        raise ProbeFailed(f"Không đo được {path.name}: {e}") from e


@cache
def _font_files() -> dict[str, Path]:
    """Family name as written in the .ass (name table id 1) → bundled font file."""
    out = {}
    for f in sorted(FONTS_DIR.glob("*.ttf")):
        name = TTFont(f, lazy=True)["name"].getDebugName(1)
        if name:
            out[name] = f
    return out


class FontMetrics:
    """Caption widths in ASS pixels for the bundled fonts."""

    def __init__(self) -> None:
        self._fonts: dict[str, tuple[dict[int, str], Any, float]] = {}

    def _load(self, family: str) -> tuple[dict[int, str], Any, float]:
        if family not in self._fonts:
            path = _font_files().get(family)
            if path is None:
                raise ProbeFailed(f"Không có font '{family}' trong assets/fonts.")
            font = TTFont(path)
            hhea = font["hhea"]
            # Any: fontTools tables are untyped; libass maps font size to ascent - descent.
            self._fonts[family] = (
                font.getBestCmap(),
                font["hmtx"],
                float(hhea.ascent - hhea.descent),
            )
        return self._fonts[family]

    def width(self, text: str, family: str, px: int) -> float:
        cmap, hmtx, height = self._load(family)
        # NFC: Vietnamese letters are single precomposed glyphs in Be Vietnam Pro.
        units = sum(
            hmtx[cmap.get(ord(ch), ".notdef")][0] for ch in unicodedata.normalize("NFC", text)
        )
        return float(units) * px / height

    def has_glyph(self, char: str, family: str) -> bool:
        """True when the bundled font draws `char` itself (not its .notdef box)."""
        return ord(char) in self._load(family)[0]
