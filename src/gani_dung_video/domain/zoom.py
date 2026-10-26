"""Zoom effects (26/10/2026): which shots are framed closer or punch in, by how much, and when.

Three kinds, all read from rules.json → styles:
- `punch_zoom` (music styles, TVC cắt nhanh): a punch-in on the second beat of a shot;
- `zoom_alternate` (speech styles, Bán hàng có lời): every other phrase framed closer, so two
  phrases of one take do not jump-cut;
- `keyword_zoom` (speech mode, Review món ăn): a punch-in on a taste word the speaker says
  ("ngon", "giòn"…, rules.json → effects.keywords) while the speaker is on screen.

Punch-ins count against the style's `max_effects` (null = no cap); the alternate framing does
not. No zoom ever enlarges a clip's pixels by more than effects.max_upscale at the final size,
so low-resolution footage is simply left unzoomed. Pure: the render draws what this decides.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple

from gani_dung_video.domain.footage import Clip
from gani_dung_video.domain.speech import SpokenWord

_EPS = 1e-9


class Span(NamedTuple):
    """A shot as far as zooming goes: where it sits on the timeline and which clip it shows."""

    t_start: float
    t_end: float
    source: str


class Zoom(NamedTuple):
    base: float  # scale of the whole shot (1.0 = framed as before)
    punch_sec: float | None  # seconds into the shot where the punch-in happens
    punch: float  # scale from punch_sec on


Room = Callable[[str], float]


def zoom_room(clips: Mapping[str, Clip], width: int, height: int, max_upscale: float) -> Room:
    """Largest zoom per clip that keeps pixel enlargement ≤ max_upscale at width × height.

    Measured at the FINAL size, so the 540p preview makes the same choices as the 1080p video.
    """

    def room(source: str) -> float:
        c = clips[source]
        cover = max(width / c.width, height / c.height)  # scale that fills the frame
        return max_upscale / cover

    return room


def spread(n: int, k: int) -> list[int]:
    """`k` of the indices 0..n-1, evenly spread (first and last included)."""
    if k <= 0 or n == 0:
        return []
    if k >= n:
        return list(range(n))
    if k == 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def effect_cap(style: Mapping[str, Any], n: int, used: int = 0) -> int:
    """How many of `n` candidates fit in the style's max_effects once `used` are taken."""
    cap = style.get("max_effects")
    return n if cap is None else max(0, min(n, int(cap) - used))


def beat_punches(spans: Sequence[Span], style: Mapping[str, Any], beat_sec: float, fps: int,
                 room: Room) -> dict[int, Zoom]:  # fmt: skip
    """Music styles: punch in on the second beat of shots at least two beats long."""
    punch = style.get("punch_zoom")
    if not punch or beat_sec <= 0:
        return {}
    scale = 1.0 + float(punch)
    cands = [i for i, s in enumerate(spans)
             if s.t_end - s.t_start >= 2 * beat_sec - 0.5 / fps
             and room(s.source) >= scale - _EPS]  # fmt: skip
    picked = spread(len(cands), effect_cap(style, len(cands)))
    return {cands[j]: Zoom(1.0, beat_sec, scale) for j in picked}


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFC", text).lower())


def _first_keyword(words: Sequence[SpokenWord], keywords: Sequence[str]) -> SpokenWord | None:
    """The first spoken word that starts one of the keywords (whole words, tone marks kept)."""
    toks = [_norm(w.text) for w in words]
    kws = [[_norm(p) for p in k.split()] for k in keywords]
    for i in range(len(toks)):
        if any(toks[i : i + len(k)] == k for k in kws if k):
            return words[i]
    return None


def speech_zooms(
    spans: Sequence[Span], words: Sequence[Sequence[SpokenWord]],
    covered: Sequence[tuple[float, float]], style: Mapping[str, Any],
    effects: Mapping[str, Any], room: Room, reserve: int = 0,
) -> dict[int, Zoom]:  # fmt: skip
    """Speech mode: alternate framing, then keyword punch-ins within `max_effects` less the
    `reserve` slots kept for other effects (slow motion).

    `words[i]` are the words of shot i in video time; `covered` are the B-roll windows (a
    punch-in under B-roll would not be seen)."""
    alt = float(style.get("zoom_alternate") or 1.0)
    out = {i: Zoom(alt, None, alt) for i, s in enumerate(spans)
           if alt > 1.0 and i % 2 == 1 and room(s.source) >= alt - _EPS}  # fmt: skip
    punch = style.get("keyword_zoom")
    if not punch:
        return out
    hold = float(effects["keyword_min_hold_sec"])
    hits: list[tuple[int, float]] = []
    for i, (s, ws) in enumerate(zip(spans, words, strict=True)):
        w = _first_keyword(ws, effects["keywords"])
        if w is None or w.start < s.t_start or w.start + hold > s.t_end + _EPS:
            continue
        if any(w.start < b and a < w.start + hold for a, b in covered):
            continue
        base = out[i].base if i in out else 1.0
        if room(s.source) >= base * (1.0 + float(punch)) - _EPS:
            hits.append((i, w.start - s.t_start))
    for j in spread(len(hits), effect_cap(style, len(hits), reserve)):
        i, at = hits[j]
        base = out[i].base if i in out else 1.0
        out[i] = Zoom(base, at, base * (1.0 + float(punch)))
    return out
