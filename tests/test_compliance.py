"""Unit tests: domain/compliance.py — C1, the claims & compliance check (Dev Package 03, T0).

C1 looks at every text that can end up on screen or in the voice-over (spoken lines, chapter
titles, highlighted keywords) and reports the banned phrases in it, with or without tone marks.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from gani_dung_video.domain.compliance import CODE, ClaimHit, check_claims
from gani_dung_video.infrastructure.config_files import load_glossary

BANNED = load_glossary().banned_claims


def phrases(hits: list[ClaimHit]) -> list[str]:
    return [h.phrase for h in hits]


def test_the_check_is_named_c1() -> None:
    assert CODE == "C1"


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("Thần dược cho mọi nhà", "thần dược"),  # with tone marks
        ("Than duoc cho moi nha", "thần dược"),  # typed without them
        ("KHÔNG CHẤT BẢO QUẢN", "không chất bảo quản"),  # upper case
        ("khong chat bao quan", "không chất bảo quản"),
        ("Hàng Minh Long giá rẻ", "minh long"),  # competitor names
        ("giò lụa ATALA", "atala"),
        ("so sánh với Foco", "foco"),
    ],
)
def test_banned_phrases_are_caught_with_or_without_tone_marks(text: str, phrase: str) -> None:
    hits = check_claims({"thoại": text}, BANNED)
    assert phrases(hits) == [phrase]


def test_text_typed_in_decomposed_unicode_is_caught_too() -> None:
    nfd = unicodedata.normalize("NFD", "Thần dược")
    assert nfd != "Thần dược"
    assert phrases(check_claims({"tiêu đề": nfd}, BANNED)) == ["thần dược"]


@pytest.mark.parametrize(
    "text",
    [
        "Giá trị thật từ xưởng",  # "trị" alone is not a claim
        "Gia tri that tu xuong",  # same, without tone marks
        "Quản trị chất lượng HACCP",
        "Focaccia nướng lò",  # "foco" inside a longer word
        "Atalanta thắng trận",  # "atala" inside a longer word
        "Minh Khang ở Long An",  # the syllables are not next to each other as "minh long"
        "Thần kỳ của nghề giò chả",  # one syllable of a phrase is not the phrase
    ],
)
def test_whole_syllable_sequences_only_no_substring_hits(text: str) -> None:
    assert check_claims({"thoại": text}, BANNED) == []


def test_each_hit_names_where_the_text_came_from() -> None:
    hits = check_claims({"thoại": "Ngon nhất", "tiêu đề": "Giò lụa", "từ nhấn": "100%"}, BANNED)
    assert [(h.source, h.phrase) for h in hits] == [("thoại", "ngon nhất"), ("từ nhấn", "100%")]
    assert hits[0].text == "Ngon nhất"


def test_clean_blank_and_single_approved_words_pass() -> None:
    fields = {"thoại": "Giò lụa làm từ thịt heo tươi", "tiêu đề": "", "từ nhấn": "HACCP"}
    assert check_claims(fields, BANNED) == []
    assert check_claims({}, BANNED) == []


def test_the_phrase_list_is_read_from_the_config_file_not_the_code(tmp_path: Path) -> None:
    cfg = tmp_path / "glossary.json"

    def hits_with(banned: list[str]) -> list[str]:
        cfg.write_text(json.dumps({"terms": [], "banned_claims": banned}), encoding="utf-8")
        banned_now = load_glossary(cfg).banned_claims
        return phrases(check_claims({"thoại": "Giò ngon tuyệt cú mèo"}, banned_now))

    assert hits_with(["cú mèo"]) == ["cú mèo"]
    assert hits_with(["cú vọ"]) == []  # editing the file changes the verdict
