"""Tests: infrastructure/clip_model.py (3c).

Checksum/download/device logic runs everywhere. Tests touching real weights skip when torch
or the files are missing; the full CLIP test (1.7 GB download) runs only with
GANI_DV_TEST_CLIP=1 — on the Mac Mini, or once on a PC with the [ai] extra.
"""

from __future__ import annotations

import hashlib
import io
import os
import urllib.error
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.infrastructure import clip_model
from gani_dung_video.infrastructure.clip_model import (
    MODELS_DIR,
    SemanticError,
    aesthetic_layers,
    ensure_aesthetic_file,
    pick_device,
)
from gani_dung_video.infrastructure.config_files import load_rules

CFG = load_rules()["semantic"]
PAYLOAD = b"fake weights"


def cfg_for(sha: str) -> dict[str, Any]:
    return {**CFG, "aesthetic_file": "head.pth", "aesthetic_sha256": sha}


def test_device_auto_prefers_apple_gpu_never_cuda() -> None:
    assert pick_device("auto", mps_available=True) == "mps"
    assert pick_device("auto", mps_available=False) == "cpu"
    assert pick_device("cuda", mps_available=False) == "cuda"  # only when asked


def test_checksum_mismatch_is_refused(tmp_path: Path) -> None:
    (tmp_path / "head.pth").write_bytes(PAYLOAD)
    with pytest.raises(SemanticError, match="SHA-256"):
        ensure_aesthetic_file(cfg_for("0" * 64), tmp_path)


def test_missing_file_is_downloaded_then_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []

    def fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        seen.append(timeout)
        return io.BytesIO(PAYLOAD)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    path = ensure_aesthetic_file(cfg_for(hashlib.sha256(PAYLOAD).hexdigest()), tmp_path)
    assert path.read_bytes() == PAYLOAD and seen == [clip_model.DOWNLOAD_TIMEOUT_SEC]


def test_download_failure_is_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(url: str, timeout: float) -> io.BytesIO:
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    with pytest.raises(SemanticError, match="Không tải được"):
        ensure_aesthetic_file(cfg_for("0" * 64), tmp_path)
    assert not (tmp_path / "head.pth").exists()


def test_head_layers_are_ordered_and_unknown_keys_refused() -> None:
    state = {"layers.2.weight": np.ones((1, 2)), "layers.2.bias": np.zeros(1),
             "layers.0.weight": np.ones((2, 3)), "layers.0.bias": np.zeros(2)}  # fmt: skip
    assert [w.shape for w, _ in aesthetic_layers(state)] == [(2, 3), (1, 2)]
    with pytest.raises(SemanticError, match="khóa lạ"):
        aesthetic_layers({"model.fc.weight": np.ones(1)})


def test_real_laion_head_loads_safely() -> None:
    torch = pytest.importorskip("torch")
    if not (MODELS_DIR / CFG["aesthetic_file"]).is_file():
        pytest.skip("chưa tải bộ chấm LAION vào models/")
    path = ensure_aesthetic_file(CFG)
    state = torch.load(path, map_location="cpu", weights_only=True)
    layers = aesthetic_layers(state)
    assert layers[0][0].shape == (1024, 768) and layers[-1][0].shape == (1, 16)


@pytest.mark.skipif(os.environ.get("GANI_DV_TEST_CLIP") != "1", reason="đặt GANI_DV_TEST_CLIP=1")
def test_real_clip_backend_end_to_end() -> None:
    pytest.importorskip("open_clip")
    backend = clip_model.load_clip_backend(CFG)
    red = np.zeros((224, 224, 3), dtype=np.uint8)
    red[..., 0] = 255
    img = backend.embed_images([red])
    txt = backend.embed_texts(["a plain red square", "a plain blue square"])
    assert img.shape == (1, 768) and np.allclose(np.linalg.norm(img, axis=1), 1.0, atol=1e-3)
    sims = img @ txt.T
    assert sims[0, 0] > sims[0, 1]
    score = float(img[0] @ backend.aesthetic_w + backend.aesthetic_b)
    assert 0.0 < score < 10.0
