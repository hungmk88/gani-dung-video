"""Tests: the draft step for 3-minute videos (phase 7c) — schema, prompt, V1/V7/V10, cost cap.

Claude is a fake that answers with a valid draft built from the plan (tests/chapter_drafts.py);
each test then breaks one thing. No key, no network, 0đ.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from chapter_drafts import OPTS, build_answer, make_plan_and_pick, rules_on

from gani_dung_video.application.draft_service import DraftError, DraftsFile, build_drafts
from gani_dung_video.domain.drafts import (
    SYSTEM_PROMPT,
    build_user_prompt,
    response_schema,
    system_prompt_for,
)
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.domain.verify import VerifyContext, parse_response, verify_drafts
from gani_dung_video.domain.verify_report import VerifyReportFile
from gani_dung_video.infrastructure.claude_client import AiReply
from gani_dung_video.infrastructure.config_files import load_glossary, load_rules

SHIPPED = load_rules()
GLOSSARY = load_glossary()
RULES = rules_on()
PLAN, PICK = make_plan_and_pick(RULES)
CHAPTERS = [c.id for c in PLAN.chapters]


def ctx(letters: str = "AB") -> VerifyContext:
    return VerifyContext(plan=PLAN, pick=PICK, rules=RULES, banned=GLOSSARY.banned_claims,
                         letters=list(letters))  # fmt: skip


def answer(letters: str = "AB") -> dict[str, Any]:
    return build_answer(PLAN, PICK, letters)


def codes(issues: list[Any], level: str = "ai") -> set[str]:
    return {i.code for i in issues if i.level == level}


def check(ans: dict[str, Any], letters: str = "AB") -> list[Any]:
    resp, issues = parse_response(ans, ctx(letters))
    assert resp is not None, [str(i) for i in issues]
    return verify_drafts(resp, ctx(letters))[1]


# ─────────────── the helper itself: a valid answer passes every check ───────────────


def test_the_valid_three_minute_answer_passes_every_check() -> None:
    issues = check(answer())
    assert codes(issues) == set(), [str(i) for i in issues if i.level == "ai"]


# ─────────────── schema and prompts ───────────────


def test_a_short_video_schema_is_unchanged() -> None:
    schema = response_schema(["A", "B"])
    props = schema["properties"]["drafts"]["items"]["properties"]
    assert "chapter_titles" not in props


def test_a_chapter_schema_asks_for_one_title_per_chapter() -> None:
    schema = response_schema(["A", "B"], CHAPTERS)
    item = schema["properties"]["drafts"]["items"]
    assert "chapter_titles" in item["properties"] and "chapter_titles" in item["required"]
    titles = item["properties"]["chapter_titles"]["items"]
    assert titles["properties"]["chapter"]["enum"] == CHAPTERS
    assert titles["required"] == ["chapter", "title"] and titles["additionalProperties"] is False


def test_the_system_prompt_only_grows_for_chapters() -> None:
    short = compile_plan(VideoOptions(**{**OPTS, "do_dai_giay": 30, "nhac": "khong_nhac"}),
                         SHIPPED, music=None, available_tags=set())  # fmt: skip
    assert system_prompt_for(short) == SYSTEM_PROMPT
    text = system_prompt_for(PLAN)
    assert text.startswith(SYSTEM_PROMPT) and len(text) > len(SYSTEM_PROMPT)
    assert "chapter_titles" in text and "per_chapter" in text


def test_the_user_prompt_lists_the_chapters_and_the_text_rules() -> None:
    data = json.loads(build_user_prompt(PLAN, PICK, None, [], ["A", "B"]).split("<du_lieu>")[1]
                      .split("</du_lieu>")[0])  # fmt: skip
    assert [c["id"] for c in data["chapters"]] == CHAPTERS
    first = data["chapters"][0]
    assert first["blocks"] and first["title_beats"] >= 1 and first["beats"] > 0
    assert data["text_rules"]["per_chapter"] == [3, 5]
    assert data["text_rules"]["title_max_words"] == 6
    assert len(data["candidates"]) == len(PLAN.blocks)


# ─────────────── V1: the titles ───────────────


def test_titles_must_name_each_chapter_once() -> None:
    ans = answer()
    ans["drafts"][0]["chapter_titles"] = ans["drafts"][0]["chapter_titles"][:2]
    resp, issues = parse_response(ans, ctx())
    assert resp is None and codes(issues) == {"V1"}
    assert any("tiêu đề" in i.msg and i.draft == "A" for i in issues)


def test_a_title_for_an_unknown_chapter_or_twice_is_v1() -> None:
    ans = answer()
    ans["drafts"][1]["chapter_titles"][0]["chapter"] = "khong_co"
    assert codes(parse_response(ans, ctx())[1]) == {"V1"}
    twice = answer()
    twice["drafts"][0]["chapter_titles"].append(dict(twice["drafts"][0]["chapter_titles"][0]))
    assert codes(parse_response(twice, ctx())[1]) == {"V1"}


# ─────────────── V7: lines of text per chapter ───────────────


def lines_in(draft: dict[str, Any], chapter: str) -> list[dict[str, Any]]:
    roles = next(c.blocks for c in PLAN.chapters if c.id == chapter)
    return [s for s in draft["segments"] if s["block"] in roles]


def test_too_few_lines_in_a_chapter_is_v7() -> None:
    ans = answer()
    for s in lines_in(ans["drafts"][0], CHAPTERS[0])[1:]:
        s["text"] = ""
    issues = [i for i in check(ans) if i.code == "V7"]
    assert issues and CHAPTERS[0] in issues[0].msg and issues[0].draft == "A"


def test_too_many_lines_in_a_chapter_is_v7() -> None:
    ans = answer()
    for s in lines_in(ans["drafts"][1], CHAPTERS[1])[:7]:
        s["text"] = "Một dòng chữ"
    issues = [i for i in check(ans) if i.code == "V7" and i.level == "ai"]
    assert any(CHAPTERS[1] in i.msg and i.draft == "B" for i in issues)


def test_the_hook_and_the_cta_are_not_counted_as_chapter_lines() -> None:
    ans = answer()
    assert ans["drafts"][0]["segments"][0]["text"] == "Một ngày ở xưởng"
    assert codes(check(ans)) == set()  # 3–5 lines in every chapter, plus hook and CTA lines


def test_a_short_video_still_uses_the_per_block_rule() -> None:
    assert "per_block" in SHIPPED["styles"]["ke_chuyen_nghe"]["text"]
    assert "per_chapter" not in SHIPPED["styles"]["ke_chuyen_nghe"]["text"]


# ─────────────── V10: chapter titles ───────────────


def set_title(ans: dict[str, Any], letter: str, chapter: str, title: str) -> None:
    d = next(x for x in ans["drafts"] if x["draft"] == letter)
    next(t for t in d["chapter_titles"] if t["chapter"] == chapter)["title"] = title


@pytest.mark.parametrize(
    "title",
    ["", "   ", "Một hai ba bốn năm sáu bảy tám", "Giò chả tốt nhất thành phố"],
)
def test_bad_titles_are_v10(title: str) -> None:
    ans = answer()
    set_title(ans, "A", CHAPTERS[0], title)
    issues = check(ans)
    assert "V10" in codes(issues)
    assert any(i.code == "V10" and i.draft == "A" and CHAPTERS[0] in i.msg for i in issues)


def test_two_chapters_with_the_same_title_are_v10() -> None:
    ans = answer()
    set_title(ans, "B", CHAPTERS[1], "Khởi đầu sạch sẽ")  # the title of chapter 1
    assert "V10" in codes(check(ans))


def test_the_same_title_in_two_different_drafts_is_fine() -> None:
    ans = answer()
    assert ans["drafts"][0]["chapter_titles"] == ans["drafts"][1]["chapter_titles"]
    assert codes(check(ans)) == set()


def test_a_plan_without_title_limit_only_checks_empty_and_banned() -> None:
    rules = copy.deepcopy(dict(RULES))
    plan = PLAN.model_copy(update={"text_rules": {k: v for k, v in PLAN.text_rules.items()
                                                  if k != "title_max_words"}})  # fmt: skip
    c = VerifyContext(plan=plan, pick=PICK, rules=rules, banned=GLOSSARY.banned_claims,
                      letters=["A", "B"])  # fmt: skip
    ans = answer()
    set_title(ans, "A", CHAPTERS[0], "Một hai ba bốn năm sáu bảy tám chín mười")
    resp, _ = parse_response(ans, c)
    assert resp is not None and "V10" not in codes(verify_drafts(resp, c)[1])


# ─────────────── the whole step with a fake Claude ───────────────


class FakeClaude:
    def __init__(self, *answers: object, tokens: tuple[int, int] = (14000, 10000)) -> None:
        self.answers, self.tokens = list(answers), tokens
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.schemas: list[Mapping[str, Any]] = []

    def __call__(self, system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        self.systems.append(system)
        self.prompts.append(user)
        self.schemas.append(schema)
        ans = self.answers.pop(0)
        text = ans if isinstance(ans, str) else json.dumps(ans, ensure_ascii=False)
        return AiReply(text=text, input_tokens=self.tokens[0], output_tokens=self.tokens[1],
                       stop_reason="end_turn", model="fake-claude")  # fmt: skip


def write_folder(folder: Path) -> None:
    edit = folder / "edit"
    edit.mkdir(parents=True)
    (edit / "plan.json").write_text(PLAN.model_dump_json(), encoding="utf-8")
    (edit / "candidates.json").write_text(PICK.model_dump_json(), encoding="utf-8")
    opts = {"phong_cach": OPTS["phong_cach"], "muc_dich": OPTS["muc_dich"],
            "do_dai_giay": 180, "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip
    (folder / "o.json").write_text(json.dumps(opts), encoding="utf-8")


def run(folder: Path, fake: FakeClaude) -> Any:
    return build_drafts(folder / "o.json", folder, RULES, GLOSSARY, caller=fake)


def test_one_call_writes_chapter_drafts_with_titles(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude(answer())
    result, out = run(tmp_path, fake)
    assert isinstance(result, DraftsFile) and out is not None and result.attempts == 1
    assert [d.draft for d in result.drafts] == ["A", "B"]
    for d in result.drafts:
        assert [t.chapter for t in d.chapter_titles] == CHAPTERS
        assert d.segments[-1].t_end == pytest.approx(PLAN.planned_beats * PLAN.beat_sec, abs=0.05)
    saved = DraftsFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert saved.drafts[0].chapter_titles[0].title == "Khởi đầu sạch sẽ"


def test_the_call_uses_the_chapter_prompt_and_schema(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude(answer())
    run(tmp_path, fake)
    assert "chapter_titles" in fake.systems[0]
    item = fake.schemas[0]["properties"]["drafts"]["items"]
    assert (
        item["properties"]["chapter_titles"]["items"]["properties"]["chapter"]["enum"] == CHAPTERS
    )


def test_the_cap_for_three_minutes_is_30000_and_a_short_video_keeps_20000(tmp_path: Path) -> None:
    assert (
        RULES["ai"]["cost_cap_vnd"]["180"] == 30000 and RULES["ai"]["cost_cap_vnd"]["60"] == 20000
    )
    write_folder(tmp_path)
    result, _ = run(tmp_path, FakeClaude(answer()))
    assert result.cap_vnd == 30000


def test_a_bad_title_is_sent_back_and_only_that_draft_is_asked_again(tmp_path: Path) -> None:
    write_folder(tmp_path)
    bad = answer()
    set_title(bad, "B", CHAPTERS[2], "")
    fixed = {"drafts": [next(d for d in answer()["drafts"] if d["draft"] == "B")]}
    fake = FakeClaude(bad, fixed)
    result, _ = run(tmp_path, fake)
    assert result.attempts == 2 and [d.draft for d in result.drafts] == ["A", "B"]
    assert "V10" in fake.prompts[1] and "Bản A đã đạt" in fake.prompts[1]
    rep = VerifyReportFile.model_validate_json(
        (tmp_path / "edit" / "verify_report.json").read_text(encoding="utf-8")
    )
    assert [r.passed for r in rep.rounds] == [False, True]
    assert any(f.code == "V10" for f in rep.rounds[0].findings)


def test_the_report_lists_v10_among_the_checks_of_a_chapter_plan(tmp_path: Path) -> None:
    write_folder(tmp_path)
    run(tmp_path, FakeClaude(answer()))
    rep = VerifyReportFile.model_validate_json(
        (tmp_path / "edit" / "verify_report.json").read_text(encoding="utf-8")
    )
    assert rep.extra_codes == ["V10"]


def test_three_failed_rounds_give_up_without_writing_drafts(tmp_path: Path) -> None:
    write_folder(tmp_path)
    bad = answer()
    set_title(bad, "A", CHAPTERS[0], "")
    only_a = {"drafts": [next(d for d in bad["drafts"] if d["draft"] == "A")]}
    with pytest.raises(DraftError):
        run(tmp_path, FakeClaude(bad, only_a, only_a))
    assert not (tmp_path / "edit" / "drafts.json").exists()


def test_the_dry_run_estimate_uses_the_chapter_prompt(tmp_path: Path) -> None:
    write_folder(tmp_path)
    est, path = build_drafts(tmp_path / "o.json", tmp_path, RULES, GLOSSARY, dry_run=True)
    assert path is None and est.cap_vnd == 30000
    assert est.input_tokens > 5000  # a real prompt, not a stub
