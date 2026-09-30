"""C1 — claims & compliance check (Dev Package 03, T0, decision 01/10/2026).

One question for every text that can reach a viewer, whatever its source: does it contain a
phrase from glossary.json → banned_claims? Sources are named by the caller ("thoại" for spoken
lines, "tiêu đề" for chapter titles, "từ nhấn" for highlighted keywords) so a finding says
where to fix it. Matching is done by glossary.find_banned_claims: whole syllables next to each
other, case and tone marks ignored.

Pure functions, no I/O: the phrase list comes from the caller (load_glossary()).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gani_dung_video.domain.glossary import find_banned_claims

CODE = "C1"


@dataclass(frozen=True)
class ClaimHit:
    """One banned phrase found in one text."""

    source: str  # which kind of text, as named by the caller
    text: str  # the offending text, unchanged
    phrase: str  # the banned phrase from the glossary


def check_claims(fields: Mapping[str, str], banned: Sequence[str]) -> list[ClaimHit]:
    """List every banned phrase in every text, in the order the texts were given.

    Args:
        fields: Source name → text to check. Blank texts are fine.
        banned: Banned phrases (glossary.banned_claims).

    Returns:
        One ClaimHit per (text, phrase); empty when everything is clean.
    """
    return [
        ClaimHit(source, text, phrase)
        for source, text in fields.items()
        for phrase in find_banned_claims(text, banned)
    ]
