"""Tests: infrastructure/music_index.py — the music folder as the web UI sees it (ticket 14/10).

Real ffmpeg + numpy on small synthetic click tracks, the same way the plan step measures them.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.infrastructure import music_index as mi
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.music_analysis import MusicError, read_library
from gani_dung_video.infrastructure.music_index import MusicIndex

CFG = load_rules()["music_analysis"]
SR = 8000


def click_track(bpm: float, dur: float = 20.0, first: float = 0.5) -> np.ndarray:
    period = 60.0 / bpm
    x = np.zeros(int(dur * SR))
    t = np.arange(int(0.3 * SR)) / SR
    burst = np.sin(2 * np.pi * (55 + 90 * np.exp(-25 * t)) * t) * np.exp(-9 * t)
    k, at = 0, first
    while at < dur - 0.3:
        i = int(at * SR)
        x[i : i + len(burst)] += burst * (1.0 if k % 4 == 0 else 0.55)
        k, at = k + 1, first + (k + 1) * period
    x += 0.01 * np.random.default_rng(1).normal(size=len(x))
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def library(folder: Path, tracks: dict[str, dict[str, Any]]) -> None:
    (folder / "library.json").write_text(json.dumps({"tracks": tracks}), encoding="utf-8")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    f = tmp_path / "nhac"
    f.mkdir()
    return f


def index_of(folder: Path, tmp_path: Path) -> MusicIndex:
    return MusicIndex(folder, tmp_path / "jobs" / "music-cache.json", CFG)


def settle(idx: MusicIndex) -> None:
    assert idx.wait_idle(60.0)


# ─────────────── listing ───────────────


def test_a_library_track_has_its_bpm_at_once_with_title_and_rights(
    folder: Path, tmp_path: Path
) -> None:
    write_wav(folder / "lib.wav", click_track(90))
    library(folder, {"lib.wav": {"bpm": 90, "first_beat_sec": 0.4, "title": "Ấm áp",
                                 "rights": "royalty_free"}})  # fmt: skip
    tracks, _pending = index_of(folder, tmp_path).snapshot()
    [t] = tracks
    assert (t.file, t.bpm, t.bpm_source, t.first_beat_sec) == ("lib.wav", 90.0, "library", 0.4)
    assert (t.title, t.rights) == ("Ấm áp", "royalty_free")


def test_title_and_rights_default_when_library_json_does_not_say(
    folder: Path, tmp_path: Path
) -> None:
    write_wav(folder / "am_ap_92.wav", click_track(92))
    library(folder, {"am_ap_92.wav": {"bpm": 92, "first_beat_sec": 0.35}})  # the old format
    [t], _ = index_of(folder, tmp_path).snapshot()
    assert (t.title, t.rights) == ("am_ap_92", "thu_vien_noi_bo")


def test_an_unlisted_track_is_pending_then_measured_by_the_worker(
    folder: Path, tmp_path: Path
) -> None:
    write_wav(folder / "t92.wav", click_track(92))
    idx = index_of(folder, tmp_path)
    [first], _pending = idx.snapshot()
    assert first.bpm_source in ("pending", "measured")  # the worker may already be done
    settle(idx)
    [t], pending = idx.snapshot()
    assert t.bpm_source == "measured" and t.bpm == pytest.approx(92.0, abs=0.5)
    assert t.duration_sec == pytest.approx(20.0, abs=0.2) and pending == 0


def test_the_worker_also_fills_in_the_length_of_listed_tracks(folder: Path, tmp_path: Path) -> None:
    write_wav(folder / "lib.wav", click_track(90))
    library(folder, {"lib.wav": {"bpm": 90, "first_beat_sec": 0.4}})
    idx = index_of(folder, tmp_path)
    idx.snapshot()
    settle(idx)
    [t], pending = idx.snapshot()
    assert t.bpm_source == "library" and t.bpm == 90.0  # the person's number, not the machine's
    assert t.duration_sec == pytest.approx(20.0, abs=0.2) and pending == 0


def test_a_track_without_a_clear_beat_is_unknown_but_has_a_length(
    folder: Path, tmp_path: Path
) -> None:
    noise = np.random.default_rng(7).normal(scale=0.2, size=int(15 * SR))
    write_wav(folder / "noise.wav", noise)
    idx = index_of(folder, tmp_path)
    idx.snapshot()
    settle(idx)
    [t], pending = idx.snapshot()
    assert t.bpm_source == "unknown" and t.bpm is None
    assert t.duration_sec == pytest.approx(15.0, abs=0.2) and pending == 0


def test_not_audio_hidden_and_nested_files_are_not_listed(folder: Path, tmp_path: Path) -> None:
    write_wav(folder / "ok.wav", click_track(100))
    (folder / "ghi_chu.txt").write_text("x", encoding="utf-8")
    write_wav(folder / ".an.wav", click_track(100))
    (folder / "thu_muc_con").mkdir()
    write_wav(folder / "thu_muc_con" / "trong.wav", click_track(100))
    tracks, _ = index_of(folder, tmp_path).snapshot()
    assert [t.file for t in tracks] == ["ok.wav"]


def test_at_most_max_tracks_are_listed(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mi, "MAX_TRACKS", 3)
    for i in range(5):
        (folder / f"{i}.wav").write_bytes(b"RIFF")
    tracks, _ = index_of(folder, tmp_path).snapshot()
    assert len(tracks) == 3


def test_a_broken_library_json_is_an_error_not_an_empty_list(folder: Path, tmp_path: Path) -> None:
    (folder / "library.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(MusicError, match=r"library\.json"):
        index_of(folder, tmp_path).snapshot()


# ─────────────── cache ───────────────


def test_the_cache_survives_a_restart_and_is_not_measured_twice(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_wav(folder / "t92.wav", click_track(92))
    one = index_of(folder, tmp_path)
    one.snapshot()
    settle(one)
    assert (tmp_path / "jobs" / "music-cache.json").is_file()

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("measured again")

    monkeypatch.setattr(mi, "track_info", boom)
    monkeypatch.setattr(mi, "track_duration", boom)
    [t], pending = index_of(folder, tmp_path).snapshot()  # a fresh object, same cache file
    assert t.bpm_source == "measured" and t.bpm == pytest.approx(92.0, abs=0.5) and pending == 0


def test_a_changed_file_is_measured_again(folder: Path, tmp_path: Path) -> None:
    f = folder / "t.wav"
    write_wav(f, click_track(92))
    idx = index_of(folder, tmp_path)
    idx.snapshot()
    settle(idx)
    write_wav(f, click_track(110, dur=25.0))  # same name, other content
    [t], _ = idx.snapshot()
    assert t.bpm_source == "pending"  # the old answer is not trusted
    settle(idx)
    [t], _ = idx.snapshot()
    assert t.bpm == pytest.approx(110.0, abs=0.6) and t.duration_sec == pytest.approx(25.0, abs=0.2)


def test_a_track_removed_from_the_library_is_measured_not_kept_as_declared(
    folder: Path, tmp_path: Path
) -> None:
    write_wav(folder / "lib.wav", click_track(100))
    library(folder, {"lib.wav": {"bpm": 77, "first_beat_sec": 0.4}})
    idx = index_of(folder, tmp_path)
    idx.snapshot()
    settle(idx)
    (folder / "library.json").unlink()
    [t], _ = idx.snapshot()
    assert t.bpm_source == "pending"  # 77 was a person's number, never a measurement
    settle(idx)
    [t], _ = idx.snapshot()
    assert t.bpm_source == "measured" and t.bpm == pytest.approx(100.0, abs=0.6)


def test_the_music_folder_and_library_json_are_never_written(folder: Path, tmp_path: Path) -> None:
    write_wav(folder / "t.wav", click_track(92))
    library(folder, {})
    before = sorted((p.name, p.stat().st_mtime_ns) for p in folder.iterdir())
    idx = index_of(folder, tmp_path)
    idx.snapshot()
    settle(idx)
    assert sorted((p.name, p.stat().st_mtime_ns) for p in folder.iterdir()) == before


# ─────────────── library.json format (migration) ───────────────


def test_the_old_library_format_still_loads_and_the_new_keys_are_checked(folder: Path) -> None:
    library(folder, {"a.mp3": {"bpm": 92, "first_beat_sec": 0.35}})
    assert read_library(folder)["a.mp3"].title is None
    library(
        folder,
        {"a.mp3": {"bpm": 92, "first_beat_sec": 0.35, "title": "A", "rights": "mua_ban_quyen"}},
    )
    assert read_library(folder)["a.mp3"].rights == "mua_ban_quyen"
    for bad in ({"rights": "free"}, {"title": ""}, {"title": "x" * 121}, {"extra": 1}):
        library(folder, {"a.mp3": {"bpm": 92, "first_beat_sec": 0.35, **bad}})
        with pytest.raises(MusicError):
            read_library(folder)
