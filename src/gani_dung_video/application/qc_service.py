"""Use case: render, check the real file, fix what code can fix, at most 3 rounds (phase 5b).

Before rendering: captions that overlap (T6) stop everything; captions that are too long
(T7) get smaller fonts, down to 64 px, or stop everything. After each render: T1–T5 on the
file. Loudness off (T3) → loudness pass 1 + a linear second pass. Errors code cannot fix
(T1, T2, T4) end the loop at once: re-rendering the same EDL would give the same file.

T8 (phase 5c-3, speech styles, final render only) listens to the finished video with Whisper
and comes after this loop: spoken words cannot be re-rendered better, so it only reports.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from gani_dung_video.domain.asr_check import (
    AsrResult,
    T8Measures,
    compare,
    diagnose,
    measures_of,
    to_tokens,
    within_spans,
)
from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.glossary import Term
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
    fit_keys,
)
from gani_dung_video.domain.speech import fix_names
from gani_dung_video.infrastructure.ffmpeg_render import RenderError
from gani_dung_video.infrastructure.media_probe import measure as measure_file
from gani_dung_video.infrastructure.media_probe import probe_media
from gani_dung_video.infrastructure.whisper_asr import AsrError, Transcriber


class RenderFn(Protocol):
    """Render one round. `true_peak_db` (only given after a peak the encoder made, phase 7d)
    replaces the ceiling the mix is limited to."""

    def __call__(
        self,
        font_px: Mapping[str, int],
        loudnorm: Mapping[str, float] | None,
        /,
        true_peak_db: float | None = None,
    ) -> None: ...


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
    # What the last render measured (phase 6b: the dashboard shows real numbers). None when
    # nothing was measured (stopped before rendering, wrong container, silence).
    lufs: float | None = None
    true_peak_db: float | None = None
    # What Whisper measured (T8, speech-mode finals only); None when nobody listened, so a
    # report written before phase 6f still loads.
    t8: T8Measures | None = None


def check_file_measured(video: Path, e: Edl, rules: Rules, probe: Prober = probe_media,
                        measure: Measurer = measure_file
                        ) -> tuple[list[Finding], Measures | None]:  # fmt: skip
    """T1–T5 on a rendered file, and the numbers they were judged on (None when the
    container was wrong and nothing else was measured)."""
    cfg = rules["qc"]
    timeout = float(cfg["probe_timeout_sec"])
    facts = probe(video, timeout)
    found = check_media(facts, e, cfg)
    if any(f.code == "T1" for f in found):
        return found, None  # wrong container: the other measurements would be meaningless
    measures = measure(video, cfg, facts.duration, timeout)
    return found + check_measures(measures, e, cfg), measures


def check_file(video: Path, e: Edl, rules: Rules, probe: Prober = probe_media,
               measure: Measurer = measure_file) -> list[Finding]:  # fmt: skip
    """T1–T5 on a rendered file."""
    return check_file_measured(video, e, rules, probe, measure)[0]


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def _before_render(e: Edl, rules: Rules, width: WidthFn
                   ) -> tuple[dict[str, int] | None, list[str], list[Finding]]:  # fmt: skip
    base = {k: int(v) for k, v in rules["render"]["font_px"].items() if k in fit_keys(e)}
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


def _lower_ceiling(e: Edl, m: Measures, rules: Rules) -> float:
    """The ceiling to limit the mix to on the next render: aim `margin` under the limit after the
    encoder adds what it added this time (peak measured minus the ceiling that was asked for)."""
    cfg = rules["qc"]
    added = m.true_peak_db - e.true_peak_db
    aim = float(cfg["max_true_peak_db"]) - added - float(cfg["peak_retry_margin_db"])
    return round(max(aim, float(cfg["min_true_peak_db"])), 1)


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
    ceiling: float | None = None  # lowered true-peak ceiling, set once after an encoder peak
    found: list[Finding] = []
    measures: Measures | None = None
    rounds = 0
    for rounds in range(1, int(rules["qc"]["max_rounds"]) + 1):
        if ceiling is None:
            render(sizes, loud)
        else:
            render(sizes, loud, true_peak_db=ceiling)
        found, measures = check_file_measured(video, e, rules, probe, measure)
        fixable = {f.fix for f in found if f.fix in ("loudness", "peak")}
        if any(f.level == "error" for f in found) or not fixable:
            break
        if rounds >= int(rules["qc"]["max_rounds"]):
            break
        if "peak" in fixable and ceiling is None and measures is not None:
            ceiling = _lower_ceiling(e, measures, rules)
            fixes.append(f"T3: dựng lại vòng {rounds + 1} với trần đỉnh {ceiling:g} dBTP "
                         "(bộ mã hóa AAC làm đỉnh cao hơn mức đã giới hạn)")  # fmt: skip
        elif "loudness" in fixable:  # measured again each round, as before phase 7d
            loud = loudness_pass1()
            fixes.append(f"T3: dựng lại vòng {rounds + 1} với chuẩn hóa âm lượng 2 lượt")
        else:
            break  # a lower ceiling was already tried: report it instead of looping
    passed = not any(f.level in ("error", "fix") for f in found)
    return QcReport(video=video.name, passed=passed, rounds=rounds, font_px=sizes,
                    loudness_two_pass=loud is not None, fixes=fixes, findings=found,
                    lufs=_finite(measures.lufs) if measures else None,
                    true_peak_db=_finite(measures.true_peak_db) if measures else None)  # fmt: skip


def _listen(path: Path, e: Edl, rules: Rules, transcribe: Transcriber, terms: Sequence[Term]
            ) -> AsrResult:  # fmt: skip
    """Transcribe `path`, apply the glossary, keep the words heard over the voice, compare."""
    cfg = rules["qc"]["t8"]
    heard = to_tokens(fix_names(transcribe(path).words, terms))
    heard = within_spans(heard, e.duck, float(cfg["span_margin_sec"]))
    return compare(to_tokens([w for g in e.karaoke for w in g]), heard)


def listen_speech_asr(
    video: Path, e: Edl, rules: Rules, transcribe: Transcriber, terms: Sequence[Term],
    voice_only: Callable[[], Path] | None = None,
) -> tuple[list[Finding], T8Measures | None]:  # fmt: skip
    """T8 on a rendered speech-mode video (phase 5c-3): WER blocks, subtitle drift warns.
    Also returns what was measured (None when nothing was listened to or compared).

    When WER fails and the video has music, `voice_only` (renders the mix without music) is
    listened to as well, so the message says whether music masks the voice.
    A recogniser that cannot run is reported as a warning, never raised: the video is done.
    """
    if e.mode != "speech" or not e.karaoke:
        return [], None
    try:
        mix = _listen(video, e, rules, transcribe, terms)
        voice: AsrResult | None = None
        if mix.wer > float(rules["qc"]["t8"]["max_wer"]) and e.music is not None and voice_only:
            try:
                voice = _listen(voice_only(), e, rules, transcribe, terms)
            except RenderError:
                voice = None  # cannot tell why; the plain verdict still stands
    except AsrError as err:
        return [Finding(code="T8", level="warn",
                        msg=f"Không chạy được hậu kiểm Whisper: {err}")], None  # fmt: skip
    cfg = rules["qc"]["t8"]
    return diagnose(mix, voice, cfg), measures_of(mix, voice, cfg)


def check_speech_asr(
    video: Path, e: Edl, rules: Rules, transcribe: Transcriber, terms: Sequence[Term],
    voice_only: Callable[[], Path] | None = None,
) -> list[Finding]:  # fmt: skip
    """The findings of `listen_speech_asr` (T8), without the numbers."""
    return listen_speech_asr(video, e, rules, transcribe, terms, voice_only)[0]
