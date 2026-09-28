"""Integration tests (5a): drafts.json + plan.json + inventory → video, srt, cover, log."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.draft_service import DraftsFile
from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.render_service import build_render
from gani_dung_video.cli import main
from gani_dung_video.domain.beats import DraftSegment, SceneRef, snap_to_beats
from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import beats_of, compile_plan
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
RULES = load_rules()
OPTS: dict[str, Any] = {"phong_cach": "tvc_cat_nhanh", "muc_dich": "ban_hang", "do_dai_giay": 15,
                        "so_ban_nhap": 1, "nhac": "khong_nhac"}  # fmt: skip


def ff(out: Path, src: str, dur: float) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{src}:d={dur}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(out)], check=True, timeout=120)  # fmt: skip


@pytest.fixture(scope="module")
def folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("dung")
    ff(d / "a.mp4", "testsrc2=s=360x640:r=30", 8.0)
    ff(d / "b.mp4", "smptebars=s=640x360:r=30", 8.0)
    build_inventory(d, RULES)
    plan = compile_plan(VideoOptions(**OPTS), RULES, music=None, available_tags={"thanh_pham"})
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    refs = {"a_s0_0": SceneRef(clip="a.mp4", start=0, end=8, peak=2),
            "b_s0_0": SceneRef(clip="b.mp4", start=0, end=8, peak=4)}  # fmt: skip
    cuts: list[DraftSegment] = []
    texts = {plan.blocks[0].role: "Giòn ngon", plan.blocks[-1].role: "Nhắn tin đặt hàng"}
    for block in plan.blocks:  # 2-beat shots (1 if odd), alternating the two clips
        left = block.beats
        while left:
            n = min(2, left)
            first = left == block.beats
            cuts.append(
                DraftSegment(
                    block=block.role,
                    scene_id=["a_s0_0", "b_s0_0"][len(cuts) % 2],
                    beats=n,
                    text=texts.get(block.role, "") if first else "",
                )
            )
            left -= n
    snapped = snap_to_beats(cuts, beats_of(plan), refs)
    drafts = DraftsFile(model="fake", attempts=1, input_tokens=1, output_tokens=1, cost_vnd=1.0,
                        cap_vnd=20000, notes=[],
                        drafts=[SnappedDraft(draft="A", label="Tối ưu", segments=snapped,
                                             missing_shots=[], warnings=[])])  # fmt: skip
    (d / "edit" / "drafts.json").write_text(drafts.model_dump_json(), encoding="utf-8")
    return d


def duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return float(r.stdout)


def test_preview_render_writes_video_subtitles_cover_and_log(folder: Path) -> None:
    [res] = build_render(folder, preview=True, rules=RULES)
    out = folder / "edit" / "render" / "A"
    assert res.video == out / "nhap_A_540p.mp4" and res.video.is_file()
    # TVC without music: 12 beats on the 1.3 s grid = 15.6 s, plus the 2 s end card
    assert res.duration_sec == pytest.approx(17.6)
    assert duration(res.video) == pytest.approx(res.duration_sec, abs=0.1)
    srt = (out / "phude.srt").read_text(encoding="utf-8")
    assert "Giòn ngon" in srt and "Nhắn tin đặt hàng" in srt
    assert (out / "cover.jpg").stat().st_size > 1000
    log = json.loads((out / "render_log.json").read_text(encoding="utf-8"))
    assert log["draft"] == "A" and log["options"]["style"] == "tvc_cat_nhanh"
    assert log["music"] is None and [s["source"] for s in log["shots"]][:2] == ["a.mp4", "b.mp4"]
    assert log["brand_placeholders"] == ["logo", "zalo"]


def test_unknown_draft_letter_is_refused(folder: Path) -> None:
    with pytest.raises(ConfigError, match="Z"):
        build_render(folder, draft="Z", preview=True, rules=RULES)


def test_drafts_json_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="draft"):
        build_render(tmp_path, preview=True, rules=RULES)


def test_cli_render_preview(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render", str(folder), "--preview", "--ban", "A"]) == 0
    out = capsys.readouterr().out
    assert "nhap_A_540p.mp4" in out and "17.6s" in out
