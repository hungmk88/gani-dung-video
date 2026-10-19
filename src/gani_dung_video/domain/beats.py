"""Beat maths (phase 4a): BPM folding, beat grid, music loops, snapping cuts to beats.

Pure functions, no I/O. Spec v2.1 §7.3 (snap_to_beats), X12 (BPM out of style range),
X13 (track shorter than video), X18 (beat vs action peak).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.footage import clip_stems
from gani_dung_video.domain.scenes import Scene

FPS = 30
PEAK_RATIOS = (0.35, 0.40, 0.45, 0.50)  # spec §7.3: peak at 35% of the shot, back off to 50%
_EPS = 1e-9


class SnapError(ValueError):
    """A draft cannot be laid on the beat grid (message in Vietnamese, says what to fix)."""


class SceneRef(BaseModel):
    """A pickable "đoạn đẹp": the window an editor may cut from, plus its action peak."""

    model_config = ConfigDict(frozen=True)

    clip: str
    start: float
    end: float
    peak: float


class DraftSegment(BaseModel):
    """One cut of an AI draft: which segment, how many beats long, and its on-screen text."""

    model_config = ConfigDict(frozen=True)

    block: str
    scene_id: str
    beats: int
    text: str = ""


class SnappedSegment(DraftSegment):
    """A draft cut placed on the timeline (t_*) and on the source clip (src_*), in seconds."""

    t_start: float
    t_end: float
    src_in: float
    src_out: float
    peak_ratio: float | None  # 0.35 normal, up to 0.50 = X18 back-off, None = clamped
    clip: str = ""  # source file of the segment (from its SceneRef)


def _deviation(bpm: float, lo: float, hi: float) -> float:
    if lo <= bpm <= hi:
        return 0.0
    return (lo - bpm) / lo if bpm < lo else (bpm - hi) / hi


def fold_bpm(
    bpm: float, bpm_range: tuple[float, float] | None, max_deviation: float = 0.2
) -> tuple[float, str]:
    """X12: pick the tempo to count beats at: as-is, half or double, whichever fits the range.

    Returns (effective_bpm, mode) with mode giu_nguyen | dem_2_thanh_1 | tach_1_thanh_2.
    If even the best choice stays more than max_deviation outside the range, the track is
    kept as-is (preflight already warns).
    """
    if bpm_range is None:
        return bpm, "giu_nguyen"
    lo, hi = bpm_range
    candidates = [(bpm, "giu_nguyen"), (bpm / 2, "dem_2_thanh_1"), (bpm * 2, "tach_1_thanh_2")]
    eff, mode = min(candidates, key=lambda c: _deviation(c[0], lo, hi))
    if _deviation(eff, lo, hi) > max_deviation:
        return bpm, "giu_nguyen"
    return eff, mode


FOLD_NAMES = {  # how the X12 counts read in messages for people
    "giu_nguyen": "giữ nguyên",
    "dem_2_thanh_1": "đếm 2 phách thành 1",
    "tach_1_thanh_2": "tách 1 phách thành 2",
}


def other_folds(
    bpm: float, bpm_range: tuple[float, float] | None, chosen: str, max_deviation: float = 0.2
) -> list[tuple[float, str]]:
    """The X12 counts other than `chosen` that are still within max_deviation of the range,
    best fit first: the planner falls back on one when `chosen` cannot reach the length."""
    if bpm_range is None:
        return []
    lo, hi = bpm_range
    candidates = [(bpm, "giu_nguyen"), (bpm / 2, "dem_2_thanh_1"), (bpm * 2, "tach_1_thanh_2")]
    ok = [c for c in candidates if c[1] != chosen and _deviation(c[0], lo, hi) <= max_deviation]
    return sorted(ok, key=lambda c: _deviation(c[0], lo, hi))


def beat_grid(beat_sec: float, count: int, first: float = 0.0) -> list[float]:
    """count + 1 beat boundaries: a segment of n beats from boundary i ends at boundary i + n."""
    return [first + k * beat_sec for k in range(count + 1)]


def beats_that_fit(
    segment_sec: float, beat_sec: float, allowed: Sequence[int], frame_sec: float = 1.0 / FPS
) -> list[int]:
    """Allowed beat counts whose cut is guaranteed to fit inside a segment of segment_sec seconds.

    snap_to_beats rounds both ends of a cut to a whole frame, so a cut of n beats can come out
    up to one frame longer than n * beat_sec (the two roundings differ by < 1 frame). A segment
    must therefore hold n beats plus one frame, or the snap would refuse it ("quá ngắn") for
    some positions on the grid, after the AI was told the scene fits.
    """
    return [n for n in allowed if n * beat_sec + frame_sec <= segment_sec + _EPS]


def loop_info(
    track_sec: float, first_beat_sec: float, bpm: float, need_sec: float, unit_beats: int
) -> tuple[float, int] | None:
    """X13: (loop_sec, repeats) when the music that can be heard is shorter than `need_sec`.

    `need_sec` is everything the music must cover: the content plus the end card, since the
    music runs to the end of the file. What can be heard starts at the first beat
    (`-ss first_beat`), so `first_beat_sec` is not available track.

    The loop is a whole number of bars (unit_beats source beats) counted from the first beat,
    so the beat grid stays unbroken across the join. None also when not even one bar fits.
    """
    if track_sec - first_beat_sec >= need_sec - _EPS:
        return None
    bar = unit_beats * 60.0 / bpm
    bars = math.floor((track_sec - first_beat_sec) / bar)
    if bars < 1:
        return None
    loop_sec = bars * bar
    return loop_sec, math.ceil(need_sec / loop_sec)


def segment_refs(scenes: Sequence[Scene], style: str) -> dict[str, SceneRef]:
    """Ids the AI may pick: "<clip stem>_s<scene index>_<n-th segment of the style>"
    (stems from `clip_stems`, unique even when two clips differ only by extension)."""
    out: dict[str, SceneRef] = {}
    stems = clip_stems(sc.clip for sc in scenes)
    for sc in scenes:
        stem = stems[sc.clip]
        for k, seg in enumerate(sc.segments.get(style, [])):
            out[f"{stem}_s{sc.index}_{k}"] = SceneRef(
                clip=sc.clip, start=seg.start, end=seg.end, peak=seg.peak
            )
    return out


def _frame(t: float, fps: int) -> float:
    return round(t * fps) / fps


def _place(ref: SceneRef, dur: float) -> tuple[float, float | None]:
    """Source in-point and the peak ratio used (None = clamped to the segment edge)."""
    for ratio in PEAK_RATIOS:
        cand = ref.peak - ratio * dur
        if ref.start - _EPS <= cand and cand + dur <= ref.end + _EPS:
            return max(cand, ref.start), ratio
    return min(max(ref.peak - PEAK_RATIOS[0] * dur, ref.start), ref.end - dur), None


def snap_to_beats(
    segments: Sequence[DraftSegment], beats: Sequence[float], scenes: Mapping[str, SceneRef],
    fps: int = FPS,
) -> list[SnappedSegment]:  # fmt: skip
    """Lay the draft's cuts on the beat grid (spec §7.3).

    Every cut point is rounded to a whole frame from the beat itself (not from the previous
    cut), so rounding error never accumulates and consecutive cuts touch exactly.

    Raises:
        SnapError: unknown scene, non-positive beats, more beats than the music/grid has, or
            a segment shorter than the beats it must fill.
    """
    out: list[SnappedSegment] = []
    i = 0
    for seg in segments:
        if seg.beats < 1:
            raise SnapError(f"Đoạn '{seg.scene_id}' có {seg.beats} phách; phải từ 1 phách trở lên.")
        j = i + seg.beats
        if j >= len(beats):
            raise SnapError("Vượt độ dài nhạc: các cảnh cần nhiều phách hơn nhạc/lưới có.")
        ref = scenes.get(seg.scene_id)
        if ref is None:
            raise SnapError(f"Cảnh '{seg.scene_id}' không có trong danh sách cảnh dùng được.")
        t0, t1 = _frame(beats[i], fps), _frame(beats[j], fps)
        dur = t1 - t0
        if ref.end - ref.start + _EPS < dur:
            raise SnapError(
                f"Cảnh '{seg.scene_id}' quá ngắn: {ref.end - ref.start:.2f}s < {dur:.2f}s "
                f"({seg.beats} phách)."
            )
        src_in, ratio = _place(ref, dur)
        out.append(SnappedSegment(**seg.model_dump(), t_start=t0, t_end=t1, src_in=src_in,
                                  src_out=src_in + dur, peak_ratio=ratio,
                                  clip=ref.clip))  # fmt: skip
        i = j
    return out
