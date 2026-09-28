"""Phase 3a tests: no_speech rule, glossary on timed words, transcribe use case, adapter, CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.transcribe_service import transcribe_folder
from gani_dung_video.cli import main
from gani_dung_video.domain.speech import RawTranscript, SpokenWord, build_transcript, is_no_speech
from gani_dung_video.infrastructure.config_files import ConfigError, load_glossary, load_rules
from gani_dung_video.infrastructure.whisper_asr import AsrError, Transcriber

RULES = load_rules()
CFG = RULES["speech"]
TERMS = load_glossary().term_objects()
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def w(text: str, start: float, end: float, prob: float = 0.9) -> SpokenWord:
    return SpokenWord(text=text, start=start, end=end, prob=prob)


def raw(words: list[SpokenWord], speech: float = 5.0) -> RawTranscript:
    return RawTranscript(language="vi", duration_sec=10.0, speech_sec=speech, words=words)


SPEECH = [w(" Xin", 0.0, 0.3), w(" chào", 0.3, 0.6), w(" gani", 0.7, 0.9), w(" foods", 0.9, 1.3)]


# ─────────────── domain ───────────────


@pytest.mark.parametrize(
    ("words", "speech", "expected"),
    [(SPEECH, 5.0, False), ([], 0.0, True), (SPEECH[:2], 5.0, True),  # too few words
     (SPEECH, 0.5, True)],  # VAD found < 1 s of voice (e.g. machine noise)
)  # fmt: skip
def test_is_no_speech(words: list[SpokenWord], speech: float, expected: bool) -> None:
    assert is_no_speech(raw(words, speech), CFG) is expected


def test_build_transcript_fixes_brand_and_keeps_timing() -> None:
    t = build_transcript("a.mp4", "small", "k", raw(SPEECH), TERMS, CFG)
    assert t.text == "Xin chào GANI"
    assert t.words[-1] == w("GANI", 0.7, 1.3)  # merged word spans both, keeps first prob
    assert (t.word_count, t.no_speech, t.model, t.cache_key) == (3, False, "small", "k")


# ─────────────── use case with a fake recogniser ───────────────


def _fake_loader(calls: list[str], by_file: Mapping[str, RawTranscript]) -> Any:
    def loader(model: str, _cfg: Mapping[str, Any]) -> Transcriber:
        calls.append(f"load:{model}")

        def transcribe(path: Path) -> RawTranscript:
            calls.append(path.name)
            if path.name not in by_file:
                raise AsrError(f"Không nhận dạng được {path.name}: hỏng")
            return by_file[path.name]

        return transcribe

    return loader


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("cần ffmpeg")
    for name in ("noi.mp4", "xuong.mp4"):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=gray:s=64x64:r=10:d=1", "-c:v", "libx264", "-pix_fmt",
                        "yuv420p", str(tmp_path / name)], check=True, timeout=60)  # fmt: skip
    build_inventory(tmp_path, RULES)
    return tmp_path


def test_transcribe_folder_writes_files_and_summary(folder: Path) -> None:
    calls: list[str] = []
    fake = _fake_loader(calls, {"noi.mp4": raw(SPEECH), "xuong.mp4": raw([], 0.0)})
    s, out = transcribe_folder(folder, "small", RULES, loader=fake)
    assert calls == ["load:small", "noi.mp4", "xuong.mp4"]
    assert s.co_loi_noi and not s.failed
    assert {c.file: c.no_speech for c in s.clips} == {"noi.mp4": False, "xuong.mp4": True}
    assert json.loads(out.read_text(encoding="utf-8"))["co_loi_noi"] is True
    t = json.loads((folder / "edit" / "transcripts" / "noi.mp4.json").read_text(encoding="utf-8"))
    assert t["text"] == "Xin chào GANI"


def test_rerun_uses_cache_and_never_loads_model(folder: Path) -> None:
    by_file = {"noi.mp4": raw(SPEECH), "xuong.mp4": raw([], 0.0)}
    transcribe_folder(folder, "small", RULES, loader=_fake_loader([], by_file))
    calls: list[str] = []
    s, _ = transcribe_folder(folder, "small", RULES, loader=_fake_loader(calls, by_file))
    assert calls == [] and all(c.cached for c in s.clips)


def test_changing_model_invalidates_cache(folder: Path) -> None:
    by_file = {"noi.mp4": raw(SPEECH), "xuong.mp4": raw([], 0.0)}
    transcribe_folder(folder, "small", RULES, loader=_fake_loader([], by_file))
    calls: list[str] = []
    transcribe_folder(folder, "large-v3-turbo", RULES, loader=_fake_loader(calls, by_file))
    assert calls == ["load:large-v3-turbo", "noi.mp4", "xuong.mp4"]


def test_corrupt_cache_is_redone(folder: Path) -> None:
    by_file = {"noi.mp4": raw(SPEECH), "xuong.mp4": raw([], 0.0)}
    transcribe_folder(folder, "small", RULES, loader=_fake_loader([], by_file))
    (folder / "edit" / "transcripts" / "noi.mp4.json").write_text("{hỏng", encoding="utf-8")
    calls: list[str] = []
    transcribe_folder(folder, "small", RULES, loader=_fake_loader(calls, by_file))
    assert calls == ["load:small", "noi.mp4"]


def test_one_failing_clip_does_not_stop_the_rest(folder: Path) -> None:
    s, _ = transcribe_folder(
        folder, "small", RULES, loader=_fake_loader([], {"noi.mp4": raw(SPEECH)})
    )
    assert [c.file for c in s.clips] == ["noi.mp4"] and "xuong.mp4" in s.failed


def test_no_speech_anywhere_sets_co_loi_noi_false(folder: Path) -> None:
    fake = _fake_loader([], {"noi.mp4": raw([], 0.0), "xuong.mp4": raw([], 0.0)})
    assert transcribe_folder(folder, "small", RULES, loader=fake)[0].co_loi_noi is False


def test_default_model_comes_from_rules(folder: Path) -> None:
    calls: list[str] = []
    transcribe_folder(folder, None, RULES, loader=_fake_loader(calls, {}))
    assert calls[0] == f"load:{CFG['model_default']}"


def test_requires_inventory_first(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="inventory"):
        transcribe_folder(tmp_path, "small", RULES, loader=_fake_loader([], {}))


def test_corrupt_inventory_is_reported(tmp_path: Path) -> None:
    (tmp_path / "edit").mkdir()
    (tmp_path / "edit" / "inventory.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="hỏng"):
        transcribe_folder(tmp_path, "small", RULES, loader=_fake_loader([], {}))


# ─────────────── CLI ───────────────


def test_cli_transcribe(folder: Path, monkeypatch: pytest.MonkeyPatch,
                        capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    import gani_dung_video.application.transcribe_service as svc

    fake = _fake_loader([], {"noi.mp4": raw(SPEECH), "xuong.mp4": raw([], 0.0)})
    monkeypatch.setattr(svc, "load_transcriber", fake)
    assert main(["transcribe", str(folder), "--model", "small"]) == 0
    out = capsys.readouterr().out
    assert "có lời nói trong footage: CÓ" in out and "xuong.mp4: không lời" in out


def test_cli_transcribe_without_inventory(tmp_path: Path) -> None:
    assert main(["transcribe", str(tmp_path)]) == 2


# ─────────────── real adapter (cached tiny model, offline) ───────────────

TINY = Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-tiny"


@pytest.mark.skipif(not (HAS_FFMPEG and TINY.is_dir()), reason="cần ffmpeg + model tiny có sẵn")
def test_real_whisper_on_a_tone_finds_no_speech(tmp_path: Path) -> None:
    from gani_dung_video.infrastructure.whisper_asr import load_transcriber

    clip = tmp_path / "tone.mp4"
    tone = "sine=frequency=1000:duration=3"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", tone, str(clip)], check=True, timeout=60)  # fmt: skip
    r = load_transcriber("tiny", CFG)(clip)
    assert r.language == "vi" and 2.5 <= r.duration_sec <= 3.5
    assert is_no_speech(r, CFG)


class _FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel so no model/network is touched."""

    calls: ClassVar[list[dict[str, Any]]] = []
    fail_offline: ClassVar[bool] = False
    fail_online: ClassVar[bool] = False

    def __init__(self, model: str, **kw: Any) -> None:
        _FakeWhisperModel.calls.append(kw)
        offline = kw.get("local_files_only", False)
        if (offline and self.fail_offline) or (not offline and self.fail_online):
            raise OSError("không có model")


