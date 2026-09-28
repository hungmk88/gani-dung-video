"""Tests (5b): infrastructure/media_probe.py on a synthetic clip with a black gap and a freeze."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.media_probe import (
    FontMetrics,
    ProbeFailed,
    measure,
    probe_media,
)

QC = load_rules()["qc"]
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("qc") / "mau.mp4"
    graph = ("testsrc2=s=320x568:r=30:d=2[a];color=c=black:s=320x568:r=30:d=0.5[b];"
             "testsrc2=s=320x568:r=30:d=0.04,tpad=stop_mode=clone:stop_duration=1.5[c];"
             "testsrc2=s=320x568:r=30:d=1[d];[a][b][c][d]concat=n=4:v=1")  # fmt: skip
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    graph, "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                    "-ac", "2", "-shortest", str(out)], check=True, timeout=120)  # fmt: skip
    return out


@needs_ffmpeg
def test_probe_reports_size_fps_frames_and_audio(sample: Path) -> None:
    f = probe_media(sample, 120)
    assert (f.width, f.height, f.fps, f.video_codec, f.pix_fmt) == (320, 568, 30.0, "h264",
                                                                    "yuv420p")  # fmt: skip
    assert f.frames == pytest.approx(round(f.duration * 30), abs=1)
    assert (f.audio_codec, f.sample_rate, f.channels) == ("aac", 48000, 2)


@needs_ffmpeg
def test_one_pass_finds_the_black_gap_the_freeze_and_the_loudness(sample: Path) -> None:
    m = measure(sample, QC, duration=5.0, timeout=120)
    [(start, end)] = m.blacks
    assert start == pytest.approx(2.0, abs=0.05) and end == pytest.approx(2.5, abs=0.05)
    assert len(m.freezes) == 1 and m.freezes[0][1] - m.freezes[0][0] > 1.0
    assert -30 < m.lufs < -10 and m.true_peak_db < 0


@needs_ffmpeg
def test_unreadable_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / "x.mp4").write_bytes(b"not a video")
    with pytest.raises(ProbeFailed):
        probe_media(tmp_path / "x.mp4", 30)


def test_text_width_follows_font_weight_and_size() -> None:
    fm = FontMetrics()
    bold = fm.width("Bò viên GANI tròn đều", "Be Vietnam Pro ExtraBold", 72)
    light = fm.width("Bò viên GANI tròn đều", "Be Vietnam Pro Light", 72)
    assert 500 < bold < 1100 and light < bold
    assert fm.width("Bò viên GANI tròn đều", "Be Vietnam Pro ExtraBold", 36) == pytest.approx(
        bold / 2)  # fmt: skip
    assert fm.width("", "Be Vietnam Pro Light", 72) == 0


def test_unknown_font_is_refused() -> None:
    with pytest.raises(ProbeFailed, match="font"):
        FontMetrics().width("x", "Comic Sans", 72)
