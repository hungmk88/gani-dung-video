"""Unit tests: domain/speech_edit.py — audio-first editing for Bán hàng có lời (5c-1)."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.speech_edit import (
    Phrase,
    aroll_timeline,
    classify_phrases,
    default_overlays,
    duck_expression,
    duck_gain,
    duck_intervals,
    split_phrases,
    usable_phrases,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
CFG = RULES["speech_edit"]
STYLE = RULES["styles"]["ban_hang_co_loi"]
FPS = 30


def words(spec: str, start: float = 0.0, step: float = 0.3, gaps: dict[int, float] | None = None
          ) -> list[SpokenWord]:  # fmt: skip
    """'a b. c' → words 0.3 s long, back to back, with extra silence before word i in gaps."""
    out, t = [], start
    for i, w in enumerate(spec.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append(SpokenWord(text=w, start=round(t, 3), end=round(t + step, 3), prob=0.9))
        t += step
    return out


def texts(phrases: list[Phrase]) -> list[str]:
    return [p.text for p in phrases]


# ─────────────── splitting ───────────────


def test_split_at_sentence_ends_and_real_pauses() -> None:
    ws = words("Giò lụa GANI ngon. Đặt ngay hôm nay nhé bạn ơi", gaps={7: 0.4})
    got = split_phrases(ws, "a.mp4", CFG, STYLE)
    assert texts(got) == ["Giò lụa GANI ngon.", "Đặt ngay hôm", "nay nhé bạn ơi"]
    assert [p.id for p in got] == ["a_p0", "a_p1", "a_p2"]


def test_commas_split_only_long_phrases() -> None:
    short = split_phrases(words("Bò viên, giò lụa ngon"), "a.mp4", CFG, STYLE)
    assert texts(short) == ["Bò viên, giò lụa ngon"]  # 1.5 s: the comma stays inside
    long_text = (
        "một hai ba bốn năm sáu bảy tám chín mười mười_một mười_hai mười_ba mười_bốn "
        "mười_lăm, mười_sáu mười_bảy mười_tám"
    )  # 4.5 s before the comma
    got = split_phrases(words(long_text), "a.mp4", CFG, STYLE)
    assert len(got) == 2 and got[0].text.endswith(",")


def test_cut_window_pads_the_speech_but_never_eats_the_neighbour_words() -> None:
    ws = words("Xin chào. Mời bạn", step=0.5, gaps={2: 1.0})  # 1 s of silence before "Mời"
    a, b = split_phrases(ws, "a.mp4", CFG, STYLE)
    assert a.cut_in == pytest.approx(0.0)  # clip start: nothing before the first word
    assert a.cut_out == pytest.approx(1.0 + 0.12)  # 120 ms after the last word
    assert b.cut_in == pytest.approx(2.0 - 0.08)  # 80 ms before the first word
    tight = words("Một hai. Ba bốn", step=0.5)  # no silence at all between the two phrases
    x, y = split_phrases(tight, "a.mp4", CFG, STYLE)
    assert x.cut_out <= y.cut_in + 1e-9  # the pads meet in the middle, no overlap


def test_tiny_fillers_are_dropped() -> None:
    ws = words("Ờ. Giò lụa GANI thơm ngon", gaps={1: 0.5})
    assert texts(split_phrases(ws, "a.mp4", CFG, STYLE)) == ["Giò lụa GANI thơm ngon"]


def test_phrases_with_banned_claims_are_never_offered() -> None:
    ws = words("Giò ngon nhất Sài Gòn. Giò làm mỗi sáng.")
    got = usable_phrases(split_phrases(ws, "a.mp4", CFG, STYLE), ["ngon nhất"], CFG)
    assert texts(got) == ["Giò làm mỗi sáng."]


def test_overlong_phrases_are_not_offered() -> None:
    ws = words(" ".join(["từ"] * 40))  # 12 s with no punctuation and no pause
    assert usable_phrases(split_phrases(ws, "a.mp4", CFG, STYLE), [], CFG) == []


def test_classify_phrases_reports_why_each_dropped_phrase_was_excluded() -> None:
    ws = words("Giò ngon nhất Sài Gòn. Giò làm mỗi sáng.")
    phrases = split_phrases(ws, "a.mp4", CFG, STYLE)
    ok, dropped = classify_phrases(phrases, ["ngon nhất"], CFG)
    assert texts(ok) == ["Giò làm mỗi sáng."]
    assert len(dropped) == 1 and dropped[0].reason == "tu_cam"
    assert dropped[0].id == phrases[0].id and dropped[0].clip == "a.mp4"
    assert dropped[0].text == phrases[0].text


def test_classify_phrases_flags_overlong_ones_as_qua_dai() -> None:
    ws = words(" ".join(["từ"] * 40))
    phrases = split_phrases(ws, "a.mp4", CFG, STYLE)
    ok, dropped = classify_phrases(phrases, [], CFG)
    assert ok == [] and [d.reason for d in dropped] == ["qua_dai"]


def test_classify_phrases_keeps_nothing_dropped_when_all_are_usable() -> None:
    ws = words("Giò lụa GANI thơm ngon")
    phrases = split_phrases(ws, "a.mp4", CFG, STYLE)
    ok, dropped = classify_phrases(phrases, [], CFG)
    assert ok == phrases and dropped == []


# ─────────────── A-roll timeline ───────────────


def test_timeline_chains_phrases_frame_accurately_and_retimes_words() -> None:
    ws = words("Xin chào. Mời bạn", step=0.5, gaps={2: 1.0})
    a, b = split_phrases(ws, "a.mp4", CFG, STYLE)
    cuts = aroll_timeline([b, a], FPS)  # any order: the editor chooses
    assert [c.phrase_id for c in cuts] == ["a_p1", "a_p0"]
    assert cuts[0].t_start == 0.0 and cuts[1].t_start == cuts[0].t_end
    for c in cuts:
        for t in (c.t_start, c.t_end, c.src_in, c.src_out):
            assert t * FPS == pytest.approx(round(t * FPS), abs=1e-6)
    first_word = cuts[0].words[0]  # "Mời" at 2.0 s in the clip, cut starts at 1.92
    assert first_word.start == pytest.approx(0.08, abs=1 / FPS)


# ─────────────── B-roll overlays ───────────────


def test_default_overlays_keep_the_speaker_on_hook_and_cta() -> None:
    ws = words("Một hai ba bốn. Năm sáu bảy tám. Chín mười một hai. Ba bốn năm sáu.")
    cuts = aroll_timeline(split_phrases(ws, "a.mp4", CFG, STYLE), FPS)
    slots = default_overlays(cuts, beat_sec=0.5, beats_allowed=[2, 4], max_shift=0.15)
    covered = {s.phrase_id for s in slots}
    assert cuts[0].phrase_id not in covered and cuts[-1].phrase_id not in covered
    assert covered == {cuts[1].phrase_id, cuts[2].phrase_id}
    for s in slots:
        assert round((s.t_end - s.t_start) / 0.5) in (2, 4)


def test_overlay_start_snaps_to_a_beat_only_when_close() -> None:
    ws = words("Một hai ba bốn. Năm sáu bảy tám. Chín mười một hai. Ba bốn năm sáu.")
    cuts = aroll_timeline(split_phrases(ws, "a.mp4", CFG, STYLE), FPS)
    slots = default_overlays(cuts, beat_sec=0.5, beats_allowed=[2, 4], max_shift=0.15)
    for s in slots:
        phrase_start = next(c.t_start for c in cuts if c.phrase_id == s.phrase_id)
        on_beat = abs(s.t_start / 0.5 - round(s.t_start / 0.5)) < 1e-6
        assert on_beat or s.t_start == phrase_start
        assert abs(s.t_start - phrase_start) <= 0.15 + 1 / FPS


# ─────────────── ducking ───────────────


def test_duck_intervals_merge_close_speech_and_ignore_the_gaps() -> None:
    ws = words("Xin chào. Mời bạn", step=0.5, gaps={2: 1.0})
    cuts = aroll_timeline(split_phrases(ws, "a.mp4", CFG, STYLE), FPS)
    spans = duck_intervals(cuts, merge_gap=0.4)
    assert len(spans) == 1  # the 0.2 s cut gap between phrases is below merge_gap
    assert spans[0][0] == pytest.approx(cuts[0].words[0].start)


def test_duck_gain_is_minus_18_db_under_speech_with_150_ms_ramps() -> None:
    spans = [(1.0, 2.0)]
    g = 10 ** (-18 / 20)
    assert duck_gain(0.5, spans, -18, 0.15) == pytest.approx(1.0)
    assert duck_gain(1.5, spans, -18, 0.15) == pytest.approx(g)
    assert g < duck_gain(0.925, spans, -18, 0.15) < 1.0  # half-way down the ramp
    assert duck_gain(2.2, spans, -18, 0.15) == pytest.approx(1.0)


def test_duck_expression_for_ffmpeg_matches_the_python_gain() -> None:
    spans = [(1.0, 2.0), (3.0, 3.5)]
    expr = duck_expression(spans, -18, 0.15)
    ns = {"clip": lambda x, lo, hi: min(max(x, lo), hi), "max": max}
    for t in (0.2, 0.9, 1.5, 2.1, 3.2, 4.0):
        assert eval(expr, {**ns, "t": t}) == pytest.approx(duck_gain(t, spans, -18, 0.15))
    assert expr.count("clip(") == 2 * len(spans)


def test_zero_duck_fade_is_a_hard_step_in_python_and_in_ffmpeg() -> None:
    spans = [(1.0, 2.0), (3.0, 3.5)]
    g = 10 ** (-18 / 20)
    expr = duck_expression(spans, -18, 0.0)
    assert "/0.000000" not in expr  # ffmpeg would divide by zero
    ns = {"between": lambda x, lo, hi: float(lo <= x <= hi), "max": max}
    for t, want in ((0.99, 1.0), (1.0, g), (1.5, g), (2.0, g), (2.01, 1.0), (3.2, g), (4.0, 1.0)):
        assert duck_gain(t, spans, -18, 0.0) == pytest.approx(want)
        assert eval(expr, {**ns, "t": t}) == pytest.approx(want)