@pytest.fixture
def fake_wm(monkeypatch: pytest.MonkeyPatch) -> type[_FakeWhisperModel]:
    import faster_whisper

    _FakeWhisperModel.calls, _FakeWhisperModel.fail_offline = [], False
    _FakeWhisperModel.fail_online = False
    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisperModel)
    return _FakeWhisperModel


def test_cached_model_loads_offline_without_network(fake_wm: type[_FakeWhisperModel]) -> None:
    from gani_dung_video.infrastructure.whisper_asr import load_transcriber

    load_transcriber("small", CFG)
    assert [c.get("local_files_only") for c in fake_wm.calls] == [True]


def test_uncached_model_falls_back_to_download(fake_wm: type[_FakeWhisperModel]) -> None:
    from gani_dung_video.infrastructure.whisper_asr import load_transcriber

    fake_wm.fail_offline = True
    load_transcriber("large-v3-turbo", CFG)
    assert [c.get("local_files_only", False) for c in fake_wm.calls] == [True, False]


def test_unloadable_model_raises_actionable_error(fake_wm: type[_FakeWhisperModel]) -> None:
    from gani_dung_video.infrastructure.whisper_asr import load_transcriber

    fake_wm.fail_offline = fake_wm.fail_online = True
    with pytest.raises(AsrError, match="Máy mới cần mạng"):
        load_transcriber("khong-co-model", CFG)


