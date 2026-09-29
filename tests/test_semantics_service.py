"""Integration tests (3c): frame grabbing + semantics use case with a FAKE CLIP backend.

No torch needed: the fake embeds a frame as its quadrant colour means, so identical shots
give identical vectors and a cut gives a different one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.application import semantics_service
from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.scenes_service import build_scenes
from gani_dung_video.application.semantics_service import build_semantics
from gani_dung_video.cli import main
from gani_dung_video.domain.scenes import Scene, Segment
from gani_dung_video.domain.semantics import ClipBackend, RgbFrame, Vectors
from gani_dung_video.infrastructure.clip_model import SemanticError
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.video_frames import VisionError, grab_frames

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
RULES = load_rules()
LABELS = list(RULES["semantic"]["labels"])
DIM = 12


def ff(out: Path, graph: str, dur: float) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex",
                    graph, "-t", str(dur), "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(out)], check=True, timeout=120)  # fmt: skip


def unit(*xs: float) -> Vectors:
    return _norm(np.asarray(xs))


def _norm(m: np.ndarray) -> Vectors:
    return np.asarray(m / np.linalg.norm(m, axis=-1, keepdims=True), dtype=np.float32)


def fake_images(frames: Sequence[RgbFrame]) -> Vectors:
    rows = []
    for f in frames:
        h, w = f.shape[0] // 2, f.shape[1] // 2
        quads = [f[:h, :w], f[:h, w:], f[h:, :w], f[h:, w:]]
        rows.append(np.concatenate([q.reshape(-1, 3).mean(axis=0) for q in quads]) - 128.0)
    return _norm(np.stack(rows))


BARS: list[Vectors] = []  # embedding of the SMPTE-bars frame, filled by the `clips` fixture
KHAC_PROMPTS = set(RULES["semantic"]["labels"]["khac"])


def fake_texts(texts: Sequence[str]) -> Vectors:
    """Random unit vector per prompt, except that "khac" prompts point at the bars frame."""
    rows = [
        BARS[0]
        if t in KHAC_PROMPTS
        else np.random.default_rng(int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)).normal(
            size=DIM
        )
        for t in texts
    ]
    return _norm(np.stack(rows))


def fake_backend(calls: list[int] | None = None) -> ClipBackend:
    def images(frames: Sequence[RgbFrame]) -> Vectors:
        if calls is not None:
            calls.append(len(frames))
        return fake_images(frames)

    return ClipBackend(name="fake-clip", embed_images=images, embed_texts=fake_texts,
                       aesthetic_w=np.full(DIM, 0.5), aesthetic_b=4.0)  # fmt: skip


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("ngu_nghia")
    ff(d / "dai.mp4", "testsrc2=s=360x640:r=30", 12.0)  # one still take → several segments
    ff(d / "cat.mp4", "testsrc2=s=360x640:r=30:d=2[a];smptebars=s=360x640:r=30:d=2[b];"
                      "[a][b]concat=n=2:v=1", 4.0)  # fmt: skip
    build_inventory(d, RULES)
    build_scenes(d, RULES)
    BARS[:] = [fake_images(grab_frames(d / "cat.mp4", [3.0], 180))[0]]
    return d


def _scenes(folder: Path) -> dict[str, Any]:
    # Any: raw JSON read back from scenes.json, checked field by field in the asserts.
    data: dict[str, Any] = json.loads((folder / "edit" / "scenes.json").read_text(encoding="utf-8"))
    return data


def test_grab_frames_at_times_in_rgb(clips: Path) -> None:
    a, b = grab_frames(clips / "cat.mp4", [0.5, 3.0], 180)
    assert a.shape == (320, 180, 3) and a.dtype == np.uint8
    assert np.abs(a.astype(int) - b.astype(int)).mean() > 10  # before vs after the cut


def test_grab_frames_unreadable(tmp_path: Path) -> None:
    (tmp_path / "x.mp4").write_bytes(b"not video")
    with pytest.raises(VisionError):
        grab_frames(tmp_path / "x.mp4", [0.0], 180)


def test_semantics_end_to_end(clips: Path) -> None:
    result, out = build_semantics(clips, RULES, backend=fake_backend())
    assert out == clips / "edit" / "scenes.json" and result.semantic_model == "fake-clip"
    data = _scenes(clips)
    for sc in data["scenes"]:
        assert list(sc["label_probs"]) == LABELS
        assert sum(sc["label_probs"].values()) == pytest.approx(1.0, abs=1e-3)
        for segs in sc["segments"].values():
            assert all(g["aesthetic"] is not None for g in segs)
    dai = [p for p in data["similar"] if p["clip"] == "dai.mp4"]
    assert dai and all(p["similarity"] >= 0.95 for p in dai)  # same still shot → warned
    assert not [p for p in data["similar"] if p["clip"] == "cat.mp4"]


def test_similar_warning_is_one_per_pair_not_one_per_style(clips: Path) -> None:
    build_semantics(clips, RULES, backend=fake_backend())
    dai = [p for p in _scenes(clips)["similar"] if p["clip"] == "dai.mp4"]
    keys = [(p["peak_a"], p["peak_b"]) for p in dai]
    assert len(keys) == len(set(keys))  # the same two moments are reported once
    assert all(p["peak_a"] < p["peak_b"] and len(p["styles"]) >= 1 for p in dai)


def test_same_two_moments_in_several_styles_are_one_warning() -> None:
    vec = {1.0: unit(1, 0, 0), 9.0: unit(0.99, 0.05, 0)}

    def seg(peak: float) -> Segment:
        return Segment(start=peak - 1, end=peak + 1, peak=peak)

    styles = {"tvc_cat_nhanh": [seg(9.0), seg(1.0)], "sang_trong": [seg(1.0), seg(9.0)],
              "review_mon_an": [seg(1.0)]}  # fmt: skip
    sc = Scene(clip="c.mp4", index=0, start=0.0, end=12.0, sharpness=100, motion="tinh",
               jitter=0, consistency=1, segments=styles)  # fmt: skip
    (pair,) = semantics_service._similar("c.mp4", [sc], vec, 0.95)
    assert (pair.peak_a, pair.peak_b) == (1.0, 9.0)
    assert pair.styles == ["sang_trong", "tvc_cat_nhanh"]


def test_khac_segments_are_excluded_from_usable_counts(clips: Path) -> None:
    result, _ = build_semantics(clips, RULES, backend=fake_backend())
    bars = next(s for s in result.scenes if s.clip == "cat.mp4" and s.start >= 2.0)
    assert bars.labels[:1] == ["khac"]
    segs = [g for lst in bars.segments.values() for g in lst]
    assert segs and all(g.labels[:1] == ["khac"] for g in segs)
    still = [s for s in result.scenes if s.clip == "dai.mp4"]
    assert still and all(s.labels[:1] != ["khac"] for s in still)  # only the bars are "khac"
    for style in RULES["styles"]:  # usable = every segment of a clean scene except khac ones
        expected = sum(
            g.labels[:1] != ["khac"]
            for s in result.scenes if not s.blurry
            for g in s.segments.get(style, [])
        )  # fmt: skip
        assert result.usable_by_style[style] == expected
    assert result.usable_by_style["tvc_cat_nhanh"] < sum(
        len(s.segments["tvc_cat_nhanh"]) for s in result.scenes
    )


def test_rules_carry_the_agreed_thresholds_and_labels() -> None:
    cfg = RULES["semantic"]
    assert cfg["similar_warn"] == 0.95
    assert {"nguoi_thao_tac", "thiet_bi", "khu_vuc", "thanh_pham", "mon_an", "mat_cat",
            "chung_nhan", "nguoi_noi", "khac"} == set(cfg["labels"])  # fmt: skip


def test_rerun_reuses_cached_vectors(clips: Path) -> None:
    build_semantics(clips, RULES, backend=fake_backend())
    calls: list[int] = []
    build_semantics(clips, RULES, backend=fake_backend(calls))
    assert calls == []


def test_requires_scenes(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="scenes"):
        build_semantics(tmp_path, RULES, backend=fake_backend())


def test_cli_semantics(
    clips: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semantics_service, "load_clip_backend", lambda cfg: fake_backend())
    assert main(["semantics", str(clips)]) == 0
    out = capsys.readouterr().out
    assert "Model: fake-clip" in out and "Cặp thời điểm giống nhau" in out


def test_cli_semantics_without_ai_extra(clips: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(cfg: object) -> ClipBackend:
        raise SemanticError("Chưa cài nhóm [ai]")

    monkeypatch.setattr(semantics_service, "load_clip_backend", missing)
    assert main(["semantics", str(clips)]) == 2


def test_embedding_logs_a_progress_line_per_batch_for_the_job_watchdog(
    clips: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Phase 6c: the daemon watches the child's output; CLIP is silent for minutes otherwise."""
    fresh = tmp_path / "copy"
    shutil.copytree(clips, fresh, ignore=shutil.ignore_patterns("clip"))  # no cached vectors
    rules = {**RULES, "semantic": {**RULES["semantic"], "progress_batch": 2}}
    calls: list[int] = []
    with caplog.at_level(logging.INFO, logger=semantics_service.logger.name):
        build_semantics(fresh, rules, fake_backend(calls))
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[clip")]
    assert lines and max(calls) <= 2  # embedded in batches of at most 2 frames
    assert lines[0].startswith("[clip 1/2] ") and any(x.startswith("[clip 2/2] ") for x in lines)
    for clip_no in (1, 2):
        mine = [x for x in lines if x.startswith(f"[clip {clip_no}/2]")]
        counts = [tuple(int(n) for n in x.rsplit(" ", 1)[1].split("/")) for x in mine]
        assert counts == sorted(counts) and counts[-1][0] == counts[-1][1]  # ends at n/n
        assert all("nhúng khung" in x for x in mine)
