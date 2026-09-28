"""Scene meaning (phase 3c): CLIP zero-shot labels, LAION aesthetic score, near-duplicates.

Pure numpy on vectors the CLIP backend produced; the backend itself (torch, open_clip) is
injected as plain callables so tests run with a fake one on machines without torch.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Vectors = NDArray[np.float32]  # one L2-normalised embedding per row
RgbFrame = NDArray[np.uint8]  # H × W × 3, RGB order
ImageEmbedder = Callable[[Sequence[RgbFrame]], Vectors]
TextEmbedder = Callable[[Sequence[str]], Vectors]


@dataclass(frozen=True)
class ClipBackend:
    """What phase 3c needs from a vision-language model (real: infrastructure/clip_model.py).

    aesthetic_w/aesthetic_b: the LAION predictor collapsed to score = vec · w + b.
    """

    name: str
    embed_images: ImageEmbedder
    embed_texts: TextEmbedder
    aesthetic_w: NDArray[np.float64]
    aesthetic_b: float


def _normalise(m: NDArray[np.floating]) -> Vectors:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return (m / np.where(norms == 0, 1.0, norms)).astype(np.float32)


def label_centroids(
    table: Mapping[str, Sequence[str]], embed_texts: TextEmbedder
) -> tuple[list[str], Vectors]:
    """One direction per label = normalised mean of its English prompts (prompt ensembling)."""
    names = list(table)
    prompts = [p for n in names for p in table[n]]
    vecs = embed_texts(prompts)
    rows, k = [], 0
    for n in names:
        rows.append(vecs[k : k + len(table[n])].mean(axis=0))
        k += len(table[n])
    return names, _normalise(np.stack(rows))


def label_probs(images: Vectors, centroids: Vectors, logit_scale: float = 100.0) -> Vectors:
    """Softmax over labels of scaled cosine similarity (CLIP's own zero-shot recipe)."""
    logits = logit_scale * (images @ centroids.T)
    logits -= logits.max(axis=1, keepdims=True) if len(logits) else 0.0
    e = np.exp(logits)
    return (e / e.sum(axis=1, keepdims=True)).astype(np.float32)


def pick_labels(probs: NDArray[np.floating], names: Sequence[str], min_prob: float) -> list[str]:
    """Labels with probability ≥ min_prob, most likely first; [] when the model is unsure."""
    order = np.argsort(-probs, kind="stable")
    return [names[i] for i in order if probs[i] >= min_prob]


def collapse_linear_head(
    layers: Sequence[tuple[NDArray[np.floating], NDArray[np.floating]]],
) -> tuple[NDArray[np.float64], float]:
    """Fold a stack of Linear layers (no activation between them) into score = x · w + b.

    The LAION v2 predictor is Linear→Dropout→…→Linear with no non-linearity, so at inference
    it is one affine map; folding it removes torch from the scoring path.

    Raises:
        ValueError: the last layer does not have exactly 1 output.
    """
    w_acc = np.asarray(layers[0][0], dtype=np.float64)
    b_acc = np.asarray(layers[0][1], dtype=np.float64)
    for w, b in layers[1:]:
        w64 = np.asarray(w, dtype=np.float64)
        w_acc, b_acc = w64 @ w_acc, w64 @ b_acc + np.asarray(b, dtype=np.float64)
    if w_acc.shape[0] != 1:
        raise ValueError(f"Aesthetic head must end with 1 output, got {w_acc.shape[0]}")
    return w_acc[0], float(b_acc[0])


def aesthetic_scores(vecs: Vectors, w: NDArray[np.floating], b: float) -> list[float]:
    """LAION aesthetic score (≈1–10, uncalibrated for GANI footage) per embedding row."""
    return [float(x) for x in vecs.astype(np.float64) @ np.asarray(w, dtype=np.float64) + b]


def similar_pairs(vecs: Sequence[Vectors], threshold: float) -> list[tuple[int, int, float]]:
    """Index pairs (i < j) whose cosine similarity is ≥ threshold (spec §7.1: same angle)."""
    out: list[tuple[int, int, float]] = []
    for i, j in itertools.combinations(range(len(vecs)), 2):
        sim = float(np.dot(vecs[i], vecs[j]))
        if sim >= threshold:
            out.append((i, j, round(sim, 4)))
    return out
