"""Unit tests: domain/semantics.py — CLIP labels, LAION score, near-duplicate check (3c).

Pure numpy: vectors are hand-made, no torch, no model download.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from gani_dung_video.domain.semantics import (
    Vectors,
    aesthetic_scores,
    collapse_linear_head,
    label_centroids,
    label_probs,
    pick_labels,
    similar_pairs,
)

NAMES = ["nguoi_thao_tac", "thiet_bi", "mon_an"]


def unit(*xs: float) -> np.ndarray:
    v = np.asarray(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


# ─────────────── zero-shot labels ───────────────


def test_centroid_is_normalised_mean_of_prompts() -> None:
    table = {"a": ["p1", "p2"], "b": ["p3"]}
    vec = {"p1": unit(1, 0, 0), "p2": unit(0, 1, 0), "p3": unit(0, 0, 1)}

    def embed(texts: Sequence[str]) -> Vectors:
        return np.stack([vec[t] for t in texts]).astype(np.float32)

    names, cents = label_centroids(table, embed)
    assert names == ["a", "b"]
    assert np.allclose(cents[0], unit(1, 1, 0)) and np.allclose(cents[1], unit(0, 0, 1))


def test_probs_are_softmax_rows_peaking_on_the_closest_label() -> None:
    cents = np.stack([unit(1, 0, 0), unit(0, 1, 0), unit(0, 0, 1)])
    imgs = np.stack([unit(0.9, 0.1, 0), unit(0, 0.2, 1)])
    p = label_probs(imgs, cents)
    assert p.shape == (2, 3) and np.allclose(p.sum(axis=1), 1.0)
    assert p[0].argmax() == 0 and p[1].argmax() == 2


def test_pick_labels_keeps_confident_ones_best_first() -> None:
    assert pick_labels(np.array([0.1, 0.5, 0.4]), NAMES, 0.3) == ["thiet_bi", "mon_an"]
    assert pick_labels(np.array([0.34, 0.33, 0.33]), NAMES, 0.5) == []  # nothing sure


def test_empty_frames_give_empty_probs() -> None:
    p = label_probs(np.zeros((0, 3), dtype=np.float32), np.eye(3, dtype=np.float32))
    assert p.shape == (0, 3)


# ─────────────── LAION aesthetic head ───────────────


def test_linear_head_collapses_to_one_dot_product() -> None:
    rng = np.random.default_rng(0)
    layers = [(rng.normal(size=(o, i)), rng.normal(size=o)) for i, o in [(6, 5), (5, 3), (3, 1)]]
    x = rng.normal(size=6)
    y = x
    for w, b in layers:  # what the MLP does at inference (dropout is off)
        y = w @ y + b
    w1, b1 = collapse_linear_head(layers)
    assert w1.shape == (6,) and float(x @ w1 + b1) == pytest.approx(float(y[0]))


def test_linear_head_rejects_non_scalar_output() -> None:
    with pytest.raises(ValueError, match="1 output"):
        collapse_linear_head([(np.ones((2, 3)), np.zeros(2))])


def test_aesthetic_scores_are_dot_plus_bias() -> None:
    vecs = np.stack([unit(1, 0), unit(0, 1)])
    assert aesthetic_scores(vecs, np.array([2.0, 4.0]), 1.0) == pytest.approx([3.0, 5.0])


# ─────────────── near-duplicate segments (spec §7.1, > 0.85) ───────────────


def test_similar_pairs_flags_only_near_duplicates() -> None:
    vecs = [unit(1, 0, 0), unit(0.99, 0.1, 0), unit(0, 1, 0)]
    pairs = similar_pairs(vecs, 0.85)
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]
    assert pairs[0][2] == pytest.approx(0.995, abs=1e-3)


def test_similar_pairs_needs_two_items() -> None:
    assert similar_pairs([], 0.85) == [] and similar_pairs([unit(1, 0)], 0.85) == []
