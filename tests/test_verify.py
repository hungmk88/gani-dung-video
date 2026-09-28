"""Unit tests: domain/verify.py — option-compliance checks V1–V9 on AI drafts (4c, spec §7.4)."""

from __future__ import annotations

import copy
from typing import Any

from gani_dung_video.domain.drafts import AiResponse
from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
from gani_dung_video.domain.plan import Plan, PlanBlock
from gani_dung_video.domain.verify import VerifyContext, parse_response, verify_drafts
from gani_dung_video.infrastructure.config_files import load_glossary, load_rules

RULES = load_rules()
BANNED = load_glossary().banned_claims


def cand(cid: str, clip: str, start: float, labels: list[str]) -> Candidate:
    return Candidate(id=cid, clip=clip, start=start, end=start + 5.0, peak=start + 2.0,
                     duration=5.0, fits_beats=[4, 6], labels=labels, aesthetic=5.0)  # fmt: skip


CANDS = [
    cand("a0", "a.mp4", 0, ["nguoi_thao_tac"]), cand("a1", "a.mp4", 10, ["thiet_bi"]),
    cand("a2", "a.mp4", 20, ["thiet_bi"]), cand("a3", "a.mp4", 30, ["thiet_bi"]),
    cand("b0", "b.mp4", 0, ["thanh_pham"]), cand("b1", "b.mp4", 6, ["thanh_pham"]),
    cand("c0", "c.mp4", 0, ["thiet_bi"]), cand("c1", "c.mp4", 20, ["nguoi_thao_tac"]),
    cand("d0", "d.mp4", 0, ["thiet_bi"]), cand("d1", "d.mp4", 20, ["thiet_bi"]),
    cand("e0", "e.mp4", 0, ["thanh_pham"]),
]  # fmt: skip


def plan(
    purpose: str = "gioi_thieu_xuong", drafts: int = 2, text_rules: dict[str, Any] | None = None
) -> Plan:
    blocks = [PlanBlock(role="hook", beats=4), PlanBlock(role="main", beats=8,
              required_tags=["nguoi_thao_tac", "thiet_bi"]),
              PlanBlock(role="cta", beats=4, text="Nhắn tin nhận báo giá sỉ")]  # fmt: skip
    return Plan(rules_version="2.0",
                options={"style": "ke_chuyen_nghe", "purpose": purpose, "duration": 15,
                         "drafts": drafts, "music": "khong_nhac"},
                driver="grid", music=None, beat_sec=0.75, target_beats=16, planned_beats=16,
                beats_per_scene_allowed=[4, 6], blocks=blocks,
                text_rules=text_rules or {"max_words": 8, "per_block": 1},
                draft_strategy=["toi_uu", "doi_hook_doi_mach"][:drafts])  # fmt: skip


PICK = PickFile(rules_version="2.0", style="ke_chuyen_nghe", beat_sec=0.75,
                beats_per_scene_allowed=[4, 6], dishes=[],
                blocks=[BlockCandidates(role=r, beats=4, required_tags=[], missing_tags=[],
                                        candidates=CANDS) for r in ("hook", "main", "cta")],
                missing_shots=[], similar_pairs=[("d0", "d1")])  # fmt: skip


def seg(block: str, sid: str, beats: int = 4, text: str = "") -> dict[str, Any]:
    return {"block": block, "scene_id": sid, "beats": beats, "text": text}


def good() -> dict[str, Any]:
    return {"drafts": [
        {"draft": "A", "label": "Tối ưu", "missing_shots": [], "warnings": [], "segments": [
            seg("hook", "a0", text="Một ngày ở xưởng"), seg("main", "a1"), seg("main", "b0"),
            seg("cta", "c0", text="Nhắn tin nhận báo giá sỉ")]},
        {"draft": "B", "label": "Đổi hook", "missing_shots": [], "warnings": [], "segments": [
            seg("hook", "c1", text="Tay nghề từng mẻ giò"), seg("main", "a2"), seg("main", "b1"),
            seg("cta", "d0", text="Nhắn tin nhận báo giá sỉ ngay")]},
    ]}  # fmt: skip


def ctx(p: Plan | None = None, **kw: Any) -> VerifyContext:
    return VerifyContext(plan=p or plan(), pick=PICK, rules=RULES, banned=BANNED, **kw)


def run(raw: object, c: VerifyContext | None = None) -> tuple[AiResponse | None, list[Any]]:
    c = c or ctx()
    resp, issues = parse_response(raw, c)
    if resp is None:
        return None, issues
    fixed, more = verify_drafts(resp, c)
    return fixed, issues + more


def codes(issues: list[Any], level: str | None = None) -> list[str]:
    return [i.code for i in issues if level is None or i.level == level]


