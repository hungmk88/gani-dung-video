"""Unit tests: domain/zoom.py — punch-ins on the beat or on a taste word, alternate framing."""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.footage import Clip
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.zoom import Span, Zoom, beat_punches, speech_zooms, spread, zoom_room
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
EFFECTS = RULES["effects"]


def clip(name: str, w: int, h: int) -> Clip:
    return Clip(file=name, size_bytes=1, duration_sec=60.0, width=w, height=h, rotation=0,
                orientation="doc", fps=30.0, video_codec="h264", pix_fmt="yuv420p",
                color_transfer=None, color_primaries=None, is_hdr=False, has_audio_stream=True,
                audio_tracks=1)  # fmt: skip


SIZES = {"hd.mp4": (1080, 1920), "4k.mp4": (2160, 3840), "720.mp4": (720, 1280),
         "ngang.mp4": (1920, 1080)}  # fmt: skip
CLIPS = {name: clip(name, *size) for name, size in SIZES.items()}
ROOM = zoom_room(CLIPS, 1080, 1920, float(EFFECTS["max_upscale"]))


def r(z: Zoom) -> tuple[float, float | None, float]:
    """A zoom rounded to the microsecond, to compare with plain numbers."""
    at = None if z.punch_sec is None else round(z.punch_sec, 6)
    return (round(z.base, 6), at, round(z.punch, 6))


def w(text: str, a: float, b: float) -> SpokenWord:
    return SpokenWord(text=text, start=a, end=b, prob=0.9)


def test_spread_keeps_first_and_last_and_never_repeats() -> None:
    assert spread(10, 3) == [0, 4, 9] or spread(10, 3) == [0, 5, 9]
    assert spread(4, 9) == [0, 1, 2, 3]
    assert spread(5, 1) == [0] and spread(0, 3) == [] and spread(5, 0) == []


def test_room_follows_how_much_the_clip_is_already_enlarged() -> None:
    assert ROOM("hd.mp4") == pytest.approx(1.15)  # 1080×1920 fills the frame as is
    assert ROOM("4k.mp4") == pytest.approx(2.3)
    assert ROOM("720.mp4") < 1.0 and ROOM("ngang.mp4") < 1.0  # already blown up: no zoom


# ─────────────── music styles: punch on the beat ───────────────

TVC = RULES["styles"]["tvc_cat_nhanh"]


def spans(n: int, beats: int, beat: float = 0.5, source: str = "hd.mp4") -> list[Span]:
    return [Span(i * beats * beat, (i + 1) * beats * beat, source) for i in range(n)]


def test_tvc_punches_on_the_second_beat_of_two_beat_shots_up_to_max_effects() -> None:
    z = beat_punches(spans(8, 2), TVC, 0.5, 30, ROOM)
    assert len(z) == int(TVC["max_effects"]) == 3
    assert set(z) == {0, 4, 7} or set(z) == {0, 3, 7}  # spread over the video
    assert all(r(v) == (1.0, 0.5, 1.05) for v in z.values())


def test_one_beat_shots_and_blown_up_clips_get_no_punch() -> None:
    assert beat_punches(spans(8, 1), TVC, 0.5, 30, ROOM) == {}
    assert beat_punches(spans(8, 2, source="720.mp4"), TVC, 0.5, 30, ROOM) == {}


def test_styles_without_punch_zoom_are_untouched() -> None:
    assert beat_punches(spans(8, 2), RULES["styles"]["sang_trong"], 0.5, 30, ROOM) == {}


# ─────────────── speech mode: alternate framing, punch on a taste word ───────────────

REVIEW: dict[str, Any] = RULES["styles"]["review_mon_an"]
SALES: dict[str, Any] = RULES["styles"]["ban_hang_co_loi"]


