"""Unit tests: domain/asr_check.py — T8, Whisper listens to the finished video (5c-3)."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.asr_check import (
    AsrResult,
    Tok,
    align,
    compare,
    diagnose,
    to_tokens,
    within_spans,
)
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.infrastructure.config_files import load_rules

CFG = load_rules()["qc"]["t8"]


def word(text: str, start: float, end: float | None = None) -> SpokenWord:
    return SpokenWord(text=text, start=start, end=end if end is not None else start + 0.3,
                      prob=0.9)  # fmt: skip


def toks(spec: str, start: float = 0.0, step: float = 0.3) -> list[Tok]:
    return to_tokens([word(w, round(start + i * step, 3)) for i, w in enumerate(spec.split())])


def texts(ts: list[Tok]) -> list[str]:
    return [t.text for t in ts]


# ─────────────── tokens ───────────────


def test_tokens_ignore_case_and_punctuation_and_keep_the_start() -> None:
    got = to_tokens([word("GANI,", 1.0), word("Giò", 1.4)])
    assert texts(got) == ["gani", "giò"] and [t.start for t in got] == [1.0, 1.4]


def test_a_multi_syllable_word_becomes_syllables_sharing_its_start() -> None:
    got = to_tokens([word("giò lụa", 2.0, 2.6)])
    assert texts(got) == ["giò", "lụa"] and [t.start for t in got] == [2.0, 2.0]


def test_tokens_drop_words_that_are_only_punctuation() -> None:
    assert to_tokens([word("...", 0.0), word("ngon", 0.5)]) == [Tok(text="ngon", start=0.5)]


# ─────────────── alignment ───────────────


def test_align_identical_sequences_pairs_every_position() -> None:
    assert align(["a", "b", "c"], ["a", "b", "c"]) == [(0, 0), (1, 1), (2, 2)]


def test_align_marks_substitution_deletion_and_insertion() -> None:
    assert align(["a", "b", "c"], ["a", "x", "c"]) == [(0, 0), (1, 1), (2, 2)]  # 1 substitution
    assert align(["a", "b", "c"], ["a", "c"]) == [(0, 0), (1, None), (2, 1)]  # b missing
    assert align(["a", "c"], ["a", "b", "c"]) == [(0, 0), (None, 1), (1, 2)]  # b extra


def test_align_handles_empty_sides() -> None:
    assert align([], ["a"]) == [(None, 0)]
    assert align(["a"], []) == [(0, None)]
    assert align([], []) == []


# ─────────────── WER and drift ───────────────


def test_wer_counts_substitutions_deletions_and_insertions() -> None:
    r = compare(toks("giò lụa gani ngon"), toks("giò lúa ngon"))  # 1 sub + 1 deletion of 4
    assert r.ref_count == 4 and r.errors == 2 and r.wer == pytest.approx(0.5)


def test_perfect_match_has_zero_wer_and_zero_drift() -> None:
    r = compare(toks("giò lụa gani"), toks("giò lụa gani"))
    assert r.wer == 0.0 and r.median_drift_sec == pytest.approx(0.0)


def test_nothing_heard_is_total_failure_without_a_drift() -> None:
    r = compare(toks("giò lụa gani"), [])
    assert r.wer == 1.0 and r.median_drift_sec is None


def test_no_reference_words_means_nothing_to_judge() -> None:
    r = compare([], toks("ồn ào"))
    assert r.ref_count == 0 and r.wer == 0.0


def test_drift_is_the_median_over_matched_words_only() -> None:
    ref = toks("một hai ba bốn năm", step=1.0)  # 0, 1, 2, 3, 4
    hyp = [Tok(text="một", start=0.10), Tok(text="hai", start=1.05), Tok(text="ba", start=2.40),
           Tok(text="xxx", start=9.0), Tok(text="năm", start=4.10)]  # fmt: skip
    r = compare(ref, hyp)  # matched: 0.10, 0.05, 0.40, 0.10 → median 0.10; "bốn" is an error
    assert r.errors == 1 and r.median_drift_sec == pytest.approx(0.10)


def test_one_wild_word_does_not_move_the_median() -> None:
    ref = toks("a b c d e", step=1.0)
    hyp = [Tok(text=t.text, start=t.start + (5.0 if t.text == "c" else 0.05)) for t in ref]
    assert compare(ref, hyp).median_drift_sec == pytest.approx(0.05)


# ─────────────── words heard outside the voice ───────────────


def test_words_heard_outside_speech_spans_are_ignored() -> None:
    hyp = [Tok(text="la", start=0.5), Tok(text="giò", start=2.0), Tok(text="lụa", start=2.4),
           Tok(text="ê", start=9.0)]  # fmt: skip
    kept = within_spans(hyp, [(1.8, 2.8)], margin=0.3)
    assert texts(kept) == ["giò", "lụa"]  # "la" (music lyrics) and "ê" (end card) are dropped


def test_the_margin_keeps_words_a_little_outside_the_span() -> None:
    hyp = [Tok(text="a", start=1.6), Tok(text="b", start=1.4)]
    assert texts(within_spans(hyp, [(2.0, 3.0)], margin=0.5)) == ["a"]  # 1.6 >= 2.0 - 0.5


def test_no_spans_means_nothing_is_kept() -> None:
    assert within_spans([Tok(text="a", start=1.0)], [], margin=0.3) == []


# ─────────────── verdicts ───────────────


def res(wer: float, drift: float | None = 0.05, ref: int = 20) -> AsrResult:
    return AsrResult(ref_count=ref, errors=round(wer * ref), wer=wer, matched=ref,
                     median_drift_sec=drift)  # fmt: skip


def test_clean_video_has_no_findings() -> None:
    assert diagnose(res(0.05), None, CFG) == []


def test_wer_exactly_at_the_limit_passes() -> None:
    assert diagnose(res(0.15), None, CFG) == []


def test_wer_above_the_limit_blocks_delivery() -> None:
    [f] = diagnose(res(0.30), None, CFG)
    assert f.code == "T8" and f.level == "error" and f.fix is None and "30%" in f.msg


def test_music_masking_is_named_when_the_music_free_mix_is_clean() -> None:
    [f] = diagnose(res(0.30), res(0.05), CFG)
    assert f.level == "error" and "nhạc" in f.msg.lower() and "5%" in f.msg


def test_hard_to_hear_voice_is_named_when_even_the_music_free_mix_fails() -> None:
    [f] = diagnose(res(0.30), res(0.28), CFG)
    assert f.level == "error" and "giọng" in f.msg.lower() and "nhạc đè" not in f.msg.lower()


def test_drift_above_the_limit_is_only_a_warning() -> None:
    [f] = diagnose(res(0.05, drift=0.20), None, CFG)
    assert f.code == "T8" and f.level == "warn" and "200ms" in f.msg


def test_both_problems_are_both_reported() -> None:
    found = diagnose(res(0.30, drift=0.20), None, CFG)
    assert sorted(f.level for f in found) == ["error", "warn"]


def test_nothing_to_judge_gives_no_findings() -> None:
    assert diagnose(res(0.0, drift=None, ref=0), None, CFG) == []