# ─────────────── happy path ───────────────


def test_a_compliant_response_has_no_issues() -> None:
    resp, issues = run(good())
    assert resp is not None and issues == []


# ─────────────── V1: structure, known ids, known blocks, draft count ───────────────


def test_v1_not_json_or_wrong_shape_asks_the_ai_again() -> None:
    bad: list[object] = ["not json", {"drafts": "x"}, {"nope": []}]
    for raw in bad:
        resp, issues = run(raw)
        assert resp is None and codes(issues) == ["V1"] and issues[0].level == "ai"


def test_v1_unknown_scene_id_or_block_or_wrong_draft_count() -> None:
    r = good()
    r["drafts"][0]["segments"][1]["scene_id"] = "zz_s0_0"
    r["drafts"][1]["segments"][0]["block"] = "khong_co"
    _, issues = run(r)
    assert codes(issues) == ["V1", "V1"]
    one = good()
    one["drafts"] = one["drafts"][:1]
    _, issues = run(one)
    assert codes(issues) == ["V1"] and "2 bản" in issues[0].msg


def test_v1_json_text_is_accepted() -> None:
    import json

    resp, issues = run(json.dumps(good()))
    assert resp is not None and issues == []


# ─────────────── V2 + V3: beats ───────────────


def test_v3_beats_outside_the_allowed_set_are_rounded_by_code() -> None:
    r = good()
    r["drafts"][0]["segments"][0]["beats"] = 5  # allowed {4, 6}: 5 → 4 (ties go down)
    resp, issues = run(r)
    assert codes(issues) == ["V3"] and issues[0].level == "auto"
    assert resp is not None and resp.drafts[0].segments[0].beats == 4


def test_v2_total_beats_must_match_the_plan() -> None:
    r = good()
    r["drafts"][0]["segments"][2]["beats"] = 6  # main 10 (within one scene of 8), total 18 ≠ 16
    _, issues = run(r)
    assert codes(issues, "ai") == ["V2"] and issues[0].draft == "A"


def test_v2_block_more_than_one_scene_off() -> None:
    r = good()
    for s in r["drafts"][0]["segments"][1:3]:
        s["block"] = "hook"  # hook 12 (plan 4), main 0 (plan 8): both > 6 beats off
    _, issues = run(r)
    assert codes(issues, "ai") == ["V2", "V2"]


def test_v2_block_off_by_one_scene_is_tolerated_but_total_is_strict() -> None:
    p = plan()
    hook = p.blocks[0].model_copy(update={"beats": 6})
    p = p.model_copy(update={"blocks": [hook, *p.blocks[1:]], "planned_beats": 18,
                             "target_beats": 18})  # fmt: skip
    r = good()  # hook 4 instead of 6 (one scene off), total 16 instead of 18
    _, issues = run(r, ctx(p))
    assert codes(issues) == ["V2", "V2"]  # only the totals, once per draft
    assert all("tổng" in i.msg.lower() for i in issues)


# ─────────────── V4: repeats, clip limits, spacing, near-duplicates ───────────────


def test_v4_same_segment_twice_is_refused() -> None:
    r = good()
    r["drafts"][0]["segments"][2]["scene_id"] = "a1"
    _, issues = run(r)
    assert "V4" in codes(issues, "ai")


def test_v4_more_than_3_segments_of_one_clip() -> None:
    r = good()
    cta = seg("cta", "a3", text="Nhắn tin nhận báo giá sỉ")
    r["drafts"][0]["segments"] = [seg("hook", "a0", text="Một ngày"), seg("main", "a1"),
                                  seg("main", "a2"), cta]  # fmt: skip
    _, issues = run(r)
    assert any(i.code == "V4" and "a.mp4" in i.msg for i in issues)


def test_v4_segments_of_one_clip_closer_than_3_s() -> None:
    r = good()
    r["drafts"][1]["segments"][2]["scene_id"] = "b0"  # b0 0–5 s and b1 6–11 s: 1 s apart
    r["drafts"][1]["segments"][1]["scene_id"] = "b1"
    _, issues = run(r)
    assert any(i.code == "V4" and "3" in i.msg for i in issues)


def test_v4_both_halves_of_a_near_duplicate_pair() -> None:
    r = good()
    r["drafts"][1]["segments"][1]["scene_id"] = "d1"  # d0 already in B's cta
    _, issues = run(r)
    assert any(i.code == "V4" and "giống" in i.msg for i in issues)


# ─────────────── V5, V6 ───────────────


