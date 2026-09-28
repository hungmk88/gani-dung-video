"""Integration tests: inventory a folder of tiny clips generated with ffmpeg (phase 2)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.cli import main
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.footage_fs import fingerprint, list_videos

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
RULES = load_rules()


def make_clip(path: Path, size: str = "160x90", audio: bool = True, extra: list[str] | None = None,
              color: str = "red") -> None:  # fmt: skip
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d=1"]  # fmt: skip
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *(extra or []), str(path)]
    subprocess.run(cmd, check=True, timeout=60)


@pytest.fixture(scope="module")
def footage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("Xuong")
    make_clip(d / "ngang.mp4")
    make_clip(d / "doc.MOV", size="90x160")
    make_clip(d / "cam-cam.mp4", audio=False, color="blue")
    # Tag HLG/BT.2020 in the H.264 VUI like an iPhone does; ffmpeg's -color_trc output
    # option is dropped for a synthetic lavfi source, so pass it to x264 directly.
    make_clip(
        d / "hdr.mp4",
        extra=["-x264-params", "transfer=arib-std-b67:colorprim=bt2020:colormatrix=bt2020nc"],
    )
    shutil.copy(d / "ngang.mp4", d / "ngang 2.mp4")  # true duplicate (iPhone/AirDrop style)
    shutil.copy(d / "ngang.mp4", d / "ngang (1).mp4")  # true duplicate (browser download style)
    make_clip(d / "canh.mp4", color="green")
    make_clip(d / "canh 2.mp4", color="yellow")  # same-name pattern, different content
    (d / "hong.mp4").write_bytes(b"not a video" * 100)
    (d / "ghi-chu.txt").write_text("không phải video", encoding="utf-8")
    (d / ".an.mp4").write_bytes(b"x")
    return d


def test_inventory_end_to_end(footage: Path) -> None:
    before = {p.name: p.stat().st_mtime_ns for p in footage.iterdir() if p.name != "edit"}
    inv, out = build_inventory(footage, RULES)
    clips = {c.file: c for c in inv.clips}

    assert set(clips) == {"canh 2.mp4", "canh.mp4", "cam-cam.mp4", "doc.MOV", "hdr.mp4",
                          "ngang.mp4"}  # fmt: skip
    assert {(d.file, d.duplicate_of) for d in inv.duplicates} == {
        ("ngang 2.mp4", "ngang.mp4"), ("ngang (1).mp4", "ngang.mp4"),
    }  # fmt: skip
    assert [s.file for s in inv.skipped] == ["hong.mp4"] and inv.skipped[0].reason

    assert clips["doc.MOV"].orientation == "doc" and clips["ngang.mp4"].orientation == "ngang"
    assert clips["hdr.mp4"].is_hdr and clips["hdr.mp4"].color_transfer == "arib-std-b67"
    assert not clips["ngang.mp4"].is_hdr
    assert clips["cam-cam.mp4"].has_audio_stream is False and clips["cam-cam.mp4"].audio_tracks == 0
    assert clips["ngang.mp4"].has_audio_stream and clips["ngang.mp4"].fps == 30.0
    assert 0.9 <= clips["ngang.mp4"].duration_sec <= 1.2
    assert inv.total_duration_sec == pytest.approx(sum(c.duration_sec for c in inv.clips))

    assert out == footage / "edit" / "inventory.json"
    assert json.loads(out.read_text(encoding="utf-8"))["rules_version"] == RULES["rules_version"]
    # Source files are never modified (spec §7.1); only edit/ is new.
    after = {p.name: p.stat().st_mtime_ns for p in footage.iterdir() if p.name != "edit"}
    assert after == before


def test_list_videos_skips_hidden_and_non_video(footage: Path) -> None:
    names = [p.name for p in list_videos(footage, RULES["footage"]["video_extensions"])]
    assert ".an.mp4" not in names and "ghi-chu.txt" not in names and "doc.MOV" in names


def test_fingerprint_reads_head_and_tail_only(tmp_path: Path) -> None:
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"H" * 10 + b"MIDDLE-1" + b"T" * 10)
    b.write_bytes(b"H" * 10 + b"MIDDLE-2" + b"T" * 10)  # differs only in the unread middle
    c.write_bytes(b"H" * 10 + b"MIDDLE-1" + b"X" * 10)  # differs in the tail
    assert fingerprint(a, 10) == fingerprint(b, 10)  # documented trade-off (no full hash)
    assert fingerprint(a, 10) != fingerprint(c, 10)
    small = tmp_path / "s"
    small.write_bytes(b"abc")
    assert fingerprint(small, 10) != fingerprint(a, 10)


def test_rerun_overwrites_inventory(footage: Path) -> None:
    _, out1 = build_inventory(footage, RULES)
    _, out2 = build_inventory(footage, RULES)
    assert out1 == out2 and not (footage / "edit" / "inventory.json.tmp").exists()


def test_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Không tìm thấy thư mục"):
        build_inventory(tmp_path / "khong-co", RULES)


def test_empty_folder_gives_empty_inventory(tmp_path: Path) -> None:
    inv, _ = build_inventory(tmp_path, RULES)
    assert (inv.clips, inv.duplicates, inv.skipped, inv.total_duration_sec) == ([], [], [], 0.0)


def test_cli_inventory(footage: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inventory", str(footage)]) == 0
    out = capsys.readouterr().out
    assert "6 clip" in out and "HDR (cần chuyển SDR khi render): 1" in out and "hong.mp4" in out


def test_cli_inventory_missing_folder(tmp_path: Path) -> None:
    assert main(["inventory", str(tmp_path / "khong-co")]) == 2


def test_cli_inventory_without_ffprobe(footage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert main(["inventory", str(footage)]) == 2


class _Done:
    def __init__(self, code: int, out: bytes, err: bytes = b"") -> None:
        self.returncode, self.stdout, self.stderr = code, out, err


@pytest.mark.parametrize(
    ("result", "fragment"),
    [
        (subprocess.TimeoutExpired("ffprobe", 5), "quá 5s"),
        (_Done(1, b"", b"line1\nmoov atom not found"), "moov atom not found"),
        (_Done(1, b""), "mã 1"),
        (_Done(0, b"{not json"), "không đọc được"),
        (_Done(0, b"[1, 2]"), "không đúng dạng"),
    ],
)
def test_probe_error_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: object, fragment: str
) -> None:
    from gani_dung_video.infrastructure import ffprobe as ff

    def fake_run(*_a: object, **_k: object) -> object:
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ff.ProbeError, match=fragment):
        ff.probe(tmp_path / "x.mp4", 5, exe="ffprobe")
