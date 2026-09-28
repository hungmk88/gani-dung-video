"""Integration tests (4c): plan + candidates → Claude (FAKE caller) → V1–V9 → snap → drafts.json.

No network and no key: the caller is a plain function returning canned answers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application import draft_service
from gani_dung_video.application.draft_service import (
    CostCapReached,
    DraftError,
    DraftsFile,
    build_drafts,
)
from gani_dung_video.cli import main
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.infrastructure.claude_client import AiError, AiReply
from gani_dung_video.infrastructure.config_files import ConfigError, load_glossary, load_rules
from gani_dung_video.infrastructure.settings import Settings, SettingsError

RULES = load_rules()
GLOSSARY = load_glossary()


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GANI_DV_CLAUDE_MODEL", raising=False)


CTA = "Nhắn tin nhận báo giá sỉ"
OPTS: dict[str, Any] = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong",
                        "do_dai_giay": 15, "so_ban_nhap": 2, "nhac": "khong_nhac",
                        "ghi_chu": "Giới thiệu xưởng sạch"}  # fmt: skip
LABELS = ["nguoi_thao_tac", "thiet_bi", "chung_nhan", "thiet_bi"]


def cid(k: int, j: int) -> str:
    return f"c{k}_s0_{j}"


def write_folder(folder: Path) -> Path:
    edit = folder / "edit"
    edit.mkdir(parents=True)
    plan = compile_plan(VideoOptions(**OPTS), RULES, music=None, available_tags=set(LABELS))
    (edit / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    cands = [Candidate(id=cid(k, j), clip=f"c{k}.mp4", start=10.0 * j, end=10.0 * j + 5.0,
                       peak=10.0 * j + 2.0, duration=5.0, fits_beats=[4, 6], labels=[LABELS[k]],
                       aesthetic=5.0) for k in range(4) for j in range(2)]  # fmt: skip
    pick = PickFile(rules_version="2.0", style="ke_chuyen_nghe", beat_sec=plan.beat_sec,
                    beats_per_scene_allowed=[4, 6], dishes=[],
                    blocks=[BlockCandidates(role=b.role, beats=b.beats,
                                            required_tags=b.required_tags, missing_tags=[],
                                            candidates=cands) for b in plan.blocks],
                    missing_shots=[], similar_pairs=[])  # fmt: skip
    (edit / "candidates.json").write_text(pick.model_dump_json(), encoding="utf-8")
    opt = folder / "o.json"
    opt.write_text(json.dumps(OPTS), encoding="utf-8")
    return opt


def seg(block: str, sid: str, text: str = "") -> dict[str, Any]:
    return {"block": block, "scene_id": sid, "beats": 4, "text": text}


def draft(letter: str, ids: list[str], hook_text: str = "Một ngày ở xưởng") -> dict[str, Any]:
    # 15 s at 0.75 s/beat = 20 beats: hook 4, main 12 (3 × 4), cta 4
    blocks = ["hook", "main", "main", "main", "cta"]
    texts = [hook_text, "", "", "", CTA]
    segments = [seg(b, i, t) for b, i, t in zip(blocks, ids, texts, strict=True)]
    return {"draft": letter, "label": letter, "missing_shots": [], "warnings": [],
            "segments": segments}  # fmt: skip


A = draft("A", [cid(0, 0), cid(1, 0), cid(2, 0), cid(3, 0), cid(0, 1)])
B = draft("B", [cid(1, 1), cid(2, 1), cid(3, 1), cid(0, 0), cid(1, 0)], "Tay nghề từng mẻ giò")
B_SAME_HOOK = draft("B", [cid(0, 0), cid(2, 1), cid(3, 1), cid(1, 1), cid(1, 0)])


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
    return build_drafts(folder / "o.json", folder, rules or RULES, GLOSSARY, caller=fake)


def log_lines(folder: Path) -> list[dict[str, Any]]:
    text = (folder / "edit" / "ai_log.jsonl").read_text(encoding="utf-8")
    return [json.loads(x) for x in text.splitlines()]


# ─────────────── happy path ───────────────


def test_one_call_gives_snapped_drafts_and_a_cost_log(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude({"drafts": [A, B]})
    result, out = run(tmp_path, fake)
    assert out == tmp_path.resolve() / "edit" / "drafts.json"
    assert result.attempts == 1 and len(fake.prompts) == 1
    assert [d.draft for d in result.drafts] == ["A", "B"]
    a = result.drafts[0].segments
    assert (a[0].t_start, a[-1].t_end) == (0.0, 15.0)  # 20 beats × 0.75 s, on the beat grid
    assert a[0].src_in == pytest.approx(2.0 - 0.35 * 3.0)  # peak at 35 % of the 3 s shot
    # 9,000 in × $2/M + 3,000 out × $10/M = $0.048 → 1,224 đ
    assert result.cost_vnd == pytest.approx(1224.0)
    [line] = log_lines(tmp_path)
    assert line["attempt"] == 1 and line["cost_vnd"] == pytest.approx(1224.0)
    assert "Giới thiệu xưởng sạch" in fake.prompts[0]  # the note reaches the AI, fenced


def test_log_never_contains_the_prompt_or_a_key(tmp_path: Path) -> None:
    write_folder(tmp_path)
    run(tmp_path, FakeClaude({"drafts": [A, B]}))
    text = (tmp_path / "edit" / "ai_log.jsonl").read_text(encoding="utf-8")
    assert "Giới thiệu xưởng sạch" not in text and "sk-" not in text


# ─────────────── retries (R2) ───────────────


def test_a_failed_check_is_sent_back_and_fixed(tmp_path: Path) -> None:
    write_folder(tmp_path)
    bad = json.loads(json.dumps(A))
    bad["segments"][-1]["text"] = "Liên hệ ngay"  # V8: CTA text missing
    fake = FakeClaude({"drafts": [bad, B]}, {"drafts": [A]})
    result, _ = run(tmp_path, fake)
    assert result.attempts == 2
    assert "V8 [A]" in fake.prompts[1] and "các bản nháp: A." in fake.prompts[1]
    assert [d.draft for d in result.drafts] == ["A", "B"]  # B kept from the first answer


def test_only_the_duplicate_draft_is_regenerated_for_v9(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude({"drafts": [A, B_SAME_HOOK]}, {"drafts": [B]})
    result, _ = run(tmp_path, fake)
    assert "các bản nháp: B." in fake.prompts[1] and "V9 [B]" in fake.prompts[1]
    assert result.drafts[1].segments[0].scene_id == cid(1, 1)


def test_truncated_answer_is_retried(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude('{"drafts": [', {"drafts": [A, B]})
    fake.stop = "max_tokens"
    orig = fake.__call__

    def first_truncated(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        reply = orig(system, user, schema)
        fake.stop = "end_turn"
        return reply

    result, _ = build_drafts(tmp_path / "o.json", tmp_path, RULES, GLOSSARY,
                             caller=first_truncated)  # fmt: skip
    assert isinstance(result, DraftsFile) and result.attempts == 2
    assert "max_tokens" in fake.prompts[1]


def test_gives_up_after_three_rounds_and_writes_no_drafts(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude("not json", "not json", "not json")
    with pytest.raises(DraftError, match="3 vòng") as e:
        run(tmp_path, fake)
    assert [i.code for i in e.value.issues] == ["V1"]
    assert not (tmp_path / "edit" / "drafts.json").exists()
    assert len(log_lines(tmp_path)) == 3


def test_rounded_beats_that_break_the_total_are_sent_back(tmp_path: Path) -> None:
    write_folder(tmp_path)
    long = json.loads(json.dumps(A))
    long["segments"][1]["beats"], long["segments"][2]["beats"] = 6, 6
    long["segments"][3]["beats"] = 0  # V3 rounds 0 → 4; total 24 ≠ 20 → V2
    fake = FakeClaude({"drafts": [long, B]}, {"drafts": [A]})
    result, _ = run(tmp_path, fake)
    assert result.attempts == 2 and "V2 [A]" in fake.prompts[1] and "V3 [A]" not in fake.prompts[1]


def test_snap_failure_is_sent_back_to_the_ai(tmp_path: Path) -> None:
    write_folder(tmp_path)
    path = tmp_path / "edit" / "candidates.json"
    pick = PickFile.model_validate_json(path.read_text(encoding="utf-8"))
    short = [c.model_copy(update={"end": c.start + 2.5}) if c.id == cid(3, 0) else c
             for c in pick.blocks[1].candidates]  # 2.5 s cannot hold 4 beats = 3 s  # fmt: skip
    blocks = [b.model_copy(update={"candidates": short}) for b in pick.blocks]
    new = pick.model_copy(update={"blocks": blocks})
    path.write_text(new.model_dump_json(), encoding="utf-8")
    fixed_a = draft("A", [cid(0, 0), cid(1, 0), cid(2, 0), cid(3, 1), cid(0, 1)])
    fake = FakeClaude({"drafts": [A, B]}, {"drafts": [fixed_a]})
    result, _ = run(tmp_path, fake)
    assert result.attempts == 2 and "SNAP [A]" in fake.prompts[1] and "quá ngắn" in fake.prompts[1]


def test_a_failed_api_call_is_logged_without_the_key(tmp_path: Path) -> None:
    # Found on the first real call (29/09/2026, "credit balance is too low"): the error left no
    # trace in ai_log.jsonl because it was raised before the log line was written.
    write_folder(tmp_path)

    def broken(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        raise AiError("Claude API trả lỗi 400: credit balance is too low")

    with pytest.raises(AiError):
        build_drafts(tmp_path / "o.json", tmp_path, RULES, GLOSSARY, caller=broken)
    [line] = log_lines(tmp_path)
    assert line["attempt"] == 1 and line["error"].startswith("Claude API trả lỗi 400")
    assert line["cost_vnd"] == 0 and "Giới thiệu xưởng sạch" not in json.dumps(line)


# ─────────────── cost cap ───────────────


def test_cost_cap_stops_before_a_call_that_could_break_it(tmp_path: Path) -> None:
    write_folder(tmp_path)
    tight = {**RULES, "ai": {**RULES["ai"], "cost_cap_vnd": {"60": 1000}}}
    fake = FakeClaude({"drafts": [A, B]})
    with pytest.raises(CostCapReached, match="trần"):
        run(tmp_path, fake, tight)
    assert fake.prompts == []  # never called
    assert log_lines(tmp_path)[-1]["stopped"] == "cost_cap"


def test_cost_cap_counts_what_earlier_rounds_spent(tmp_path: Path) -> None:
    write_folder(tmp_path)
    cap = {**RULES, "ai": {**RULES["ai"], "max_tokens": 4000, "cost_cap_vnd": {"60": 2500}}}
    fake = FakeClaude("not json", {"drafts": [A, B]}, tokens=(9000, 4000))  # 1,479 đ per call
    with pytest.raises(CostCapReached):
        run(tmp_path, fake, cap)
    assert len(fake.prompts) == 1


# ─────────────── dry run, config errors ───────────────


def test_dry_run_estimates_without_calling(tmp_path: Path) -> None:
    write_folder(tmp_path)
    fake = FakeClaude()
    est, path = run_dry(tmp_path, fake)
    assert fake.prompts == [] and path is None
    assert est.model == RULES["ai"]["model"] and est.input_tokens > 500
    assert est.worst_call_vnd > est.input_only_vnd > 0
    assert est.cap_vnd == 20000 and est.max_attempts == 3


def run_dry(folder: Path, fake: FakeClaude) -> Any:
    return build_drafts(folder / "o.json", folder, RULES, GLOSSARY, caller=fake, dry_run=True)


def test_model_can_be_overridden_per_machine(tmp_path: Path) -> None:
    write_folder(tmp_path)
    est, _ = build_drafts(tmp_path / "o.json", tmp_path, RULES, GLOSSARY, dry_run=True,
                          settings=Settings(gani_dv_claude_model="claude-sonnet-5"))  # fmt: skip
    assert est.model == "claude-sonnet-5"


def test_missing_key_is_reported_before_any_call(tmp_path: Path) -> None:
    write_folder(tmp_path)
    with pytest.raises(SettingsError):
        build_drafts(tmp_path / "o.json", tmp_path, RULES, GLOSSARY, settings=Settings())


def test_candidates_are_required(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / "edit" / "candidates.json").unlink()
    with pytest.raises(ConfigError, match="pick"):
        run(tmp_path, FakeClaude())


# ─────────────── CLI ───────────────


def test_cli_draft_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    opt = write_folder(tmp_path)
    assert main(["draft", str(opt), str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "không gọi API" in out and "20.000đ" in out


def test_cli_draft_uses_the_real_caller_factory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opt = write_folder(tmp_path)
    monkeypatch.setattr(draft_service, "load_settings",
                        lambda: Settings(anthropic_api_key="test-key-not-real"))  # fmt: skip
    monkeypatch.setattr(draft_service, "make_caller",
                        lambda key, model, cfg: FakeClaude({"drafts": [A, B]}))  # fmt: skip
    assert main(["draft", str(opt), str(tmp_path)]) == 0
    assert "2 bản nháp" in capsys.readouterr().out


def test_cli_draft_failure_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opt = write_folder(tmp_path)
    monkeypatch.setattr(draft_service, "load_settings",
                        lambda: Settings(anthropic_api_key="test-key-not-real"))  # fmt: skip
    monkeypatch.setattr(draft_service, "make_caller",
                        lambda key, model, cfg: FakeClaude("x", "x", "x"))  # fmt: skip
    assert main(["draft", str(opt), str(tmp_path)]) == 1