def test_v5_segment_outside_the_candidates_of_its_block() -> None:
    main = PICK.blocks[1].model_copy(update={"candidates": CANDS[4:]})  # no a*-segments
    pick = PICK.model_copy(update={"blocks": [PICK.blocks[0], main, PICK.blocks[2]]})
    c = VerifyContext(plan=plan(), pick=pick, rules=RULES, banned=BANNED)
    _, issues = run(good(), c)
    assert codes(issues, "ai") == ["V5", "V5"]  # A main a1, B main a2


def test_v6_required_tag_missing_is_only_a_warning_unless_listed() -> None:
    r = good()
    r["drafts"][0]["segments"][1]["scene_id"] = "e0"  # A main: e0 + b0 → no worker, no machine
    _, issues = run(r)
    assert codes(issues) == ["V6"] and issues[0].level == "warn" and issues[0].draft == "A"
    r["drafts"][0]["missing_shots"] = ["Công nhân thao tác"]
    _, issues = run(r)
    assert issues == []


# ─────────────── V7: text length, density, banned claims ───────────────


def test_v7_too_many_words_and_too_many_texts_per_block() -> None:
    r = good()
    r["drafts"][0]["segments"][1]["text"] = "một hai ba bốn năm sáu bảy tám chín"  # 9 > 8
    r["drafts"][0]["segments"][2]["text"] = "thêm"  # 2 texts in main, per_block = 1
    _, issues = run(r)
    assert codes(issues, "ai").count("V7") == 2


def test_v7_every_n_scenes_density_for_tvc() -> None:
    p = plan(text_rules={"max_words": 4, "every_n_scenes": 2})
    r = good()
    for s in r["drafts"][0]["segments"][:3]:
        s["text"] = "chữ"  # 4 texts on 4 scenes (cta counts) > ceil(4 / 2) = 2
    _, issues = run(r, ctx(p))
    assert "V7" in codes(issues, "ai")


def test_v7_banned_claim_in_text() -> None:
    r = good()
    r["drafts"][1]["segments"][0]["text"] = "Giò ngon nhất"
    _, issues = run(r)
    assert any(i.code == "V7" and "ngon nhất" in i.msg and i.draft == "B" for i in issues)


def test_v7_cta_block_text_is_exempt_from_the_word_limit() -> None:
    p = plan(text_rules={"max_words": 4})  # CTA "Nhắn tin nhận báo giá sỉ" has 6 words
    r = good()
    r["drafts"][0]["segments"][0]["text"] = "Một ngày"
    r["drafts"][1]["segments"][0]["text"] = "Tay nghề"
    _, issues = run(r, ctx(p))
    assert issues == []


# ─────────────── V8: CTA and promotion deadline ───────────────


def test_v8_cta_text_must_appear_in_the_last_block() -> None:
    r = good()
    r["drafts"][0]["segments"][3]["text"] = "Liên hệ ngay"
    _, issues = run(r)
    assert codes(issues, "ai") == ["V8"] and issues[0].draft == "A"


def test_v8_promotion_needs_its_deadline_on_screen() -> None:
    p = plan(purpose="khuyen_mai")
    p = p.model_copy(update={"blocks": [*p.blocks[:2], p.blocks[2].model_copy(
        update={"text": "Ưu đãi đến 30/10"})]})  # fmt: skip
    r = good()
    for d in r["drafts"]:
        d["segments"][3]["text"] = "Ưu đãi đến 30/10"
    _, issues = run(r, ctx(p, promo_deadline="30/10"))
    assert issues == []
    r["drafts"][1]["segments"][3]["text"] = "Ưu đãi đến cuối tháng"
    _, issues = run(r, ctx(p, promo_deadline="30/10"))
    assert codes(issues, "ai") == ["V8", "V8"]  # CTA text and deadline both missing in B


# ─────────────── V9: drafts must really differ ───────────────


def test_v9_same_hook_or_too_much_overlap_flags_the_later_draft() -> None:
    r = good()
    b = r["drafts"][1]["segments"]
    b[0]["scene_id"] = "a0"  # same hook as A
    b[1]["scene_id"], b[2]["scene_id"] = "a1", "b0"  # 3 of 4 scenes shared = 75% > 60%
    _, issues = run(r)
    v9 = [i for i in issues if i.code == "V9"]
    assert len(v9) == 2 and {i.draft for i in v9} == {"B"}


def test_single_draft_skips_v9() -> None:
    r = good()
    r["drafts"] = r["drafts"][:1]
    _, issues = run(r, ctx(plan(drafts=1)))
    assert issues == []


def test_verify_does_not_mutate_the_input() -> None:
    r = good()
    r["drafts"][0]["segments"][0]["beats"] = 5
    before = copy.deepcopy(r)
    run(r)
    assert r == before
