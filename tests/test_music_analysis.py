"""Tests: infrastructure/music_analysis.py — tempo from audio (numpy), library, path allowlist."""

from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.music_analysis import (
    MusicError,
    analyse_track,
    estimate_tempo,
    read_library,
    resolve_music_path,
    track_info,
)

CFG = load_rules()["music_analysis"]
EXT = CFG["extensions"]
SR = 8000
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")


def make_track(bpm: float, first: float = 0.5, dur: float = 60.0, sr: int = SR,
               noise: float = 0.01) -> np.ndarray:  # fmt: skip
    """Kick every beat (louder on beat 1 of each bar) over a soft pad, like the test tracks."""
    period = 60.0 / bpm
    x = np.zeros(int(dur * sr))
    t = np.arange(int(0.3 * sr)) / sr
    burst = np.sin(2 * np.pi * (55 + 90 * np.exp(-25 * t)) * t) * np.exp(-9 * t)
    k, at = 0, first
    while at < dur - 0.3:
        i = int(at * sr)
        x[i : i + len(burst)] += burst * (1.0 if k % 4 == 0 else 0.55)
        k, at = k + 1, first + (k + 1) * period
    tt = np.arange(len(x)) / sr
    x += 0.12 * np.sin(2 * np.pi * 220 * tt) * (0.6 + 0.4 * np.sin(2 * np.pi * tt / (8 * period)))
    x += np.random.default_rng(1).normal(scale=noise, size=len(x))
    return x.astype(np.float32)


def phase_gap(a: float, b: float, period: float) -> float:
    """Distance between two beat positions, ignoring whole periods."""
    return abs((a - b + period / 2) % period - period / 2)


def write_wav(path: Path, x: np.ndarray, sr: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# ─────────────── estimate_tempo ───────────────


@pytest.mark.parametrize("bpm", [60.0, 72.0, 84.0, 108.0, 124.0, 140.0])
def test_tempo_and_first_beat_of_a_clean_track(bpm: float) -> None:
    r = estimate_tempo(make_track(bpm), SR, CFG)
    assert r is not None
    assert r.bpm == pytest.approx(bpm, abs=0.3)
    period = 60.0 / bpm  # the first kick is at 0.5 s; reported inside the first period
    assert phase_gap(r.first_beat_sec, 0.5, period) < 0.03
    assert 0.0 <= r.first_beat_sec <= period
    assert r.duration_sec == pytest.approx(60.0)


def test_first_beat_is_reported_inside_the_first_period() -> None:
    r = estimate_tempo(make_track(108.0, first=1.7), SR, CFG)
    period = 60.0 / 108.0
    assert r is not None and 0.0 <= r.first_beat_sec <= period
    assert phase_gap(r.first_beat_sec, 1.7, period) < 0.03  # same phase as the real beats


def test_beat_position_stays_accurate_over_a_long_track() -> None:
    r = estimate_tempo(make_track(124.0, dur=200.0), SR, CFG)
    assert r is not None
    period = 60.0 / r.bpm
    real = 0.5 + 100 * 60.0 / 124.0  # the real 101st beat
    predicted = r.first_beat_sec + round((real - r.first_beat_sec) / period) * period
    assert predicted == pytest.approx(real, abs=0.06)  # under 2 frames of drift after 49 s


def test_noise_and_silence_have_no_beat() -> None:
    rng = np.random.default_rng(3)
    assert estimate_tempo(rng.normal(scale=0.2, size=30 * SR).astype(np.float32), SR, CFG) is None
    assert estimate_tempo(np.zeros(30 * SR, dtype=np.float32), SR, CFG) is None


def test_too_short_audio_has_no_beat() -> None:
    assert estimate_tempo(make_track(100.0, dur=2.0), SR, CFG) is None


# ─────────────── decoding through ffmpeg ───────────────


@needs_ffmpeg
def test_analyse_track_decodes_a_file(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", make_track(96.0, dur=40.0))
    r = analyse_track(tmp_path / "a.wav", CFG)
    assert r is not None and r.bpm == pytest.approx(96.0, abs=0.3)
    assert r.duration_sec == pytest.approx(40.0, abs=0.1)


@needs_ffmpeg
def test_analyse_track_rejects_a_file_that_is_not_audio(tmp_path: Path) -> None:
    (tmp_path / "x.mp3").write_bytes(b"not audio at all")
    with pytest.raises(MusicError, match="giải mã"):
        analyse_track(tmp_path / "x.mp3", CFG)


# ─────────────── library.json ───────────────


def test_library_is_read_and_missing_file_means_empty(tmp_path: Path) -> None:
    assert read_library(tmp_path) == {}
    (tmp_path / "library.json").write_text(
        json.dumps({"tracks": {"am_ap_92.mp3": {"bpm": 92, "first_beat_sec": 0.35}}}),
        encoding="utf-8",
    )
    lib = read_library(tmp_path)
    assert lib["am_ap_92.mp3"].bpm == 92 and lib["am_ap_92.mp3"].first_beat_sec == 0.35


BAD_LIBRARIES = [
    '{"tracks": {"a.mp3": {"bpm": 92}}}',
    '{"tracks": {"a.mp3": {"bpm": -1, "first_beat_sec": 0}}}',
    '{"tracks": {"a.mp3": {"bpm": "92", "first_beat_sec": 0}}}',
    "not json",
    "[]",
]


@pytest.mark.parametrize("body", BAD_LIBRARIES)
def test_bad_library_is_refused(tmp_path: Path, body: str) -> None:
    (tmp_path / "library.json").write_text(body, encoding="utf-8")
    with pytest.raises(MusicError, match=r"library\.json"):
        read_library(tmp_path)


# ─────────────── path allowlist ───────────────


def test_music_path_must_stay_inside_allowed_folders(tmp_path: Path) -> None:
    allowed, other = tmp_path / "nhac", tmp_path / "khac"
    allowed.mkdir()
    other.mkdir()
    (allowed / "a.mp3").write_bytes(b"x")
    (other / "b.mp3").write_bytes(b"x")
    (allowed / "n.txt").write_bytes(b"x")
    assert resolve_music_path("a.mp3", [allowed], EXT) == (allowed / "a.mp3").resolve()
    assert resolve_music_path(str(allowed / "a.mp3"), [allowed], EXT).name == "a.mp3"
    for bad in ("../khac/b.mp3", str(other / "b.mp3"), "n.txt", "missing.mp3"):
        with pytest.raises(MusicError, match="thư mục cho phép"):
            resolve_music_path(bad, [allowed], EXT)


# ─────────────── track_info: library first, then measurement ───────────────


@needs_ffmpeg
def test_library_bpm_wins_over_measurement(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", make_track(96.0, dur=30.0))
    (tmp_path / "library.json").write_text(
        json.dumps({"tracks": {"a.wav": {"bpm": 90, "first_beat_sec": 0.4}}}), encoding="utf-8"
    )
    info = track_info(tmp_path / "a.wav", read_library(tmp_path), CFG)
    assert info is not None and info.source == "library"
    assert (info.bpm, info.first_beat_sec) == (90, 0.4)
    assert info.duration_sec == pytest.approx(30.0, abs=0.1)


@needs_ffmpeg
def test_unknown_track_is_measured_and_noise_gives_none(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", make_track(96.0, dur=30.0))
    write_wav(tmp_path / "n.wav", np.random.default_rng(3).normal(scale=0.2, size=30 * SR))
    info = track_info(tmp_path / "a.wav", {}, CFG)
    assert info is not None and info.source == "detected" and info.bpm == pytest.approx(96, abs=0.3)
    assert track_info(tmp_path / "n.wav", {}, CFG) is None
