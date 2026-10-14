"""Unit tests: domain/preflight.py + config/rules.json (spec v2.1 §5.3.A, §6, §10)."""

from __future__ import annotations

import copy
import itertools
from typing import Any

import pytest

from gani_dung_video.domain.preflight import Result, check_bpm, preflight_check, scenes_needed
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
KM_OK = {"uu_dai": "Giảm 20%", "thoi_han": "31/10/2026"}
NOTE_20 = (
    "Mở bằng cảnh cắt lát giò thật chậm, sau đó là quy trình gói lá chuối trong xưởng "
    "sạch, chữ trắng tối giản, kết bằng logo và số Zalo đặt hàng"
)
X_CODES = {f"X{i}" for i in range(1, 19)}


def opts(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "phong_cach": "tvc_cat_nhanh", "muc_dich": "ban_hang", "do_dai_giay": 30,
        "so_ban_nhap": 1, "nhac": "ai_tu_chon",
    }  # fmt: skip
    base.update(kw)
    return base


def run(**kw: Any) -> Result:
    return preflight_check(opts(**kw), RULES)


def codes(r: Result) -> set[str]:
    return {i["code"] for i in r["errors"] + r["warnings"] + r["adjustments"]}


# ─────────────── rules.json shape ───────────────


def test_rules_dimensions_give_1440_combos() -> None:
    dims = (
        len(RULES["styles"]), len(RULES["purposes"]), len(RULES["durations"]),
        len(RULES["drafts"]["allowed"]), len(RULES["music_modes"]),
    )  # fmt: skip
    assert dims == (6, 4, 5, 3, 4)
    assert dims[0] * dims[1] * dims[2] * dims[3] * dims[4] == 1440


def test_conflicts_x1_to_x18_with_spec_levels() -> None:
    assert set(RULES["conflicts"]) == X_CODES
    blocks = {k for k, c in RULES["conflicts"].items() if c["level"] == "block"}
    assert blocks == {"X1", "X2", "X3", "X4", "X5", "X14"}  # spec §6.3
    for c in RULES["conflicts"].values():
        assert c["level"] in {"block", "warn", "auto"}
        assert c["stage"] in {"preflight", "plan", "ai", "render"}


def test_matrices_cover_every_pair() -> None:
    for s in RULES["styles"]:
        assert set(RULES["compat_style_purpose"][s]) == set(RULES["purposes"])
        assert set(RULES["compat_style_duration"][s]) == set(RULES["durations"])
        assert set(RULES["scenes_needed"][s]) == set(RULES["durations"])


def test_purpose_block_ratios_sum_to_one() -> None:
    for p in RULES["purposes"].values():
        assert sum(r for _, r in p["blocks"]) == pytest.approx(1.0)


def test_brand_is_gani_everywhere() -> None:
    # Decision 29/09/2026: the full company name appears only in the end-card footer line;
    # 14/10/2026: that name is GANI FOODS (was GANI FOODY), and the old one is gone everywhere.
    assert "FOODY" not in str(RULES["render"]["brand"])
    rules = copy.deepcopy(RULES)
    assert rules["render"]["brand"].pop("footer").startswith("GANI FOODS – ")
    rules.pop("_ghi_chu_render")
    assert "FOODS" not in str(rules)


# ─────────────── spec §5.3.A scene table ───────────────

SCENE_TABLE = {
    "tvc_cat_nhanh": [11, 23, 35, 46, 138], "ban_hang_co_loi": [6, 12, 18, 24, 72],
    "ke_chuyen_nghe": [5, 10, 15, 20, 60], "review_mon_an": [8, 15, 23, 30, 90],
    "sang_trong": [4, 8, 11, 15, 45], "theo_mo_ta": [5, 10, 15, 20, 60],
}  # fmt: skip


@pytest.mark.parametrize("style", list(SCENE_TABLE))
def test_scenes_needed_matches_spec_table(style: str) -> None:
    got = [scenes_needed(RULES, style, d) for d in (15, 30, 45, 60, 180)]
    assert got == SCENE_TABLE[style]


def test_scene_table_consistent_with_scene_avg() -> None:
    # Guards against typos: every cell ≈ duration ÷ scene_avg_sec (±1 scene).
    for style, row in RULES["scenes_needed"].items():
        avg = RULES["styles"][style]["scene_avg_sec"]
        for d, n in row.items():
            assert abs(n - int(d) / avg) <= 1, (style, d, n)


