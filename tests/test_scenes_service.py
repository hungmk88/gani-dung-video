"""Integration tests: OpenCV analysis + scenes use case on ffmpeg-generated clips (3b)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.scenes_service import build_scenes
from gani_dung_video.cli import main
from gani_dung_video.domain.scenes import FrameStats
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.video_frames import VisionError, analyse_clip

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
RULES = load_rules()
SIZE = "360x640"


def ff(out: Path, graph: str, dur: float = 4.0) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex",
                    graph, "-t", str(dur), "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(out)], check=True, timeout=120)  # fmt: skip


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("canh")
    src = f"testsrc2=s={SIZE}:r=30"
    # Hard cut at 2 s: textured test pattern → SMPTE colour bars.
    ff(d / "cat.mp4", f"{src}:d=2[a];smptebars=s={SIZE}:r=30:d=2[b];[a][b]concat=n=2:v=1")
    ff(d / "tinh.mp4", src)
    ff(d / "mo.mp4", f"{src},boxblur=12:2")
    # Steady pan: scroll the pattern left at constant speed.
    ff(d / "lia.mp4", f"{src},scroll=h=0.01")
    # Hand shake: crop window jumping back and forth every frame.
    ff(d / "rung.mp4", "testsrc2=s=440x720:r=30,crop=360:640:"
                       "x='40+35*sin(n*2.3)':y='40+35*cos(n*3.1)'")  # fmt: skip
    (d / "hong.mp4").write_bytes(b"x" * 64)
    build_inventory(d, RULES)
    return d


# Any: raw JSON read back from scenes.json, checked field by field in the asserts.
def _by_clip(folder: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads((folder / "edit" / "scenes.json").read_text(encoding="utf-8"))
    out: dict[str, list[dict[str, Any]]] = {}
    for s in data["scenes"]:
        out.setdefault(s["clip"], []).append(s)
    return out


def test_analyse_clip_samples_at_5_fps_in_display_size(clips: Path) -> None:
    st = analyse_clip(clips / "tinh.mp4", 5, 360)
    assert 19 <= len(st) <= 21  # 4 s × 5 fps
    assert st[0].cut_score == 0.0 and st[1].t == pytest.approx(0.2, abs=0.05)


def test_analyse_clip_unreadable(tmp_path: Path) -> None:
    (tmp_path / "x.mp4").write_bytes(b"not video")
    with pytest.raises(VisionError):
        analyse_clip(tmp_path / "x.mp4", 5, 360)


def test_scenes_end_to_end(clips: Path) -> None:
    result, out = build_scenes(clips, RULES)
    by = _by_clip(clips)
    cut = by["cat.mp4"]
    assert len(cut) == 2 and cut[1]["start"] == pytest.approx(2.0, abs=0.25)
    assert by["tinh.mp4"][0]["motion"] == "tinh"
    assert by["lia.mp4"][0]["motion"] == "lia" and not by["lia.mp4"][0]["shaky"]
    assert by["rung.mp4"][0]["motion"] == "rung" and by["rung.mp4"][0]["shaky"]
    assert by["rung.mp4"][0]["segments"]["tvc_cat_nhanh"] == []  # nothing usable in a shaky take
    assert len(by["tinh.mp4"][0]["segments"]["tvc_cat_nhanh"]) == 1  # 4 s: no room for a 2nd
    assert by["mo.mp4"][0]["blurry"] and not by["tinh.mp4"][0]["blurry"]
    assert "hong.mp4" not in by  # skipped by inventory already
    assert set(result.usable_by_style) == set(RULES["styles"])
    assert out == clips / "edit" / "scenes.json"


def test_rerun_uses_cache_and_rules_apply_without_decoding(clips: Path) -> None:
    build_scenes(clips, RULES)
    calls: list[str] = []

    def spy(path: Path, fps: float, width: int) -> list[FrameStats]:
        calls.append(path.name)
        return analyse_clip(path, fps, width)

    strict = {**RULES, "vision": {**RULES["vision"], "blur_floor": 1e9}}
    r, _ = build_scenes(clips, strict, analyse=spy)
    assert calls == [] and all(s.blurry for s in r.scenes)
    assert all(v == 0 for v in r.usable_by_style.values())


def test_changed_sampling_invalidates_cache(clips: Path) -> None:
    build_scenes(clips, RULES)
    calls: list[str] = []

    def spy(path: Path, fps: float, width: int) -> list[FrameStats]:
        calls.append(path.name)
        return analyse_clip(path, fps, width)

    other = {**RULES, "vision": {**RULES["vision"], "sample_fps": 2}}
    build_scenes(clips, other, analyse=spy)
    assert sorted(calls) == ["cat.mp4", "lia.mp4", "mo.mp4", "rung.mp4", "tinh.mp4"]


def test_unreadable_clip_is_skipped(clips: Path) -> None:
    def broken(path: Path, fps: float, width: int) -> list[FrameStats]:
        if path.name == "cat.mp4":
            raise VisionError("hỏng")
        return analyse_clip(path, fps, width)

    other = {**RULES, "vision": {**RULES["vision"], "analysis_width": 200}}  # force re-analysis
    r, _ = build_scenes(clips, other, analyse=broken)
    assert "cat.mp4" not in {s.clip for s in r.scenes} and r.scenes


def test_requires_inventory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="inventory"):
        build_scenes(tmp_path, RULES)


def test_cli_scenes(clips: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenes", str(clips)]) == 0
    out = capsys.readouterr().out
    assert "Đã tách:" in out and "Rung cả cảnh: 1" in out and "Lia máy: 1" in out
    assert "Cửa sổ 2s rung: 2/10" in out


def test_cli_scenes_without_inventory(tmp_path: Path) -> None:
    assert main(["scenes", str(tmp_path)]) == 2
