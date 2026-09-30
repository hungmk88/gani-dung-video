"""Tests (5b): application/qc_service.py — check after render, auto-fix, at most 3 rounds.

Render, probe and measure are fakes, so every branch runs in milliseconds.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from gani_dung_video.application.qc_service import QcReport, render_checked
from gani_dung_video.domain.edl import Caption, Edl, Shot
from gani_dung_video.domain.qc import Measures, MediaFacts
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
LONG = "Bò viên GANI tròn đều dai giòn làm mỗi sáng"


def edl(captions: list[Caption] | None = None) -> Edl:
    shots = [Shot(source="a.mp4", src_in=0, src_out=3, t_start=0, t_end=3, block="b", hdr=False,
                  dissolve_out_sec=0, pad_sec=0)]  # fmt: skip
    return Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500,
               shots=shots, content_end=3.0, end_card_sec=2.0, duration=5.0, music=None,
               ambient_db=-20, lufs=-14.0, true_peak_db=-1.5, font="Be Vietnam Pro ExtraBold",
               captions=captions or [Caption(start=0, end=3, text="Một ngày",
                                             kind="text")])  # fmt: skip


FACTS = MediaFacts(width=540, height=960, fps=30.0, duration=5.0, frames=150, video_codec="h264",
                   pix_fmt="yuv420p", audio_codec="aac", sample_rate=48000, channels=2)  # fmt: skip


def width_of(text: str, font: str, px: int) -> float:
    return len(text) * px * 0.55


class Rig:
    """Fake render/probe/measure; `loud` is what each successive measurement returns."""

    def __init__(self, *loud: float, blacks: list[tuple[float, float]] | None = None,
                 freezes: list[tuple[float, float]] | None = None,
                 peaks: list[float] | None = None) -> None:  # fmt: skip
        self.loud, self.blacks, self.freezes = list(loud), blacks or [], freezes or []
        self.peaks = peaks or [-1.4]  # what each successive measurement returns (last repeats)
        self.renders: list[tuple[dict[str, int], Mapping[str, float] | None]] = []
        self.ceilings: list[float | None] = []  # the true-peak ceiling each render was given
        self.pass1 = 0

    def render(self, font_px: Mapping[str, int], loudnorm: Mapping[str, float] | None,
               true_peak_db: float | None = None) -> None:  # fmt: skip
        self.renders.append((dict(font_px), loudnorm))
        self.ceilings.append(true_peak_db)

    def loudness_pass1(self) -> Mapping[str, float]:
        self.pass1 += 1
        return {"input_i": -20.0, "input_tp": -3.0, "input_lra": 2.0, "input_thresh": -30.0,
                "target_offset": 0.1}  # fmt: skip

    def probe(self, path: Path, timeout: float) -> MediaFacts:
        return FACTS

    def measure(self, path: Path, cfg: Mapping[str, object], duration: float,
                timeout: float) -> Measures:  # fmt: skip
        lufs = self.loud.pop(0) if len(self.loud) > 1 else self.loud[0]
        peak = self.peaks.pop(0) if len(self.peaks) > 1 else self.peaks[0]
        return Measures(lufs=lufs, true_peak_db=peak, blacks=self.blacks, freezes=self.freezes)


def run(rig: Rig, e: Edl | None = None) -> object:
    return render_checked(e or edl(), Path("v.mp4"), RULES, rig.render, rig.loudness_pass1,
                          width_of, probe=rig.probe, measure=rig.measure)  # fmt: skip


def test_a_good_first_render_passes_in_one_round() -> None:
    rig = Rig(-14.1)
    rep = run(rig)
    assert rep.passed and rep.rounds == 1 and rep.fixes == []  # type: ignore[attr-defined]
    assert rig.renders == [({"caption": 72, "cta": 80}, None)] and rig.pass1 == 0


def test_loudness_off_triggers_one_two_pass_rerender() -> None:
    rig = Rig(-16.0, -14.05)
    rep = run(rig)
    assert rep.passed and rep.rounds == 2 and rig.pass1 == 1  # type: ignore[attr-defined]
    assert rig.renders[1][1] is not None and rig.renders[1][1]["input_i"] == -20.0
    assert any("T3" in f for f in rep.fixes)  # type: ignore[attr-defined]


def test_gives_up_after_three_rounds() -> None:
    rig = Rig(-16.0, -16.0, -16.0, -16.0)
    rep = run(rig)
    assert not rep.passed and rep.rounds == 3 and len(rig.renders) == 3  # type: ignore[attr-defined]
    assert [f.code for f in rep.findings] == ["T3"]  # type: ignore[attr-defined]


def test_black_frames_fail_without_a_pointless_rerender() -> None:
    rig = Rig(-14.1, blacks=[(1.0, 1.5)])
    rep = run(rig)
    assert not rep.passed and rep.rounds == 1  # type: ignore[attr-defined]
    assert [f.code for f in rep.findings] == ["T4"]  # type: ignore[attr-defined]


def test_a_freeze_is_reported_but_still_passes() -> None:
    rep = run(Rig(-14.1, freezes=[(0.2, 2.5)]))
    assert rep.passed and [f.code for f in rep.findings] == ["T5"]  # type: ignore[attr-defined]


def test_long_caption_is_shrunk_before_rendering() -> None:
    rig = Rig(-14.1)
    rep = run(rig, edl([Caption(start=0, end=3, text=LONG, kind="text")]))
    assert rep.passed and rig.renders[0][0] == {"caption": 68, "cta": 80}  # type: ignore[attr-defined]
    assert any("T7" in f and "68" in f for f in rep.fixes)  # type: ignore[attr-defined]


def test_unfixable_caption_or_overlap_stops_before_rendering() -> None:
    for caps in ([Caption(start=0, end=3, text=f"{LONG} {LONG}", kind="text")],
                 [Caption(start=0, end=2, text="A", kind="text"),
                  Caption(start=1, end=3, text="B", kind="cta")]):  # fmt: skip
        rig = Rig(-14.1)
        rep = run(rig, edl(caps))
        assert not rep.passed and rep.rounds == 0 and rig.renders == []  # type: ignore[attr-defined]
        assert rep.findings[0].code in ("T6", "T7") and rep.findings[0].level == "error"  # type: ignore[attr-defined]


# ─────────────── measured numbers in the report (6b) ───────────────


def test_the_report_carries_the_measured_loudness_and_peak() -> None:
    rep = run(Rig(-14.1))
    assert rep.lufs == -14.1 and rep.true_peak_db == -1.4  # type: ignore[attr-defined]


def test_the_numbers_are_those_of_the_last_render_round() -> None:
    rep = run(Rig(-16.0, -14.05))
    assert rep.rounds == 2 and rep.lufs == -14.05  # type: ignore[attr-defined]


def test_a_report_stopped_before_rendering_has_no_numbers() -> None:
    rep = run(Rig(-14.1), edl([Caption(start=0, end=3, text=f"{LONG} {LONG}", kind="text")]))
    assert rep.rounds == 0 and rep.lufs is None and rep.true_peak_db is None  # type: ignore[attr-defined]


def test_silence_is_stored_as_no_number_not_minus_infinity() -> None:
    rep = run(Rig(float("-inf")))
    assert rep.lufs is None  # type: ignore[attr-defined]
    QcReport.model_validate_json(rep.model_dump_json())  # type: ignore[attr-defined]


def test_a_report_written_before_6b_still_loads() -> None:
    old = ('{"video": "v.mp4", "passed": true, "rounds": 1, "font_px": {}, '
           '"loudness_two_pass": false, "fixes": [], "findings": []}')  # fmt: skip
    rep = QcReport.model_validate_json(old)
    assert rep.lufs is None and rep.true_peak_db is None


# ─────────────── a peak the encoder made (phase 7d) ───────────────
# Found on the first 180 s preview (01/10/2026): the mix was -14.0 LUFS / -1.9 dBTP, the AAC
# encoder delivered -0.3 dBTP. A second loudness pass cannot help (the float mix was already
# right); limiting the mix to a lower ceiling by the overshoot can.


def test_a_peak_made_by_the_encoder_is_fixed_by_one_render_with_a_lower_ceiling() -> None:
    rig = Rig(-14.1, peaks=[-0.3, -1.3])
    rep = run(rig)
    assert rep.passed and rep.rounds == 2 and rig.pass1 == 0  # type: ignore[attr-defined]
    assert rig.ceilings[0] is None  # the first render uses the rules' ceiling
    # the file was 1.2 dB above the -1.5 ceiling it asked for; aim 1.2 + 0.3 margin under the limit
    assert rig.ceilings[1] == -2.5  # -1.0 (limit) - 1.2 (what the encoder added) - 0.3 (margin)
    assert any("T3" in f and "-2.5" in f for f in rep.fixes)  # type: ignore[attr-defined]
    assert rep.true_peak_db == -1.3  # type: ignore[attr-defined]


def test_the_ceiling_is_not_lowered_below_the_floor() -> None:
    rig = Rig(-14.1, peaks=[6.0, -1.3])
    run(rig)
    assert rig.ceilings[1] == RULES["qc"]["min_true_peak_db"]


def test_if_the_lower_ceiling_does_not_help_it_stops_instead_of_looping() -> None:
    rig = Rig(-14.1, peaks=[-0.3, -0.4, -0.5])
    rep = run(rig)
    assert not rep.passed and rep.rounds == 2 and len(rig.renders) == 2  # type: ignore[attr-defined]
    assert [f.code for f in rep.findings] == ["T3"]  # type: ignore[attr-defined]
    assert "đỉnh" in rep.findings[0].msg  # type: ignore[attr-defined]


def test_a_good_peak_never_asks_for_a_lower_ceiling() -> None:
    rig = Rig(-14.1, peaks=[-1.4])
    run(rig)
    assert rig.ceilings == [None]


def test_loudness_off_still_uses_the_two_pass_fix_and_no_lower_ceiling() -> None:
    rig = Rig(-16.0, -14.05)
    run(rig)
    assert rig.ceilings == [None, None] and rig.pass1 == 1
