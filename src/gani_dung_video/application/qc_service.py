"""Use case: render, check the real file, fix what code can fix, at most 3 rounds (phase 5b).

Before rendering: captions that overlap (T6) stop everything; captions that are too long
(T7) get smaller fonts, down to 64 px, or stop everything. After each render: T1–T5 on the
file. Loudness off (T3) → loudness pass 1 + a linear second pass. Errors code cannot fix
(T1, T2, T4) end the loop at once: re-rendering the same EDL would give the same file.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.qc import (
    Finding,
    Measures,
    MediaFacts,
    WidthFn,
    check_captions,
    check_measures,
    check_media,
    fit_font_px,
)
from gani_dung_video.infrastructure.media_probe import measure as measure_file
from gani_dung_video.infrastructure.media_probe import probe_media

RenderFn = Callable[[Mapping[str, int], Mapping[str, float] | None], None]
LoudnessPass1 = Callable[[], Mapping[str, float]]
Prober = Callable[[Path, float], MediaFacts]
Measurer = Callable[[Path, Mapping[str, Any], float, float], Measures]


class QcReport(BaseModel):
    """Content of qc_report.json next to each video."""

    video: str
    passed: bool
    rounds: int  # renders done (0 = stopped before rendering)
    font_px: dict[str, int]
    loudness_two_pass: bool
    fixes: list[str]  # what code changed, in Vietnamese
    findings: list[Finding]  # what is left: errors, fixes that did not work, warnings


def check_file(video: Path, e: Edl, rules: Rules, probe: Prober = probe_media,
               measure: Measurer = measure_file) -> list[Finding]:  # fmt: skip
    """T1–T5 on a rendered file."""
    cfg = rules["qc"]
    timeout = float(cfg["probe_timeout_sec"])
    facts = probe(video, timeout)
    found = check_media(facts, e, cfg)
    if any(f.code == "T1" for f in found):
        return found  # wrong container: the other measurements would be meaningless
    return found + check_measures(measure(video, cfg, facts.duration, timeout), e, cfg)


def _before_render(e: Edl, rules: Rules, width: WidthFn
                   ) -> tuple[dict[str, int] | None, list[str], list[Finding]]:  # fmt: skip
    base = {k: int(v) for k, v in rules["render"]["font_px"].items() if k in ("caption", "cta")}
    found = check_captions(e, rules, base, width)
    stop = [f for f in found if f.code == "T6"]
    if stop:
        return None, [], stop
    if not found:
        return base, [], []
    sizes = fit_font_px(e, rules, base, width)
    if sizes is None:
        msg = [f.model_copy(update={"level": "error", "fix": None,
                                    "msg": f.msg + " Cần rút ngắn chữ."})
               for f in found]  # fmt: skip
        return None, [], msg
    fixes = [f"T7: cỡ chữ {k} {base[k]}→{v}px để không quá {rules['qc']['max_lines']} dòng"
             for k, v in sizes.items() if v != base[k]]  # fmt: skip
    return sizes, fixes, []


def render_checked(
    e: Edl, video: Path, rules: Rules, render: RenderFn, loudness_pass1: LoudnessPass1,
    width: WidthFn, probe: Prober = probe_media, measure: Measurer = measure_file,
) -> QcReport:  # fmt: skip
    """Render with QC; always returns the report (passed=False → do not deliver)."""
    sizes, fixes, stop = _before_render(e, rules, width)
    if sizes is None:
        return QcReport(video=video.name, passed=False, rounds=0, font_px={},
                        loudness_two_pass=False, fixes=[], findings=stop)  # fmt: skip
    loud: Mapping[str, float] | None = None
    found: list[Finding] = []
    rounds = 0
    for rounds in range(1, int(rules["qc"]["max_rounds"]) + 1):
        render(sizes, loud)
        found = check_file(video, e, rules, probe, measure)
        if any(f.level == "error" for f in found) or not any(f.fix == "loudness" for f in found):
            break
        if rounds < int(rules["qc"]["max_rounds"]):
            loud = loudness_pass1()
            fixes.append(f"T3: dựng lại vòng {rounds + 1} với chuẩn hóa âm lượng 2 lượt")
    passed = not any(f.level in ("error", "fix") for f in found)
    return QcReport(video=video.name, passed=passed, rounds=rounds, font_px=sizes,
                    loudness_two_pass=loud is not None, fixes=fixes, findings=found)  # fmt: skip
