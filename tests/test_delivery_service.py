"""Integration tests (T-E): render → E1 checks → manifest.json → delivery tree. Real ffmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from ffmpeg_marks import needs_render_ffmpeg

from gani_dung_video.application import render_service
from gani_dung_video.application.draft_service import DraftsFile
from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.render_service import SpeechDraftsFile, build_render
from gani_dung_video.cli import main
from gani_dung_video.domain.beats import DraftSegment, SceneRef, snap_to_beats
from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.edl import SpeechDraft
from gani_dung_video.domain.manifest import Manifest, sha256_of
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import MusicInfo, Plan, beats_of, compile_plan
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.speech_edit import SpeechCut
from gani_dung_video.infrastructure.config_files import ConfigError, GlossaryFile, load_rules

pytestmark = needs_render_ffmpeg
RULES = load_rules()
PATTERN = re.compile(r"^GANI_[a-z0-9-]+_[a-z0-9-]+_[a-z0-9-]+_\d+s_9x16_v\d+_\d{8}\.mp4$")
TODAY = datetime.now().astimezone().strftime("%Y%m%d")
CHECKS = ["E1-DUR", "E1-DUR-GAP", "E1-FORMAT", "E1-LOUD", "E1-PICTURE", "E1-SAFE", "E1-GLYPH", "C1"]


def make_clip(out: Path, src: str, dur: float) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{src}:d={dur}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(out)], check=True, timeout=120)  # fmt: skip


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("clips")
    make_clip(d / "a.mp4", "testsrc2=s=360x640:r=30", 8.0)
    make_clip(d / "b.mp4", "smptebars=s=640x360:r=30", 8.0)
    return d


def _scenes(allowed: list[int], total: int) -> list[int]:
    """Split `total` beats into scenes of the allowed sizes (smallest first, always finishable)."""
    ok = [True] + [False] * total
    for n in range(1, total + 1):
        ok[n] = any(n >= a and ok[n - a] for a in allowed)
    out, left = [], total
    while left:
        step = next(a for a in sorted(allowed) if a <= left and ok[left - a])
        out.append(step)
        left -= step
    return out


def make_project(root: Path, clips: Path, name: str, opts: dict[str, Any],
                 texts: dict[str, str] | None = None,
                 music: MusicInfo | None = None) -> Path:  # fmt: skip
    """A footage folder with plan.json and drafts.json for a beat-driven style."""
    d = root / name
    d.mkdir()
    for f in ("a.mp4", "b.mp4"):
        shutil.copy(clips / f, d / f)
    build_inventory(d, RULES)
    plan: Plan = compile_plan(VideoOptions(**opts), RULES, music=music, available_tags=set())
    if plan.music is not None and music is not None:  # the plan step resolves the file
        plan = plan.model_copy(update={"music": plan.music.model_copy(update={"path": music.path})})
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    refs = {"a_s0_0": SceneRef(clip="a.mp4", start=0, end=8, peak=2),
            "b_s0_0": SceneRef(clip="b.mp4", start=0, end=8, peak=4)}  # fmt: skip
    words = texts or {plan.blocks[0].role: "Giòn ngon", plan.blocks[-1].role: "Nhắn tin đặt hàng"}
    cuts: list[DraftSegment] = []
    for block in plan.blocks:
        for i, beats in enumerate(_scenes(plan.beats_per_scene_allowed, block.beats)):
            cuts.append(
                DraftSegment(
                    block=block.role,
                    scene_id=["a_s0_0", "b_s0_0"][len(cuts) % 2],
                    beats=beats,
                    text=words.get(block.role, "") if i == 0 else "",
                )
            )
    snapped = snap_to_beats(cuts, beats_of(plan), refs)
    drafts = DraftsFile(model="fake", attempts=1, input_tokens=1, output_tokens=1, cost_vnd=1.0,
                        cap_vnd=20000, notes=[],
                        drafts=[SnappedDraft(draft="A", label="Tối ưu", segments=snapped,
                                             missing_shots=[], warnings=[])])  # fmt: skip
    (d / "edit" / "drafts.json").write_text(drafts.model_dump_json(), encoding="utf-8")
    return d


def opts(style: str = "tvc_cat_nhanh", purpose: str = "ban_hang") -> dict[str, Any]:
    return {"phong_cach": style, "muc_dich": purpose, "do_dai_giay": 15, "so_ban_nhap": 1,
            "nhac": "khong_nhac"}  # fmt: skip


def manifest_of(folder: Path) -> Manifest:
    text = (folder / "edit" / "render" / "A" / "manifest.json").read_text(encoding="utf-8")
    return Manifest.model_validate_json(text)


def assert_numbers(m: Manifest) -> None:
    """Every E1 item carries the number it was judged on (E1-FORMAT is a yes/no on the
    container; E1-LOUD has no number only when the video is silent by plan)."""
    for i in m.e1.items:
        if i.code == "E1-FORMAT":
            continue
        silent = i.code == "E1-LOUD" and "không tiếng" in i.detail
        assert i.value is not None or silent, (i.code, i.detail)


def delivery(folder: Path, campaign: str) -> Path:
    return folder / "edit" / "delivery" / campaign


# ─────────────── a video that passes ───────────────


def test_a_passing_render_gets_a_manifest_and_an_e3_copy(tmp_path: Path, clips: Path) -> None:
    folder = make_project(tmp_path, clips, "xuong", opts())
    log = {"mode": "music", "attempt": 1, "drafts": ["A"], "model": "claude-sonnet-5-5",
           "input_tokens": 3373, "output_tokens": 1949, "cost_vnd": 669.02}  # fmt: skip
    (folder / "edit" / "ai_log.jsonl").write_text(json.dumps(log) + "\n", encoding="utf-8")
    [res] = build_render(folder, preview=True, rules=RULES, product="Giò Lụa",
                         campaign="Ra mắt Tháng 10")  # fmt: skip
    assert res.qc_passed
    m = manifest_of(folder)
    # E2: every field the owner listed
    assert m.options == {"style": "tvc_cat_nhanh", "purpose": "ban_hang", "duration": 15,
                         "drafts": 1, "music": "khong_nhac"}  # fmt: skip
    plan_bytes = (folder / "edit" / "plan.json").read_bytes()
    assert m.plan_sha256 == sha256_of(plan_bytes) and len(m.rules_sha256) == 64
    assert len(m.glossary_sha256) == 64 and m.rules_version == RULES["rules_version"]
    assert m.models == {"ai": "fake", "asr": None}
    assert m.source_files == ["a.mp4", "b.mp4"]
    assert m.created_at.endswith("+00:00") and m.resolution == "540x960" and m.draft == "A"
    assert [i.code for i in m.e1.items] == CHECKS and m.e1.passed
    assert_numbers(m)
    # TVC 15 s: 12 beats, the 1.3 s grid is stretched to 1.25 s, so plan and render are both 15 s
    assert m.deviation_vs_plan == pytest.approx(0.0, abs=0.1)
    assert m.deviation_vs_chosen == pytest.approx(0.0, abs=0.1)
    assert (m.e1.deviation_vs_chosen, m.e1.deviation_vs_plan) == (
        m.deviation_vs_chosen,
        m.deviation_vs_plan,
    )
    # E4: the real bill from ai_log.jsonl and the real clock
    assert m.cost.source == "ai_log.jsonl" and m.cost.ai_share_cost_vnd == pytest.approx(669.02)
    assert m.cost.ai_run_input_tokens == 3373 and m.cost.ai_run_output_tokens == 1949
    assert m.cost.render_sec > 0
    # E3: the copy under the campaign tree
    root = delivery(folder, "ra-mat-thang-10")
    assert {p.name for p in root.iterdir()} == {"raw", "drafts", "final", "metadata"}
    [copy] = list((root / "drafts").iterdir())
    assert PATTERN.match(copy.name) and m.delivered_as == copy.name
    assert copy.name.startswith("GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_v1_" + TODAY)
    assert copy.stat().st_size == res.video.stat().st_size
    meta = root / "metadata" / f"{copy.stem}.manifest.json"
    on_disk = (res.video.parent / "manifest.json").read_text(encoding="utf-8")
    assert meta.read_text(encoding="utf-8") == on_disk
    assert list((root / "final").iterdir()) == [] and list((root / "raw").iterdir()) == []


def test_a_second_render_the_same_day_is_v2_and_v1_is_kept(tmp_path: Path, clips: Path) -> None:
    folder = make_project(tmp_path, clips, "xuong", opts())
    for _ in range(2):
        build_render(folder, preview=True, rules=RULES)
    names = sorted(p.name for p in (delivery(folder, "xuong") / "drafts").iterdir())
    versions = [re.search(r"_v(\d+)_", n) for n in names]
    assert [v.group(1) for v in versions if v] == ["1", "2"]
    assert manifest_of(folder).delivered_as == names[1]


def test_without_a_product_the_default_is_used_and_a_stranger_is_flagged(
    tmp_path: Path, clips: Path
) -> None:
    folder = make_project(tmp_path, clips, "xuong", opts())
    build_render(folder, preview=True, rules=RULES, product="Xúc xích")
    m = manifest_of(folder)
    assert m.delivered_as is not None and "_xuc-xich_" in m.delivered_as
    assert m.warnings and "danh mục" in m.warnings[0]
    build_render(folder, preview=True, rules=RULES)
    assert "_chung_" in str(manifest_of(folder).delivered_as)


def test_the_render_command_takes_the_product_and_the_campaign(
    tmp_path: Path, clips: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_project(tmp_path, clips, "xuong", opts())
    code = main(["render", str(folder), "--preview", "--ban", "A", "--san-pham", "Chả bò",
                 "--chien-dich", "Tết 2027"])  # fmt: skip
    assert code == 0 and "QC: ĐẠT" in capsys.readouterr().out
    [copy] = list((delivery(folder, "tet-2027") / "drafts").iterdir())
    assert copy.name.startswith("GANI_cha-bo_tvc-cat-nhanh_ban-hang_15s_9x16_v1_")


# ─────────────── three videos that must be blocked ───────────────


def _assert_blocked(folder: Path, code: str, res_notes: list[str]) -> None:
    m = manifest_of(folder)
    assert not m.e1.passed and m.delivered_as is None
    failed = [i.code for i in m.e1.items if not i.passed]
    assert code in failed
    assert any(n.startswith(f"{code}:") for n in res_notes)
    qc = json.loads((folder / "edit/render/A/qc_report.json").read_text(encoding="utf-8"))
    assert qc["passed"] is False
    assert not (folder / "edit" / "delivery").exists()  # nothing was delivered


def _ask_for_30_seconds(folder: Path) -> None:
    plan = json.loads((folder / "edit" / "plan.json").read_text(encoding="utf-8"))
    plan["options"]["duration"] = 30  # the user asked for 30 s, the grid made 15.0 s
    (folder / "edit" / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def test_blocked_1a_a_plan_that_drifted_from_the_chosen_length(tmp_path: Path, clips: Path) -> None:
    """The render matches its plan (E1-DUR passes) but the plan is 50% short of what was chosen."""
    folder = make_project(tmp_path, clips, "plan_troi", opts())
    _ask_for_30_seconds(folder)
    by_plan = {**RULES, "qc": {**RULES["qc"], "duration_reference": "plan"}}  # the gap guard
    [res] = build_render(folder, preview=True, rules=by_plan)
    assert not res.qc_passed
    _assert_blocked(folder, "E1-DUR-GAP", res.qc_notes)
    items = {i.code: i for i in manifest_of(folder).e1.items}
    assert items["E1-DUR"].passed and not items["E1-DUR-GAP"].passed
    assert items["E1-DUR-GAP"].value == pytest.approx(-15.0, abs=0.1)
    assert items["E1-DUR-GAP"].limit == pytest.approx(3.0)


def test_blocked_1b_a_wrong_length_measured_against_the_chosen_length(
    tmp_path: Path, clips: Path
) -> None:
    folder = make_project(tmp_path, clips, "sai_dai", opts())
    _ask_for_30_seconds(folder)
    literal = {**RULES, "qc": {**RULES["qc"], "duration_reference": "chosen"}}
    [res] = build_render(folder, preview=True, rules=literal)
    assert not res.qc_passed
    _assert_blocked(folder, "E1-DUR", res.qc_notes)
    dur = next(i for i in manifest_of(folder).e1.items if i.code == "E1-DUR")
    assert dur.value == pytest.approx(-15.0, abs=0.1) and dur.limit == 0.5


def test_blocked_1c_a_speech_video_far_outside_the_voice_limit(tmp_path: Path, clips: Path) -> None:
    """8 s of speech for a "30 s" option: 73% short, the limit is ±7.5 s."""
    d = tmp_path / "loi"
    d.mkdir()
    shutil.copy(clips / "a.mp4", d / "a.mp4")
    build_inventory(d, RULES)
    speech = {"phong_cach": "ban_hang_co_loi", "muc_dich": "ban_hang", "do_dai_giay": 30,
              "so_ban_nhap": 1, "nhac": "khong_nhac"}  # fmt: skip
    plan = compile_plan(VideoOptions(**speech), RULES, music=None, available_tags=set())
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    word = SpokenWord(text="Giò", start=0.1, end=0.5, prob=0.9)
    cut = SpeechCut(phrase_id="a_p0", clip="a.mp4", src_in=0.0, src_out=8.0, t_start=0.0,
                    t_end=8.0, words=[word])  # fmt: skip
    sd = SpeechDraftsFile(model="fake", drafts=[SpeechDraft(draft="A", label="x", cuts=[cut])])
    (d / "edit" / "speech_drafts.json").write_text(sd.model_dump_json(), encoding="utf-8")
    [res] = build_render(d, preview=True, rules=RULES, asr=False)
    assert not res.qc_passed
    _assert_blocked(d, "E1-DUR", res.qc_notes)
    dur = next(i for i in manifest_of(d).e1.items if i.code == "E1-DUR")
    assert dur.value == pytest.approx(-22.0, abs=0.2) and dur.limit == pytest.approx(7.5)
    assert "Chế độ lời: cho phép ±7.5s" in dur.detail


def test_blocked_2_text_outside_the_safe_zone(tmp_path: Path, clips: Path) -> None:
    wide = "GionNgonMaiMaiKhongChanDauCoGiLaLamNaoDayAiOi"  # one word, ~1700 px at 72 px
    folder = make_project(tmp_path, clips, "chu_rong", opts(), texts={"hook": wide})
    [res] = build_render(folder, preview=True, rules=RULES)
    assert not res.qc_passed
    _assert_blocked(folder, "E1-SAFE", res.qc_notes)
    safe = next(i for i in manifest_of(folder).e1.items if i.code == "E1-SAFE")
    assert safe.value is not None and safe.value < 0


def test_blocked_3_a_banned_claim_never_reaches_ffmpeg(
    tmp_path: Path, clips: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = make_project(tmp_path, clips, "tu_cam", opts())
    glossary = GlossaryFile.model_construct(terms=[], banned_claims=["giòn ngon"], visual={},
                                            caption_highlight_keywords=[])  # fmt: skip
    monkeypatch.setattr(render_service, "load_glossary", lambda: glossary)
    with pytest.raises(ConfigError, match="C1"):
        build_render(folder, preview=True, rules=RULES)
    assert not (folder / "edit" / "render" / "A" / "manifest.json").exists()
    assert not (folder / "edit" / "delivery").exists()


# ─────────────── ten sample videos ───────────────

STYLES = ["tvc_cat_nhanh", "ke_chuyen_nghe", "review_mon_an", "sang_trong"]
PURPOSES = ["ban_hang", "gioi_thieu_xuong"]
SAMPLES = [(s, p) for s in STYLES for p in PURPOSES]
SAMPLES += [("tvc_cat_nhanh", "ke_chuyen_thuong_hieu"), ("ke_chuyen_nghe", "ke_chuyen_thuong_hieu")]
PRODUCTS = ["gio-lua", "cha-lua", "cha-bo", "nem-chua", "bo-vien"]


@pytest.mark.parametrize(("style", "purpose"), SAMPLES)
def test_ten_sample_videos_each_have_a_full_manifest_and_a_conforming_name(
    style: str, purpose: str, tmp_path: Path, clips: Path
) -> None:
    folder = make_project(tmp_path, clips, "mau", opts(style, purpose))
    product = PRODUCTS[SAMPLES.index((style, purpose)) % len(PRODUCTS)]
    [res] = build_render(folder, preview=True, rules=RULES, product=product, campaign="mau")
    m = manifest_of(folder)
    assert res.qc_passed and m.e1.passed, [i.detail for i in m.e1.items if not i.passed]
    assert [i.code for i in m.e1.items] == CHECKS
    assert_numbers(m)
    assert m.delivered_as is not None and PATTERN.match(m.delivered_as)
    assert f"_{product}_{style.replace('_', '-')}_" in m.delivered_as
    assert m.cost.render_sec > 0 and m.models["ai"] == "fake" and m.source_files
    assert (delivery(folder, "mau") / "drafts" / m.delivered_as).is_file()


@pytest.mark.parametrize("purpose", ["ban_hang", "gioi_thieu_xuong"])
def test_theo_mo_ta_passes_t2_when_the_encoder_drops_trailing_b_frames(
    purpose: str, tmp_path: Path, clips: Path
) -> None:
    """Real ffmpeg render of the style that used to stop at T2 with 507 of 510 frames."""
    folder = make_project(tmp_path, clips, "mau", opts("theo_mo_ta", purpose))
    [res] = build_render(folder, preview=True, rules=RULES, product="bo-vien", campaign="mau")
    assert res.qc_passed, res.qc_notes
    assert not [n for n in res.qc_notes if n.startswith("T2")]


# ─────────────── the length is the chosen length (hướng B, 13/10/2026) ───────────────


@pytest.mark.parametrize(
    "style", ["ke_chuyen_nghe", "tvc_cat_nhanh", "review_mon_an", "sang_trong"]
)
def test_without_music_every_style_renders_exactly_the_chosen_length(
    tmp_path: Path, clips: Path, style: str
) -> None:
    folder = make_project(tmp_path, clips, style, opts(style, "gioi_thieu_xuong"))
    plan = Plan.model_validate_json((folder / "edit" / "plan.json").read_text(encoding="utf-8"))
    assert plan.planned_beats * plan.beat_sec == pytest.approx(15.0)
    [res] = build_render(folder, preview=True, rules=RULES)
    m = manifest_of(folder)
    assert m.deviation_vs_chosen == pytest.approx(0.0, abs=0.04)  # at most one frame
    dur = next(i for i in m.e1.items if i.code == "E1-DUR")
    assert dur.passed and res.qc_passed, res.qc_notes


def _mean_volume_db(video: Path, start: float, length: float) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(length),
                        "-i", str(video), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, check=True, timeout=120)  # fmt: skip
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    assert m, r.stderr[-400:]
    return float(m.group(1))


def _luma(video: Path, at: float) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(at), "-i", str(video),
                        "-frames:v", "1", "-vf", "signalstats,metadata=print", "-an", "-f", "null",
                        "-"], capture_output=True, text=True, check=True, timeout=120)  # fmt: skip
    m = re.search(r"lavfi\.signalstats\.YAVG=([\d.]+)", r.stderr)
    assert m, r.stderr[-400:]
    return float(m.group(1))


def _music_project(tmp_path: Path, clips: Path, name: str, style: str, bpm: float) -> Path:
    song = tmp_path / f"{name}.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=40", str(song)],
                   check=True, timeout=120)  # fmt: skip
    info = MusicInfo(track=song.name, bpm=bpm, first_beat_sec=0.0, duration_sec=40.0,
                     source="library", path=str(song))  # fmt: skip
    with_music = {**opts(style, "gioi_thieu_xuong"), "nhac": "thu_vien", "nhac_file": song.name}
    return make_project(tmp_path, clips, name, with_music, music=info)


def test_with_music_a_plan_over_the_chosen_length_is_cut_there_and_fades_out(
    tmp_path: Path, clips: Path
) -> None:
    # Kể chuyện nghề at 100 BPM plans 26 beats × 0.6 s = 15.6 s for a "15 s" video.
    folder = _music_project(tmp_path, clips, "cat_nhe", "ke_chuyen_nghe", 100.0)
    plan = Plan.model_validate_json((folder / "edit" / "plan.json").read_text(encoding="utf-8"))
    assert plan.planned_beats * plan.beat_sec == pytest.approx(15.6) and plan.trim_to_sec == 15.0
    [res] = build_render(folder, preview=True, rules=RULES)
    m = manifest_of(folder)
    assert res.qc_passed, res.qc_notes
    assert m.deviation_vs_chosen == pytest.approx(0.0, abs=0.04)
    video = folder / "edit" / "render" / "A" / "nhap_A_540p.mp4"
    assert _mean_volume_db(video, 14.8, 0.2) < _mean_volume_db(video, 10.0, 0.2) - 10  # the tail
    assert _luma(video, 14.95) < 0.5 * _luma(video, 10.0)  # fading to black


def test_with_music_a_plan_that_fits_is_left_alone(tmp_path: Path, clips: Path) -> None:
    folder = _music_project(tmp_path, clips, "vua_khit", "ke_chuyen_nghe", 80.0)
    plan = Plan.model_validate_json((folder / "edit" / "plan.json").read_text(encoding="utf-8"))
    assert plan.planned_beats * plan.beat_sec == pytest.approx(15.0) and plan.trim_to_sec is None
    [res] = build_render(folder, preview=True, rules=RULES)
    assert res.qc_passed, res.qc_notes
    video = folder / "edit" / "render" / "A" / "nhap_A_540p.mp4"
    assert abs(_mean_volume_db(video, 14.7, 0.2) - _mean_volume_db(video, 10.0, 0.2)) < 3  # no fade


def test_a_cut_beyond_ten_percent_is_stopped_by_the_gap_guard_not_hidden_by_the_cut(
    tmp_path: Path, clips: Path
) -> None:
    """Sang trọng at 104 BPM (counted at 52): every block needs one 5-beat scene of 5.8 s, so the
    plan is 17.3 s (+15%)."""
    folder = _music_project(tmp_path, clips, "troi_xa", "sang_trong", 104.0)
    plan = Plan.model_validate_json((folder / "edit" / "plan.json").read_text(encoding="utf-8"))
    assert plan.trim_to_sec == 15.0 and any(w.startswith("W_DUR_GAP") for w in plan.warnings)
    [res] = build_render(folder, preview=True, rules=RULES)
    assert not res.qc_passed
    _assert_blocked(folder, "E1-DUR-GAP", res.qc_notes)
    items = {i.code: i for i in manifest_of(folder).e1.items}
    assert items["E1-DUR"].passed  # the video itself is exactly 15 s long …
    assert items["E1-DUR-GAP"].value == pytest.approx(2.31, abs=0.05)  # … the plan was not
    assert "chọn bài có BPM hợp phong cách" in items["E1-DUR-GAP"].detail


# ─────────────── T-F: review block at delivery; versions across drafts/ and final/ ───────


def test_a_delivered_final_video_starts_pending_and_nothing_else_does() -> None:
    from gani_dung_video.application.delivery_service import initial_review

    block = initial_review("GANI_x_v1.mp4", False, "2026-10-15T08:00:00Z")
    assert block is not None and block.status == "pending"
    assert block.requested_at == "2026-10-15T08:00:00Z"
    assert initial_review("GANI_x_v1.mp4", True, "2026-10-15T08:00:00Z") is None  # a preview
    assert initial_review(None, False, "2026-10-15T08:00:00Z") is None  # blocked, not delivered


def test_a_preview_delivery_has_no_review_block(tmp_path: Path, clips: Path) -> None:
    folder = make_project(tmp_path, clips, "xuong", opts())
    build_render(folder, preview=True, rules=RULES)
    m = manifest_of(folder)
    assert m.delivered_as is not None and m.review is None and m.manifest_version == 2


def test_an_approved_video_in_final_keeps_its_version_number(tmp_path: Path) -> None:
    """After approval drafts/ is empty again: v1 must not be handed out a second time."""
    from datetime import date
    from types import SimpleNamespace

    from gani_dung_video.application.delivery_service import DeliveryTarget, export_name

    plan = SimpleNamespace(options={"style": "tvc_cat_nhanh", "purpose": "ban_hang",
                                    "duration": 15})  # fmt: skip
    e = SimpleNamespace(platform="")  # the name reads only the platform of the EDL
    target = DeliveryTarget(product="gio-lua", campaign="ra-mat", day=date(2026, 10, 15))
    root, first, _ = export_name(e, plan, tmp_path, RULES, target)  # type: ignore[arg-type]
    assert "_v1_" in first
    (root / "final" / first).write_bytes(b"x")  # the approved copy; drafts/ is empty
    _, second, _ = export_name(e, plan, tmp_path, RULES, target)  # type: ignore[arg-type]
    assert "_v2_" in second and second != first


def test_a_real_final_render_is_delivered_pending_and_a_person_can_approve_it(
    tmp_path: Path, clips: Path
) -> None:
    """The whole T-F chain with the real renderer: 1080×1920 final → drafts/ + `pending` in both
    manifests → `review approve` moves the file to final/ → the next video gets v2, not v1."""
    import time

    from gani_dung_video.application import review_service as rs
    from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb

    folder = make_project(tmp_path, clips, "xuong", opts())
    db = QueueDb(tmp_path / "q" / "queue.db")
    jid = db.enqueue(NewJob(kind="render", project_dir=str(folder), options_file="o", draft="A",
                            final=True, max_attempts=1), time.time())  # fmt: skip
    assert db.claim(time.time(), 1) is not None  # the job starts before the video is made
    [res] = build_render(folder, preview=False, rules=RULES, product="Giò Lụa", campaign="Ra mắt")
    assert res.qc_passed
    db.finish(jid, time.time(), "completed", "passed", 0, None, str(res.video))
    m = manifest_of(folder)
    assert m.resolution == "1080x1920" and m.manifest_version == 2
    assert m.review is not None and m.review.status == "pending" and m.delivered_as is not None
    root = delivery(folder, "ra-mat")
    assert (root / "drafts" / m.delivered_as).is_file()
    meta = root / "metadata" / f"{Path(m.delivered_as).stem}.manifest.json"
    assert meta.read_text(encoding="utf-8") == (res.video.parent / "manifest.json").read_text(
        encoding="utf-8")  # fmt: skip

    [row] = rs.list_reviews(db, RULES)
    assert row.block.status == "pending" and row.problems == [] and row.overdue is False
    done = rs.approve(db, RULES, jid, "Hùng", None)
    assert done.block.status == "approved"
    assert (root / "final" / m.delivered_as).is_file()
    assert not (root / "drafts" / m.delivered_as).exists()
    assert (res.video.parent / "manifest.json").is_file()
    after = manifest_of(folder)
    assert after.review is not None and after.review.decided_by == "Hùng"

    # the next video of the day must not take the version that is now in final/
    build_render(folder, preview=False, rules=RULES, product="Giò Lụa", campaign="Ra mắt")
    names = sorted(p.name for p in (root / "drafts").iterdir())
    assert len(names) == 1 and "_v2_" in names[0]