def test_device_and_compute_type_come_from_rules(fake_wm: type[_FakeWhisperModel]) -> None:
    # Regression: device="auto" picked the Dell's GeForce MX130 (CUDA cc 5.0) and
    # ctranslate2 4.8 segfaulted on the first decoded word. Device is config, default cpu.
    from gani_dung_video.infrastructure.whisper_asr import load_transcriber

    load_transcriber("small", CFG)
    assert fake_wm.calls[0]["device"] == CFG["device"] == "cpu"
    assert fake_wm.calls[0]["compute_type"] == CFG["compute_type"] == "int8"


# ─────────────── rule 28/09/2026: ratio ≥ 30% + confidence ≥ 0.6 ───────────────


def rawp(n: int, dur: float, speech: float, prob: float) -> RawTranscript:
    words = [w(f"từ{i}", i * 0.3, i * 0.3 + 0.2, prob) for i in range(n)]
    return RawTranscript(language="vi", duration_sec=dur, speech_sec=speech, words=words)


@pytest.mark.parametrize(
    ("r", "expected"),
    [
        # Real workshop clips (Xuong GANI, 28/09): chatter picked up by the mic → no speech.
        (rawp(7, 24.1, 2.432, 0.56), True),  # "Rồi, ăn ăn mảu đây mẹ mẹ"
        (rawp(5, 30.4, 3.2, 0.67), True),  # "3, 2, 1, máy chạy": confident but only 11%
        (rawp(4, 91.9, 2.048, 0.43), True),  # 2% of the clip
        # A presenter talking to camera → speech.
        (rawp(40, 20.0, 15.0, 0.85), False),
        # Enough ratio but mumbled/unclear (avg confidence < 0.6) → not reliable speech.
        (rawp(30, 20.0, 12.0, 0.5), True),
        # Exactly on the thresholds counts as speech.
        (rawp(3, 10.0, 3.0, 0.6), False),
        (rawp(0, 0.0, 0.0, 0.9), True),  # zero-length clip
    ],
)
def test_speech_needs_ratio_and_confidence(r: RawTranscript, expected: bool) -> None:
    assert is_no_speech(r, CFG) is expected


def test_rule_change_is_applied_to_cached_transcripts(folder: Path) -> None:
    by_file = {"noi.mp4": rawp(5, 30.0, 3.2, 0.67), "xuong.mp4": rawp(0, 1.0, 0.0, 0.0)}
    loose = {**RULES, "speech": {**CFG, "min_speech_ratio": 0.0, "min_avg_confidence": 0.0}}
    s1, _ = transcribe_folder(folder, "small", loose, loader=_fake_loader([], by_file))
    assert s1.co_loi_noi is True
    calls: list[str] = []
    s2, _ = transcribe_folder(folder, "small", RULES, loader=_fake_loader(calls, by_file))
    assert calls == []  # cache reused — Whisper not run again
    assert s2.co_loi_noi is False and all(c.cached and c.no_speech for c in s2.clips)
