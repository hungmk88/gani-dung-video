"""Tests (5b): application/qc_service.py — check after render, auto-fix, at most 3 rounds.

Render, probe and measure are fakes, so every branch runs in milliseconds.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from gani_dung_video.application.qc_service import render_checked
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
                 freezes: list[tuple[float, float]] | None = None) -> None:  # fmt: skip
        self.loud, self.blacks, self.freezes = list(loud), blacks or [], freezes or []
        self.renders: list[tuple[dict[str, int], Mapping[str, float] | None]] = []
        self.pass1 = 0

    def render(self, font_px: Mapping[str, int], loudnorm: Mapping[str, float] | None) -> None:
        self.renders.append((dict(font_px), loudnorm))

    def loudness_pass1(self) -> Mapping[str, float]:
        self.pass1 += 1
        return {"input_i": -20.0, "input_tp": -3.0, "input_lra": 2.0, "input_thresh": -30.0,
                "target_offset": 0.1}  # fmt: skip

    def probe(self, path: Path, timeout: float) -> MediaFacts:
        return FACTS

    def measure(self, path: Path, cfg: Mapping[str, object], duration: float,
                timeout: float) -> Measures:  # fmt: skip
        lufs = self.loud.pop(0) if len(self.loud) > 1 else self.loud[0]
        return Measures(lufs=lufs, true_peak_db=-1.4, blacks=self.blacks, freezes=self.freezes)


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
