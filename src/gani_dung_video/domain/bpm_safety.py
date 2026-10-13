"""BPM safety (ticket 13/10/2026): can the chosen track build a video of this style and length?

The plan is built on the track's beats, and every block needs at least one whole scene, so a
slow track can make a "15 s" video plan far longer than 15 s. The render then cuts the tail and
E1-DUR-GAP stops delivery when the plan is more than `qc.duration_plan_gap_pct` off. This module
tells the person earlier, at music selection, using a table measured with `compile_plan`
(`rules.music.bpm_safe_by_style_duration`), and states the verdict of a compiled plan exactly.

Levels:
    OK        the plan fits (within `qc.duration_trim_threshold_sec`): nothing is cut.
    WARNING   the tail is cut, within ±`duration_plan_gap_pct`: the video is built.
    BLOCKING  the plan is more than ±`duration_plan_gap_pct` off: delivery would be stopped.

The table lists, per style and chosen length, the BPM ranges (integer BPM, `bpm_safe_domain`)
of WARNING and BLOCKING. Anything else inside the domain is OK. It is a lookup for callers that
have no plan yet (the web wizard); a compiled plan is judged by `bpm_measure.level_of_plan`,
which is exact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # preflight imports this module: a runtime import would be circular
    from gani_dung_video.domain.preflight import Rules

BPM_BLOCKED_EXIT_CODE = 4  # `plan` exits with this when the track cannot build the video
BPM_BLOCKED_MARKER = "BPM BỊ CHẶN: "  # the line the queue reads back (see draft_job)

Level = Literal["OK", "WARNING", "BLOCKING"]
Range = tuple[int, int]


@dataclass(frozen=True)
class BpmCheck:
    level: Level
    bpm: float
    buildable: list[Range]  # BPM ranges in the table's domain that are not BLOCKING


def _entry(rules: Rules, style: str, duration: int) -> Mapping[str, Sequence[Sequence[int]]] | None:
    table = rules["music"]["bpm_safe_by_style_duration"]
    found = table.get(style, {}).get(str(duration))
    return found if isinstance(found, Mapping) else None


def _inside(bpm: int, ranges: Sequence[Sequence[int]]) -> bool:
    return any(lo <= bpm <= hi for lo, hi in ranges)


def check_bpm_safe(rules: Rules, style: str, duration: int, bpm: float) -> BpmCheck:
    """Look the track's BPM up in the measured table (nearest whole BPM).

    Outside `bpm_safe_domain`, or for a style/length the table does not list (speech styles),
    the answer is OK: the table says nothing there, the plan step still judges exactly.
    """
    lo, hi = (int(x) for x in rules["music"]["bpm_safe_domain"])
    entry = _entry(rules, style, duration)
    whole = round(bpm)
    if entry is None or not lo <= whole <= hi:
        return BpmCheck("OK", bpm, [(lo, hi)])
    blocked = [(int(a), int(b)) for a, b in entry["blocked"]]
    if _inside(whole, blocked):
        level: Level = "BLOCKING"
    elif _inside(whole, entry["warning"]):
        level = "WARNING"
    else:
        level = "OK"
    return BpmCheck(level, bpm, complement(blocked, lo, hi))


def complement(blocked: Sequence[Range], lo: int, hi: int) -> list[Range]:
    """The BPM ranges inside [lo, hi] that `blocked` leaves free."""
    out: list[Range] = []
    cur = lo
    for a, b in sorted(blocked):
        if a > cur:
            out.append((cur, a - 1))
        cur = max(cur, b + 1)
    if cur <= hi:
        out.append((cur, hi))
    return out


def format_ranges(ranges: Sequence[Range]) -> str:
    """'66–82, 88–99, 131–165' (a single BPM stays a single number)."""
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges)


def message(rules: Rules, code: str, style: str, duration: int, bpm: float,
            buildable: Sequence[Range], **extra: object) -> str:  # fmt: skip
    """The Vietnamese text for W_BPM_BLOCK / W_BPM_CUT (templates live in rules.json)."""
    return str(rules["compat_msgs"][code].format(
        bpm=f"{bpm:g}", style=rules["styles"][style]["name"], duration=duration,
        pct=f"{float(rules['qc']['duration_plan_gap_pct']) * 100:g}",
        ranges=format_ranges(buildable), **extra))  # fmt: skip
