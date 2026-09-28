"""Footage inventory rules (spec v2.1 §7.1, phase 2): pure functions over ffprobe data.

No I/O here: ffprobe output and file names come in, a typed Clip comes out, so every rule
(HDR flag, portrait/landscape after rotation, duplicate candidates) is testable without video.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict

# Any: ffprobe JSON (-show_streams -show_format); fields are read defensively below.
Probe = Mapping[str, Any]


class ProbeDataError(ValueError):
    """ffprobe answered, but the file has no usable video stream (message in Vietnamese)."""


class Clip(BaseModel):
    """One source clip as seen by the pipeline. Width/height are AFTER applying rotation."""

    model_config = ConfigDict(frozen=True)

    file: str
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    rotation: int  # 0 | 90 | 180 | 270, as stored by the camera
    orientation: str  # doc | ngang | vuong
    fps: float
    video_codec: str
    pix_fmt: str | None
    color_transfer: str | None
    color_primaries: str | None
    is_hdr: bool
    has_audio_stream: bool
    audio_tracks: int


class Duplicate(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: str
    duplicate_of: str


class Skipped(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: str
    reason: str


class Inventory(BaseModel):
    """Content of edit/inventory.json."""

    folder: str
    rules_version: str
    clips: list[Clip]
    duplicates: list[Duplicate]
    skipped: list[Skipped]
    total_duration_sec: float


def parse_fps(rate: str | None) -> float:
    """ffprobe frame rate "30000/1001" → 29.97; "0/0" or missing → 0.0."""
    if not rate or "/" not in rate:
        return 0.0
    num, den = rate.split("/", 1)
    try:
        return round(float(num) / float(den), 3) if float(den) else 0.0
    except ValueError:
        return 0.0


def rotation_of(stream: Probe) -> int:
    """Camera rotation normalised to 0/90/180/270 (iPhone stores -90 in display matrix)."""
    raw: object = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            raw = sd["rotation"]
    raw = (stream.get("tags") or {}).get("rotate", raw)
    try:
        return round(float(str(raw))) % 360
    except ValueError:
        return 0


def orientation(width: int, height: int) -> str:
    return "doc" if height > width else "ngang" if width > height else "vuong"


def _first(streams: Sequence[Probe], kind: str) -> Probe | None:
    return next((s for s in streams if s.get("codec_type") == kind), None)


def _duration(probe: Probe, video: Probe) -> float:
    for src in (probe.get("format") or {}, video):
        try:
            return round(float(src.get("duration", "")), 3)
        except (TypeError, ValueError):
            continue
    return 0.0


def clip_from_probe(file: str, size_bytes: int, probe: Probe, hdr_transfers: Sequence[str]) -> Clip:
    """Build a Clip from ffprobe JSON.

    Raises:
        ProbeDataError: no video stream, or zero width/height (not a usable clip).
    """
    streams = list(probe.get("streams") or [])
    video = _first(streams, "video")
    if video is None or not video.get("width") or not video.get("height"):
        raise ProbeDataError("không có luồng hình (file âm thanh, ảnh hoặc file hỏng)")
    rot = rotation_of(video)
    w, h = int(video["width"]), int(video["height"])
    if rot in (90, 270):  # stored landscape + rotate flag = shown portrait
        w, h = h, w
    transfer = video.get("color_transfer")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    return Clip(
        file=file,
        size_bytes=size_bytes,
        duration_sec=_duration(probe, video),
        width=w,
        height=h,
        rotation=rot,
        orientation=orientation(w, h),
        fps=parse_fps(video.get("avg_frame_rate")) or parse_fps(video.get("r_frame_rate")),
        video_codec=str(video.get("codec_name", "?")),
        pix_fmt=video.get("pix_fmt"),
        color_transfer=transfer,
        color_primaries=video.get("color_primaries"),
        is_hdr=transfer in hdr_transfers,
        has_audio_stream=bool(audio),
        audio_tracks=len(audio),
    )


def duplicate_candidates(names: Sequence[str], patterns: Sequence[str]) -> list[tuple[str, str]]:
    """Pair "IMG_8461 2.MOV" / "clip (1).mp4" with the original of the same name, if present.

    Only these pairs are hashed later — the spec forbids hashing the whole 4K folder.
    The extension must match (case-insensitive); "Canh 2.mp4" without "Canh.mp4" is no pair.

    Example:
        >>> duplicate_candidates(["A.MOV", "A 2.MOV", "B (1).mp4"], [r"^(?P<base>.+) \\d+$"])
        [('A 2.MOV', 'A.MOV')]
    """
    by_key = {(PurePath(n).stem, PurePath(n).suffix.lower()): n for n in names}
    compiled = [re.compile(p) for p in patterns]
    pairs: list[tuple[str, str]] = []
    for n in names:
        p = PurePath(n)
        for rx in compiled:
            m = rx.match(p.stem)
            original = by_key.get((m.group("base"), p.suffix.lower())) if m else None
            if original and original != n:
                pairs.append((n, original))
                break
    return pairs
