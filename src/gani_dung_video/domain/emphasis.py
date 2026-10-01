"""Keyword emphasis for captions (Dev Package 03, T-C). Pure functions.

Which words of a text are approved keywords (glossary.json → caption_highlight_keywords)?
Matching is by whole syllables next to each other, ignoring case and tone marks, exactly like
the banned-claims check, so "haccp" and "HACCP," both count and "HACCPX" does not.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from gani_dung_video.domain.glossary import fold_tokens

_WORD = re.compile(r"[\w%]+", re.UNICODE)


def _patterns(keywords: Sequence[str]) -> list[list[str]]:
    """Folded syllable sequences, longest first so "hút chân không" beats "chân không"."""
    pats = [fold_tokens(k) for k in keywords]
    return sorted((p for p in pats if p), key=len, reverse=True)


def keyword_indexes(words: Sequence[str], keywords: Sequence[str]) -> set[int]:
    """Positions in `words` (one entry per spoken word) that belong to a keyword."""
    folded = ["".join(fold_tokens(w)) for w in words]
    hit: set[int] = set()
    i = 0
    while i < len(folded):
        n = next((len(p) for p in _patterns(keywords) if folded[i : i + len(p)] == p), 0)
        if n:
            hit.update(range(i, i + n))
            i += n
        else:
            i += 1
    return hit


def keyword_spans(text: str, keywords: Sequence[str]) -> list[tuple[int, int]]:
    """Character spans (start, end) of every keyword occurrence in `text`."""
    found = list(_WORD.finditer(text))
    folded = ["".join(fold_tokens(m.group(0))) for m in found]
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(found):
        n = next((len(p) for p in _patterns(keywords) if folded[i : i + len(p)] == p), 0)
        if n:
            spans.append((found[i].start(), found[i + n - 1].end()))
            i += n
        else:
            i += 1
    return spans


def emphasise(text: str, keywords: Sequence[str], on: str, off: str) -> str:
    """`text` with `on` before and `off` after every keyword; all other characters untouched."""
    out, last = [], 0
    for a, b in keyword_spans(text, keywords):
        out += [text[last:a], on, text[a:b], off]
        last = b
    return "".join(out) + text[last:]
