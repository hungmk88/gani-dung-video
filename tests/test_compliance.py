"""Unit tests: domain/compliance.py — C1, the claims & compliance check (Dev Package 03, T0).

C1 looks at every text that can end up on screen or in the voice-over (spoken lines, chapter
titles, highlighted keywords) and reports the banned phrases in it, with or without tone marks.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from gani_dung_video.domain.compliance import (
    CODE,
    KARAOKE_JOIN_MAX_GAP_SEC,
    ClaimHit,
    check_claims,
    check_edl_claims,
    edl_texts,
)
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


# ─────────────── audit C3: a phrase that crosses two karaoke groups ───────────────


def _edl_with_karaoke(groups: list[list[str]]) -> Edl:
    shot = Shot(source="a.mp4", src_in=0, src_out=3, t_start=0, t_end=3, block="hook",
                hdr=False, dissolve_out_sec=0, pad_sec=0)  # fmt: skip
    karaoke = [[SpokenWord(text=t, start=0.0, end=0.3, prob=0.9) for t in g] for g in groups]
    return Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500,
               shots=[shot], content_end=3.0, end_card_sec=2.0, duration=5.0, music=None,
               ambient_db=None, lufs=-14.0, true_peak_db=-2.0, font="x", captions=[],
               karaoke=karaoke, emphasis=[])  # fmt: skip


def test_a_phrase_split_across_two_karaoke_groups_is_caught() -> None:
    # Spoken as one sentence, drawn as two groups: each group alone is clean.
    e = _edl_with_karaoke([["Giò", "lụa", "ngon"], ["nhất", "Sài", "Gòn"]])
    assert check_claims(edl_texts(e), BANNED) == []  # the per-group check cannot see it
    hits = check_edl_claims(e, BANNED)
    assert [h.phrase for h in hits] == ["ngon nhất"]
    assert hits[0].source == "lời 1–2"


def test_a_phrase_inside_one_group_is_reported_once() -> None:
    e = _edl_with_karaoke([["Giò", "ngon", "nhất"], ["Sài", "Gòn"]])
    assert [(h.source, h.phrase) for h in check_edl_claims(e, BANNED)] == [("lời 1", "ngon nhất")]


def test_a_phrase_can_cross_more_than_two_groups() -> None:
    e = _edl_with_karaoke([["Giò", "chất"], ["lượng"], ["nhất", "vùng"]])
    assert [h.phrase for h in check_edl_claims(e, BANNED)] == ["chất lượng nhất"]


def test_clean_speech_stays_clean_across_groups() -> None:
    e = _edl_with_karaoke([["Giá"], ["trị", "thật"], ["từ", "xưởng"]])
    assert check_edl_claims(e, BANNED) == []


# ─────────────── NVH 14/10: a phrase is joined across groups only inside one phrase ───────────────


def _timed(groups: list[tuple[float, str]], shots: list[tuple[float, float]]) -> Edl:
    """Karaoke groups that start at the given seconds (0.25 s per word) over the given shots."""
    karaoke = [[SpokenWord(text=w, start=t0 + 0.25 * i, end=t0 + 0.25 * i + 0.25, prob=0.9)
                for i, w in enumerate(text.split())] for t0, text in groups]  # fmt: skip
    cuts = [Shot(source="a.mp4", src_in=0, src_out=b - a, t_start=a, t_end=b, block="speech",
                 hdr=False, dissolve_out_sec=0, pad_sec=0) for a, b in shots]  # fmt: skip
    return Edl(style="ban_hang_co_loi", width=540, height=960, fps=30, video_kbps=2500,
               shots=cuts, content_end=shots[-1][1], end_card_sec=2.0,
               duration=shots[-1][1] + 2.0, music=None, ambient_db=None, lufs=-14.0,
               true_peak_db=-2.0, font="x", captions=[], karaoke=karaoke, emphasis=[],
               mode="speech")  # fmt: skip


def test_the_join_limit_is_half_a_second() -> None:
    assert KARAOKE_JOIN_MAX_GAP_SEC == 0.5


def test_groups_in_one_phrase_with_a_short_pause_are_joined() -> None:
    # "...uy tín" ends at 1.25 s, "nhất là..." starts at 1.45 s: 0.2 s apart, one shot
    e = _timed([(0.0, "Sản phẩm của xưởng uy tín"), (1.45, "nhất là giò lụa")], [(0.0, 4.0)])
    assert [(h.source, h.phrase) for h in check_edl_claims(e, BANNED)] == [
        ("lời 1–2", "uy tín nhất")
    ]


def test_a_pause_of_exactly_the_limit_still_joins() -> None:
    e = _timed([(0.0, "Sản phẩm của xưởng uy tín"), (1.75, "nhất là giò lụa")], [(0.0, 4.0)])
    assert [h.phrase for h in check_edl_claims(e, BANNED)] == ["uy tín nhất"]  # gap 0.5 s


def test_groups_in_different_shots_are_never_joined() -> None:
    """NVH condition 2: "…uy tín" closes one phrase, "nhất là…" opens the next one."""
    e = _timed(
        [(0.0, "Sản phẩm của xưởng uy tín"), (1.3, "nhất là giò lụa")], [(0.0, 1.3), (1.3, 4.0)]
    )
    assert check_edl_claims(e, BANNED) == []


def test_a_silence_longer_than_the_limit_breaks_the_join() -> None:
    """NVH condition 3: 0.8 s of silence inside one shot is not one sentence any more."""
    e = _timed([(0.0, "Sản phẩm uy tín"), (1.55, "nhất vùng này")], [(0.0, 4.0)])  # gap 0.8 s
    assert check_edl_claims(e, BANNED) == []


def test_only_the_joins_that_hold_are_checked_in_a_longer_chain() -> None:
    # g1+g2 sit in shot 1 ("chất lượng" + "nhất"); g2 ends "ngon" and g3 opens shot 2 with
    # "nhất": that pair is across a cut and must stay silent.
    e = _timed([(0.0, "Giò chất lượng"), (0.9, "nhất vùng này ngon"), (1.95, "nhất")],
               [(0.0, 1.9), (1.9, 4.0)])  # fmt: skip
    hits = check_edl_claims(e, BANNED)
    assert [(h.source, h.phrase) for h in hits] == [("lời 1–2", "chất lượng nhất")]


def test_a_group_that_itself_crosses_a_shot_boundary_is_still_checked_whole() -> None:
    """One group is one line on screen: the per-group check is unchanged by the join rule."""
    e = _timed([(0.0, "Ai cũng nói uy tín nhất")], [(0.0, 0.8), (0.8, 4.0)])
    assert [h.phrase for h in check_claims(edl_texts(e), BANNED)] == ["uy tín nhất"]
    assert [(h.source, h.phrase) for h in check_edl_claims(e, BANNED)] == [("lời 1", "uy tín nhất")]
