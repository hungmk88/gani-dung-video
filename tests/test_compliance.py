"""Unit tests: domain/compliance.py — C1, the claims & compliance check (Dev Package 03, T0).

C1 looks at every text that can end up on screen or in the voice-over (spoken lines, chapter
titles, highlighted keywords) and reports the banned phrases in it, with or without tone marks.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from gani_dung_video.domain.compliance import CODE, ClaimHit, check_claims, edl_texts
from gani_dung_video.domain.edl import Caption, Edl, Shot
from gani_dung_video.domain.speech import SpokenWord
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


def test_edl_texts_lists_every_text_a_viewer_can_read_or_hear() -> None:
    shot = Shot(source="a.mp4", src_in=0, src_out=3, t_start=0, t_end=3, block="hook",
                hdr=False, dissolve_out_sec=0, pad_sec=0)  # fmt: skip
    caps = [Caption(start=0, end=1, text="Giò lụa", kind="text"),
            Caption(start=1, end=2, text="Chương một", kind="title"),
            Caption(start=2, end=3, text="Nhắn tin", kind="cta"),
            Caption(start=2, end=3, text="Chữ hai", kind="text")]  # fmt: skip
    words = [SpokenWord(text=t, start=i * 0.3, end=i * 0.3 + 0.3, prob=0.9)
             for i, t in enumerate(["Nhà", "máy", "HACCP"])]  # fmt: skip
    e = Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500, shots=[shot],
            content_end=3.0, end_card_sec=2.0, duration=5.0, music=None, ambient_db=None,
            lufs=-14.0, true_peak_db=-2.0, font="x", captions=caps, karaoke=[words],
            emphasis=["HACCP"])  # fmt: skip
    assert edl_texts(e) == {
        "chữ 1": "Giò lụa", "tiêu đề 1": "Chương một", "CTA 1": "Nhắn tin",
        "chữ 2": "Chữ hai", "lời 1": "Nhà máy HACCP", "từ nhấn 1": "HACCP",
    }  # fmt: skip


def test_the_phrase_list_is_read_from_the_config_file_not_the_code(tmp_path: Path) -> None:
    cfg = tmp_path / "glossary.json"

    def hits_with(banned: list[str]) -> list[str]:
        cfg.write_text(json.dumps({"terms": [], "banned_claims": banned}), encoding="utf-8")
        banned_now = load_glossary(cfg).banned_claims
        return phrases(check_claims({"thoại": "Giò ngon tuyệt cú mèo"}, banned_now))

    assert hits_with(["cú mèo"]) == ["cú mèo"]
    assert hits_with(["cú vọ"]) == []  # editing the file changes the verdict