def phrases() -> tuple[list[Span], list[list[SpokenWord]]]:
    ss = [Span(0.0, 3.0, "hd.mp4"), Span(3.0, 6.0, "hd.mp4"), Span(6.0, 9.0, "hd.mp4"),
          Span(9.0, 12.0, "hd.mp4")]  # fmt: skip
    ws = [
        [w("Hôm", 0.1, 0.4), w("nay", 0.4, 0.7), w("ăn", 0.7, 1.0), w("thử", 1.0, 1.3)],
        [w("Miếng", 3.1, 3.5), w("chả", 3.5, 3.8), w("giòn", 3.8, 4.2), w("rụm", 4.2, 4.6)],
        [w("Thật", 6.1, 6.4), w("sự", 6.4, 6.7), w("đáng", 6.7, 7.0), w("tiền.", 7.0, 7.4)],
        [w("Ngon", 9.1, 9.5), w("lắm", 9.5, 9.9)],
    ]
    return ss, ws


def test_review_punches_on_taste_words_while_the_speaker_is_seen() -> None:
    ss, ws = phrases()
    z = speech_zooms(ss, ws, [], REVIEW, EFFECTS, ROOM)
    assert {i: r(v) for i, v in z.items()} == {
        1: (1.0, 0.8, 1.08),  # "giòn" at 3.8 s
        2: (1.0, 0.7, 1.08),  # "đáng tiền" at 6.7 s
        3: (1.0, 0.1, 1.08),  # "Ngon" (any case)
    }


def test_no_punch_under_broll_or_too_close_to_the_end_of_the_phrase() -> None:
    ss, ws = phrases()
    ws[3] = [w("Ăn", 9.1, 9.5), w("ngon", 11.7, 11.95)]  # 0.3 s left < keyword_min_hold_sec
    z = speech_zooms(ss, ws, [(3.5, 4.5)], REVIEW, EFFECTS, ROOM)  # B-roll over "giòn"
    assert set(z) == {2}


def test_tone_marks_count_and_words_match_whole() -> None:
    ss, _ = phrases()
    ws: list[list[SpokenWord]] = [[w("ngón", 0.1, 0.5)], [w("thơmm", 3.1, 3.5)],
                                  [w("ngonlành", 6.1, 6.5)], []]  # fmt: skip
    assert speech_zooms(ss, ws, [], REVIEW, EFFECTS, ROOM) == {}


def test_review_punches_are_capped_by_max_effects() -> None:
    ss = [Span(i * 3.0, i * 3.0 + 3.0, "hd.mp4") for i in range(6)]
    ws = [[w("ngon", i * 3.0 + 0.5, i * 3.0 + 0.9)] for i in range(6)]
    z = speech_zooms(ss, ws, [], REVIEW, EFFECTS, ROOM)
    assert len(z) == int(REVIEW["max_effects"]) == 3 and min(z) == 0 and max(z) == 5


def test_sales_frames_every_other_phrase_closer_when_the_clip_allows() -> None:
    ss, ws = phrases()
    z = speech_zooms(ss, ws, [], SALES, EFFECTS, ROOM)
    assert z == {1: Zoom(1.12, None, 1.12), 3: Zoom(1.12, None, 1.12)}  # no keyword_zoom here
    assert speech_zooms(ss, ws, [], SALES, EFFECTS, ROOM, alternate=False) == {}  # switched off
    low = [s._replace(source="720.mp4") for s in ss]
    assert speech_zooms(low, ws, [], SALES, EFFECTS, ROOM) == {}


def test_a_punch_on_top_of_the_alternate_framing_must_fit_too() -> None:
    ss, ws = phrases()
    both = {**SALES, "keyword_zoom": 0.08}
    z = speech_zooms(ss, ws, [], both, EFFECTS, ROOM)
    assert z[1] == Zoom(1.12, None, 1.12)  # 1.12 × 1.08 > 1.15 on a 1080p clip: no punch
    deep = [s._replace(source="4k.mp4") for s in ss]
    assert r(speech_zooms(deep, ws, [], both, EFFECTS, ROOM)[1]) == (1.12, 0.8, 1.2096)
