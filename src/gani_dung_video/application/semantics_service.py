"""Use case: CLIP labels, LAION score and near-duplicate warnings → edit/scenes.json (3c).

Frames embedded per clip: the middle of every 2 s window (→ scene labels) and the action
peak of every segment (→ segment score/labels, duplicate check). Vectors are cached in
edit/clip/<clip>.npz, so re-running with new thresholds or prompts does not re-embed.
Run after `scenes`: re-running `scenes` rewrites scenes.json without these fields.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import ValidationError

from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.scenes import Scene, ScenesFile, SimilarPair, usable_by_style
from gani_dung_video.domain.semantics import (
    ClipBackend,
    RgbFrame,
    Vectors,
    aesthetic_scores,
    label_centroids,
    label_probs,
    pick_labels,
    similar_pairs,
)
from gani_dung_video.infrastructure.clip_model import load_clip_backend
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.video_frames import VisionError, grab_frames

logger = logging.getLogger(__name__)
SCENES_FILE = "scenes.json"
CLIP_DIR = "clip"
Grabber = Callable[[Path, Sequence[float], int], list[RgbFrame]]


def _read_scenes(folder: Path) -> ScenesFile:
    p = folder / EDIT_DIR / SCENES_FILE
    if not p.is_file():
        raise ConfigError(f"Chưa có {EDIT_DIR}/{SCENES_FILE} — chạy lệnh `scenes` trước.")
    try:
        return ScenesFile.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{SCENES_FILE} hỏng — chạy lại lệnh `scenes`.") from e


def _clip_times(scenes: Sequence[Scene]) -> list[float]:
    times = {round((w.start + w.end) / 2, 3) for sc in scenes for w in sc.windows}
    times |= {g.peak for sc in scenes for segs in sc.segments.values() for g in segs}
    return sorted(times)


def _clip_vectors(
    folder: Path, clip: str, times: list[float], backend: ClipBackend, width: int, grab: Grabber
) -> dict[float, Vectors]:
    """Vector per time; reuses edit/clip/<clip>.npz when file, model and width are unchanged."""
    src, cache = folder / clip, folder / EDIT_DIR / CLIP_DIR / f"{clip}.npz"
    st = src.stat()
    key = f"{st.st_size}-{st.st_mtime_ns}-{backend.name}-{width}"
    known: dict[float, Vectors] = {}
    if cache.is_file():
        with np.load(cache) as z:
            if str(z["key"]) == key:
                known = {round(float(t), 3): v for t, v in zip(z["times"], z["vecs"], strict=True)}
    todo = [t for t in times if t not in known]
    if todo:
        logger.info("Đang nhúng CLIP: %s (%d khung)", clip, len(todo))
        known.update(zip(todo, backend.embed_images(grab(src, todo, width)), strict=True))
        ts = sorted(known)
        tmp = cache.with_name(cache.name + ".tmp.npz")
        np.savez(tmp, key=np.array(key), times=np.array(ts), vecs=np.stack([known[t] for t in ts]))
        os.replace(tmp, cache)
    return known


def _annotate(
    scenes: list[Scene], vec: Mapping[float, Vectors], names: list[str], cents: Vectors,
    backend: ClipBackend, min_prob: float,
) -> list[Scene]:  # fmt: skip
    out = []
    for sc in scenes:
        wv = [vec[round((w.start + w.end) / 2, 3)] for w in sc.windows]
        mean = label_probs(np.stack(wv), cents).mean(axis=0) if wv else np.zeros(len(names))
        segs = {}
        for style, lst in sc.segments.items():
            pv = np.stack([vec[g.peak] for g in lst]) if lst else np.zeros((0, cents.shape[1]))
            pv = pv.astype(np.float32)
            scores = aesthetic_scores(pv, backend.aesthetic_w, backend.aesthetic_b)
            probs = label_probs(pv, cents)
            segs[style] = [g.model_copy(update={"aesthetic": round(s, 3),
                                                "labels": pick_labels(p, names, min_prob)})
                           for g, s, p in zip(lst, scores, probs, strict=True)]  # fmt: skip
        out.append(sc.model_copy(update={
            "segments": segs, "labels": pick_labels(mean, names, min_prob),
            "label_probs": {n: round(float(p), 4) for n, p in zip(names, mean, strict=True)},
        }))  # fmt: skip
    return out


def _similar(
    clip: str, scenes: Sequence[Scene], vec: Mapping[float, Vectors], threshold: float
) -> list[SimilarPair]:
    """Near-duplicate moments of one clip, one entry per pair of peak frames.

    Segments of different styles overlap the same moments with different ranges, so the
    pair is keyed by the peak times and lists every style that picked both.
    """
    found: dict[tuple[float, float], tuple[float, set[str]]] = {}
    for style in sorted({s for sc in scenes for s in sc.segments}):
        segs = [g for sc in scenes for g in sc.segments.get(style, []) if not g.excluded]
        for i, j, sim in similar_pairs([vec[g.peak] for g in segs], threshold):
            key = tuple(sorted((segs[i].peak, segs[j].peak)))
            found.setdefault((key[0], key[1]), (sim, set()))[1].add(style)
    return [
        SimilarPair(clip=clip, peak_a=a, peak_b=b, similarity=sim, styles=sorted(styles))
        for (a, b), (sim, styles) in sorted(found.items())
    ]


def build_semantics(
    folder: Path, rules: Rules | None = None, backend: ClipBackend | None = None,
    grab: Grabber = grab_frames,
) -> tuple[ScenesFile, Path]:  # fmt: skip
    """Add CLIP labels, LAION scores and duplicate warnings to <folder>/edit/scenes.json.

    Raises:
        ConfigError: scenes.json missing/corrupt.
        SemanticError: the CLIP backend cannot be loaded.
    """
    rules = rules if rules is not None else load_rules()
    cfg: Mapping[str, Any] = rules["semantic"]
    folder = folder.expanduser().resolve()
    sf = _read_scenes(folder)
    backend = backend or load_clip_backend(cfg)
    names, cents = label_centroids(cfg["labels"], backend.embed_texts)
    (folder / EDIT_DIR / CLIP_DIR).mkdir(parents=True, exist_ok=True)
    by_clip: dict[str, list[Scene]] = {}
    for sc in sf.scenes:
        by_clip.setdefault(sc.clip, []).append(sc)
    scenes: list[Scene] = []
    similar: list[SimilarPair] = []
    for clip, group in by_clip.items():
        try:
            vec = _clip_vectors(folder, clip, _clip_times(group), backend,
                                int(cfg["frame_width"]), grab)  # fmt: skip
        except VisionError as e:
            logger.warning("Bỏ qua %s: %s", clip, e)
            scenes.extend(group)
            continue
        scenes.extend(_annotate(group, vec, names, cents, backend, float(cfg["label_min_prob"])))
        similar.extend(_similar(clip, group, vec, float(cfg["similar_warn"])))
    result = sf.model_copy(update={
        "scenes": scenes, "semantic_model": backend.name, "similar": similar,
        "usable_by_style": usable_by_style(scenes, rules["styles"]),
    })  # fmt: skip
    path = folder / EDIT_DIR / SCENES_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return result, path
