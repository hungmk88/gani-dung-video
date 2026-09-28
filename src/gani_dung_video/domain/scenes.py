"""Scene rules (phase 3b): split, blur/shake flags, "đoạn đẹp" per style — pure functions.

Input is a per-sample metric series produced by infrastructure/video_frames.py (no OpenCV
here), so every rule is tested on synthetic numbers.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict


class FrameStats(BaseModel):
    """Metrics of one sampled frame, relative to the previous sample."""

    model_config = ConfigDict(frozen=True)

    t: float  # seconds from clip start
    sharpness: float  # variance of Laplacian at analysis width
    cut_score: float  # 0 = same colours as previous sample, 1 = totally different
    dx: float  # global shift in px (phase correlation) since previous sample
    dy: float
    activity: float  # mean absolute grey difference: local action (hands, machines)


class Segment(BaseModel):
    """The best sub-range of a scene for one style; peak = action peak inside it."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    peak: float


class Window(BaseModel):
    """A ~2 s slice of a scene with its own motion verdict (decision 28/09/2026: a long
    handheld take is judged window by window, not thrown away for a few shaky seconds)."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    motion: str  # tinh | lia | rung
    jitter: float
    consistency: float
    shaky: bool


class Scene(BaseModel):
    clip: str
    index: int
    start: float
    end: float
    sharpness: float  # median over the scene
    motion: str  # tinh | lia | rung
    jitter: float
    consistency: float
    blurry: bool = False
    shaky: bool = False  # True when every window is shaky (no usable span left)
    windows: list[Window] = []
    segments: dict[str, list[Segment]] = {}  # style → "đoạn đẹp" inside usable spans

    @property
    def duration(self) -> float:
        return self.end - self.start


class ScenesFile(BaseModel):
    """Content of edit/scenes.json."""

    folder: str
    rules_version: str
    scenes: list[Scene]
    usable_by_style: dict[str, int]  # so_canh_dung_duoc for preflight X5/X6


def split_scenes(
    stats: Sequence[FrameStats], duration: float, cut_threshold: float, min_scene_sec: float
) -> list[tuple[int, int]]:
    """Index ranges [i0, i1) of stats per scene. Cuts closer than min_scene_sec are ignored
    (a flash or a hand passing the lens is not a new scene)."""
    if not stats:
        return []
    bounds = [0]
    for i in range(1, len(stats)):
        if stats[i].cut_score > cut_threshold and stats[i].t - stats[bounds[-1]].t >= min_scene_sec:
            bounds.append(i)
    # A last scene shorter than the minimum is merged back into the previous one.
    if len(bounds) > 1 and duration - stats[bounds[-1]].t < min_scene_sec:
        bounds.pop()
    return [(b, bounds[k + 1] if k + 1 < len(bounds) else len(stats)) for k, b in enumerate(bounds)]


def classify_motion(
    stats: Sequence[FrameStats], cfg: Mapping[str, Any]
) -> tuple[str, float, float]:
    """Return (tinh | lia | rung, jitter px, direction consistency 0–1).

    Why not "large motion = shaky" (spec §3.2-6): a deliberate pan moves a lot but steadily
    in one direction and smoothly; hand shake moves back and forth or in jolts.
    Consistency = |mean vector| / mean |vector|; jitter = mean change of the motion vector
    between samples.
    """
    moves = [(s.dx, s.dy) for s in stats[1:]]
    if not moves:
        return "tinh", 0.0, 1.0
    mags = [math.hypot(x, y) for x, y in moves]
    mean_mag = statistics.fmean(mags)
    if mean_mag < float(cfg["static_px"]):
        return "tinh", 0.0, 1.0
    mx, my = statistics.fmean(x for x, _ in moves), statistics.fmean(y for _, y in moves)
    consistency = math.hypot(mx, my) / mean_mag
    jitter = statistics.fmean(
        math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in itertools.pairwise(moves)
    ) if len(moves) > 1 else 0.0  # fmt: skip
    # Jerky = the vector jumps by a large share of its own size each sample, even if the
    # drift happens to point one way over a short 2 s window.
    jerky = consistency < float(cfg["pan_min_consistency"]) or (
        jitter / mean_mag > float(cfg["pan_max_jerk_ratio"])
    )
    shaky = jitter > float(cfg["shake_jitter_px"]) and jerky
    return ("rung" if shaky else "lia"), round(jitter, 3), round(consistency, 3)


def blur_flags(sharpness: Sequence[float], floor: float) -> list[bool]:
    """Blurry = median sharpness below the absolute floor, whatever the rest of the folder does.

    Decision 28/09/2026 (option B): a relative "least sharp 20%" rule was dropped — it
    flagged sharp scenes when all footage was good and let real blur through on a bad day.
    Motion blur that keeps a high Laplacian is left to the aesthetic score (phase 3c).
    """
    return [s < floor for s in sharpness]


def pick_segment(
    stats: Sequence[FrameStats], start: float, end: float, lo: float, hi: float,
    peak_ratio: float = 0.35,
) -> Segment | None:  # fmt: skip
    """Best window of length min(hi, scene) for a style, with the action peak at ~35% of it
    (skill heuristic, spec §7.3). None if the scene is shorter than the style minimum `lo`."""
    length = min(hi, end - start)
    if length < lo or not stats:
        return None
    peak = max(stats, key=lambda s: s.activity).t
    s0 = min(max(start, peak - peak_ratio * length), end - length)
    return Segment(start=round(s0, 3), end=round(s0 + length, 3), peak=round(peak, 3))


def style_segment_range(style: Mapping[str, Any]) -> tuple[float, float]:
    """segment_sec of a style, falling back to scene_sec (e.g. "Theo mô tả")."""
    lo, hi = style.get("segment_sec") or style["scene_sec"]
    return float(lo), float(hi)


def usable_by_style(scenes: Sequence[Scene], styles: Mapping[str, Any]) -> dict[str, int]:
    """Usable "đoạn đẹp" per style (so_canh_dung_duoc for X5/X6): segments of non-blurry
    scenes; segments already lie inside shake-free spans."""
    return {
        name: sum(len(sc.segments.get(name, [])) for sc in scenes if not sc.blurry)
        for name in styles
    }


def motion_windows(
    stats: Sequence[FrameStats], start: float, end: float, window_sec: float,
    cfg: Mapping[str, Any],
) -> list[Window]:  # fmt: skip
    """Cut [start, end) into window_sec slices and classify the motion of each one.

    A tail shorter than half a window is merged into the previous window (too few samples
    to judge alone).
    """
    if not stats or end <= start:
        return []
    n = max(1, math.floor((end - start) / window_sec))
    if end - start - n * window_sec >= window_sec / 2:
        n += 1
    bounds = [start + k * window_sec for k in range(n)] + [end]
    out: list[Window] = []
    for a, b in itertools.pairwise(bounds):
        part = [s for s in stats if a <= s.t < b]
        motion, jitter, consistency = classify_motion(part, cfg)
        out.append(Window(start=round(a, 3), end=round(b, 3), motion=motion, jitter=jitter,
                          consistency=consistency, shaky=motion == "rung"))  # fmt: skip
    return out


def usable_spans(windows: Sequence[Window]) -> list[tuple[float, float]]:
    """Join adjacent non-shaky windows into (start, end) spans."""
    spans: list[tuple[float, float]] = []
    for w in windows:
        if w.shaky:
            continue
        if spans and spans[-1][1] == w.start:
            spans[-1] = (spans[-1][0], w.end)
        else:
            spans.append((w.start, w.end))
    return spans


def _candidates(
    stats: Sequence[FrameStats], spans: Sequence[tuple[float, float]], lo: float, hi: float,
    gap: float,
) -> list[tuple[float, Segment]]:  # fmt: skip
    """One candidate per slot of (hi + gap) s inside each span, scored by peak activity."""
    out: list[tuple[float, Segment]] = []
    for a, b in spans:
        cursor = a
        while b - cursor >= lo:
            slot_end = min(b, cursor + hi + gap)
            part = [s for s in stats if cursor <= s.t < slot_end]
            seg = pick_segment(part, cursor, slot_end, lo, hi)
            if seg is not None:
                out.append((max(s.activity for s in part), seg))
            cursor = slot_end
    return out


def pick_clip_segments(
    stats: Sequence[FrameStats], spans: Sequence[tuple[float, float]], lo: float, hi: float,
    gap: float, max_n: int,
) -> list[Segment]:  # fmt: skip
    """Up to max_n "đoạn đẹp" of one clip for a style, sorted by time.

    Spec §5.3.C / check V4: several segments may come from one long clip if they are at
    least `gap` s apart, at most 3 per clip. The busiest moments (action peak) win.
    """
    chosen: list[Segment] = []
    ranked = sorted(_candidates(stats, spans, lo, hi, gap), key=lambda c: (-c[0], c[1].start))
    for _, seg in ranked:
        if len(chosen) >= max_n:
            break
        if all(seg.start - c.end >= gap - 1e-6 or c.start - seg.end >= gap - 1e-6
               for c in chosen):  # fmt: skip
            chosen.append(seg)
    return sorted(chosen, key=lambda c: c.start)
