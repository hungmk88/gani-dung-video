"""CLIP ViT-L/14 (OpenAI weights, via open_clip) + LAION aesthetic predictor v2 (phase 3c).

torch/open_clip are the optional `[ai]` extra and are imported inside load_clip_backend()
only, so the rest of the app and the test suite run on machines without them.

Weights: CLIP comes from the Hugging Face cache (downloaded once, ~1.7 GB). The LAION head
(3.7 MB) is downloaded into models/ and checked against the SHA-256 pinned in rules.json
before it is unpickled, and then loaded with torch.load(weights_only=True).
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gani_dung_video.domain.semantics import ClipBackend, RgbFrame, Vectors, collapse_linear_head
from gani_dung_video.infrastructure.config_files import CONFIG_DIR

logger = logging.getLogger(__name__)
MODELS_DIR = CONFIG_DIR.parent / "models"
DOWNLOAD_TIMEOUT_SEC = 60
_BATCH = 16
_LAYER_KEY = re.compile(r"^layers\.(\d+)\.(weight|bias)$")


class SemanticError(RuntimeError):
    """CLIP/LAION cannot be loaded (message in Vietnamese, says what to do)."""


def pick_device(preference: str, mps_available: bool) -> str:
    """Resolve the configured device: "auto" = Apple GPU (MPS) when present, else CPU.

    CUDA only when asked explicitly: the Dell's GeForce MX130 is too old for current CUDA
    builds (same finding as the speech device note).
    """
    if preference != "auto":
        return preference
    return "mps" if mps_available else "cpu"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_aesthetic_file(cfg: Mapping[str, Any], models_dir: Path = MODELS_DIR) -> Path:
    """Return the verified LAION head file, downloading it once if missing.

    Raises:
        SemanticError: download failed or the file does not match the pinned SHA-256.
    """
    path = models_dir / str(cfg["aesthetic_file"])
    if not path.is_file():
        models_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        try:
            url = str(cfg["aesthetic_url"])
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SEC) as r:
                tmp.write_bytes(r.read())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SemanticError(f"Không tải được bộ chấm thẩm mỹ LAION: {e}") from e
        tmp.replace(path)
    if sha256_of(path) != str(cfg["aesthetic_sha256"]):
        raise SemanticError(
            f"{path.name} sai mã SHA-256 (file hỏng hoặc bị thay) — xóa file để tải lại; "
            "không nạp file lạ."
        )
    return path


def aesthetic_layers(state: Mapping[str, Any]) -> list[tuple[NDArray[Any], NDArray[Any]]]:
    """Ordered (weight, bias) pairs of the LAION MLP state dict (keys layers.N.weight/bias).

    Any: values are torch tensors or numpy arrays; both expose a numpy view via np.asarray.
    """
    idx: dict[int, dict[str, NDArray[Any]]] = {}
    for key, val in state.items():
        m = _LAYER_KEY.match(key)
        if m is None:
            raise SemanticError(f"File LAION có khóa lạ '{key}' — không phải bản v2 đã ghim.")
        arr = val.detach().cpu().numpy() if hasattr(val, "detach") else np.asarray(val)
        idx.setdefault(int(m.group(1)), {})[m.group(2)] = arr
    return [(idx[n]["weight"], idx[n]["bias"]) for n in sorted(idx)]


def load_clip_backend(cfg: Mapping[str, Any]) -> ClipBackend:
    """Load CLIP + LAION head once and return them as plain callables.

    Raises:
        SemanticError: the [ai] extra is missing, or a model cannot be loaded.
    """
    try:
        import open_clip
        import torch
        from PIL import Image
    except ImportError as e:
        raise SemanticError(
            "Chưa cài nhóm [ai] (torch, open-clip-torch): chạy `pip install -e .[ai]`."
        ) from e
    head = ensure_aesthetic_file(cfg)
    # map_location: the file was saved on a CUDA GPU; Mac (MPS) and CPU-only PCs lack CUDA.
    state = torch.load(head, map_location="cpu", weights_only=True)
    w, b = collapse_linear_head(aesthetic_layers(state))
    device = pick_device(str(cfg["device"]), torch.backends.mps.is_available())
    name, tag = str(cfg["clip_model"]), str(cfg["clip_pretrained"])
    try:
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=tag,
                                                                     device=device)  # fmt: skip
    except (RuntimeError, OSError, ValueError) as e:
        raise SemanticError(f"Không nạp được CLIP {name}/{tag}: {e}. Máy mới cần mạng.") from e
    model.eval()
    tokenizer = open_clip.get_tokenizer(name)
    logger.info("CLIP %s/%s trên %s", name, tag, device)

    def _unit(t: Any) -> Vectors:  # Any: torch.Tensor (torch has no stub-free import here)
        t = t / t.norm(dim=-1, keepdim=True)
        return np.asarray(t.float().cpu().numpy(), dtype=np.float32)

    def embed_images(frames: Sequence[RgbFrame]) -> Vectors:
        rows = []
        with torch.no_grad():
            for i in range(0, len(frames), _BATCH):
                chunk = frames[i : i + _BATCH]
                batch = torch.stack([preprocess(Image.fromarray(f)) for f in chunk])
                rows.append(_unit(model.encode_image(batch.to(device))))
        return np.concatenate(rows) if rows else np.zeros((0, len(w)), dtype=np.float32)

    def embed_texts(texts: Sequence[str]) -> Vectors:
        with torch.no_grad():
            return _unit(model.encode_text(tokenizer(list(texts)).to(device)))

    return ClipBackend(name=f"{name}/{tag}", embed_images=embed_images,
                       embed_texts=embed_texts, aesthetic_w=w, aesthetic_b=b)  # fmt: skip
