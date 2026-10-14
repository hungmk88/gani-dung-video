"""Integration tests (5c-2): phrases + candidates → Claude (FAKE) → S1–S6 → speech_drafts.json.

No network and no key: the caller is a plain function returning canned answers. Mirrors
test_draft_service.py's shape; the loop itself lives in a separate module (speech_draft_service)
so 4c's already-shipped retry loop is never touched (decision 29/09/2026).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application import speech_draft_service
from gani_dung_video.application.draft_service import DryRun
from gani_dung_video.application.render_service import SpeechDraftsFile
from gani_dung_video.application.speech_draft_service import (
    SpeechCostCapReached,
    SpeechDraftError,
    SpeechDraftsResult,
    build_speech_drafts,
)
from gani_dung_video.cli import main
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.speech_edit import Phrase, PhrasesFile
from gani_dung_video.domain.verify_report import VerifyReportFile
from gani_dung_video.infrastructure.claude_client import AiError, AiReply
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.settings import Settings, SettingsError

RULES = load_rules()


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GANI_DV_CLAUDE_MODEL", raising=False)


CTA_TEXT = "Nhắn tin nhận báo giá sỉ"
OPTS: dict[str, Any] = {"phong_cach": "ban_hang_co_loi", "muc_dich": "gioi_thieu_xuong",
                        "do_dai_giay": 15, "so_ban_nhap": 2, "nhac": "khong_nhac",
                        "ghi_chu": "Nhấn mạnh giò lụa"}  # fmt: skip


def phrase(pid: str, clip: str, sec: float) -> Phrase:
    w = SpokenWord(text=pid, start=0.0, end=sec, prob=0.9)
    return Phrase(id=pid, clip=clip, start=0.0, end=sec, cut_in=0.0, cut_out=sec, text=pid,
                 words=[w])  # fmt: skip


def cand(cid: str, clip: str, start: float, labels: list[str]) -> Candidate:
    return Candidate(id=cid, clip=clip, start=start, end=start + 4.0, peak=start + 2.0,
                     duration=4.0, fits_beats=[2, 4], labels=labels, aesthetic=5.0)  # fmt: skip


def write_folder(folder: Path) -> Path:
    edit = folder / "edit"
    edit.mkdir(parents=True)
    plan = compile_plan(VideoOptions(**OPTS), RULES, music=None, available_tags={"thanh_pham"})
    (edit / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    phrases = PhrasesFile(rules_version="2.0", style="ban_hang_co_loi", phrases=[
        phrase("hook", "a.mp4", 3.0), phrase("m1", "a.mp4", 4.5), phrase("m2", "a.mp4", 4.5),
        phrase("cta", "a.mp4", 3.0), phrase("m3", "a.mp4", 3.0), phrase("m4", "a.mp4", 4.5),
        phrase("ctb", "a.mp4", 3.0),
    ], excluded=[])  # fmt: skip
    (edit / "phrases.json").write_text(phrases.model_dump_json(), encoding="utf-8")
    cands = [cand("br0", "b.mp4", 0, ["thanh_pham"]), cand("br1", "b.mp4", 10, ["thanh_pham"])]
    pick = PickFile(rules_version="2.0", style="ban_hang_co_loi", beat_sec=plan.beat_sec,
                    beats_per_scene_allowed=[2, 4], dishes=[],
                    blocks=[BlockCandidates(role=b.role, beats=b.beats,
                                            required_tags=b.required_tags, missing_tags=[],
                                            candidates=cands) for b in plan.blocks],
                    missing_shots=[], similar_pairs=[])  # fmt: skip
    (edit / "candidates.json").write_text(pick.model_dump_json(), encoding="utf-8")
    opt = folder / "o.json"
    opt.write_text(json.dumps(OPTS), encoding="utf-8")
    return opt


def seg(pid: str, block: str, broll: str | None = None) -> dict[str, Any]:
    return {"phrase_id": pid, "block": block, "broll": broll}


def draft(letter: str, label: str = "x") -> dict[str, Any]:
    return {"draft": letter, "label": label, "missing_shots": [], "warnings": [], "segments": [
        seg("hook", "hook"), seg("m1", "main", "br0"), seg("m2", "main"), seg("cta", "cta"),
    ]}  # fmt: skip


def draft_b(letter: str = "B") -> dict[str, Any]:
    return {"draft": letter, "label": "Đổi mở đầu", "missing_shots": [], "warnings": [],
           "segments": [seg("m3", "hook"), seg("m4", "main", "br1"), seg("m1", "main"),
                        seg("ctb", "cta")]}  # fmt: skip


A = draft("A", "Tối ưu")
B = draft_b("B")


class FakeClaude:
    """Returns the canned answers in order and records every prompt it was sent."""

    def __init__(self, *answers: object, tokens: tuple[int, int] = (9000, 3000),
                 stop: str = "end_turn") -> None:  # fmt: skip
        self.answers, self.tokens, self.stop = list(answers), tokens, stop
        self.prompts: list[str] = []

    def __call__(self, system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        self.prompts.append(user)
        ans = self.answers.pop(0)
        text = ans if isinstance(ans, str) else json.dumps(ans, ensure_ascii=False)
        return AiReply(text=text, input_tokens=self.tokens[0], output_tokens=self.tokens[1],
                       stop_reason=self.stop, model="fake-claude")  # fmt: skip


def run(folder: Path, fake: FakeClaude, rules: dict[str, Any] | None = None) -> Any:
    return build_speech_drafts(folder / "o.json", folder, rules or RULES, caller=fake)


def log_lines(folder: Path) -> list[dict[str, Any]]:
    text = (folder / "edit" / "ai_log.jsonl").read_text(encoding="utf-8")
    return [json.loads(x) for x in text.splitlines()]


# ─────────────── happy path ───────────────


def test_one_call_gives_resolved_drafts_and_a_cost_log(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude({"drafts": [A, B]})
    result, out = run(tmp_path, fake)
    assert out == tmp_path.resolve() / "edit" / "speech_drafts.json"
    assert isinstance(result, SpeechDraftsResult)
    assert result.attempts == 1 and len(fake.prompts) == 1
    assert [d.draft for d in result.drafts] == ["A", "B"]
    a = result.drafts[0]
    assert a.label == "Tối ưu" and a.cuts[0].t_start == 0.0 and a.cuts[-1].t_end == 15.0
    assert len(a.broll) == 1 and a.broll[0].clip == "b.mp4"
    # 9,000 in × $2/M + 3,000 out × $10/M = $0.048 → 1,224 đ
    assert result.cost_vnd == pytest.approx(1224.0)
    [line] = log_lines(tmp_path)
    assert line["attempt"] == 1 and line["cost_vnd"] == pytest.approx(1224.0)
    assert line["mode"] == "speech"
    assert "Nhấn mạnh giò lụa" in fake.prompts[0]  # the note reaches the AI, fenced
    saved = SpeechDraftsFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert saved.model == "fake-claude" and len(saved.drafts) == 2


def test_log_never_contains_the_prompt_or_a_key(tmp_path: Path) -> None:
    write_folder(tmp_path)
    run(tmp_path, FakeClaude({"drafts": [A, B]}))
    text = (tmp_path / "edit" / "ai_log.jsonl").read_text(encoding="utf-8")
    assert "Nhấn mạnh giò lụa" not in text and "sk-" not in text


# ─────────────── retries (R2) ───────────────


def test_a_failed_check_is_sent_back_and_fixed(tmp_path: Path) -> None:
    write_folder(tmp_path)
    bad = json.loads(json.dumps(A))
    bad["segments"][0]["broll"] = "br0"  # S1: B-roll on the opening phrase
    fake = FakeClaude({"drafts": [bad, B]}, {"drafts": [A]})
    result, _ = run(tmp_path, fake)
    assert result.attempts == 2
    assert "S1 [A]" in fake.prompts[1] and "các bản nháp: A." in fake.prompts[1]
    assert [d.draft for d in result.drafts] == ["A", "B"]  # B kept from the first answer


def test_only_the_duplicate_draft_is_regenerated_for_s6(tmp_path: Path) -> None:
    write_folder(tmp_path)
    same_open = json.loads(json.dumps(B))
    same_open["segments"][0]["phrase_id"] = "hook"  # same opening as A
    fake = FakeClaude({"drafts": [A, same_open]}, {"drafts": [B]})
    result, _ = run(tmp_path, fake)
    assert "các bản nháp: B." in fake.prompts[1] and "S6 [B]" in fake.prompts[1]
    assert result.drafts[1].cuts[0].phrase_id == "m3"


def test_truncated_answer_is_retried(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude('{"drafts": [', {"drafts": [A, B]})
    fake.stop = "max_tokens"
    orig = fake.__call__

    def first_truncated(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        reply = orig(system, user, schema)
        fake.stop = "end_turn"
        return reply

    result, _ = build_speech_drafts(tmp_path / "o.json", tmp_path, RULES,
                                    caller=first_truncated)  # fmt: skip
    assert isinstance(result, SpeechDraftsResult) and result.attempts == 2
    assert "max_tokens" in fake.prompts[1]


def test_gives_up_after_three_rounds_and_writes_no_drafts(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude("not json", "not json", "not json")
    with pytest.raises(SpeechDraftError, match="3 vòng") as e:
        run(tmp_path, fake)
    assert [i.code for i in e.value.issues] == ["S1"]
    assert not (tmp_path / "edit" / "speech_drafts.json").exists()
    assert len(log_lines(tmp_path)) == 3


def test_broll_too_tight_for_any_beat_count_is_sent_back(tmp_path: Path) -> None:
    write_folder(tmp_path)
    # Shrink m1 so no beat count (2 or 4 × 0.6 s beat) fits inside its cut window, and give the
    # seconds to m2 and m4: S2 judges the total of the cuts, which must stay at the target.
    path = tmp_path / "edit" / "phrases.json"
    ph = PhrasesFile.model_validate_json(path.read_text(encoding="utf-8"))
    by_id = {p.id: p for p in ph.phrases}
    lost = (by_id["m1"].cut_out - by_id["m1"].cut_in) - 0.5

    def reshape(p: Phrase) -> Phrase:
        if p.id == "m1":
            return p.model_copy(update={"cut_out": p.cut_in + 0.5})
        if p.id in ("m2", "m4"):  # A and B both hold seconds m1 gave up
            return p.model_copy(update={"cut_out": p.cut_out + lost})
        return p

    short = [reshape(p) for p in ph.phrases]
    path.write_text(ph.model_copy(update={"phrases": short}).model_dump_json(), encoding="utf-8")
    fixed_a = json.loads(json.dumps(A))
    fixed_a["segments"][1]["broll"] = None  # no B-roll on the now-too-short m1
    fake = FakeClaude({"drafts": [A, B]}, {"drafts": [fixed_a]})
    result, _ = run(tmp_path, fake)
    assert result.attempts == 2 and "SNAP [A]" in fake.prompts[1] and "quá ngắn" in fake.prompts[1]


def test_a_failed_api_call_is_logged_without_the_key(tmp_path: Path) -> None:
    write_folder(tmp_path)

    def broken(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        raise AiError("Claude API trả lỗi 400: credit balance is too low")

    with pytest.raises(AiError):
        build_speech_drafts(tmp_path / "o.json", tmp_path, RULES, caller=broken)
    [line] = log_lines(tmp_path)
    assert line["attempt"] == 1 and line["error"].startswith("Claude API trả lỗi 400")
    assert line["cost_vnd"] == 0 and "Nhấn mạnh giò lụa" not in json.dumps(line)


# ─────────────── cost cap ───────────────


def test_cost_cap_stops_before_a_call_that_could_break_it(tmp_path: Path) -> None:
    write_folder(tmp_path)
    tight = {**RULES, "ai": {**RULES["ai"], "cost_cap_vnd": {"60": 1000}}}
    fake = FakeClaude({"drafts": [A, B]})
    with pytest.raises(SpeechCostCapReached, match="trần"):
        run(tmp_path, fake, tight)
    assert fake.prompts == []
    assert log_lines(tmp_path)[-1]["stopped"] == "cost_cap"


def test_max_vnd_lowers_the_cap_below_the_table(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude({"drafts": [A, B]})
    with pytest.raises(SpeechCostCapReached):
        build_speech_drafts(tmp_path / "o.json", tmp_path, RULES, caller=fake, max_vnd=100.0)
    assert fake.prompts == [] and log_lines(tmp_path)[-1]["cap_vnd"] == 100.0


# ─────────────── dry run, config errors ───────────────


def test_dry_run_estimates_without_calling(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude()
    est, path = build_speech_drafts(tmp_path / "o.json", tmp_path, RULES, caller=fake,
                                    dry_run=True)  # fmt: skip
    assert fake.prompts == [] and path is None
    assert isinstance(est, DryRun)
    assert est.model == RULES["ai"]["model"] and est.input_tokens > 300
    assert est.worst_call_vnd > est.input_only_vnd > 0
    assert est.cap_vnd == 20000 and est.max_attempts == 3


def test_missing_key_is_reported_before_any_call(tmp_path: Path) -> None:
    write_folder(tmp_path)
    with pytest.raises(SettingsError):
        build_speech_drafts(tmp_path / "o.json", tmp_path, RULES, settings=Settings())


def test_phrases_are_required(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / "edit" / "phrases.json").unlink()
    with pytest.raises(ConfigError, match="phrases"):
        run(tmp_path, FakeClaude())


def test_non_speech_style_is_refused(tmp_path: Path) -> None:
    opt = write_folder(tmp_path)
    plan_path = tmp_path / "edit" / "plan.json"
    from gani_dung_video.domain.plan import Plan

    music_plan = Plan.model_validate_json(plan_path.read_text(encoding="utf-8")).model_copy(
        update={"driver": "grid", "options": {**json.loads(plan_path.read_text(
            encoding="utf-8"))["options"], "style": "ke_chuyen_nghe"}})  # fmt: skip
    plan_path.write_text(music_plan.model_dump_json(), encoding="utf-8")
    with pytest.raises(ConfigError, match="dẫn nhịp theo lời"):
        run(tmp_path, FakeClaude())
    assert opt.is_file()  # options file itself untouched


# ─────────────── CLI ───────────────


def test_cli_draft_dry_run_dispatches_to_speech_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opt = write_folder(tmp_path)
    assert main(["draft", str(opt), str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "không gọi API" in out and "20.000đ" in out


def test_cli_draft_uses_the_real_caller_factory_for_speech(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opt = write_folder(tmp_path)
    monkeypatch.setattr(speech_draft_service, "load_settings",
                        lambda: Settings(anthropic_api_key="test-key-not-real"))  # fmt: skip
    monkeypatch.setattr(speech_draft_service, "make_caller",
                        lambda key, model, cfg: FakeClaude({"drafts": [A, B]}))  # fmt: skip
    assert main(["draft", str(opt), str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 bản nháp" in out and "B-roll" in out


def test_cli_draft_failure_exit_1_for_speech(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opt = write_folder(tmp_path)
    monkeypatch.setattr(speech_draft_service, "load_settings",
                        lambda: Settings(anthropic_api_key="test-key-not-real"))  # fmt: skip
    monkeypatch.setattr(speech_draft_service, "make_caller",
                        lambda key, model, cfg: FakeClaude("x", "x", "x"))  # fmt: skip
    assert main(["draft", str(opt), str(tmp_path)]) == 1


# ───────────── 6i: every round's S1–S6 findings are kept in edit/verify_report.json ─────────────


def report_of(folder: Path) -> VerifyReportFile:
    return VerifyReportFile.model_validate_json(
        (folder / "edit" / "verify_report.json").read_text(encoding="utf-8")
    )


def test_a_speech_draft_that_passed_leaves_a_speech_mode_report(tmp_path: Path) -> None:
    write_folder(tmp_path)
    run(tmp_path, FakeClaude({"drafts": [A, B]}))
    rep = report_of(tmp_path)
    assert (rep.mode, rep.final_status, len(rep.rounds)) == ("speech", "passed", 1)
    r = rep.rounds[0]
    assert (r.round, r.passed, r.parsed, r.drafts) == (1, True, True, ["A", "B"])
    assert (r.input_tokens, r.output_tokens) == (9000, 3000) and r.cost_vnd > 0


def test_a_speech_check_sent_back_and_fixed_shows_in_both_rounds(tmp_path: Path) -> None:
    write_folder(tmp_path)
    bad = json.loads(json.dumps(A))
    bad["segments"][0]["broll"] = "br0"  # S1: B-roll on the opening phrase
    run(tmp_path, FakeClaude({"drafts": [bad, B]}, {"drafts": [A]}))
    rep = report_of(tmp_path)
    assert rep.final_status == "passed" and [r.passed for r in rep.rounds] == [False, True]
    assert [r.drafts for r in rep.rounds] == [["A", "B"], ["A"]]
    assert ("S1", "A") in [(f.code, f.draft) for f in rep.rounds[0].findings if f.level == "ai"]
    assert not [f for f in rep.rounds[1].findings if f.level == "ai"]


def test_three_failed_speech_rounds_leave_the_history_and_no_drafts(tmp_path: Path) -> None:
    write_folder(tmp_path)
    with pytest.raises(SpeechDraftError):
        run(tmp_path, FakeClaude("not json", "not json", "not json"))
    assert not (tmp_path / "edit" / "speech_drafts.json").exists()
    rep = report_of(tmp_path)
    assert (rep.mode, rep.final_status, [r.round for r in rep.rounds]) == (
        "speech",
        "failed",
        [1, 2, 3],
    )
    assert all([f.code for f in r.findings] == ["S1"] and not r.parsed for r in rep.rounds)


def test_speech_cost_cap_and_api_errors_are_recorded(tmp_path: Path) -> None:
    write_folder(tmp_path)
    tight = {**RULES, "ai": {**RULES["ai"], "cost_cap_vnd": {"60": 1000}}}
    with pytest.raises(SpeechCostCapReached):
        run(tmp_path, FakeClaude({"drafts": [A, B]}), tight)
    assert (report_of(tmp_path).final_status, report_of(tmp_path).rounds) == ("cost_cap", [])

    def broken(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        raise AiError("Claude API trả lỗi 400: credit balance is too low")

    with pytest.raises(AiError):
        build_speech_drafts(tmp_path / "o.json", tmp_path, RULES, caller=broken)
    assert report_of(tmp_path).final_status == "api_error"


def test_a_speech_report_that_cannot_be_written_never_breaks_the_draft(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / "edit" / "verify_report.json").mkdir()
    result, path = run(tmp_path, FakeClaude({"drafts": [A, B]}))
    assert isinstance(result, SpeechDraftsResult) and path is not None and path.is_file()
