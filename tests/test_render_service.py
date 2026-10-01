"""Integration tests (5a): drafts.json + plan.json + inventory → video, srt, cover, log."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application import render_service
from gani_dung_video.application.draft_service import DraftsFile
from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.render_service import SpeechDraftsFile, build_render, recheck
from gani_dung_video.cli import main
from gani_dung_video.domain.beats import DraftSegment, SceneRef, snap_to_beats
from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.edl import BrollPlacement, SpeechDraft, build_speech_edl
from gani_dung_video.domain.footage import Inventory
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import Plan, beats_of, compile_plan
from gani_dung_video.domain.speech import RawTranscript, SpokenWord
from gani_dung_video.domain.speech_edit import Overlay, SpeechCut
from gani_dung_video.infrastructure.config_files import ConfigError, GlossaryFile, load_rules
from gani_dung_video.infrastructure.ffmpeg_render import render_voice_only

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
RULES = load_rules()
OPTS: dict[str, Any] = {"phong_cach": "tvc_cat_nhanh", "muc_dich": "ban_hang", "do_dai_giay": 15,
                        "so_ban_nhap": 1, "nhac": "khong_nhac"}  # fmt: skip


def ff(out: Path, src: str, dur: float) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{src}:d={dur}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(out)], check=True, timeout=120)  # fmt: skip


@pytest.fixture(scope="module")
def folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("dung")
    ff(d / "a.mp4", "testsrc2=s=360x640:r=30", 8.0)
    ff(d / "b.mp4", "smptebars=s=640x360:r=30", 8.0)
    build_inventory(d, RULES)
    plan = compile_plan(VideoOptions(**OPTS), RULES, music=None, available_tags={"thanh_pham"})
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    refs = {"a_s0_0": SceneRef(clip="a.mp4", start=0, end=8, peak=2),
            "b_s0_0": SceneRef(clip="b.mp4", start=0, end=8, peak=4)}  # fmt: skip
    cuts: list[DraftSegment] = []
    texts = {plan.blocks[0].role: "Giòn ngon", plan.blocks[-1].role: "Nhắn tin đặt hàng"}
    for block in plan.blocks:  # 2-beat shots (1 if odd), alternating the two clips
        left = block.beats
        while left:
            n = min(2, left)
            first = left == block.beats
            cuts.append(
                DraftSegment(
                    block=block.role,
                    scene_id=["a_s0_0", "b_s0_0"][len(cuts) % 2],
                    beats=n,
                    text=texts.get(block.role, "") if first else "",
                )
            )
            left -= n
    snapped = snap_to_beats(cuts, beats_of(plan), refs)
    drafts = DraftsFile(model="fake", attempts=1, input_tokens=1, output_tokens=1, cost_vnd=1.0,
                        cap_vnd=20000, notes=[],
                        drafts=[SnappedDraft(draft="A", label="Tối ưu", segments=snapped,
                                             missing_shots=[], warnings=[])])  # fmt: skip
    (d / "edit" / "drafts.json").write_text(drafts.model_dump_json(), encoding="utf-8")
    return d


SPEECH_OPTS: dict[str, Any] = {"phong_cach": "ban_hang_co_loi", "muc_dich": "ban_hang",
                               "do_dai_giay": 30, "so_ban_nhap": 1,
                               "nhac": "khong_nhac"}  # fmt: skip


def word(text: str, start: float, end: float) -> SpokenWord:
    return SpokenWord(text=text, start=start, end=end, prob=0.9)


@pytest.fixture(scope="module")
def speech_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("dung_loi")
    ff(d / "a.mp4", "testsrc2=s=360x640:r=30", 8.0)  # the speaker
    ff(d / "b.mp4", "smptebars=s=640x360:r=30", 8.0)  # B-roll
    build_inventory(d, RULES)
    plan = compile_plan(VideoOptions(**SPEECH_OPTS), RULES, music=None, available_tags=set())
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    cuts = [
        SpeechCut(phrase_id="a_p0", clip="a.mp4", src_in=0.0, src_out=2.0, t_start=0.0, t_end=2.0,
                 words=[word("Giò", 0.1, 0.5), word("lụa", 0.5, 0.9), word("GANI", 0.9, 1.8)]),
        SpeechCut(phrase_id="a_p1", clip="a.mp4", src_in=2.0, src_out=5.0, t_start=2.0, t_end=5.0,
                 words=[word("Bò", 2.1, 2.5), word("viên", 2.5, 2.9), word("tròn", 2.9, 3.8)]),
        SpeechCut(phrase_id="a_p2", clip="a.mp4", src_in=5.0, src_out=8.0, t_start=5.0, t_end=8.0,
                 words=[word("Nhắn", 5.1, 5.6), word("tin", 5.6, 6.0), word("ngay", 6.0, 7.8)]),
    ]  # fmt: skip
    broll = [BrollPlacement(overlay=Overlay(phrase_id="a_p1", t_start=2.4, t_end=3.6, beats=2),
                            clip="b.mp4", start=0.0, end=1.2, peak=0.6)]  # fmt: skip
    speech_draft = SpeechDraft(draft="A", label="Thử render chế độ lời (ghép tay)", cuts=cuts,
                               broll=broll, missing_shots=[], warnings=[])  # fmt: skip
    sd = SpeechDraftsFile(model="THU-TAY-khong-AI", cost_vnd=0.0, drafts=[speech_draft])
    (d / "edit" / "speech_drafts.json").write_text(sd.model_dump_json(), encoding="utf-8")
    return d


def duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return float(r.stdout)


def test_preview_render_writes_video_subtitles_cover_and_log(folder: Path) -> None:
    [res] = build_render(folder, preview=True, rules=RULES)
    out = folder / "edit" / "render" / "A"
    assert res.video == out / "nhap_A_540p.mp4" and res.video.is_file()
    # TVC without music: 12 beats on the 1.3 s grid = 15.6 s, plus the 2 s end card
    assert res.duration_sec == pytest.approx(17.6)
    assert duration(res.video) == pytest.approx(res.duration_sec, abs=0.1)
    srt = (out / "phude.srt").read_text(encoding="utf-8")
    assert "Giòn ngon" in srt and "Nhắn tin đặt hàng" in srt
    assert (out / "cover.jpg").stat().st_size > 1000
    log = json.loads((out / "render_log.json").read_text(encoding="utf-8"))
    assert log["draft"] == "A" and log["options"]["style"] == "tvc_cat_nhanh"
    assert log["music"] is None and [s["source"] for s in log["shots"]][:2] == ["a.mp4", "b.mp4"]
    assert log["brand_placeholders"] == ["logo", "zalo"]
    qc = json.loads((out / "qc_report.json").read_text(encoding="utf-8"))
    assert qc["passed"] is True and qc["rounds"] >= 1 and res.qc_passed
    assert log["qc"]["passed"] is True


def _glossary(banned: list[str], keywords: list[str]) -> GlossaryFile:
    # model_construct: skip load-time validation so a bad list can reach the render-time gate
    return GlossaryFile.model_construct(terms=[], banned_claims=banned, visual={},
                                        caption_highlight_keywords=keywords)  # fmt: skip


def _no_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every ffmpeg entry point; the list records what would have run."""
    ran: list[str] = []

    def boom(*a: Any, **k: Any) -> None:
        ran.append("ffmpeg")
        raise AssertionError("ffmpeg must not run")

    monkeypatch.setattr(render_service, "render", boom)
    monkeypatch.setattr(render_service, "measure_loudnorm", boom)
    return ran


