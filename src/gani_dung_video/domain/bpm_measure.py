"""Measuring BPM safety with the real planner (ticket 13/10/2026).

`level_of_plan` is the exact verdict of a compiled plan; `sweep_*` produce and re-check the table
in rules.music.bpm_safe_by_style_duration (see bpm_safety for the levels and the lookup).
"""

from __future__ import annotations

from collections.abc import Sequence

from gani_dung_video.domain.bpm_safety import Level, check_bpm_safe, message
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import MusicInfo, Plan, PlanError, compile_plan, natural_sec
from gani_dung_video.domain.preflight import Rules

_EPS = 1e-9


def level_of_plan(plan: Plan, rules: Rules) -> Level:
    """The exact verdict of a compiled plan (what E1-DUR-GAP will say at delivery)."""
    if plan.driver == "speech":
        return "OK"
    chosen = float(plan.options["duration"])
    limit = float(rules["qc"]["duration_plan_gap_pct"]) * chosen
    if abs(natural_sec(plan) - chosen) > limit + _EPS:
        return "BLOCKING"
    return "WARNING" if plan.trim_to_sec is not None else "OK"


def blocked_text(rules: Rules, plan: Plan, bpm: float) -> str:
    """Why `plan` stops here, with the exact numbers and what to do (exit code 4)."""
    style, duration = str(plan.options["style"]), int(plan.options["duration"])
    nat = natural_sec(plan)
    buildable = check_bpm_safe(rules, style, duration, bpm).buildable
    return message(
        rules,
        "W_BPM_STOP",
        style,
        duration,
        bpm,
        buildable,
        plan=f"{nat:.1f}",
        gap=f"{nat - duration:+.1f}",
        share=f"{(nat - duration) / duration * 100:+.0f}",
    )


def levels_at(rules: Rules, style: str, duration: int, bpms: Sequence[int]) -> list[Level]:
    """The exact level the planner gives at each of `bpms` (3-minute videos are gated in the
    shipped rules, so the check opens them for the measurement only).

    Raises:
        PlanError: the style cannot build this length (speech style, 3 minutes).
    """
    cfg = {**rules, "durations": {**rules["durations"], str(duration): {
        **rules["durations"][str(duration)], "enabled": True}}}  # fmt: skip
    opts = VideoOptions(phong_cach=style, muc_dich="gioi_thieu_xuong", do_dai_giay=duration,
                        so_ban_nhap=1, nhac="thu_vien", nhac_file="x.mp3")  # fmt: skip
    out: list[Level] = []
    for bpm in bpms:
        track = MusicInfo(track="x.mp3", bpm=float(bpm), first_beat_sec=0.4,
                          duration_sec=900.0, source="library")  # fmt: skip
        plan = compile_plan(opts, cfg, music=track, available_tags=set())
        out.append(level_of_plan(plan, cfg))
    return out


def sweep_entry(rules: Rules, style: str, duration: int, lo: int, hi: int
                ) -> dict[str, list[list[int]]]:  # fmt: skip
    """Measure one table entry: compile the plan at every whole BPM in [lo, hi].

    This is how the table in rules.json is produced and how the test keeps it honest.
    """
    levels = levels_at(rules, style, duration, list(range(lo, hi + 1)))
    return {"blocked": _runs(levels, "BLOCKING", lo), "warning": _runs(levels, "WARNING", lo)}


def _runs(levels: Sequence[Level], wanted: Level, lo: int) -> list[list[int]]:
    out: list[list[int]] = []
    for i, level in enumerate(levels):
        if level != wanted:
            continue
        bpm = lo + i
        if out and out[-1][1] == bpm - 1:
            out[-1][1] = bpm
        else:
            out.append([bpm, bpm])
    return out


def sweep_table(rules: Rules, styles: Sequence[str], durations: Sequence[int],
                lo: int, hi: int) -> dict[str, dict[str, dict[str, list[list[int]]]]]:  # fmt: skip
    """The whole table; a style or length the plan cannot compile (speech 3-minute) is left out."""
    out: dict[str, dict[str, dict[str, list[list[int]]]]] = {}
    for style in styles:
        for duration in durations:
            try:
                entry = sweep_entry(rules, style, duration, lo, hi)
            except PlanError:
                continue
            out.setdefault(style, {})[str(duration)] = entry
    return out
