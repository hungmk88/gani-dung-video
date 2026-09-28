"""Music files: tempo + first beat from audio (numpy only), library.json, path allowlist (4a).

No librosa (decision 29/09/2026): a track listed in library.json uses its declared BPM;
an unlisted track is measured here, and if no clear beat is found the plan falls back to the
virtual grid with a warning instead of cutting on a wrong beat.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gani_dung_video.domain.plan import MusicInfo
from gani_dung_video.infrastructure.ffprobe import FfprobeMissingError, ProbeError, probe

LIBRARY_FILE = "library.json"
MAX_LIBRARY_BYTES = 1_000_000
MAX_DECODE_SEC = 1200  # never decode more than 20 minutes of audio
MIN_ANALYSIS_SEC = 8.0
Samples = NDArray[np.float32]
_PositiveNumber = Annotated[float, Field(gt=0, strict=True)]
_NonNegativeNumber = Annotated[float, Field(ge=0, strict=True)]


class MusicError(RuntimeError):
    """A music file or library.json cannot be used (message in Vietnamese)."""


class TempoResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    bpm: float
    first_beat_sec: float  # first beat inside the first period; later beats = + k × 60/bpm
    duration_sec: float
    confidence: float


class LibraryTrack(BaseModel):
    """A checked track: BPM and first-beat offset were verified by a person."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bpm: _PositiveNumber
    first_beat_sec: _NonNegativeNumber


# ─────────────── tempo estimation ───────────────


def _onsets(x: Samples, hop: int) -> NDArray[np.float64] | None:
    """Positive rise of log-compressed frame energy (onsets[i] is at time (i + 1) × hop / sr)."""
    n = len(x) // hop
    env = np.sqrt(np.mean(x[: n * hop].astype(np.float64).reshape(n, hop) ** 2, axis=1))
    if n < 3 or float(env.max()) < 1e-4:
        return None
    comp = np.log1p(20.0 * env / env.max())
    return np.asarray(np.maximum(np.diff(comp), 0.0), dtype=np.float64)


def _autocorr(o: NDArray[np.float64]) -> NDArray[np.float64]:
    c = o - o.mean()
    f = np.fft.rfft(c, 2 * len(c))
    ac = np.fft.irfft(f * np.conj(f))[: len(c)]
    return np.asarray(ac / (ac[0] + 1e-12), dtype=np.float64)


def _refine(o: NDArray[np.float64], fr: float, bpm0: float) -> tuple[float, int]:
    """Search BPM ± 1.5 and every phase for the beat comb that lands on most onset energy.

    Fitting the whole track (not one lag) keeps the beat position accurate minutes later.
    """
    sm = np.convolve(o, [0.25, 0.5, 0.25], mode="same")
    best = (-1.0, bpm0, 0)
    for bpm in bpm0 + np.arange(-1.5, 1.5001, 0.02):
        period = 60.0 * fr / bpm
        beats = np.arange(int((len(sm) - 1) / period))
        if len(beats) < 4:
            continue
        idx = np.rint(np.arange(int(period))[:, None] + beats[None, :] * period).astype(int)
        score = sm[np.minimum(idx, len(sm) - 1)].mean(axis=1)
        p = int(score.argmax())
        if score[p] > best[0]:
            best = (float(score[p]), float(bpm), p)
    return best[1], best[2]


def estimate_tempo(samples: Samples, sr: int, cfg: Mapping[str, Any]) -> TempoResult | None:
    """BPM and first-beat time of mono audio, or None when there is no clear beat.

    Onset autocorrelation finds the period, then a comb fit over the whole track refines the
    BPM and phase. `confidence` = how much the best period stands out from the other lags.
    """
    x = samples[: int(float(cfg["max_seconds"]) * sr)]
    if len(x) < MIN_ANALYSIS_SEC * sr:
        return None
    hop = int(cfg["hop"])
    fr = sr / hop
    o = _onsets(x, hop)
    if o is None:
        return None
    ac = _autocorr(o)
    lo = max(1, int(fr * 60.0 / float(cfg["max_bpm"])))
    hi = min(len(ac) - 2, int(np.ceil(fr * 60.0 / float(cfg["min_bpm"]))))
    if hi <= lo + 1:
        return None
    window = ac[lo : hi + 1]
    lag = lo + int(window.argmax())
    conf = float(ac[lag] / (np.mean(np.abs(window)) + 1e-12))
    if conf < float(cfg["min_confidence"]):
        return None
    y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
    denom = y0 - 2 * y1 + y2
    lag_f = lag + (0.5 * (y0 - y2) / denom if denom != 0 else 0.0)
    bpm, phase = _refine(np.maximum(o, 0.0), fr, 60.0 * fr / lag_f)
    period_sec = 60.0 / bpm
    first = min((phase + 1) / fr, period_sec)
    return TempoResult(bpm=round(bpm, 2), first_beat_sec=round(first, 3),
                       duration_sec=len(samples) / sr, confidence=round(conf, 2))  # fmt: skip