def test_c1_stops_a_caption_with_a_banned_claim_before_ffmpeg_runs(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_service, "load_glossary", lambda: _glossary(["giòn ngon"], []))
    ran = _no_ffmpeg(monkeypatch)
    with pytest.raises(ConfigError, match=r"C1.*giòn ngon") as err:
        build_render(folder, draft="A", preview=True, rules=RULES)
    assert ran == [] and "chữ" in str(err.value)  # names where the text came from


def test_c1_also_reads_the_spoken_words_of_a_speech_video(
    speech_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_service, "load_glossary", lambda: _glossary(["lụa gani"], []))
    ran = _no_ffmpeg(monkeypatch)
    with pytest.raises(ConfigError, match=r"C1.*lụa gani") as err:
        build_render(speech_folder, draft="A", preview=True, rules=RULES, asr=False)
    assert ran == [] and "lời" in str(err.value)


def test_c1_checks_the_highlight_keywords_too(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A keyword list that skipped load-time validation (edited in memory) is still caught here.
    monkeypatch.setattr(
        render_service, "load_glossary", lambda: _glossary(["tốt nhất"], ["tốt nhất"])
    )
    ran = _no_ffmpeg(monkeypatch)
    with pytest.raises(ConfigError, match=r"C1.*tốt nhất") as err:
        build_render(folder, draft="A", preview=True, rules=RULES)
    assert ran == [] and "từ nhấn" in str(err.value)


def test_a_clean_render_highlights_the_approved_keyword_in_the_ass_file(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_service, "load_glossary", lambda: _glossary(["tốt nhất"], ["giòn"]))
    [res] = build_render(folder, draft="A", preview=True, rules=RULES)
    ass = (res.video.parent / "sub.ass").read_text(encoding="utf-8")
    assert r"\fscx" in ass and "giòn".lower() in ass.lower()
    srt = (res.video.parent / "phude.srt").read_text(encoding="utf-8")
    assert "Giòn ngon" in srt and "{" not in srt  # the closed captions stay plain


def test_unknown_draft_letter_is_refused(folder: Path) -> None:
    with pytest.raises(ConfigError, match="Z"):
        build_render(folder, draft="Z", preview=True, rules=RULES)


def test_drafts_json_is_required(tmp_path: Path) -> None:
    # plan.json now has to be read first to know which drafts file the style needs
    # (drafts.json or speech_drafts.json), so this pins down the "drafts.json missing"
    # case specifically rather than relying on incidental read order.
    ff(tmp_path / "a.mp4", "testsrc2=s=360x640:r=30", 8.0)
    build_inventory(tmp_path, RULES)
    plan = compile_plan(VideoOptions(**OPTS), RULES, music=None, available_tags=set())
    (tmp_path / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    with pytest.raises(ConfigError, match="draft"):
        build_render(tmp_path, preview=True, rules=RULES)


def test_cli_render_preview(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render", str(folder), "--preview", "--ban", "A"]) == 0
    out = capsys.readouterr().out
    assert "QC: ĐẠT" in out
    assert "nhap_A_540p.mp4" in out and "17.6s" in out


def test_cli_qc_rechecks_an_existing_video(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_render(folder, preview=True, rules=RULES)
    assert main(["qc", str(folder), "--ban", "A"]) == 0
    assert "QC: ĐẠT" in capsys.readouterr().out


def test_cli_qc_without_a_rendered_video(folder: Path) -> None:
    assert main(["qc", str(folder), "--ban", "A", "--final"]) == 2  # no 1080p render yet


# ─────────────── speech mode (5c-1) ───────────────


def test_speech_mode_renders_the_voice_the_broll_overlay_and_the_cta(speech_folder: Path) -> None:
    [res] = build_render(speech_folder, preview=True, rules=RULES)
    out = speech_folder / "edit" / "render" / "A"
    assert res.video == out / "nhap_A_540p.mp4" and res.video.is_file()
    assert res.duration_sec == pytest.approx(10.0)  # 8 s of speech + 2 s end card
    assert duration(res.video) == pytest.approx(res.duration_sec, abs=0.1)
    srt = (out / "phude.srt").read_text(encoding="utf-8")
    assert "Nhắn tin đặt hàng" in srt  # ban_hang's CTA, shown over the last phrase
    assert (out / "cover.jpg").stat().st_size > 1000
    log = json.loads((out / "render_log.json").read_text(encoding="utf-8"))
    assert log["draft"] == "A" and log["options"]["style"] == "ban_hang_co_loi"
    assert [s["source"] for s in log["shots"]] == ["a.mp4", "a.mp4", "a.mp4"]
    assert log["qc"]["passed"] is True and res.qc_passed


def test_speech_drafts_json_is_required_for_a_speech_style(tmp_path: Path) -> None:
    d = tmp_path / "no_speech_drafts"
    d.mkdir()
    ff(d / "a.mp4", "testsrc2=s=360x640:r=30", 8.0)
    build_inventory(d, RULES)
    plan = compile_plan(VideoOptions(**SPEECH_OPTS), RULES, music=None, available_tags=set())
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    with pytest.raises(ConfigError, match="speech_drafts"):
        build_render(d, preview=True, rules=RULES)


def test_cli_render_speech_mode(speech_folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render", str(speech_folder), "--preview", "--ban", "A"]) == 0
    out = capsys.readouterr().out
    assert "QC: ĐẠT" in out and "nhap_A_540p.mp4" in out and "10s" in out


# ─────────────── T8: Whisper listens to the final video (5c-3) ───────────────

# A cheap "final": same code path as the 1080p render, at preview size.
SMALL_FINAL = {**RULES, "render": {**RULES["render"], "final": RULES["render"]["preview"]}}
SPOKEN = [word("Giò", 0.1, 0.5), word("lụa", 0.5, 0.9), word("GANI", 0.9, 1.8),
          word("Bò", 2.1, 2.5), word("viên", 2.5, 2.9), word("tròn", 2.9, 3.8),
          word("Nhắn", 5.1, 5.6), word("tin", 5.6, 6.0), word("ngay", 6.0, 7.8)]  # fmt: skip


class Listener:
    """A fake Whisper: answers with fixed words and counts how often it was asked."""

    def __init__(self, words: list[SpokenWord]) -> None:
        self.words, self.calls = words, 0

    def __call__(self, path: Path) -> RawTranscript:
        self.calls += 1
        return RawTranscript(language="vi", duration_sec=10.0, speech_sec=7.0, words=self.words)


def test_final_speech_render_is_listened_to_and_passes_when_heard_right(
    speech_folder: Path,
) -> None:
    ear = Listener(SPOKEN)
    [res] = build_render(speech_folder, preview=False, rules=SMALL_FINAL, transcribe=ear)
    assert res.video.name == "video_A_1080x1920.mp4" and res.qc_passed and ear.calls == 1
    assert not any("T8" in n for n in res.qc_notes)


def test_final_speech_render_fails_qc_when_the_voice_cannot_be_heard(speech_folder: Path) -> None:
    ear = Listener([word("ồn", 0.1, 0.5)])
    [res] = build_render(speech_folder, preview=False, rules=SMALL_FINAL, transcribe=ear)
    assert not res.qc_passed and any(n.startswith("T8:") for n in res.qc_notes)
    report = json.loads((res.video.parent / "qc_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False and any(f["code"] == "T8" for f in report["findings"])


def test_no_asr_and_preview_never_load_the_recogniser(speech_folder: Path) -> None:
    ear = Listener([word("ồn", 0.1, 0.5)])
    [res] = build_render(speech_folder, preview=False, rules=SMALL_FINAL, asr=False,
                         transcribe=ear)  # fmt: skip
    assert res.qc_passed
    build_render(speech_folder, preview=True, rules=SMALL_FINAL, transcribe=ear)
    assert ear.calls == 0


def test_qc_command_listens_to_an_existing_final_video(speech_folder: Path) -> None:
    build_render(speech_folder, preview=False, rules=SMALL_FINAL, asr=False)
    bad = Listener([word("ồn", 0.1, 0.5)])
    found = recheck(speech_folder, "A", preview=False, rules=SMALL_FINAL, transcribe=bad)
    assert any("T8" in f and f.startswith("ERROR") for f in found) and bad.calls == 1
    quiet = recheck(speech_folder, "A", preview=False, rules=SMALL_FINAL, asr=False, transcribe=bad)
    assert not any("T8" in f for f in quiet) and bad.calls == 1


def test_cli_accepts_no_asr(speech_folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render", str(speech_folder), "--preview", "--ban", "A", "--no-asr"]) == 0
    assert main(["qc", str(speech_folder), "--ban", "A", "--no-asr"]) == 0
    assert "QC: ĐẠT" in capsys.readouterr().out


def test_voice_only_mix_has_the_video_timeline_and_needs_no_music_file(
    speech_folder: Path, tmp_path: Path
) -> None:
    edit = speech_folder / "edit"
    plan = Plan.model_validate_json((edit / "plan.json").read_text(encoding="utf-8"))
    inv = Inventory.model_validate_json((edit / "inventory.json").read_text(encoding="utf-8"))
    sd = SpeechDraftsFile.model_validate_json(
        (edit / "speech_drafts.json").read_text(encoding="utf-8"))  # fmt: skip
    draft = sd.drafts[0]
    e = build_speech_edl(draft.cuts, draft.broll, plan, {c.file: c for c in inv.clips}, RULES,
                         preview=True)  # fmt: skip
    wav = render_voice_only(e, speech_folder, tmp_path, RULES)
    assert wav.suffix == ".wav" and duration(wav) == pytest.approx(e.duration, abs=0.1)


def test_t8_uses_its_own_whisper_model_not_the_transcription_default(
    speech_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def fake_loader(model: str, cfg: Any) -> Listener:
        asked.append(model)
        return Listener(SPOKEN)

    monkeypatch.setattr(render_service, "load_transcriber", fake_loader)
    build_render(speech_folder, preview=False, rules=SMALL_FINAL)
    assert asked == [RULES["qc"]["t8"]["model"]] == ["small"]
    assert RULES["speech"]["model_default"] != "small"  # the two settings are independent


# ─────────────── 6f: qc_report.json keeps the T8 numbers ───────────────


def qc_of(res: Any) -> dict[str, Any]:
    return dict(json.loads((res.video.parent / "qc_report.json").read_text(encoding="utf-8")))


def test_a_final_speech_render_that_passes_still_records_what_whisper_measured(
    speech_folder: Path,
) -> None:
    [res] = build_render(
        speech_folder, preview=False, rules=SMALL_FINAL, transcribe=Listener(SPOKEN)
    )
    t8 = qc_of(res)["t8"]
    assert t8["wer"] == 0.0 and t8["max_wer"] == RULES["qc"]["t8"]["max_wer"]
    assert t8["median_drift_sec"] is not None and t8["voice_only_wer"] is None


def test_a_failed_listen_records_its_numbers_next_to_the_finding(speech_folder: Path) -> None:
    [res] = build_render(speech_folder, preview=False, rules=SMALL_FINAL,
                         transcribe=Listener([word("ồn", 0.1, 0.5)]))  # fmt: skip
    report = qc_of(res)
    assert report["t8"]["wer"] > report["t8"]["max_wer"]
    assert any(f["code"] == "T8" for f in report["findings"])


def test_a_video_nobody_listened_to_has_no_t8_entry(speech_folder: Path) -> None:
    [preview] = build_render(speech_folder, preview=True, rules=SMALL_FINAL,
                             transcribe=Listener(SPOKEN))  # fmt: skip
    assert qc_of(preview).get("t8") is None
    [no_asr] = build_render(speech_folder, preview=False, rules=SMALL_FINAL, asr=False)
    assert qc_of(no_asr).get("t8") is None
