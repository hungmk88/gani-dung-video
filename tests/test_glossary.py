"""Unit tests: domain/glossary.py + config/glossary.json (brand = GANI, V7 banned phrases)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gani_dung_video.domain.glossary import Word, apply_glossary, find_banned_claims, tokens
from gani_dung_video.infrastructure.config_files import CONFIG_DIR, ConfigError, load_glossary

G = load_glossary()
TERMS = G.term_objects()


def fix(text: str) -> str:
    words = [Word(w, float(i), float(i) + 0.5) for i, w in enumerate(text.split())]
    return " ".join(w.text for w in apply_glossary(words, TERMS))


def test_brand_target_is_gani_only() -> None:
    raw = (CONFIG_DIR / "glossary.json").read_text(encoding="utf-8")
    assert "Gani Foods" not in raw and "FOODY" not in raw
    assert [t.correct for t in TERMS if "gani" in t.correct.lower()] == ["GANI"]


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("sản phẩm của gani foods", "sản phẩm của GANI"),
        ("Gani Food, chào cả nhà", "GANI, chào cả nhà"),
        ("xưởng ga ni phu đi đạt chuẩn", "xưởng GANI đạt chuẩn"),
        ("đạt chuẩn hát sép", "đạt chuẩn HACCP"),
        ("chứng nhận vê xê a.", "chứng nhận VCA."),
        ("nước lộc thuỷ", "nước Lộc Thủy"),
        ("dò lụa ngon", "giò lụa ngon"),
        ("chà quế nướng", "chả quế nướng"),
        ("vĩnh lọc ước lệ", "Vĩnh Lộc Ước Lễ"),
        ("gani", "GANI"),
    ],
)
def test_names_are_corrected(heard: str, expected: str) -> None:
    assert fix(heard) == expected


def test_merged_word_keeps_timing_span() -> None:
    words = [Word("của", 0.0, 0.3), Word("gani", 0.4, 0.7), Word("foods", 0.8, 1.2)]
    out = apply_glossary(words, TERMS)
    assert out[1] == Word("GANI", 0.4, 1.2) and len(out) == 2


def test_unrelated_words_untouched() -> None:
    assert fix("gà ni cơm gà") == "gà ni cơm gà"


def test_tokens_keep_percent_and_tone_marks() -> None:
    assert tokens("Đạt 100% chuẩn, NGON!") == ["đạt", "100%", "chuẩn", "ngon"]


BANNED = G.banned_claims


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Giá trị thật từ xưởng", []),
        ("Quản trị chất lượng HACCP", []),
        ("Kiên trì từng mẻ giò", []),
        ("Chữa cháy kịp thời", []),
        ("Đặc trị mọi bệnh", ["đặc trị"]),
        ("Giúp điều trị tiểu đường", ["điều trị"]),
        ("Ăn để phòng bệnh", ["phòng bệnh"]),
        ("Thịt tươi 100%", ["100%"]),
        ("Ngon nhất Sài Gòn", ["ngon nhất"]),
        ("Giò bò uy tín nhất", ["uy tín nhất"]),
        # Decision 28/09/2026 (applied 29/09): bare "nhất" wrongly flagged these.
        ("Thống nhất quy trình từng mẻ", []),
        ("Nhất định phải thử", []),
        # Dev Package 03, T0: text typed without tone marks is judged the same way.
        ("Dac tri moi benh", ["đặc trị"]),
        ("Gia tri that, quan tri chat luong", []),
    ],
)
def test_banned_claims_are_phrases(text: str, found: list[str]) -> None:
    assert find_banned_claims(text, BANNED) == found


def test_banned_list_is_the_agreed_phrase_list() -> None:
    assert set(BANNED) == {
        "100%", "duy nhất", "số 1", "điều trị", "đặc trị", "chữa trị",
        "chữa bệnh", "trị bệnh", "phòng bệnh", "ngon nhất", "tốt nhất", "rẻ nhất",
        "chất lượng nhất", "sạch nhất", "uy tín nhất",
        # Dev Package 03, T0 (decision 01/10/2026): flat list, four more phrases.
        # NVH verified the list on 13/10/2026: "hàn the" and "không chất bảo quản" are not on it.
        "thần dược", "minh long", "atala", "foco",
    }  # fmt: skip
    assert "trị" not in BANNED and "chữa" not in BANNED and "nhất" not in BANNED


# ─────────────── caption highlight keywords (Dev Package 03, T-C) ───────────────


def test_the_approved_highlight_list_starts_with_haccp_only() -> None:
    assert load_glossary().caption_highlight_keywords == ["HACCP"]


def _glossary_file(tmp_path: Path, **extra: object) -> Path:
    import json

    g = tmp_path / "glossary.json"
    g.write_text(json.dumps({"terms": [], "banned_claims": ["tốt nhất", "thần dược"], **extra}),
                 encoding="utf-8")  # fmt: skip
    return g


def test_a_file_without_the_list_means_no_highlighting(tmp_path: Path) -> None:
    assert load_glossary(_glossary_file(tmp_path)).caption_highlight_keywords == []


@pytest.mark.parametrize("keyword", ["tốt nhất", "Than Duoc", "giò lụa tốt nhất Sài Gòn"])
def test_a_highlight_keyword_that_holds_a_banned_claim_stops_loading(
    tmp_path: Path, keyword: str
) -> None:
    g = _glossary_file(tmp_path, caption_highlight_keywords=["HACCP", keyword])
    with pytest.raises(ConfigError, match="từ nhấn") as err:
        load_glossary(g)
    assert keyword in str(err.value) and "cụm cấm" in str(err.value)  # says what to fix


@pytest.mark.parametrize("bad", [[""], ["   "], ["HACCP", "haccp"]])
def test_blank_or_repeated_highlight_keywords_are_refused(tmp_path: Path, bad: list[str]) -> None:
    with pytest.raises(ConfigError, match="từ nhấn"):
        load_glossary(_glossary_file(tmp_path, caption_highlight_keywords=bad))


# ─────────────── visual dictionary (phase 4b, decision 29/09/2026) ───────────────


def test_visual_dictionary_has_the_agreed_dishes() -> None:
    visual = load_glossary().visual
    assert set(visual) == {"giò lụa", "chả mỡ chiên", "bò viên", "rửa tay", "chứng nhận",
                           "giò bò", "chả quế", "nem chua", "giò chả"}  # fmt: skip
    assert all(1 <= len(p) <= 3 for p in visual.values())
    assert all(p.isascii() for ps in visual.values() for p in ps)  # CLIP reads English only


@pytest.mark.parametrize(
    "visual",
    [{"giò lụa": []}, {"giò lụa": ["a", "b", "c", "d"]}, {"giò lụa": [""]}, {"": ["a"]},
     {"giò lụa": "a string"}],
)  # fmt: skip
def test_bad_visual_entries_are_refused(tmp_path: Path, visual: object) -> None:
    import json

    g = tmp_path / "glossary.json"
    g.write_text(json.dumps({"terms": [], "banned_claims": [], "visual": visual}), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"glossary\.json"):
        load_glossary(g)
