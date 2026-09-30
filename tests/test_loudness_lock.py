"""Loudness settings are locked (Dev Package 03, T-B, decision 01/10/2026).

Target -14 LUFS (+-1) and a render true-peak of -2.0 dBTP. The QC ceiling sits above the render
target on purpose: measured on real renders, AAC encoding adds up to ~1.6 dBTP (a -1.9 dBTP mix
came out at -0.3), so a render target of -1.5 would send nearly every file back for a second
render. Changing any of these numbers must be a deliberate decision, not an accident.
"""

from __future__ import annotations

from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()


def test_render_targets_minus_14_lufs_and_minus_2_dbtp() -> None:
    assert RULES["render"]["lufs"] == -14.0
    assert RULES["render"]["true_peak_db"] == -2.0


def test_qc_accepts_one_lu_either_side_of_the_target() -> None:
    assert RULES["qc"]["lufs_tolerance"] == 1.0


def test_qc_true_peak_ceiling_leaves_room_for_aac_overshoot() -> None:
    qc, render = RULES["qc"], RULES["render"]
    assert qc["max_true_peak_db"] == -1.0
    assert qc["max_true_peak_db"] - render["true_peak_db"] >= 1.0  # room above the render target


def test_speech_styles_duck_the_music_18_db_under_the_voice() -> None:
    speech = [s for s in RULES["styles"].values() if "duck_db" in s]
    assert speech, "no style ducks the music"
    assert {s["duck_db"] for s in speech} == {-18}