# ─────────────── files ───────────────


def decode_audio(path: Path, sample_rate: int, timeout_sec: float) -> Samples:
    """Decode any audio file to mono float samples with ffmpeg.

    Raises:
        MusicError: ffmpeg missing, timed out, or the file is not decodable audio.
    """
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise MusicError("Không tìm thấy ffmpeg. Cài ffmpeg rồi mở lại cửa sổ dòng lệnh.")
    cmd = [exe, "-v", "error", "-i", str(path), "-t", str(MAX_DECODE_SEC), "-vn", "-ac", "1",
           "-ar", str(sample_rate), "-f", "s16le", "-"]  # fmt: skip
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired as e:
        raise MusicError(f"Giải mã {path.name} quá {timeout_sec:g}s, đã dừng.") from e
    if r.returncode != 0 or len(r.stdout) < 2:
        raise MusicError(f"Không giải mã được {path.name}: file không phải âm thanh hợp lệ.")
    pcm = np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], dtype=np.int16)
    return (pcm.astype(np.float32) / 32768.0).astype(np.float32)


def analyse_track(path: Path, cfg: Mapping[str, Any]) -> TempoResult | None:
    """Decode and measure one track; None when no clear beat is found."""
    sr = int(cfg["sample_rate"])
    return estimate_tempo(decode_audio(path, sr, float(cfg["ffmpeg_timeout_sec"])), sr, cfg)


def read_library(folder: Path) -> dict[str, LibraryTrack]:
    """Tracks with verified tempo from <folder>/library.json; no file means an empty library.

    Format: {"tracks": {"am_ap_92.mp3": {"bpm": 92, "first_beat_sec": 0.35}}}

    Raises:
        MusicError: unreadable, too large, or not in the format above.
    """
    p = folder / LIBRARY_FILE
    if not p.is_file():
        return {}
    if p.stat().st_size > MAX_LIBRARY_BYTES:
        raise MusicError(f"{LIBRARY_FILE} quá lớn (> 1 MB).")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        tracks = data["tracks"]
        return {str(k): LibraryTrack.model_validate(v) for k, v in tracks.items()}
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, ValidationError) as e:
        raise MusicError(
            f'{LIBRARY_FILE} sai định dạng; cần {{"tracks": {{"ten.mp3": '
            f'{{"bpm": 92, "first_beat_sec": 0.35}}}}}} ({type(e).__name__}).'
        ) from e


def resolve_music_path(
    nhac_file: str, base_dirs: Sequence[Path], extensions: Sequence[str]
) -> Path:
    """Resolve nhac_file to an existing audio file inside one of base_dirs (no path escapes).

    Raises:
        MusicError: missing, outside the allowed folders, or not an audio extension.
    """
    roots = [d.expanduser().resolve() for d in base_dirs]
    cand = Path(nhac_file).expanduser()
    options = [cand] if cand.is_absolute() else [r / cand for r in roots]
    for opt in options:
        p = opt.resolve()
        if (p.is_file() and p.suffix.lower() in extensions
                and any(p.is_relative_to(r) for r in roots)):  # fmt: skip
            return p
    raise MusicError(
        f"File nhạc '{nhac_file}' không có, hoặc không nằm trong thư mục cho phép, "
        f"hoặc không phải file âm thanh ({', '.join(extensions)})."
    )


def _duration(path: Path, timeout_sec: float) -> float:
    try:
        return float(probe(path, timeout_sec)["format"]["duration"])
    except (ProbeError, FfprobeMissingError, KeyError, ValueError) as e:
        raise MusicError(f"Không đọc được độ dài của {path.name}: {e}") from e


def track_info(
    path: Path, library: Mapping[str, LibraryTrack], cfg: Mapping[str, Any]
) -> MusicInfo | None:
    """Library tempo when the track is listed, else measured; None when no clear beat."""
    entry = library.get(path.name)
    if entry is not None:
        return MusicInfo(track=path.name, bpm=entry.bpm, first_beat_sec=entry.first_beat_sec,
                         duration_sec=_duration(path, float(cfg["ffmpeg_timeout_sec"])),
                         source="library")  # fmt: skip
    t = analyse_track(path, cfg)
    if t is None:
        return None
    return MusicInfo(track=path.name, bpm=t.bpm, first_beat_sec=t.first_beat_sec,
                     duration_sec=t.duration_sec, source="detected")  # fmt: skip
