"""Slow motion (26/10/2026): shots filmed fast enough are played at effects.slowmo_speed.

A style opts in with `slowmo_min_fps` (Review món ăn: 60). A clip filmed at that rate or more
shown at half speed still gives the 30 fps video a new picture every frame, so cheese pulling or
steam rising looks smooth, not stepped. Slowed shots count against the style's `max_effects`
together with the zoom punch-ins (domain/zoom.py) and are spread over the video. The timeline
never moves: a slowed shot simply reads less of its clip, around the same peak moment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gani_dung_video.domain.zoom import effect_cap, spread

_FPS_SLACK = 0.99  # 59.94 fps phones count as 60


def slowmo_candidates(clip_fps: Sequence[float], usable: Sequence[bool],
                      style: Mapping[str, Any]) -> list[int]:  # fmt: skip
    """Shots whose clip was filmed at ≥ the style's slowmo_min_fps (none if it has no such key)."""
    min_fps = style.get("slowmo_min_fps")
    if not min_fps:
        return []
    return [i for i, (f, ok) in enumerate(zip(clip_fps, usable, strict=True))
            if ok and f >= float(min_fps) * _FPS_SLACK]  # fmt: skip


def reserve_for_slowmo(style: Mapping[str, Any], candidates: int) -> int:
    """Slots of max_effects kept for slow motion before the punch-ins are chosen: up to half,
    so a video with both gets some of each."""
    cap = style.get("max_effects")
    return 0 if cap is None else min(candidates, int(cap) // 2)


def slowmo_picks(candidates: Sequence[int], style: Mapping[str, Any], used: int) -> list[int]:
    """The candidates to slow down, spread over the video, within what max_effects leaves."""
    keep = spread(len(candidates), effect_cap(style, len(candidates), used))
    return [candidates[j] for j in keep]


def slowed_window(src_in: float, src_out: float, speed: float, peak_ratio: float,
                  fps: int) -> tuple[float, float]:  # fmt: skip
    """The shorter source window a slowed shot reads, keeping its peak at the same place."""
    span = src_out - src_in
    start = round((src_in + peak_ratio * (span - span * speed)) * fps) / fps
    return start, start + span * speed
