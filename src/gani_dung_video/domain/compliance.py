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

from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.glossary import find_banned_claims

CODE = "C1"

# Two karaoke groups are read as one sentence only when the silence between them is at most this
# long (NVH 14/10/2026); the people who own the claim list can tune it after real videos.
KARAOKE_JOIN_MAX_GAP_SEC = 0.5


@dataclass(frozen=True)
class ClaimHit:
    """One banned phrase found in one text."""

    source: str  # which kind of text, as named by the caller
    text: str  # the offending text, unchanged
    phrase: str  # the banned phrase from the glossary


def edl_texts(e: Edl) -> dict[str, str]:
    """Every text of one video that a viewer can read or hear, keyed by where it comes from.

    Keys are unique and say what to look at: "chữ 2" (the 2nd on-screen line), "tiêu đề 1",
    "CTA 1", "lời 3" (the 3rd spoken line, from the word timing) and "từ nhấn 1".
    """
    names = {
        "text": "chữ",
        "title": "tiêu đề",
        "cta": "CTA",
        "split_top": "nhãn so sánh",
        "split_bottom": "nhãn so sánh",
    }
    count: dict[str, int] = {}
    out: dict[str, str] = {}
    for c in e.captions:
        name = names.get(c.kind, "chữ")
        count[name] = count.get(name, 0) + 1
        out[f"{name} {count[name]}"] = c.text
    for i, group in enumerate(e.karaoke, start=1):
        out[f"lời {i}"] = " ".join(w.text for w in group)
    for i, keyword in enumerate(e.emphasis, start=1):
        out[f"từ nhấn {i}"] = keyword
    return out


def check_edl_claims(e: Edl, banned: Sequence[str]) -> list[ClaimHit]:
    """C1 over everything a viewer reads or hears: every text of `edl_texts`, plus phrases that
    the karaoke grouping splits (spoken as one sentence, drawn as two or more groups).

    Two consecutive groups are read together only when they belong to the same cut (one phrase
    of the speaker) and the silence between them is at most KARAOKE_JOIN_MAX_GAP_SEC: across a
    cut or a long pause the words are not one sentence, and joining them would flag valid
    videos (NVH 14/10/2026). A phrase wholly inside one group is reported once, under that
    group's own name; one that crosses groups is named "lời 1–2" with the text it covers.
    """
    hits = check_claims(edl_texts(e), banned)
    groups = [" ".join(w.text for w in g) for g in e.karaoke]
    for phrase in banned:
        if any(find_banned_claims(g, [phrase]) for g in groups):
            continue  # already reported (or about to be) as a single group's text
        for run in _joined_runs(e):
            span = _crossing_span([groups[i] for i in run], phrase)
            if span is not None:
                first, last = run[span[0]], run[span[1]]
                hits.append(ClaimHit(f"lời {first + 1}–{last + 1}",
                                     " ".join(groups[first : last + 1]), phrase))  # fmt: skip
                break
    return hits


def _joined_runs(e: Edl) -> list[list[int]]:
    """Indexes of karaoke groups, split wherever two neighbours are not one sentence."""
    spans = sorted((s.t_start, s.t_end) for s in e.shots)  # one shot per spoken phrase

    def cut_of(t: float) -> int:
        for i, (a, b) in enumerate(spans):
            if a - 1e-6 <= t < b - 1e-6:
                return i
        return -1  # outside every cut: never joined

    runs: list[list[int]] = []
    for i, g in enumerate(e.karaoke):
        if not g:
            continue
        if runs and e.karaoke[runs[-1][-1]]:
            prev = e.karaoke[runs[-1][-1]]
            same_cut = cut_of(prev[-1].start) == cut_of(g[0].start) != -1
            if same_cut and g[0].start - prev[-1].end <= KARAOKE_JOIN_MAX_GAP_SEC + 1e-9:
                runs[-1].append(i)
                continue
        runs.append([i])
    return runs


def _crossing_span(groups: Sequence[str], phrase: str) -> tuple[int, int] | None:
    """The smallest run of consecutive groups whose joined text holds `phrase`, if any."""
    for first in range(len(groups)):
        text = ""
        for last in range(first, len(groups)):
            text = f"{text} {groups[last]}".strip()
            if find_banned_claims(text, [phrase]):
                return (first, last) if last > first else None
    return None


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
