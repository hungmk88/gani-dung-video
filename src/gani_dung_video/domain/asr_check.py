"""T8: Whisper listens to the finished video (phase 5c-3, speech-driven styles). Pure code.

The reference is what the karaoke captions show (the words of the chosen phrases, retimed to
video time), not the whole source transcript. Two measures: WER (words the viewer cannot
hear; blocks delivery, spoken words cannot be auto-corrected) and the median start-time
drift of the words both sides agree on (a warning: Whisper's own word timing jitters by
50–100 ms, so a single word proves nothing). When the video has music and WER fails, a second
pass on the voice-only mix says whether the music masks the voice or the voice was hard to
hear to begin with.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.glossary import tokens
from gani_dung_video.domain.qc import Finding
from gani_dung_video.domain.speech import SpokenWord


class Tok(BaseModel):
    """One normalised syllable and when its word started."""

    model_config = ConfigDict(frozen=True)

    text: str
    start: float


class AsrResult(BaseModel):
    """How a Whisper transcript of the video compares with the reference words."""

    ref_count: int
    errors: int  # substitutions + deletions + insertions
    wer: float
    matched: int  # reference words heard correctly
    median_drift_sec: float | None  # None: no word was matched


def to_tokens(words: Sequence[SpokenWord]) -> list[Tok]:
    """Syllables of each word (lowercase, no punctuation); a multi-syllable word's syllables
    share its start time."""
    return [Tok(text=t, start=w.start) for w in words for t in tokens(w.text)]


def align(ref: Sequence[str], hyp: Sequence[str]) -> list[tuple[int | None, int | None]]:
    """Levenshtein alignment: (ref index, hyp index) pairs; None marks a deletion/insertion."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sub = d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1])
            d[i][j] = min(sub, d[i - 1][j] + 1, d[i][j - 1] + 1)
    out: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1]


def within_spans(hyp: Sequence[Tok], spans: Sequence[tuple[float, float]], margin: float
                 ) -> list[Tok]:  # fmt: skip
    """Keep the words heard inside a voiced span (± margin): Whisper invents words over
    music-only stretches and the end card, and those are not the voice's fault."""
    return [t for t in hyp if any(a - margin <= t.start <= b + margin for a, b in spans)]


def compare(ref: Sequence[Tok], hyp: Sequence[Tok]) -> AsrResult:
    """WER and median start drift of `hyp` against `ref`."""
    pairs = align([t.text for t in ref], [t.text for t in hyp])
    same = [
        (r, h) for r, h in pairs if r is not None and h is not None and ref[r].text == hyp[h].text
    ]
    errors = len(pairs) - len(same)
    drifts = [abs(hyp[h].start - ref[r].start) for r, h in same]
    return AsrResult(
        ref_count=len(ref), errors=errors,
        wer=errors / len(ref) if ref else 0.0, matched=len(same),
        median_drift_sec=statistics.median(drifts) if drifts else None,
    )  # fmt: skip


def diagnose(mix: AsrResult, voice_only: AsrResult | None, cfg: Mapping[str, Any]) -> list[Finding]:
    """T8 findings. `voice_only` is the same check on the mix without music, when it was run."""
    if mix.ref_count == 0:
        return []
    out: list[Finding] = []
    limit = float(cfg["max_wer"])
    if mix.wer > limit + 1e-9:
        head = (f"Whisper nghe lại thành phẩm sai {mix.wer:.0%} số từ (tối đa {limit:.0%}): "
                "người xem cũng sẽ khó nghe.")  # fmt: skip
        if voice_only is None:
            msg = head
        elif voice_only.wer <= limit + 1e-9:
            msg = (f"{head} Nhạc đè tiếng: bản bỏ nhạc chỉ sai {voice_only.wer:.0%}. "
                   "Hạ nhạc thêm, đổi bài nhạc hoặc bỏ nhạc.")  # fmt: skip
        else:
            msg = (f"{head} Giọng nói khó nghe ngay cả khi bỏ nhạc (sai {voice_only.wer:.0%}): "
                   "kiểm tra chất lượng thu âm hoặc chọn câu nói rõ hơn.")  # fmt: skip
        out.append(Finding(code="T8", level="error", msg=msg))
    drift = mix.median_drift_sec
    if drift is not None and drift > float(cfg["max_median_drift_sec"]) + 1e-9:
        out.append(Finding(code="T8", level="warn", msg=(
            f"Phụ đề lệch tiếng trung vị {drift * 1000:.0f}ms "
            f"(tối đa {float(cfg['max_median_drift_sec']) * 1000:.0f}ms)."
        )))  # fmt: skip
    return out
