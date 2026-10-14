"""Unit tests: domain/speech_verify.py — checks S1–S6 on AI speech drafts (5c-2)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
from gani_dung_video.domain.plan import Plan, PlanBlock
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.speech_ai import AiSpeechResponse
from gani_dung_video.domain.speech_edit import Phrase, PhrasesFile
from gani_dung_video.domain.speech_verify import (
    VerifyContext,
    parse_speech_response,
    verify_speech_drafts,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()


def phrase(pid: str, clip: str, sec: float) -> Phrase:
    w = SpokenWord(text="x", start=0.0, end=sec, prob=0.9)
    return Phrase(id=pid, clip=clip, start=0.0, end=sec, cut_in=0.0, cut_out=sec, text=pid,
                 words=[w])  # fmt: skip


PHRASES = PhrasesFile(rules_version="2.0", style="ban_hang_co_loi", phrases=[
    phrase("hook", "a.mp4", 3.0), phrase("m1", "a.mp4", 4.0), phrase("m2", "a.mp4", 4.0),
    phrase("cta", "a.mp4", 3.0),
    # B's own phrases (draft_service style: B shares only 1 of A's 4 phrases, ≤ the 60% limit).
    phrase("m3", "a.mp4", 3.0), phrase("m4", "a.mp4", 4.0), phrase("ctb", "a.mp4", 3.0),
], excluded=[])  # fmt: skip


def cand(cid: str, clip: str, start: float, labels: list[str]) -> Candidate:
    return Candidate(id=cid, clip=clip, start=start, end=start + 4.0, peak=start + 2.0,
                     duration=4.0, fits_beats=[2, 4], labels=labels, aesthetic=5.0)  # fmt: skip


CANDS = [
    cand("br0", "b.mp4", 0, ["thanh_pham"]), cand("br1", "b.mp4", 10, ["thanh_pham"]),
    cand("br2", "b.mp4", 20, ["thanh_pham"]), cand("br3", "b.mp4", 30, ["thanh_pham"]),
    cand("br4", "b.mp4", 5.0, ["nguoi_thao_tac"]),  # 5.0: 1 s after br0 ends (4.0)
    cand("br5", "c.mp4", 0, []),
    cand("br6", "b.mp4", 4.5, []),  # 4.5: 0.5 s after br0 ends — close, but no similar_pair
]  # fmt: skip


def plan(drafts: int = 2, duration: int = 14) -> Plan:
    blocks = [PlanBlock(role="hook", beats=0), PlanBlock(role="main", beats=0,
              required_tags=["thanh_pham"]),
              PlanBlock(role="cta", beats=0, text="Nhắn tin nhận báo giá sỉ")]  # fmt: skip
    return Plan(rules_version="2.0",
               options={"style": "ban_hang_co_loi", "purpose": "gioi_thieu_xuong",
                        "duration": duration, "drafts": drafts, "music": "khong_nhac"},
               driver="speech", music=None, beat_sec=0.6, target_beats=23, planned_beats=23,
               beats_per_scene_allowed=[2, 4], blocks=blocks, text_rules={},
               draft_strategy=["toi_uu", "doi_hook_doi_mach"][:drafts])  # fmt: skip


PICK = PickFile(rules_version="2.0", style="ban_hang_co_loi", beat_sec=0.6,
                beats_per_scene_allowed=[2, 4], dishes=[],
                blocks=[BlockCandidates(role=r, beats=0, required_tags=[], missing_tags=[],
                                        candidates=CANDS) for r in ("hook", "main", "cta")],
                missing_shots=[], similar_pairs=[("br0", "br4")])  # fmt: skip


def seg(pid: str, block: str, broll: str | None = None) -> dict[str, Any]:
    return {"phrase_id": pid, "block": block, "broll": broll}


def good() -> dict[str, Any]:
    # Each draft: 3 + 4 + 4 + 3 = 14 s, matches duration exactly. B shares only "m1" with A
    # (1 of 4 phrases = 25 %, under the 60 % overlap limit) and opens on a different phrase.
    return {"drafts": [
        {"draft": "A", "label": "Tối ưu", "missing_shots": [], "warnings": [], "segments": [
            seg("hook", "hook"), seg("m1", "main", "br0"), seg("m2", "main"), seg("cta", "cta")]},
        {"draft": "B", "label": "Đổi mở đầu", "missing_shots": [], "warnings": [], "segments": [
            seg("m3", "hook"), seg("m4", "main", "br1"), seg("m1", "main"), seg("ctb", "cta")]},
    ]}  # fmt: skip


def ctx(p: Plan | None = None, **kw: Any) -> VerifyContext:
    return VerifyContext(plan=p or plan(), phrases=PHRASES, pick=PICK, rules=RULES, **kw)


def run(raw: object, c: VerifyContext | None = None) -> tuple[AiSpeechResponse | None, list[Any]]:
    c = c or ctx()
    resp, issues = parse_speech_response(raw, c)
    if resp is None:
        return None, issues
    fixed, more = verify_speech_drafts(resp, c)
    return fixed, issues + more


def codes(issues: list[Any], level: str | None = None) -> list[str]:
    return [i.code for i in issues if level is None or i.level == level]


# ─────────────── happy path ───────────────


def test_a_compliant_response_has_no_issues() -> None:
    resp, issues = run(good())
    assert resp is not None and issues == []


# ─────────────── S1: structure, known ids, hook/cta never have B-roll ───────────────


def test_s1_not_json_or_wrong_shape_asks_the_ai_again() -> None:
    cases: list[object] = ["not json", {"drafts": "x"}, {"nope": []}]
    for raw in cases:
        resp, issues = run(raw)
        assert resp is None and codes(issues) == ["S1"] and issues[0].level == "ai"


def test_s1_unknown_phrase_or_block_or_broll_or_wrong_draft_count() -> None:
    r = good()
    r["drafts"][0]["segments"][1]["phrase_id"] = "zz"
    r["drafts"][1]["segments"][0]["block"] = "khong_co"
    _, issues = run(r)
    assert codes(issues) == ["S1", "S1"]
    r2 = good()
    r2["drafts"][0]["segments"][1]["broll"] = "unknown_id"
    _, issues = run(r2)
    assert codes(issues) == ["S1"]
    one = good()
    one["drafts"] = one["drafts"][:1]
    _, issues = run(one)
    assert codes(issues) == ["S1"] and "2 bản" in issues[0].msg


def test_s1_json_text_is_accepted() -> None:
    resp, issues = run(json.dumps(good()))
    assert resp is not None and issues == []


def test_s1_broll_on_the_opening_or_closing_phrase_is_refused() -> None:
    r = good()
    r["drafts"][0]["segments"][0]["broll"] = "br2"  # hook (opening); br2 used nowhere else
    _, issues = run(r)
    assert codes(issues, "ai") == ["S1"] and issues[0].draft == "A"
    r2 = good()
    r2["drafts"][0]["segments"][-1]["broll"] = "br3"  # cta (closing); br3 used nowhere else
    _, issues = run(r2)
    assert codes(issues, "ai") == ["S1"] and issues[0].draft == "A"


# ─────────────── S2: duration ───────────────


def test_s2_total_duration_outside_the_tolerance() -> None:
    r = good()
    r["drafts"][0]["segments"] = [seg("hook", "hook"), seg("cta", "cta")]  # 3 + 3 = 6s, need 14±20%
    _, issues = run(r)
    assert codes(issues, "ai") == ["S2"] and issues[0].draft == "A"


def test_s2_duration_within_tolerance_passes() -> None:
    p = plan(duration=12)  # 14s actual vs 12s target: +16.7%, inside ±20%
    _, issues = run(good(), ctx(p))
    assert codes(issues, "ai") == []


# ─────────────── audit 2: S2 measures the cut that is rendered, not the words ───────────────


def padded(pid: str, words_sec: float, pad: float = 0.2) -> Phrase:
    """A phrase whose words last `words_sec`; the cut window holds `pad` more (0.1 s each side)."""
    w = SpokenWord(text="x", start=0.1, end=0.1 + words_sec, prob=0.9)
    return Phrase(id=pid, clip="a.mp4", start=0.1, end=0.1 + words_sec, cut_in=0.0,
                  cut_out=words_sec + pad, text=pid, words=[w])  # fmt: skip


def padded_ctx(words: list[float]) -> tuple[VerifyContext, dict[str, Any]]:
    ids = ["hook", "m1", "m2", "cta"]
    phrases = PhrasesFile(
        rules_version="2.0",
        style="ban_hang_co_loi",
        excluded=[],
        phrases=[padded(i, s) for i, s in zip(ids, words, strict=True)],
    )
    c = VerifyContext(plan=plan(drafts=1, duration=14), phrases=phrases, pick=PICK, rules=RULES)
    raw = {"drafts": [{"draft": "A", "label": "x", "missing_shots": [], "warnings": [],
                       "segments": [seg("hook", "hook"), seg("m1", "main", "br0"),
                                    seg("m2", "main"), seg("cta", "cta")]}]}  # fmt: skip
    return c, raw


def test_s2_counts_the_padding_of_every_cut() -> None:
    """Words 4.2 + 4.2 + 4.2 + 4.1 = 16.7 s is inside 14 s +20 % (16.8 s), but the four cuts
    are 0.2 s longer each: 17.5 s, which E1-DUR would meet at delivery."""
    c, raw = padded_ctx([4.2, 4.2, 4.2, 4.1])
    _, issues = run(raw, c)
    assert codes(issues, "ai") == ["S2"]
    assert "17.5" in issues[0].msg  # the message quotes the length that is judged


def test_s2_passes_when_the_cuts_themselves_fit() -> None:
    c, raw = padded_ctx([4.0, 4.0, 4.0, 3.9])  # words 15.9 s, cuts 16.7 s: inside 16.8 s
    _, issues = run(raw, c)
    assert codes(issues, "ai") == []


def test_s2_measures_the_same_length_the_timeline_cuts() -> None:
    from gani_dung_video.domain.speech_edit import aroll_timeline, cut_length

    ps = [padded("hook", 3.07), padded("m1", 4.13), padded("m2", 3.91)]
    fps = int(RULES["render"]["fps"])
    on_timeline = aroll_timeline(ps, fps)[-1].t_end
    assert sum(cut_length(p, fps) for p in ps) == pytest.approx(on_timeline, abs=1e-9)


@pytest.mark.parametrize("target", [15, 30, 45, 60])
def test_a_draft_s2_accepts_never_exceeds_the_voice_limit_of_e1(target: int) -> None:
    """The two tolerances must keep their order: S2 holds the CUT length to ±20 %, E1-DUR
    refuses a voice video beyond max(25 %, 7 s). Anything S2 lets through is deliverable."""
    pct = float(RULES["speech_edit"]["duration_tolerance_pct"])
    v = RULES["qc"]["duration_tolerance_voice_sec"][str(target)]
    limit = max(float(v["pct"]) * target, float(v["sec"]))
    assert pct * target <= limit


# ─────────────── S3: reuse, clip limits, spacing, near-duplicates ───────────────


def test_s3_same_phrase_twice_is_refused() -> None:
    r = good()
    r["drafts"][0]["segments"][2]["phrase_id"] = "m1"
    _, issues = run(r)
    assert "S3" in codes(issues, "ai")


def test_s3_same_broll_twice_is_refused() -> None:
    r = good()
    r["drafts"][0]["segments"][2]["broll"] = "br0"
    _, issues = run(r)
    assert any(i.code == "S3" and "br0" in i.msg for i in issues)


def test_s3_more_than_3_broll_segments_of_one_clip() -> None:
    # 4 middle phrases, all with B-roll from the same clip: cap (vision.max_segments_per_clip)
    # is 3, so the 4th must be refused.
    phrases = PhrasesFile(rules_version="2.0", style="ban_hang_co_loi", phrases=[
        phrase("hook", "a.mp4", 3.0), phrase("m1", "a.mp4", 3.0), phrase("m2", "a.mp4", 3.0),
        phrase("m3", "a.mp4", 3.0), phrase("m4", "a.mp4", 3.0), phrase("cta", "a.mp4", 3.0),
    ], excluded=[])  # fmt: skip
    main_cands = [cand(f"br{i}", "b.mp4", i * 10.0, ["thanh_pham"]) for i in range(4)]
    pick = PICK.model_copy(update={"blocks": [PICK.blocks[0],
                     PICK.blocks[1].model_copy(update={"candidates": main_cands}),
                     PICK.blocks[2]]})  # fmt: skip
    r = {"drafts": [{"draft": "A", "label": "x", "missing_shots": [], "warnings": [], "segments": [
        seg("hook", "hook"), seg("m1", "main", "br0"), seg("m2", "main", "br1"),
        seg("m3", "main", "br2"), seg("m4", "main", "br3"), seg("cta", "cta"),
    ]}]}  # fmt: skip
    c = VerifyContext(plan=plan(drafts=1, duration=18), phrases=phrases, pick=pick, rules=RULES)
    _, issues = run(r, c)
    assert any(i.code == "S3" and "b.mp4" in i.msg and "tối đa 3" in i.msg for i in issues)


def test_s3_broll_segments_of_one_clip_closer_than_the_gap() -> None:
    r = good()
    r["drafts"][0]["segments"][2]["broll"] = "br6"  # br0 (m1) ends 4.0, br6 starts 4.5: < gap
    _, issues = run(r)
    assert any(i.code == "S3" and "cách nhau dưới" in i.msg and i.draft == "A" for i in issues)


def test_s3_both_halves_of_a_near_duplicate_pair() -> None:
    r = good()  # draft A already uses br0 (m1); similar_pairs = [("br0", "br4")]
    r["drafts"][0]["segments"][2]["broll"] = "br4"  # now A also uses br4 (m2)
    _, issues = run(r)
    assert any(i.code == "S3" and "giống" in i.msg and i.draft == "A" for i in issues)


# ─────────────── S4: candidate pool ───────────────


def test_s4_broll_outside_the_candidates_of_its_block() -> None:
    hook_only = PICK.blocks[0].model_copy(update={"candidates": [CANDS[0]]})
    main_none = PICK.blocks[1].model_copy(update={"candidates": []})
    pick = PICK.model_copy(update={"blocks": [hook_only, main_none, PICK.blocks[2]]})
    c = VerifyContext(plan=plan(), phrases=PHRASES, pick=pick, rules=RULES)
    _, issues = run(good(), c)
    assert "S4" in codes(issues, "ai")


# ─────────────── S5: required tags ───────────────


def test_s5_required_tag_missing_is_only_a_warning_unless_listed() -> None:
    r = good()
    r["drafts"][0]["segments"][1]["broll"] = "br5"  # no labels
    r["drafts"][0]["segments"][2]["broll"] = None
    _, issues = run(r)
    assert codes(issues) == ["S5"] and issues[0].level == "warn" and issues[0].draft == "A"
    r["drafts"][0]["missing_shots"] = ["Thành phẩm"]
    _, issues = run(r)
    assert issues == []


# ─────────────── S6: variety ───────────────


def test_s6_same_opening_phrase_or_too_much_overlap() -> None:
    r = good()
    r["drafts"][1]["segments"][0]["phrase_id"] = "hook"  # same opening as A
    r["drafts"][1]["segments"][1]["phrase_id"] = "m1"  # 3 of 4 phrases shared = 75% > 60%
    _, issues = run(r)
    s6 = [i for i in issues if i.code == "S6"]
    assert len(s6) == 2 and {i.draft for i in s6} == {"B"}


def test_single_draft_skips_s6() -> None:
    r = good()
    r["drafts"] = r["drafts"][:1]
    _, issues = run(r, ctx(plan(drafts=1)))
    assert issues == []


def test_verify_does_not_mutate_the_input() -> None:
    r = good()
    before = copy.deepcopy(r)
    run(r)
    assert r == before
