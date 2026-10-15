"""Tests: application/music_check.py + `music-check` (ticket 14/10/2026) — read only.

Real ffmpeg + numpy on small synthetic click tracks, the same way the plan step measures them.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.application.music_check import check_music_folder, format_report
from gani_dung_video.cli import main
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.music_analysis import MusicError
from gani_dung_video.infrastructure.settings import Settings

RULES = load_rules()
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


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    f = tmp_path / "nhac"
    f.mkdir()
    write_wav(f / "Am_ap_80.wav", click_track(80))  # declared, fits Kể chuyện nghề 15 s
    write_wav(f / "Sang_88.wav", click_track(88))  # measured, Sang trọng 15 s needs a cut
    write_wav(f / "Nhanh_104.wav", click_track(104))  # declared, Sang trọng 15 s is blocked
    write_wav(f / "Tieng_on.wav", np.random.default_rng(7).normal(scale=0.2, size=int(15 * SR)))
    (f / "ghi_chu.txt").write_text("không phải âm thanh", encoding="utf-8")
    (f / "library.json").write_text(json.dumps({"tracks": {
        "Am_ap_80.wav": {"bpm": 80, "first_beat_sec": 0.5},
        "Nhanh_104.wav": {"bpm": 104, "first_beat_sec": 0.5},
        "da_xoa.mp3": {"bpm": 90, "first_beat_sec": 0.2},
    }}), encoding="utf-8")  # fmt: skip
    return f


def snapshot(folder: Path) -> dict[str, tuple[int, int, bytes]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
            for p in folder.iterdir() if p.is_file()}  # fmt: skip


def verdict(chk: Any, style: str, duration: int, file: str) -> str:
    pair = next(p for p in chk.pairs if p.style == style and p.duration == duration)
    return str(pair.verdicts[file])


def test_tracks_get_their_bpm_source_and_length(folder: Path) -> None:
    chk = check_music_folder(folder, RULES)
    by = {t.file: t for t in chk.tracks}
    assert sorted(by) == ["Am_ap_80.wav", "Nhanh_104.wav", "Sang_88.wav", "Tieng_on.wav"]
    assert (by["Am_ap_80.wav"].bpm, by["Am_ap_80.wav"].source) == (80.0, "library")
    assert by["Sang_88.wav"].source == "measured"
    assert by["Sang_88.wav"].bpm == pytest.approx(88.0, abs=0.6)
    assert by["Tieng_on.wav"].source == "unknown" and by["Tieng_on.wav"].bpm is None
    assert by["Am_ap_80.wav"].duration_sec == pytest.approx(20.0, abs=0.2)
    assert by["Tieng_on.wav"].duration_sec == pytest.approx(15.0, abs=0.2)


def test_verdicts_are_the_planners_for_each_style_and_length(folder: Path) -> None:
    chk = check_music_folder(folder, RULES)
    assert verdict(chk, "ke_chuyen_nghe", 15, "Am_ap_80.wav") == "ok"
    assert verdict(chk, "sang_trong", 15, "Sang_88.wav") == "cắt"
    assert verdict(chk, "sang_trong", 15, "Nhanh_104.wav") == "chặn"
    assert verdict(chk, "sang_trong", 15, "Tieng_on.wav") == "lưới"
    assert verdict(chk, "tvc_cat_nhanh", 15, "Nhanh_104.wav") == "ok"  # TVC fits any tempo


def test_the_speech_style_is_not_listed_and_180_only_on_request(folder: Path) -> None:
    chk = check_music_folder(folder, RULES)
    assert "ban_hang_co_loi" not in {p.style for p in chk.pairs}
    assert {p.duration for p in chk.pairs} == {15, 30, 45, 60}
    with_180 = check_music_folder(folder, RULES, durations=(15, 180))
    assert {p.duration for p in with_180.pairs} == {15, 180}


def test_a_library_entry_without_a_file_and_a_non_audio_file(folder: Path) -> None:
    chk = check_music_folder(folder, RULES)
    assert chk.orphans == ["da_xoa.mp3"]
    assert "ghi_chu.txt" not in {t.file for t in chk.tracks}
    assert chk.has_library is True


def test_nothing_is_written_anywhere(folder: Path, tmp_path: Path) -> None:
    before = snapshot(folder)
    tree = sorted(p.name for p in tmp_path.rglob("*"))
    check_music_folder(folder, RULES)
    assert snapshot(folder) == before  # not library.json, not the audio files
    assert sorted(p.name for p in tmp_path.rglob("*")) == tree  # no cache, no new file


def test_a_missing_folder_and_a_broken_library_are_errors(tmp_path: Path) -> None:
    with pytest.raises(MusicError, match="không tồn tại"):
        check_music_folder(tmp_path / "khong_co", RULES)
    bad = tmp_path / "hong"
    bad.mkdir()
    (bad / "library.json").write_text("{khong phai json", encoding="utf-8")
    with pytest.raises(MusicError, match=r"library\.json"):
        check_music_folder(bad, RULES)


def test_an_empty_folder_says_so(tmp_path: Path) -> None:
    empty = tmp_path / "trong"
    empty.mkdir()
    text = format_report(check_music_folder(empty, RULES))
    assert "0 bài âm thanh" in text and "Không có file âm thanh" in text


def test_the_text_report_has_the_tracks_the_matrix_and_the_notes(folder: Path) -> None:
    text = format_report(check_music_folder(folder, RULES))
    assert "BÀI NHẠC" in text and "KHẢ NĂNG DÙNG THEO PHONG CÁCH × ĐỘ DÀI" in text
    assert "Sang_88.wav" in text and "measured" in text and "library" in text
    line = next(ln for ln in text.splitlines() if ln.startswith("Sang trọng") and "15s" in ln)
    assert "Am_ap_80" in line  # a track that fits is named in the "usable" column
    assert "da_xoa.mp3" in text  # the orphan
    assert "nửa hoặc gấp đôi" in text  # the measured-BPM caveat


def test_the_detail_table_has_one_grid_per_track(folder: Path) -> None:
    text = format_report(check_music_folder(folder, RULES), detail=True)
    assert "CHI TIẾT TỪNG BÀI" in text
    assert text.count("Kể chuyện nghề") >= 4  # the summary row set plus one row per track


# ─────────────── the command ───────────────


def test_cli_prints_the_report_and_writes_nothing(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = snapshot(folder)
    assert main(["music-check", str(folder)]) == 0
    cap = capsys.readouterr()
    assert "KHẢ NĂNG DÙNG" in cap.out and "Đang kiểm" in cap.err
    assert snapshot(folder) == before


def test_cli_without_a_folder_uses_the_machines_music_dir(
    folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GANI_DV_MUSIC_DIR", str(folder))
    assert main(["music-check"]) == 0
    assert str(folder) in capsys.readouterr().out


def test_cli_errors_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["music-check", str(tmp_path / "khong_co")]) == 2
    # no folder given and none configured on this machine: an explanation, not a crash
    monkeypatch.delenv("GANI_DV_MUSIC_DIR", raising=False)
    monkeypatch.setattr("gani_dung_video.cli.load_settings", lambda: Settings(_env_file=None))
    assert main(["music-check"]) == 2
