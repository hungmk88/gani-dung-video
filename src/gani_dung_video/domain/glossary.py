"""Glossary: fix misheard proper names in transcripts + find banned claims in on-screen text.

Vietnamese words are space-separated syllables, so matching works on syllable sequences:
- "gani foods" (2 syllables heard) → "GANI" (1 word) keeps the span start→end of the
  original words, so subtitle timing stays exact.
- Banned claims are PHRASES (decision 28/09/2026): "trị bệnh" is banned, while "giá trị"
  is not — a substring or regex \\b check would wrongly flag it, because "trị" is its own
  syllable there too. Từ cấm so theo cụm âm tiết liền nhau, không theo chuỗi con.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

# Keep '%' so "100%" stays one token; everything else non-word splits syllables.
_TOKEN = re.compile(r"[\w%]+", re.UNICODE)


@dataclass(frozen=True)
class Word:
    """One recognised word with its timing in seconds."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Term:
    """A glossary entry: the correct spelling and the ways speech recognition mishears it."""

    correct: str
    heard_as: tuple[str, ...]


def norm(text: str) -> str:
    """NFC + lowercase + strip punctuation, keeping tone marks ("Lộc," → "lộc")."""
    return "".join(_TOKEN.findall(unicodedata.normalize("NFC", text).lower()))


def tokens(text: str) -> list[str]:
    """Split text into normalised syllables.

    Example: "Đạt 100% chuẩn!" → ["đạt", "100%", "chuẩn"].
    """
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


def _match_at(toks: Sequence[str], i: int, pattern: Sequence[str]) -> bool:
    return bool(pattern) and list(toks[i : i + len(pattern)]) == list(pattern)


def _patterns(terms: Sequence[Term]) -> list[tuple[list[str], Term]]:
    """All spellings (correct + heard_as), longest first so "gani foods" wins over "gani"."""
    pats = [(tokens(s), t) for t in terms for s in (t.correct, *t.heard_as)]
    return sorted((p for p in pats if p[0]), key=lambda p: -len(p[0]))


def apply_glossary(words: Sequence[Word], terms: Sequence[Term]) -> list[Word]:
    """Replace misheard names in a word stream, keeping timing.

    Args:
        words: Recognised words in order.
        terms: Glossary entries.

    Returns:
        A new list; a matched run of N words becomes one Word spanning first.start→last.end.
        Trailing punctuation of the last matched word is kept ("food," → "GANI,").
    """
    toks = [norm(w.text) for w in words]
    pats = _patterns(terms)
    out: list[Word] = []
    i = 0
    while i < len(words):
        hit = next(((p, t) for p, t in pats if _match_at(toks, i, p)), None)
        if hit is None:
            out.append(words[i])
            i += 1
            continue
        n, last = len(hit[0]), words[i + len(hit[0]) - 1]
        tail = re.search(r"[^\w%]*$", last.text, re.UNICODE)
        text = hit[1].correct + (tail.group(0) if tail else "")
        out.append(replace(words[i], text=text, end=last.end))
        i += n
    return out


def find_banned_claims(text: str, banned: Sequence[str]) -> list[str]:
    """Return the banned phrases that appear in text as whole syllable sequences (V7).

    Example:
        >>> find_banned_claims("Giá trị thật, đặc trị mọi bệnh", ["trị bệnh", "đặc trị"])
        ['đặc trị']
    """
    toks = tokens(text)
    found: list[str] = []
    for phrase in banned:
        pat = tokens(phrase)
        if any(_match_at(toks, i, pat) for i in range(len(toks))):
            found.append(phrase)
    return found
