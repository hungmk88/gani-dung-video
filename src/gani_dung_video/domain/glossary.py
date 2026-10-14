"""Glossary: fix misheard proper names in transcripts + find banned claims in on-screen text.

Vietnamese words are space-separated syllables, so matching works on syllable sequences:
- "gani foods" (2 syllables heard) → "GANI" (1 word) keeps the span start→end of the
  original words, so subtitle timing stays exact.
- Banned claims are PHRASES (decision 28/09/2026): "trị bệnh" is banned, while "giá trị"
  is not — a substring or regex \\b check would wrongly flag it, because "trị" is its own
  syllable there too. Từ cấm so theo cụm âm tiết liền nhau, không theo chuỗi con.

Claims are compared on a folded form (audit C3): NFKC, invisible characters dropped, tone
marks and case ignored, "_" and no-break spaces are spaces, digits are split from letters
("số1" = "số 1") and a space before "%" is ignored. A text token may also be several syllables
of the phrase written together ("đặctrị"), but never a part of a syllable or of a longer word.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

# Keep '%' so "100%" stays one token; everything else non-word splits syllables.
_TOKEN = re.compile(r"[\w%]+", re.UNICODE)
# Claims only: letters, digits and "%" are separate pieces, so "số1" is "số" + "1". A number
# written with a decimal or thousands mark stays one piece: "số 1,5 kg" is not "số 1".
_CLAIM_PIECE = re.compile(r"[^\W\d_]+|\d+(?:[.,]\d+)*%?|%", re.UNICODE)
# Two-syllable words (folded) whose SECOND syllable a banned phrase starts with: "giá trị bệnh
# nhân" holds the syllables "trị bệnh" but says "value", not "cures disease". A match that
# starts on the second half of one of these words is not a claim. Words that are claims
# themselves ("điều trị") are never listed here.
_WORD_BEFORE: frozenset[tuple[str, str]] = frozenset({
    ("gia", "tri"), ("quan", "tri"), ("chinh", "tri"), ("cai", "tri"), ("thong", "tri"),
    ("van", "phong"), ("sua", "chua"),
})  # fmt: skip
_SPACE_BEFORE_PERCENT = re.compile(r"(?<=\d)\s+%")
_FOLD_LETTERS = str.maketrans({"đ": "d", "ð": "d"})  # ð (U+00F0) is not a Vietnamese letter


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


def fold_tokens(text: str) -> list[str]:
    """Syllables with tone marks removed, so "Thần dược" and "than duoc" compare equal.

    Used for claims only: people type on-screen text without tone marks, and a claim must
    not slip through that way. The glossary spellings keep their marks (see tokens()).

    Example: "Đặc TRỊ!" → ["dac", "tri"]; "Sạch 100 %" → ["sach", "100%"].
    """
    clean = unicodedata.normalize("NFKC", text)
    clean = "".join(c for c in clean if unicodedata.category(c) != "Cf").lower()
    clean = _SPACE_BEFORE_PERCENT.sub("%", clean)
    return [
        "".join(
            c for c in unicodedata.normalize("NFD", piece) if not unicodedata.combining(c)
        ).translate(_FOLD_LETTERS)
        for piece in _CLAIM_PIECE.findall(clean)
    ]


def _match_at(toks: Sequence[str], i: int, pattern: Sequence[str]) -> bool:
    return bool(pattern) and list(toks[i : i + len(pattern)]) == list(pattern)


def _match_glued(toks: Sequence[str], i: int, pattern: Sequence[str]) -> bool:
    """The phrase starts at toks[i]; each text token is one or more WHOLE syllables of it.

    "đặc", "trị" and "đặctrị" all match the phrase "đặc trị"; "giátrị" does not match "trị
    bệnh", because the token would have to start in the middle of a longer word.
    """
    if not pattern:
        return False
    joined = "".join(pattern)
    edges, at = {0}, 0
    for syllable in pattern:
        at += len(syllable)
        edges.add(at)
    offset = 0
    for tok in toks[i:]:
        end = offset + len(tok)
        if not tok or end not in edges or not joined.startswith(tok, offset):
            return False
        offset = end
        if offset == len(joined):
            return True
    return False


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
    """Return the banned phrases that appear in text as whole syllable sequences (V7, C1).

    Tone marks and case are ignored on both sides ("Dac tri" matches "đặc trị"); syllables
    still have to be next to each other, so "giá trị" never matches "trị bệnh", and a phrase
    that starts on the second syllable of a listed word ("giá trị bệnh nhân") is not a claim.

    Example:
        >>> find_banned_claims("Giá trị thật, đặc trị mọi bệnh", ["trị bệnh", "đặc trị"])
        ['đặc trị']
    """
    toks = fold_tokens(text)
    found: list[str] = []
    for phrase in banned:
        pat = fold_tokens(phrase)
        if any(_match_glued(toks, i, pat) and not _inside_word(toks, i, pat)
               for i in range(len(toks))):  # fmt: skip
            found.append(phrase)
    return found


def _inside_word(toks: Sequence[str], i: int, pattern: Sequence[str]) -> bool:
    """The match at toks[i] starts on the second syllable of a listed word ("trị" of "giá
    trị"), so it is that word, not a claim. toks[i] may hold more syllables ("trịbệnh")."""
    return i > 0 and bool(pattern) and (toks[i - 1], pattern[0]) in _WORD_BEFORE
