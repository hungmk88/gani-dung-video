"""OpenCV frame analysis: sample a clip at N fps, downscale, measure each sample (phase 3b).

OpenCV 4.10 applies the phone's rotation flag itself, so frames arrive in display
orientation (1080×1920 for portrait) — the same size inventory.json reports.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike
from numpy.typing import NDArray

from gani_dung_video.domain.scenes import FrameStats

Grey = NDArray[np.float32]
_HIST_BINS = [16, 8]  # hue × saturation: robust to exposure flicker, sensitive to a new shot
_HIST_RANGES = [0, 180, 0, 256]


class VisionError(RuntimeError):
    """A clip cannot be opened/decoded by OpenCV (message in Vietnamese)."""


def _prepare(frame: MatLike, width: int) -> tuple[Grey, MatLike]:
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, _HIST_BINS, _HIST_RANGES)
    cv2.normalize(hist, hist)
    return grey, hist


def _measure(t: float, grey: Grey, hist: MatLike,
             prev: tuple[Grey, MatLike] | None) -> FrameStats:  # fmt: skip
    sharp = float(cv2.Laplacian(grey, cv2.CV_32F).var())
    if prev is None:
        return FrameStats(t=t, sharpness=sharp, cut_score=0.0, dx=0.0, dy=0.0, activity=0.0)
    pg, ph = prev
    # Bhattacharyya distance: 0 identical colour distribution, 1 nothing in common.
    cut = float(cv2.compareHist(ph, hist, cv2.HISTCMP_BHATTACHARYYA))
    (dx, dy), _ = cv2.phaseCorrelate(pg, grey)
    activity = float(np.mean(np.abs(grey - pg)))
    return FrameStats(t=round(t, 3), sharpness=round(sharp, 2), cut_score=round(cut, 4),
                      dx=round(dx, 3), dy=round(dy, 3), activity=round(activity, 3))  # fmt: skip


def analyse_clip(path: Path, sample_fps: float, width: int) -> list[FrameStats]:
    """Sample the clip at ~sample_fps and measure each sample.

    Uses grab() for skipped frames (no pixel conversion) and the container timestamp
    (CAP_PROP_POS_MSEC) because phone footage has a variable frame rate (29.83–30.01 fps
    measured on real GANI clips), so frame_index / fps would drift.

    Raises:
        VisionError: OpenCV cannot open the file.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VisionError(f"OpenCV không mở được {path.name}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / sample_fps))
        out: list[FrameStats] = []
        prev: tuple[Grey, MatLike] | None = None
        idx = 0
        while cap.grab():
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                grey, hist = _prepare(frame, width)
                out.append(_measure(t, grey, hist, prev))
                prev = (grey, hist)
            idx += 1
    finally:
        cap.release()
    if not out:
        raise VisionError(f"Không đọc được khung hình nào từ {path.name}")
    return out
