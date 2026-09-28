"""Speech facts per clip (phase 3a): transcript model + the `no_speech` decision.

Why here and not in phase 2: whether a clip has *speech* (not just an audio stream) needs a
recogniser + voice-activity detection. The flag chooses the cut driver (spec Insight 4):
speech → LỜI (audio-first); no speech → NHẠC (beats) or LƯỚI ẢO (grid).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.glossary import Term, Word, apply_glossary


class SpokenWord(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    start: float
    end: float
    prob: float


class RawTranscript(BaseModel):
    """What the recogniser returns, before any GANI-specific rule is applied."""

    model_config = ConfigDict(frozen=True)

    language: str
    duration_sec: float
    speech_sec: float  # voiced seconds measured by VAD
    words: list[SpokenWord]


class Transcript(BaseModel):
    """Content of edit/transcripts/<clip>.json."""

    file: str
    model: str
    cache_key: str
    language: str
    duration_sec: float
    speech_sec: float
    word_count: int
    no_speech: bool
    text: str
    words: list[SpokenWord]


def is_no_speech(t: RawTranscript | Transcript, cfg: Mapping[str, Any]) -> bool:
    """True when the clip has too little *intentional* speech to drive cuts by voice.

    A clip counts as speech only if ALL hold (decision 28/09/2026, from real workshop
    footage where mic-picked chatter was 2–14% of the clip at 0.43–0.67 confidence):
    ≥ min_speech_sec voiced, ≥ min_words words, voiced ≥ min_speech_ratio of the clip,
    average word confidence ≥ min_avg_confidence.

    Works on a stored Transcript too, so changing thresholds never needs a Whisper re-run.
    """
    if t.duration_sec <= 0 or not t.words:
        return True
    avg_conf = sum(w.prob for w in t.words) / len(t.words)
    return (
        t.speech_sec < float(cfg["min_speech_sec"])
        or len(t.words) < int(cfg["min_words"])
        or t.speech_sec / t.duration_sec < float(cfg["min_speech_ratio"])
        or avg_conf < float(cfg["min_avg_confidence"])
    )


def fix_names(words: Sequence[SpokenWord], terms: Sequence[Term]) -> list[SpokenWord]:
    """Apply the glossary (e.g. "gani foods" → "GANI") while keeping word timing."""
    fixed = apply_glossary([Word(w.text.strip(), w.start, w.end) for w in words], terms)
    # A merged word keeps the probability of its first source word (same start time).
    prob_by_start = {w.start: w.prob for w in words}
    return [
        SpokenWord(text=w.text, start=w.start, end=w.end, prob=prob_by_start.get(w.start, 0.0))
        for w in fixed
    ]


def build_transcript(
    file: str, model: str, cache_key: str, raw: RawTranscript, terms: Sequence[Term],
    cfg: Mapping[str, Any],
) -> Transcript:  # fmt: skip
    """Turn a raw recogniser result into the stored transcript for one clip."""
    words = fix_names(raw.words, terms)
    return Transcript(
        file=file,
        model=model,
        cache_key=cache_key,
        language=raw.language,
        duration_sec=round(raw.duration_sec, 3),
        speech_sec=round(raw.speech_sec, 3),
        word_count=len(words),
        no_speech=is_no_speech(raw, cfg),
        text=" ".join(w.text for w in words),
        words=words,
    )