# ─────────────── spec §10 test cases checkable at preflight ───────────────
# T21 (X16) is AI-stage; T03..T22 3-minute renders are later phases — only their preflight here.

SPEC_CASES: list[tuple[str, dict[str, Any], bool, set[str]]] = [
    ("T01", opts(do_dai_giay=15, nhac="thu_vien", nhac_bpm=120), True, set()),
    ("T02", opts(muc_dich="khuyen_mai", so_ban_nhap=3, khuyen_mai=KM_OK), True, set()),
    ("T03", opts(muc_dich="gioi_thieu_xuong", do_dai_giay=180), False, {"X1"}),
    ("T04", opts(muc_dich="ke_chuyen_thuong_hieu", do_dai_giay=45, so_ban_nhap=2,
                 nhac="khong_nhac"), True, {"X11"}),
    ("T05", opts(phong_cach="ban_hang_co_loi", so_ban_nhap=2, nhac="tai_len",
                 nhac_quyen_su_dung=True, co_loi_noi=True), True, set()),
    ("T06", opts(phong_cach="ban_hang_co_loi", muc_dich="khuyen_mai", do_dai_giay=60,
                 nhac="khong_nhac", khuyen_mai=KM_OK), True, set()),
    ("T07", opts(phong_cach="ban_hang_co_loi", muc_dich="gioi_thieu_xuong",
                 co_loi_noi=False), True, {"X7"}),
    ("T08", opts(phong_cach="ke_chuyen_nghe", muc_dich="gioi_thieu_xuong", so_ban_nhap=2,
                 nhac="thu_vien", nhac_bpm=92), True, set()),
    ("T09", opts(phong_cach="ke_chuyen_nghe", muc_dich="ke_chuyen_thuong_hieu",
                 do_dai_giay=180, nhac="tai_len", nhac_quyen_su_dung=True, nhac_giay=120),
     True, {"X13"}),
    ("T10", opts(phong_cach="ke_chuyen_nghe", do_dai_giay=15, so_ban_nhap=3), True, {"X10"}),
    ("T11", opts(phong_cach="review_mon_an", do_dai_giay=45), True, set()),
    ("T12", opts(phong_cach="review_mon_an", muc_dich="gioi_thieu_xuong", so_ban_nhap=2,
                 nhac="khong_nhac"), True, {"X9"}),
    ("T13", opts(phong_cach="review_mon_an", muc_dich="khuyen_mai", do_dai_giay=15,
                 so_ban_nhap=2, nhac="tai_len", khuyen_mai=KM_OK), False, {"X14"}),
    ("T14", opts(phong_cach="sang_trong", muc_dich="ke_chuyen_thuong_hieu", do_dai_giay=60,
                 so_ban_nhap=3, nhac="thu_vien", nhac_bpm=72), True, set()),
    ("T15", opts(phong_cach="sang_trong", muc_dich="khuyen_mai", khuyen_mai=KM_OK),
     True, {"X9"}),
    ("T16", opts(phong_cach="sang_trong", muc_dich="gioi_thieu_xuong", do_dai_giay=15,
                 so_ban_nhap=2, nhac="tai_len", nhac_quyen_su_dung=True, nhac_bpm=128),
     True, {"X10", "X12"}),
    ("T17", opts(phong_cach="theo_mo_ta", do_dai_giay=60, so_ban_nhap=2,
                 ghi_chu="mở bằng cảnh đẹp nhất"), False, {"X4"}),
    ("T18", opts(phong_cach="theo_mo_ta", muc_dich="gioi_thieu_xuong", do_dai_giay=180,
                 so_ban_nhap=3, nhac="thu_vien", ghi_chu=NOTE_20), True, {"X15"}),
    ("T19", opts(phong_cach="ke_chuyen_nghe", muc_dich="khuyen_mai", nhac="thu_vien"),
     False, {"X3"}),
    ("T20", opts(phong_cach="ke_chuyen_nghe", muc_dich="gioi_thieu_xuong", do_dai_giay=45,
                 so_canh_dung_duoc=8), False, {"X5"}),
    ("T22", opts(phong_cach="ban_hang_co_loi", muc_dich="khuyen_mai", do_dai_giay=180,
                 nhac="thu_vien", khuyen_mai=KM_OK), False, {"X2"}),
]  # fmt: skip


