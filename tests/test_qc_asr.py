"""Integration tests: application/qc_service.check_speech_asr — T8 with a FAKE Whisper (5c-3).

No model and no network: the transcriber is a plain function returning canned words per file.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gani_dung_video.application.qc_service import check_speech_asr
from gani_dung_video.domain.edl import Edl, MusicTrack, Shot
from gani_dung_video.domain.glossary import Term
from gani_dung_video.domain.speech import RawTranscript, SpokenWord
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.ffmpeg_render import RenderError
from gani_dung_video.infrastructure.whisper_asr import AsrError

RULES = load_rules()
VIDEO = Path("video_A_1080x1920.mp4")
VOICE = Path("voice_only.wav")
TERMS = [Term("GANI", ("gani foods",))]


def words(spec: str, start: float = 0.0, step: float = 0.4, shift: float = 0.0
          ) -> list[SpokenWord]:  # fmt: skip
    return [SpokenWord(text=w, start=round(start + i * step + shift, 3),
                       end=round(start + i * step + shift + 0.3, 3), prob=0.9)
            for i, w in enumerate(spec.split())]  # fmt: skip


SAID = "Giò lụa GANI thơm ngon hôm nay"  # 7 words at 0.0, 0.4 … 2.4


def edl(mode: str = "speech", music: bool = False) -> Edl:
    shot = Shot(source="a.mp4", src_in=0, src_out=3.2, t_start=0, t_end=3.2, block="speech",
                hdr=False, dissolve_out_sec=0, pad_sec=0)  # fmt: skip
    track = MusicTrack(track="t.m4a", audio_in_sec=0.5, loop_sec=None, fade_out_sec=1.5)
    return Edl(style="ban_hang_co_loi", width=1080, height=1920, fps=30, video_kbps=9000,
               shots=[shot], content_end=3.2, end_card_sec=2.0, duration=5.2,
               music=track if music else None, ambient_db=0.0, lufs=-14.0, true_peak_db=-2.0,
               font="Be Vietnam Pro ExtraBold", captions=[], mode=mode,
               duck=[(0.0, 2.7)] if mode == "speech" else [], duck_db=-18.0,
               karaoke=[words(SAID)] if mode == "speech" else [])  # fmt: skip


class FakeWhisper:
    """Answers per file name and records what it was asked to listen to."""

    def __init__(self, by_name: dict[str, list[SpokenWord]]) -> None:
        self.by_name = by_name
        self.heard: list[str] = []

    def __call__(self, path: Path) -> RawTranscript:
        self.heard.append(path.name)
        ws = self.by_name[path.name]
        return RawTranscript(language="vi", duration_sec=5.2, speech_sec=2.7, words=ws)


def run(e: Edl, fake: FakeWhisper, voice_only: Callable[[], Path] | None = None) -> list[str]:
    found = check_speech_asr(VIDEO, e, RULES, fake, TERMS, voice_only=voice_only)
    return [f"{f.level}:{f.code}:{f.msg}" for f in found]


# ─────────────── verdicts ───────────────


def test_a_clean_video_passes_with_one_listen() -> None:
    fake = FakeWhisper({VIDEO.name: words(SAID)})
    assert run(edl(), fake) == [] and fake.heard == [VIDEO.name]


def test_music_styles_are_never_checked() -> None:
    fake = FakeWhisper({})
    assert run(edl(mode="music"), fake) == [] and fake.heard == []


def test_misheard_brand_name_is_fixed_by_the_glossary_before_comparing() -> None:
    heard = words("Giò lụa gani", step=0.4) + words("foods", start=1.0)  # "gani foods" → GANI
    heard += words("thơm ngon hôm nay", start=1.2)
    assert run(edl(), FakeWhisper({VIDEO.name: heard})) == []


def test_too_many_wrong_words_block_delivery() -> None:
    heard = words("Giò lúa GANI thơm nồng hôm nay")  # 2 of 7 wrong = 29 %
    [line] = run(edl(), FakeWhisper({VIDEO.name: heard}))
    assert line.startswith("error:T8:") and "29%" in line


def test_words_over_music_only_stretches_are_not_counted() -> None:
    lyrics = words("la la la", start=3.9, step=0.3)  # after the voice (ends 2.7 + 0.3 margin)
    heard = words(SAID) + lyrics
    assert run(edl(), FakeWhisper({VIDEO.name: heard})) == []


def test_late_subtitles_are_only_a_warning() -> None:
    heard = words(SAID, shift=0.25)  # every word 250 ms after its caption
    [line] = run(edl(), FakeWhisper({VIDEO.name: heard}))
    assert line.startswith("warn:T8:") and "250ms" in line


# ─────────────── music masking (second listen) ───────────────


def test_music_masking_is_named_by_listening_to_the_music_free_mix() -> None:
    fake = FakeWhisper({VIDEO.name: words("Giò ồn ồn thơm ồn hôm nay"),
                        VOICE.name: words(SAID)})  # fmt: skip
    calls: list[int] = []

    def voice_only() -> Path:
        calls.append(1)
        return VOICE

    [line] = run(edl(music=True), fake, voice_only)
    assert "nhạc đè tiếng" in line.lower() and calls == [1]
    assert fake.heard == [VIDEO.name, VOICE.name]


def test_hard_to_hear_voice_is_named_when_the_music_free_mix_fails_too() -> None:
    bad = words("Giò ồn ồn thơm ồn hôm nay")
    fake = FakeWhisper({VIDEO.name: bad, VOICE.name: bad})
    [line] = run(edl(music=True), fake, lambda: VOICE)
    assert "giọng nói khó nghe" in line.lower()


def test_no_second_listen_without_music_or_when_the_first_passes() -> None:
    calls: list[int] = []

    def voice_only() -> Path:
        calls.append(1)
        return VOICE

    bad = FakeWhisper({VIDEO.name: words("ồn ồn ồn ồn ồn ồn ồn")})
    run(edl(music=False), bad, voice_only)  # fails, but there is no music to blame
    ok = FakeWhisper({VIDEO.name: words(SAID)})
    run(edl(music=True), ok, voice_only)  # passes
    assert calls == []


def test_a_failed_voice_only_render_falls_back_to_the_plain_verdict() -> None:
    def broken() -> Path:
        raise RenderError("ffmpeg lỗi: test")

    fake = FakeWhisper({VIDEO.name: words("ồn ồn ồn ồn ồn ồn ồn")})
    [line] = run(edl(music=True), fake, broken)
    assert line.startswith("error:T8:") and "nhạc đè" not in line.lower()


# ─────────────── Whisper unavailable ───────────────


def test_a_broken_recogniser_is_reported_not_raised() -> None:
    def no_model(path: Path) -> RawTranscript:
        raise AsrError("Chưa cài faster-whisper")

    [line] = [f"{f.level}:{f.code}:{f.msg}"
              for f in check_speech_asr(VIDEO, edl(), RULES, no_model, TERMS)]  # fmt: skip
    assert line.startswith("warn:T8:") and "faster-whisper" in line


def test_check_does_not_mutate_the_edl() -> None:
    e = edl()
    before = e.model_dump()
    run(e, FakeWhisper({VIDEO.name: words(SAID)}))
    assert e.model_dump() == before


@pytest.mark.parametrize("bad", [[], [SpokenWord(text="ồn", start=0.1, end=0.3, prob=0.5)]])
def test_almost_nothing_heard_is_a_failure(bad: list[SpokenWord]) -> None:
    [line] = run(edl(), FakeWhisper({VIDEO.name: bad}))
    assert line.startswith("error:T8:") and "100%" in line
