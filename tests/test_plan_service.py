"""Integration tests (4a): options file + scenes.json + music → edit/plan.json, and the CLI."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.application.plan_service import BpmBlockedError, PreflightBlocked, build_plan
from gani_dung_video.cli import main
from gani_dung_video.domain.plan import PlanError
from gani_dung_video.domain.scenes import Scene, ScenesFile, Segment, usable_by_style
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.music_analysis import MusicError

RULES = load_rules()
SR = 8000
TAGS = ["nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham"]


def make_track(bpm: float, dur: float = 60.0, first: float = 0.5) -> np.ndarray:
    period = 60.0 / bpm
    x = np.zeros(int(dur * SR))
    t = np.arange(int(0.3 * SR)) / SR
    burst = np.sin(2 * np.pi * (55 + 90 * np.exp(-25 * t)) * t) * np.exp(-9 * t)
    k, at = 0, first
    while at < dur - 0.3:
        i = int(at * SR)
        x[i : i + len(burst)] += burst * (1.0 if k % 4 == 0 else 0.55)
        k, at = k + 1, first + (k + 1) * period
    x += 0.01 * np.random.default_rng(1).normal(size=len(x))
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def write_scenes(folder: Path, tags: list[str] | None = None, speech: bool | None = None) -> None:
    tags = TAGS if tags is None else tags
    segs = {
        style: [Segment(start=i * 6.0, end=i * 6.0 + 5.0, peak=i * 6.0 + 2.0,
                        labels=[tags[i % len(tags)]] if tags else [])
                for i in range(30)]
        for style in RULES["styles"]
    }  # fmt: skip
    sc = Scene(clip="a.mp4", index=0, start=0.0, end=200.0, sharpness=100, motion="tinh",
               jitter=0, consistency=1, segments=segs)  # fmt: skip
    (folder / "edit").mkdir(exist_ok=True)
    sf = ScenesFile(folder=str(folder), rules_version="2.0", scenes=[sc],
                    usable_by_style=usable_by_style([sc], RULES["styles"]))  # fmt: skip
    (folder / "edit" / "scenes.json").write_text(sf.model_dump_json(), encoding="utf-8")
    if speech is not None:
        (folder / "edit" / "speech.json").write_text(
            json.dumps({"model": "m", "co_loi_noi": speech, "clips": [], "failed": {}}),
            encoding="utf-8",
        )


def write_options(path: Path, **kw: Any) -> Path:
    base = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 30,
            "so_ban_nhap": 2, "nhac": "thu_vien", "nhac_file": "t.wav"}  # fmt: skip
    path.write_text(json.dumps({**base, **kw}), encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    f = tmp_path / "xuong"
    f.mkdir()
    write_scenes(f)
    write_wav(f / "t.wav", make_track(92.0))
    return f


def plan_json(folder: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((folder / "edit" / "plan.json").read_text(encoding="utf-8"))
    return data


def test_plan_end_to_end_measures_the_track(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json")
    plan, out = build_plan(opt, folder, rules=RULES)
    assert out == folder.resolve() / "edit" / "plan.json"
    assert plan.driver == "music" and plan.music is not None and plan.music.source == "detected"
    assert plan.music.bpm == pytest.approx(92.0, abs=0.3)
    assert plan.music.path == str((folder / "t.wav").resolve())  # checked path, for the render
    assert plan.target_beats == 46 and plan.planned_beats == 46 and plan.warnings == []
    assert plan_json(folder)["blocks"][0]["role"] == "hook"


def test_library_bpm_overrides_measurement(folder: Path, tmp_path: Path) -> None:
    (folder / "library.json").write_text(
        json.dumps({"tracks": {"t.wav": {"bpm": 90, "first_beat_sec": 0.4}}}), encoding="utf-8"
    )
    plan, _ = build_plan(write_options(tmp_path / "o.json"), folder, rules=RULES)
    assert plan.music is not None and plan.music.source == "library" and plan.music.bpm == 90


def test_track_outside_style_range_reports_the_x12_adjustment(folder: Path, tmp_path: Path) -> None:
    write_wav(folder / "f.wav", make_track(128.0))
    opt = write_options(tmp_path / "o.json", phong_cach="sang_trong", muc_dich="ban_hang",
                        nhac_file="f.wav")  # fmt: skip
    plan, _ = build_plan(opt, folder, rules=RULES)
    assert plan.music is not None and plan.music.fold == "dem_2_thanh_1"
    assert any(a.startswith("X12") for a in plan.adjustments)


def test_missing_required_tags_are_reported(folder: Path, tmp_path: Path) -> None:
    write_scenes(folder, tags=["thiet_bi"])
    plan, _ = build_plan(write_options(tmp_path / "o.json"), folder, rules=RULES)
    assert any(w.startswith("X8") and "nguoi_thao_tac" in w for w in plan.warnings)


def test_track_without_a_clear_beat_falls_back_to_the_grid(folder: Path, tmp_path: Path) -> None:
    write_wav(folder / "n.wav", np.random.default_rng(3).normal(scale=0.2, size=30 * SR))
    plan, _ = build_plan(write_options(tmp_path / "o.json", nhac_file="n.wav"), folder,
                         rules=RULES)  # fmt: skip
    assert plan.driver == "grid" and any(w.startswith("W_NO_BEAT") for w in plan.warnings)


def test_no_music_tvc_keeps_the_x11_warning_from_preflight(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json", phong_cach="tvc_cat_nhanh", muc_dich="ban_hang",
                        nhac="khong_nhac", nhac_file=None)  # fmt: skip
    plan, _ = build_plan(opt, folder, rules=RULES)
    assert plan.driver == "grid" and any(w.startswith("X11") for w in plan.warnings)


def test_preflight_block_stops_before_any_plan(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json", nhac="tai_len")  # rights not ticked → X14
    with pytest.raises(PreflightBlocked) as e:
        build_plan(opt, folder, rules=RULES)
    assert e.value.result["error_code"] == "X14"
    assert not (folder / "edit" / "plan.json").exists()


def test_scenes_json_is_required(tmp_path: Path) -> None:
    (tmp_path / "f").mkdir()
    with pytest.raises(ConfigError, match="scenes"):
        build_plan(write_options(tmp_path / "o.json"), tmp_path / "f", rules=RULES)


def test_music_mode_without_a_file_is_refused(folder: Path, tmp_path: Path) -> None:
    with pytest.raises(PlanError) as e:
        build_plan(write_options(tmp_path / "o.json", nhac_file=None), folder, rules=RULES)
    assert e.value.code == "PLAN_NO_TRACK"


def test_music_path_cannot_escape_the_allowed_folders(folder: Path, tmp_path: Path) -> None:
    write_wav(tmp_path / "outside.wav", make_track(92.0, dur=20.0))
    opts_dir = tmp_path / "opts"
    opts_dir.mkdir()
    opt = write_options(opts_dir / "o.json", nhac_file=str(tmp_path / "outside.wav"))
    with pytest.raises(MusicError, match="thư mục cho phép"):
        build_plan(opt, folder, rules=RULES)


def test_speech_style_compiles_when_the_footage_has_speech(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json", phong_cach="ban_hang_co_loi", muc_dich="ban_hang")
    write_scenes(folder, speech=True)
    plan, _ = build_plan(opt, folder, rules=RULES)
    assert plan.driver == "speech"


def test_cli_plan_prints_a_summary(
    folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["plan", str(write_options(tmp_path / "o.json")), str(folder)]) == 0
    out = capsys.readouterr().out
    assert "46 phách" in out and "hook: 4" in out and "plan.json" in out


def test_cli_plan_blocked_exit_1_and_bad_music_exit_2(
    folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = write_options(tmp_path / "b.json", nhac="tai_len")
    assert main(["plan", str(blocked), str(folder)]) == 1
    assert "X14" in capsys.readouterr().out
    bad = write_options(tmp_path / "c.json", nhac_file="../nope.wav")
    assert main(["plan", str(bad), str(folder)]) == 2


# ─────────────── ticket 13/10/2026: a track whose BPM cannot build the video ───────────────


def with_bpm(folder: Path, bpm: float) -> None:
    (folder / "library.json").write_text(
        json.dumps({"tracks": {"t.wav": {"bpm": bpm, "first_beat_sec": 0.4}}}), encoding="utf-8"
    )


def luxe_15(tmp_path: Path, **kw: Any) -> Path:
    return write_options(tmp_path / "o.json", phong_cach="sang_trong", muc_dich="gioi_thieu_xuong",
                         do_dai_giay=15, so_ban_nhap=1, **kw)  # fmt: skip


def test_a_track_that_cannot_build_the_video_stops_the_plan_and_writes_nothing(
    folder: Path, tmp_path: Path
) -> None:
    with_bpm(folder, 104)  # Sang trọng 15 s: 17.3 s planned, +15%
    with pytest.raises(BpmBlockedError, match="BPM 104") as err:
        build_plan(luxe_15(tmp_path), folder, rules=RULES)
    assert "+15%" in str(err.value) and "--bpm-override" in str(err.value)
    assert not (folder / "edit" / "plan.json").exists()


def test_bpm_override_goes_on_records_the_choice_and_drops_the_warning(
    folder: Path, tmp_path: Path
) -> None:
    with_bpm(folder, 104)
    plan, _ = build_plan(luxe_15(tmp_path), folder, rules=RULES, bpm_override=True)
    assert plan.options["bpm_override"] is True
    assert not any(w.startswith(("W_DUR_GAP", "W_BPM")) for w in plan.warnings)
    assert any("bpm_override" in a for a in plan.adjustments)
    assert plan_json(folder)["options"]["bpm_override"] is True


def test_the_options_file_can_carry_the_override_too(folder: Path, tmp_path: Path) -> None:
    with_bpm(folder, 104)
    plan, _ = build_plan(luxe_15(tmp_path, bpm_override=True), folder, rules=RULES)
    assert plan.options["bpm_override"] is True


def test_a_track_that_only_needs_a_cut_warns_and_goes_on(folder: Path, tmp_path: Path) -> None:
    with_bpm(folder, 110)  # +1.36 s, within 10%
    plan, _ = build_plan(luxe_15(tmp_path), folder, rules=RULES)
    assert plan.trim_to_sec == 15.0
    assert any(w.startswith("W_BPM_CUT") and "BPM 110" in w for w in plan.warnings)


def test_a_track_that_fits_has_no_bpm_warning(folder: Path, tmp_path: Path) -> None:
    with_bpm(folder, 80)  # Kể chuyện nghề 15 s: exactly 15.0 s
    opt = write_options(tmp_path / "o.json", muc_dich="gioi_thieu_xuong", do_dai_giay=15,
                        so_ban_nhap=1)  # fmt: skip
    plan, _ = build_plan(opt, folder, rules=RULES)
    assert plan.trim_to_sec is None and not any("BPM" in w for w in plan.warnings)


def test_cli_plan_exits_4_with_the_reason_on_stderr_unless_overridden(
    folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with_bpm(folder, 104)
    opt = luxe_15(tmp_path)
    assert main(["plan", str(opt), str(folder)]) == 4
    err = capsys.readouterr().err
    assert err.startswith("BPM BỊ CHẶN: ") and "BPM 104" in err and "Chọn bài khác" in err
    assert main(["plan", str(opt), str(folder), "--bpm-override"]) == 0
    out = capsys.readouterr()
    assert "BPM" not in out.err and "W_DUR_GAP" not in out.out  # the warning is switched off


def test_cli_plan_prints_the_bpm_cut_warning_on_stderr(
    folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with_bpm(folder, 110)
    assert main(["plan", str(luxe_15(tmp_path)), str(folder)]) == 0
    cap = capsys.readouterr()
    assert cap.err.startswith("CẢNH BÁO: Bài này ở BPM 110") and "cắt bớt" in cap.err
    assert "W_BPM" not in cap.out  # not repeated in the summary
