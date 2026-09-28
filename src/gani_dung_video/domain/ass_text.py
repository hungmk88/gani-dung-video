"""Shared ASS/SRT text primitives with no dependency on Edl (avoids subtitles <-> karaoke
import cycle: both subtitles.py and karaoke.py depend on this module, not on each other).
"""

from __future__ import annotations


def ass_time(t: float) -> str:
    """Seconds → ASS time h:mm:ss.cc."""
    cs = round(max(t, 0.0) * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def escape_ass(text: str) -> str:
    """Make user/AI text inert: no override blocks, no escape sequences, one line."""
    return " ".join(text.split()).replace("\\", "/").replace("{", "(").replace("}", ")")
