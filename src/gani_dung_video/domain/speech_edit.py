"""Audio-first editing (phase 5c-1, spec Insight 4 "LỜI"): speech decides the cuts.

Phrases come from Whisper word timings; the chosen phrases are chained on the timeline
(A-roll, the speaker), B-roll covers parts of it on {2, 4} beats, the music ducks under the
voice. Pure functions: transcripts and settings are passed in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.glossary import find_banned_claims
from gani_dung_video.domain.speech import SpokenWord


class Phrase(BaseModel):
    """A pickable spoken phrase and the source window to cut it with."""

    model_config = ConfigDict(frozen=True)

    id: str  # "<clip stem>_p<n>"
    clip: str
    start: float  # first word
    end: float  # last word
    cut_in: float  # padded, never inside a neighbour word
    cut_out: float
    text: str
    words: list[SpokenWord]

    @property
    def duration(self) -> float:
        return self.end - self.start


class PhraseExclusion(BaseModel):
    """A phrase kept out of phrases.json, and why (spoken words cannot be auto-corrected)."""

    model_config = ConfigDict(frozen=True)

    id: str
    clip: str
    text: str
    reason: str  # qua_dai | tu_cam


class PhrasesFile(BaseModel):
    """Content of edit/phrases.json: usable phrases for the AI to choose from (5c-2 input)."""

    rules_version: str
    style: str
    phrases: list[Phrase]
    excluded: list[PhraseExclusion]


class SpeechCut(BaseModel):
    """A phrase placed on the timeline; its words are retimed to video time."""

    model_config = ConfigDict(frozen=True)

    phrase_id: str
    clip: str
    src_in: float
    src_out: float
    t_start: float
    t_end: float
    words: list[SpokenWord]


class Overlay(BaseModel):
    """A B-roll slot covering part of a phrase (picture only; the voice keeps playing)."""

    model_config = ConfigDict(frozen=True)

    phrase_id: str
    t_start: float
    t_end: float
    beats: int


def _frame(t: float, fps: int) -> float:
    return round(t * fps) / fps


def cut_length(p: Phrase, fps: int) -> float:
    """Seconds this phrase takes on the timeline: its padded cut window, frame-accurate
    (exactly what `aroll_timeline` lays down). Longer than `p.duration`, the spoken words."""
    return _frame(p.cut_out, fps) - _frame(p.cut_in, fps)


# ─────────────── phrases ───────────────


def _breaks(words: Sequence[SpokenWord], cfg: Mapping[str, Any], style: Mapping[str, Any]
            ) -> list[int]:  # fmt: skip
    """Indices after which a phrase ends."""
    gap = float(style["silence_keep"][1])
    hard, soft = str(cfg["sentence_end"]), str(cfg["soft_break"])
    half = float(cfg["max_phrase_sec"]) / 2
    out, start = [], words[0].start
    for i, w in enumerate(words):
        last = i == len(words) - 1
        tail = w.text.strip()[-1:] if w.text.strip() else ""
        paused = not last and words[i + 1].start - w.end >= gap - 1e-9
        long_enough = w.end - start >= half - 1e-9
        if last or paused or tail in hard or (tail in soft and long_enough):
            out.append(i)
            if not last:
                start = words[i + 1].start
    return out


def split_phrases(words: Sequence[SpokenWord], clip: str, cfg: Mapping[str, Any],
                  style: Mapping[str, Any], stem: str | None = None) -> list[Phrase]:  # fmt: skip
    """Cut a clip's words into phrases with padded cut windows (pad_ms of the style).

    `stem` is the id prefix (footage.clip_stems over the whole folder); default: the file
    name without its extension."""
    if not words:
        return []
    before, after = (float(x) / 1000 for x in style["pad_ms"])
    stem, first = stem if stem is not None else clip.rsplit(".", 1)[0], 0
    out: list[Phrase] = []
    for end_idx in _breaks(words, cfg, style):
        ws = list(words[first : end_idx + 1])
        prev_end = words[first - 1].end if first else 0.0
        next_start = words[end_idx + 1].start if end_idx + 1 < len(words) else math.inf
        lo = (prev_end + ws[0].start) / 2 if first else 0.0
        hi = (ws[-1].end + next_start) / 2 if next_start != math.inf else ws[-1].end + after
        first = end_idx + 1
        if ws[-1].end - ws[0].start < float(cfg["min_phrase_sec"]) - 1e-9:
            continue  # a filler ("ờ", "à"): never worth a cut
        out.append(Phrase(id=f"{stem}_p{len(out)}", clip=clip, start=ws[0].start, end=ws[-1].end,
                          cut_in=max(ws[0].start - before, lo), cut_out=min(ws[-1].end + after, hi),
                          text=" ".join(w.text.strip() for w in ws), words=ws))  # fmt: skip
    return out


def usable_phrases(phrases: Sequence[Phrase], banned: Sequence[str], cfg: Mapping[str, Any]
                   ) -> list[Phrase]:  # fmt: skip
    """What the AI may pick: no banned claim (spoken words cannot be edited), not too long."""
    return classify_phrases(phrases, banned, cfg)[0]


def classify_phrases(
    phrases: Sequence[Phrase], banned: Sequence[str], cfg: Mapping[str, Any]
) -> tuple[list[Phrase], list[PhraseExclusion]]:
    """Like usable_phrases, but also reports why each dropped phrase was excluded
    (qua_dai = over max_phrase_sec, tu_cam = contains a banned claim; checked in that order)."""
    limit = float(cfg["max_phrase_sec"])
    ok: list[Phrase] = []
    dropped: list[PhraseExclusion] = []
    for p in phrases:
        if p.duration > limit + 1e-9:
            dropped.append(PhraseExclusion(id=p.id, clip=p.clip, text=p.text, reason="qua_dai"))
        elif find_banned_claims(p.text, banned):
            dropped.append(PhraseExclusion(id=p.id, clip=p.clip, text=p.text, reason="tu_cam"))
        else:
            ok.append(p)
    return ok, dropped


# ─────────────── timeline ───────────────


def aroll_timeline(phrases: Sequence[Phrase], fps: int) -> list[SpeechCut]:
    """Chain the chosen phrases, in the chosen order, frame-accurately."""
    out, t = [], 0.0
    for p in phrases:
        src_in, src_out = _frame(p.cut_in, fps), _frame(p.cut_out, fps)
        t_end = _frame(t + cut_length(p, fps), fps)
        shift = t - src_in
        ws = [w.model_copy(update={"start": round(w.start + shift, 3),
                                   "end": round(w.end + shift, 3)}) for w in p.words]  # fmt: skip
        out.append(SpeechCut(phrase_id=p.id, clip=p.clip, src_in=src_in, src_out=src_out,
                             t_start=t, t_end=t_end, words=ws))  # fmt: skip
        t = t_end
    return out


def _snap(t: float, beat_sec: float, max_shift: float) -> float:
    nearest = round(t / beat_sec) * beat_sec
    return nearest if abs(nearest - t) <= max_shift + 1e-9 else t


def default_overlays(cuts: Sequence[SpeechCut], beat_sec: float, beats_allowed: Sequence[int],
                     max_shift: float) -> list[Overlay]:  # fmt: skip
    """Rule-based B-roll plan (the AI refines it in 5c-2): every phrase except the first
    (hook) and the last (CTA) gets the longest allowed beat count that fits inside it; its
    start moves onto the nearest beat when that is within max_shift (X17)."""
    out = []
    for c in cuts[1:-1]:
        start = _snap(c.t_start, beat_sec, max_shift)
        if start < c.t_start - max_shift - 1e-9:
            start = c.t_start
        fits = [n for n in beats_allowed if start + n * beat_sec <= c.t_end + 1e-9]
        if fits:
            n = max(fits)
            out.append(Overlay(phrase_id=c.phrase_id, t_start=round(start, 3),
                               t_end=round(start + n * beat_sec, 3), beats=n))  # fmt: skip
    return out


# ─────────────── ducking ───────────────


def duck_intervals(cuts: Sequence[SpeechCut], merge_gap: float) -> list[tuple[float, float]]:
    """Voiced spans on the timeline (word timings), merged across short gaps."""
    spans: list[tuple[float, float]] = []
    for w in (w for c in cuts for w in c.words):
        if spans and w.start - spans[-1][1] < merge_gap:
            spans[-1] = (spans[-1][0], max(spans[-1][1], w.end))
        else:
            spans.append((w.start, w.end))
    return spans


def duck_gain(t: float, spans: Sequence[tuple[float, float]], duck_db: float, fade: float
              ) -> float:  # fmt: skip
    """Music gain at time t: 1 outside speech, 10^(duck_db/20) under it, linear ramps
    (fade 0 = a hard step at the span edges)."""
    floor = 10 ** (duck_db / 20)
    if fade <= 0:
        inside = any(a <= t <= b for a, b in spans)
        return floor if inside else 1.0
    w = max((min(max((t - (a - fade)) / fade, 0.0), 1.0) *
             min(max(((b + fade) - t) / fade, 0.0), 1.0)
             for a, b in spans), default=0.0)  # fmt: skip
    return 1 - (1 - floor) * w


def duck_expression(spans: Sequence[tuple[float, float]], duck_db: float, fade: float) -> str:
    """The same gain as an ffmpeg `volume` expression of t (eval=frame)."""
    floor = 10 ** (duck_db / 20)
    if fade <= 0:  # hard step: no ramp to divide by
        terms = [f"between(t,{a:.6f},{b:.6f})" for a, b in spans]
    else:
        terms = [f"clip((t-{a - fade:.6f})/{fade:.6f},0,1)*clip(({b + fade:.6f}-t)/{fade:.6f},0,1)"
                 for a, b in spans]  # fmt: skip
    if not terms:
        return "1"
    w = terms[0]
    for term in terms[1:]:
        w = f"max({w},{term})"
    return f"1-{1 - floor:.12f}*({w})"
