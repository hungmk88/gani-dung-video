"""Candidate scenes per block (phase 4b, spec §7.1 "fix S12"): what the AI may choose from.

Order of filtering (the most convincing shot is often not the prettiest one: hand washing,
core-temperature checks, certificates):
    1. drop flagged segments — blurry scene, "khac" (text card / outro / logo), too short
       for the style's smallest beat count at the chosen tempo;
    2. keep segments carrying the block's required tags; if none has them, take the closest
       ones by CLIP probability and report the missing shots;
    3. segments showing a dish named in the user's note come first;
    4. only then rank by aesthetic score.
Pure functions: vectors and texts are passed in.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.beats import beats_that_fit
from gani_dung_video.domain.glossary import tokens
from gani_dung_video.domain.plan import PlanBlock
from gani_dung_video.domain.scenes import Scene, SimilarPair

MIN_CANDIDATES = 6
MAX_CANDIDATES = 20


class Candidate(BaseModel):
    """One pickable segment as the AI sees it (id = snap id of phase 4a)."""

    model_config = ConfigDict(frozen=True)

    id: str
    clip: str
    start: float
    end: float
    peak: float
    duration: float
    fits_beats: list[int]  # allowed beat counts this segment is long enough for
    labels: list[str]
    aesthetic: float | None
    tag_probs: dict[str, float] = {}  # CLIP probabilities of the scene (for "closest" fallback)
    dishes: list[str] = []  # dishes of the note this segment matches


class BlockCandidates(BaseModel):
    role: str
    beats: int
    required_tags: list[str]
    missing_tags: list[str]
    candidates: list[Candidate]


class PickFile(BaseModel):
    """Content of edit/candidates.json: the only scenes the AI may choose (phase 4c input)."""

    rules_version: str
    style: str
    beat_sec: float
    beats_per_scene_allowed: list[int]
    dishes: list[str]  # dishes of the note found in the visual dictionary
    blocks: list[BlockCandidates]
    missing_shots: list[str]  # Vietnamese names of required shots nobody has
    similar_pairs: list[tuple[str, str]]  # candidate ids that look alike: never use both


def eligible_candidates(
    scenes: Sequence[Scene], style: str, beat_sec: float, allowed: Sequence[int]
) -> list[Candidate]:
    """Step 1: every usable segment of the style, in snap-id order."""
    out: list[Candidate] = []
    for sc in scenes:
        stem = sc.clip.rsplit(".", 1)[0]
        for k, g in enumerate(sc.segments.get(style, [])):
            fits = beats_that_fit(g.end - g.start, beat_sec, allowed)
            if sc.blurry or g.excluded or not fits:
                continue
            out.append(Candidate(
                id=f"{stem}_s{sc.index}_{k}", clip=sc.clip, start=g.start, end=g.end,
                peak=g.peak, duration=round(g.end - g.start, 3), fits_beats=fits,
                labels=list(g.labels), aesthetic=g.aesthetic, tag_probs=dict(sc.label_probs),
            ))  # fmt: skip
    return out


def candidate_limit(
    block_beats: int, allowed: Sequence[int], lo: int = MIN_CANDIDATES, hi: int = MAX_CANDIDATES
) -> int:
    """Twice the scenes the block may need (shortest scenes), kept within [lo, hi]."""
    scenes_needed = math.ceil(block_beats / min(allowed))
    return max(lo, min(hi, 2 * scenes_needed))


def _aes(c: Candidate) -> float:
    return c.aesthetic if c.aesthetic is not None else float("-inf")


def rank_block(
    pool: Sequence[Candidate], required_tags: Sequence[str], dish_ids: Collection[str], limit: int,
    top_up: bool = False,
) -> tuple[list[Candidate], list[str]]:  # fmt: skip
    """Steps 2–4 for one block. Returns (ranked candidates, required tags nobody has).

    `top_up` (chapters of a long video): after the scenes that carry the tags, fill the list
    with the best of the rest instead of stopping there — a chapter needs far more scenes than
    the tagged ones alone may offer, and its required tags only ask that one of them shows up.
    """
    missing = [t for t in required_tags if not any(t in c.labels for c in pool)]
    found = [t for t in required_tags if t not in missing]
    tagged = set(found)
    chosen = list(pool) if top_up or not found else [c for c in pool if tagged & set(c.labels)]

    def key(c: Candidate) -> tuple[bool, bool, float, float]:
        # Nobody has the tags: closest by CLIP probability of the missing tags comes first.
        closeness = 0.0 if found or not missing else max(c.tag_probs.get(t, 0.0) for t in missing)
        untagged = bool(found) and not (tagged & set(c.labels))
        return untagged, c.id not in dish_ids, -closeness, -_aes(c)

    return sorted(chosen, key=key)[:limit], missing


def limits_for(
    blocks: Sequence[PlanBlock], allowed: Sequence[int], lo: int, hi: int, max_total: int
) -> list[int]:
    """Candidates offered to each block: `candidate_limit`, scaled down together when their
    sum passes `max_total` (0 = no cap) so the prompt stays bounded, but never below the scenes
    a block needs at the very least. A cap nobody can meet is ignored, not starved into."""
    base = [candidate_limit(b.beats, allowed, lo, hi) for b in blocks]
    if max_total <= 0 or sum(base) <= max_total:
        return base
    floors = [min(n, math.ceil(b.beats / min(allowed))) for n, b in zip(base, blocks, strict=True)]
    room, spare = max_total - sum(floors), sum(base) - sum(floors)
    if room <= 0:
        return floors
    share = min(1.0, room / spare)
    return [f + int((n - f) * share) for f, n in zip(floors, base, strict=True)]


def deal(ranked: Sequence[Candidate], limits: Sequence[int]) -> list[list[Candidate]]:
    """Hand ranked candidates out one at a time to the block with the most room left (the
    earliest on a tie), so every block gets a fair share of the best and none gets a scene twice."""
    out: list[list[Candidate]] = [[] for _ in limits]
    for c in ranked:
        open_ = [i for i, n in enumerate(limits) if len(out[i]) < n]
        if not open_:
            break
        out[min(open_, key=lambda i: (len(out[i]) - limits[i], i))].append(c)
    return out


def rank_plan(
    blocks: Sequence[PlanBlock], pool: Sequence[Candidate], dish_ids: Collection[str],
    allowed: Sequence[int], cfg: Mapping[str, Any],
) -> tuple[list[BlockCandidates], list[str]]:  # fmt: skip
    """Candidates of every block of a plan, plus the required tags nobody has.

    A block of a short video is ranked on its own. The sub-blocks of a chapter (3-minute
    videos) are ranked together — tagged scenes first, topped up with the best others — and
    dealt out, so each has candidates of its own.
    """
    limits = limits_for(blocks, allowed, int(cfg["min_candidates"]), int(cfg["max_candidates"]),
                        int(cfg.get("max_total_candidates", 0)))  # fmt: skip
    done: dict[int, BlockCandidates] = {}
    missing_all: list[str] = []
    for i, b in enumerate(blocks):
        if i in done:
            continue
        mine = (
            [i]
            if b.chapter is None
            else [j for j, x in enumerate(blocks) if x.chapter == b.chapter]
        )
        ranked, missing = rank_block(pool, b.required_tags, dish_ids, sum(limits[j] for j in mine),
                                     top_up=b.chapter is not None)  # fmt: skip
        parts = deal(ranked, [limits[j] for j in mine]) if b.chapter is not None else [ranked]
        for j, part in zip(mine, parts, strict=True):
            done[j] = BlockCandidates(role=blocks[j].role, beats=blocks[j].beats,
                                      required_tags=blocks[j].required_tags,
                                      missing_tags=missing, candidates=part)  # fmt: skip
        missing_all += [t for t in missing if t not in missing_all]
    return [done[i] for i in range(len(blocks))], missing_all


def find_dishes(note: str | None, terms: Sequence[str]) -> list[str]:
    """Dictionary dishes named in the note, as whole syllable phrases, in dictionary order."""
    toks = tokens(note or "")
    out = []
    for term in terms:
        pat = tokens(term)
        if pat and any(toks[i : i + len(pat)] == pat for i in range(len(toks))):
            out.append(term)
    return out


def dish_matches(
    seg_vecs: Mapping[str, NDArray[np.floating]], dish_vecs: Mapping[str, NDArray[np.floating]],
    threshold: float,
) -> dict[str, list[str]]:  # fmt: skip
    """Candidate id → dishes whose text vector is at least `threshold` cosine-similar to the
    best frame of the segment (one vector, or one row per frame)."""
    out: dict[str, list[str]] = {}
    for cid, v in seg_vecs.items():
        rows = np.atleast_2d(v)
        hits = [d for d, t in dish_vecs.items() if float((rows @ t).max()) >= threshold]
        if hits:
            out[cid] = hits
    return out


def similar_id_pairs(
    cands: Sequence[Candidate], pairs: Sequence[SimilarPair]
) -> list[tuple[str, str]]:
    """Near-duplicate pairs (phase 3c) as candidate ids, so the AI avoids using both."""
    by_peak: dict[tuple[str, float], list[str]] = {}
    for c in cands:
        by_peak.setdefault((c.clip, c.peak), []).append(c.id)
    out: list[tuple[str, str]] = []
    for p in pairs:
        for a in by_peak.get((p.clip, p.peak_a), []):
            out.extend((a, b) for b in by_peak.get((p.clip, p.peak_b), []))
    return out
