"""Word-highlight subtitles for speech videos (phase 5c-1, spec §5.1 "phụ đề từng từ").

Words are grouped 3–5 per line (balanced, never splitting a sentence or a pause). Each word
gets its own ASS event showing the whole group with only that word in the highlight colour,
so exactly one word is lit at a time and the line never blinks between words. Pure code.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from gani_dung_video.domain.ass_text import ass_time, escape_ass
from gani_dung_video.domain.emphasis import keyword_indexes
from gani_dung_video.domain.speech import SpokenWord

_HARD = ".?!…"


def ass_colour(hex_rgb: str) -> str:
    """'#RRGGBB' → ASS '&H00BBGGRR&' (opaque)."""
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _runs(words: Sequence[SpokenWord], gap: float) -> list[list[SpokenWord]]:
    """Split at sentence ends and at pauses of at least `gap` seconds."""
    runs: list[list[SpokenWord]] = [[]]
    for i, w in enumerate(words):
        if runs[-1] and w.start - words[i - 1].end >= gap - 1e-9:
            runs.append([])
        runs[-1].append(w)
        if w.text.strip()[-1:] in _HARD:
            runs.append([])
    return [r for r in runs if r]


def group_words(words: Sequence[SpokenWord], cfg: Mapping[str, Any]) -> list[list[SpokenWord]]:
    """Balanced groups of min_words..max_words inside each run (short runs stay whole)."""
    most = int(cfg["max_words"])
    out: list[list[SpokenWord]] = []
    for run in _runs(words, float(cfg["group_gap_sec"])):
        n = math.ceil(len(run) / most)
        base, extra = divmod(len(run), n)
        i = 0
        for k in range(n):
            size = base + (1 if k < extra else 0)
            out.append(run[i : i + size])
            i += size
    return out


def karaoke_dialogues(groups: Sequence[Sequence[SpokenWord]], cfg: Mapping[str, Any],
                      style: str, keywords: Sequence[str] = (),
                      pop: Mapping[str, Any] | None = None) -> list[str]:  # fmt: skip
    """One Dialogue line per word; word i lights from its start until word i+1 starts.

    Args:
        keywords: approved highlight words (T-C). Their words stay enlarged in every line of
            the group, on top of the usual one-word highlight. They are not tinted while
            waiting: the highlight colour keeps meaning "this word is being said".
        pop: rules.json render.keyword_highlight (scale_pct); needed with keywords.
    """
    lit, base = ass_colour(str(cfg["highlight"])), ass_colour(str(cfg["base"]))
    big = rf"\fscx{pop['scale_pct']}\fscy{pop['scale_pct']}" if pop and keywords else ""
    lines = []
    for g in groups:
        texts = [escape_ass(w.text) for w in g]
        marked = keyword_indexes([w.text for w in g], keywords) if big else set()
        for i, w in enumerate(g):
            start = g[0].start if i == 0 else w.start
            end = g[i + 1].start if i + 1 < len(g) else w.end
            parts = []
            for j, t in enumerate(texts):
                colour = lit if j == i else base  # lit colour = "being said"; keywords wait big
                if j in marked:
                    parts.append(rf"{{\c{colour}{big}}}{t}{{\c{base}\fscx100\fscy100}}")
                elif j == i:
                    parts.append(f"{{\\c{lit}}}{t}{{\\c{base}}}")
                else:
                    parts.append(t)
            shown = " ".join(parts)
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,"
                         f"{{\\c{base}}}{shown}")  # fmt: skip
    return lines