@pytest.mark.parametrize(
    ("case", "o", "valid", "expected"), SPEC_CASES, ids=[c[0] for c in SPEC_CASES]
)
def test_spec_cases(case: str, o: dict[str, Any], valid: bool, expected: set[str]) -> None:
    r = preflight_check(o, RULES)
    assert r["valid"] is valid, r
    got = codes(r)
    assert expected <= got, (case, got)
    if not expected:
        assert not (got & X_CODES), (case, got)
    if not valid:
        assert r["error_code"] in expected and r["error_msg"]


# ─────────────── input validation ───────────────


def test_valid_combo_reports_scene_count() -> None:
    r = run()
    assert (r["valid"], r["error_code"], r["errors"], r["scenes_needed"]) == (True, "", [], 23)


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ({"phong_cach": "Product Review"}, "INVALID_PHONG_CACH"),
        ({"muc_dich": "Awareness"}, "INVALID_MUC_DICH"),
        ({"do_dai_giay": 300}, "INVALID_DO_DAI"),
        ({"do_dai_giay": "30"}, "INVALID_DO_DAI"),
        ({"do_dai_giay": True}, "INVALID_DO_DAI"),
        ({"do_dai_giay": 30.0}, "INVALID_DO_DAI"),
        ({"so_ban_nhap": 4}, "INVALID_SO_BAN_NHAP"),
        ({"so_ban_nhap": True}, "INVALID_SO_BAN_NHAP"),
        ({"nhac": "LƯỚI ẢO"}, "INVALID_NHAC"),
        ({"so_canh_dung_duoc": -1}, "INVALID_FACT"),
        ({"nhac_bpm": 0}, "INVALID_FACT"),
        ({"nhac_quyen_su_dung": "true"}, "INVALID_FACT"),
        ({"khuyen_mai": "giảm 20%"}, "INVALID_FACT"),
        ({"khuyen_mai": {"uu_dai": "x", "han": "y"}}, "INVALID_FACT"),
        ({"phong_cách": "tvc_cat_nhanh"}, "INVALID_FIELD"),
    ],
)
def test_invalid_inputs(bad: dict[str, Any], code: str) -> None:
    r = run(**bad)
    assert (r["valid"], r["error_code"], r["scenes_needed"]) == (False, code, None), r
    assert r["error_msg"]


@pytest.mark.parametrize("o", [None, [], "tvc", 42])
def test_non_mapping_options(o: object) -> None:
    assert preflight_check(o, RULES)["error_code"] == "INVALID_OPTIONS"


def test_missing_keys_report_every_required_field() -> None:
    r = preflight_check({}, RULES)
    assert {e["code"] for e in r["errors"]} == {
        "INVALID_PHONG_CACH", "INVALID_MUC_DICH", "INVALID_DO_DAI", "INVALID_SO_BAN_NHAP",
        "INVALID_NHAC",
    }  # fmt: skip


def test_invalid_choice_message_lists_allowed_values() -> None:
    assert "tvc_cat_nhanh" in run(phong_cach="TVC")["error_msg"]


# ─────────────── conflict edge cases ───────────────


def test_x14_needs_explicit_true() -> None:
    assert run(nhac="tai_len")["error_code"] == "X14"
    assert run(nhac="tai_len", nhac_quyen_su_dung=False)["error_code"] == "X14"
    assert run(nhac="tai_len", nhac_quyen_su_dung=True)["valid"]
    assert run(nhac="thu_vien")["valid"]  # rights tick only applies to uploads


def test_x4_passes_at_exactly_20_words() -> None:
    note = " ".join(["chữ"] * 20)
    assert run(phong_cach="theo_mo_ta", ghi_chu=note)["valid"]
    assert run(phong_cach="theo_mo_ta", ghi_chu=note.rsplit(" ", 1)[0])["error_code"] == "X4"


def test_x5_x6_boundaries() -> None:
    # ke_chuyen_nghe 30s needs 10 scenes; ideal = ceil(10 × 1.3) = 13.
    base = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong"}
    assert run(**base, so_canh_dung_duoc=9)["error_code"] == "X5"
    assert "X6" in codes(run(**base, so_canh_dung_duoc=10))
    assert "X6" in codes(run(**base, so_canh_dung_duoc=12))
    assert not codes(run(**base, so_canh_dung_duoc=13))


def test_x7_skipped_when_speech_unknown() -> None:
    assert "X7" not in codes(run(phong_cach="ban_hang_co_loi"))


