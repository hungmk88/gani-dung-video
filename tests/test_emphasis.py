"""Unit tests: domain/emphasis.py — which words of a caption are approved keywords (T-C).

Matching is by whole syllables, ignoring case and tone marks, like the banned-claims check.
"""

from __future__ import annotations

import pytest

from gani_dung_video.domain.emphasis import emphasise, keyword_indexes, keyword_spans

KW = ["HACCP"]


def spans(text: str, kw: list[str] = KW) -> list[str]:
    return [text[a:b] for a, b in keyword_spans(text, kw)]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Đạt chuẩn HACCP Codex 2022", ["HACCP"]),
        ("đạt chuẩn haccp", ["haccp"]),  # case does not matter
        ("HACCP, Codex 2022", ["HACCP"]),  # punctuation is not part of the word
        ("HACCP và HACCP", ["HACCP", "HACCP"]),  # every occurrence
        ("Chuẩn HACCPX", []),  # a longer word is not the keyword
        ("NOHACCP", []),
        ("Giò lụa tươi", []),
    ],
)
def test_single_word_keywords_are_found_as_whole_words(text: str, found: list[str]) -> None:
    assert spans(text) == found


def test_multi_word_keywords_ignore_tone_marks_and_case() -> None:
    kw = ["giò lụa", "hút chân không"]
    assert spans("Giò Lụa thượng hạng", kw) == ["Giò Lụa"]
    assert spans("gio lua hut chan khong", kw) == ["gio lua", "hut chan khong"]
    assert spans("giò rồi lụa", kw) == []  # the syllables must be next to each other


def test_the_longest_keyword_wins_when_two_overlap() -> None:
    kw = ["chân không", "hút chân không"]
    assert spans("Đóng gói hút chân không", kw) == ["hút chân không"]


def test_no_keywords_means_no_spans() -> None:
    assert keyword_spans("Đạt chuẩn HACCP", []) == []
    assert keyword_spans("", KW) == []


def test_emphasise_wraps_only_the_keyword_and_keeps_every_other_character() -> None:
    out = emphasise("Đạt chuẩn HACCP Codex", KW, "<", ">")
    assert out == "Đạt chuẩn <HACCP> Codex"
    assert emphasise("Không có gì", KW, "<", ">") == "Không có gì"
    assert emphasise("HACCP", KW, "<", ">") == "<HACCP>"
    assert emphasise("haccp, HACCP.", KW, "[", "]") == "[haccp], [HACCP]."


def test_keyword_indexes_mark_the_words_of_a_spoken_group() -> None:
    words = ["Nhà", "máy", "đạt", "HACCP,", "Codex"]
    assert keyword_indexes(words, KW) == {3}
    assert keyword_indexes(["gio", "lua", "ngon"], ["giò lụa"]) == {0, 1}
    assert keyword_indexes(words, []) == set()