def test_x12_variants() -> None:
    assert check_bpm(RULES, 120, 110, 130) is None
    halved = check_bpm(RULES, 240, 110, 130)
    assert halved is not None and halved["level"] == "auto" and "120" in halved["msg"]
    far = check_bpm(RULES, 170, 110, 130)
    assert far is not None and far["level"] == "warn"


def test_x13_counts_the_end_card_in_the_video_length() -> None:
    """Audit 4: 16 s of music is longer than a 15 s video but not than 15 s + the 2 s end card."""
    kw: dict[str, Any] = {
        "phong_cach": "ke_chuyen_nghe",
        "muc_dich": "gioi_thieu_xuong",
        "do_dai_giay": 15,
        "nhac": "tai_len",
        "nhac_quyen_su_dung": True,
    }
    assert "X13" in codes(run(nhac_giay=16, **kw))
    assert "X13" not in codes(run(nhac_giay=17, **kw))
    assert "X13" not in codes(run(nhac_giay=40, **kw))


def test_music_facts_ignored_without_music() -> None:
    r = run(phong_cach="sang_trong", muc_dich="ke_chuyen_thuong_hieu", nhac="khong_nhac",
            nhac_bpm=200, nhac_giay=5)  # fmt: skip
    assert not ({"X12", "X13"} & codes(r))


def test_rules_version_mismatch_warns() -> None:
    assert "RULES_VERSION" not in codes(run(rules_version=2.0))
    r = run(rules_version="1.0")
    assert r["valid"] and "RULES_VERSION" in codes(r)


# ─────────────── exhaustive sweep of all 1,440 combos ───────────────


def test_all_1440_combos_return_a_known_verdict() -> None:
    known = X_CODES | {"W_STYLE_PURPOSE", "W_STYLE_DURATION"}
    n = blocked = 0
    for s, p, d, k, m in itertools.product(
        RULES["styles"], RULES["purposes"], RULES["durations"],
        RULES["drafts"]["allowed"], RULES["music_modes"],
    ):  # fmt: skip
        r = preflight_check(
            opts(phong_cach=s, muc_dich=p, do_dai_giay=int(d), so_ban_nhap=k, nhac=m,
                 nhac_quyen_su_dung=True, khuyen_mai=KM_OK, ghi_chu=NOTE_20),
            RULES,
        )  # fmt: skip
        n += 1
        assert codes(r) <= known and r["valid"] == (not r["errors"])
        if not r["valid"]:
            blocked += 1
            assert r["error_code"] in {"X1", "X2"}
    # With complete inputs only length rules block: TVC×180 (4 purposes × 3 × 4) +
    # Khuyến mãi×180 for the other 5 styles (5 × 3 × 4).
    assert (n, blocked) == (1440, 48 + 60)


# ─────────────── ticket 13/10/2026: BPM safety warnings ───────────────


def luxe(**kw: Any) -> Result:
    return run(phong_cach="sang_trong", muc_dich="gioi_thieu_xuong", do_dai_giay=15,
               nhac="thu_vien", nhac_file="t.mp3", **kw)  # fmt: skip


def test_a_track_the_table_blocks_is_a_warning_not_an_error() -> None:
    r = luxe(nhac_bpm=104)
    assert r["valid"] and "W_BPM_BLOCK" in codes(r)
    text = next(w["msg"] for w in r["warnings"] if w["code"] == "W_BPM_BLOCK")
    assert "BPM 104" in text and "Sang trọng" in text and "66–82" in text


def test_a_track_that_only_needs_a_cut_gets_the_lighter_warning() -> None:
    r = luxe(nhac_bpm=88)
    assert r["valid"] and "W_BPM_CUT" in codes(r) and "W_BPM_BLOCK" not in codes(r)


def test_a_track_that_fits_and_an_unknown_bpm_say_nothing() -> None:
    assert not {c for c in codes(luxe(nhac_bpm=72)) if c.startswith("W_BPM")}
    assert not {c for c in codes(luxe()) if c.startswith("W_BPM")}  # the web sends no BPM yet


def test_bpm_override_silences_the_warning_and_must_be_a_boolean() -> None:
    assert not {c for c in codes(luxe(nhac_bpm=100, bpm_override=True)) if c.startswith("W_BPM")}
    assert not luxe(nhac_bpm=100, bpm_override="yes")["valid"]


def test_no_music_means_no_bpm_warning() -> None:
    assert not {c for c in codes(run(nhac="khong_nhac", nhac_bpm=100)) if c.startswith("W_BPM")}
