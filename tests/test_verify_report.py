"""Unit tests: domain/verify_report.py — what the checks said, round by round (phase 6i).

Until now the V1–V9 (music) / S1–S6 (speech) findings of a draft lived only in memory: a failed
draft wrote nothing, a passed one kept a few notes. The real case: a 30s music run failed three
rounds in a row on V4 (a scene used twice), 2.992đ spent, and nobody could say why afterwards.
This module holds the record (one per round) and turns it into one row per check.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from gani_dung_video.domain.verify_report import (
    MAX_FINDINGS_PER_ROUND,
    MAX_MSG_CHARS,
    MUSIC_CODES,
    SPEECH_CODES,
    Finding,
    RoundRecord,
    VerifyReportFile,
    check_detail,
    check_rows,
    make_round,
)


@dataclass(frozen=True)
class FakeIssue:
    """Stands in for verify.Issue / speech_verify.Issue (same four fields)."""

    code: str
    level: str
    msg: str
    draft: str | None = None


def rnd(*issues: FakeIssue, parsed: bool = True, n: int = 1) -> RoundRecord:
    return make_round(n, ["A", "B"], parsed, 702.42, 3538, 2047, "end_turn", list(issues))


def status_of(mode: str, record: RoundRecord) -> dict[str, str]:
    return {r.code: r.status for r in check_rows(mode, record)}  # type: ignore[arg-type]


# ─────────────── a round ───────────────


def test_a_round_with_no_findings_passed() -> None:
    r = rnd()
    assert r.passed is True and r.findings == [] and r.round == 1 and r.parsed is True
    assert (r.drafts, r.cost_vnd, r.input_tokens, r.output_tokens) == (
        ["A", "B"],
        702.42,
        3538,
        2047,
    )
    assert r.stop_reason == "end_turn"


def test_only_a_finding_for_the_ai_fails_the_round() -> None:
    assert rnd(FakeIssue("V4", "ai", "Cảnh 's1' dùng 2 lần.", "A")).passed is False
    assert rnd(FakeIssue("V3", "auto", "4,6 phách → 4.")).passed is True  # the code fixed it
    assert rnd(FakeIssue("V6", "warn", "thiếu cảnh bắt buộc")).passed is True  # a warning only


def test_findings_keep_code_level_draft_and_message() -> None:
    (f,) = rnd(FakeIssue("V9", "ai", "Hook trùng bản A.", "B")).findings
    assert (f.code, f.level, f.draft, f.msg) == ("V9", "ai", "B", "Hook trùng bản A.")


def test_a_round_keeps_at_most_fifty_findings_and_each_message_is_cut() -> None:
    many = [FakeIssue("V7", "ai", "x" * 1000, "A") for _ in range(MAX_FINDINGS_PER_ROUND + 20)]
    r = rnd(*many)
    assert len(r.findings) == MAX_FINDINGS_PER_ROUND
    assert all(len(f.msg) <= MAX_MSG_CHARS for f in r.findings)
    assert r.findings[0].msg.endswith("…")  # the cut is visible
    assert r.passed is False  # the cut never hides a failure


def test_an_unknown_level_is_kept_as_a_warning_not_a_crash() -> None:
    (f,) = rnd(FakeIssue("V6", "weird", "?")).findings
    assert f.level == "warn"


# ─────────────── one row per check ───────────────


def test_music_rows_list_every_check_in_order_and_default_to_pass() -> None:
    rows = check_rows("music", rnd())
    assert [r.code for r in rows] == [*MUSIC_CODES]
    assert MUSIC_CODES == ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "SNAP")
    assert {r.status for r in rows} == {"pass"}


def test_speech_rows_are_s1_to_s6_and_snap() -> None:
    rows = check_rows("speech", rnd())
    assert [r.code for r in rows] == [*SPEECH_CODES]
    assert SPEECH_CODES == ("S1", "S2", "S3", "S4", "S5", "S6", "SNAP")


def test_the_three_rounds_of_the_real_v4_failure_name_the_repeated_scenes() -> None:
    msgs = ["Cảnh 'VID_150353_s0_2' dùng 2 lần.", "Cảnh 'VID_160553_s0_0' dùng 2 lần."]
    rows = {
        r.code: r for r in check_rows("music", rnd(*[FakeIssue("V4", "ai", m, "A") for m in msgs]))
    }
    assert rows["V4"].status == "fail" and rows["V4"].messages == tuple(msgs)
    assert rows["V2"].status == "pass" and rows["V9"].status == "pass"


def test_status_precedence_fail_over_fixed_over_warn_over_pass() -> None:
    both = status_of("music", rnd(FakeIssue("V3", "auto", "a"), FakeIssue("V3", "ai", "b")))
    assert both["V3"] == "fail"
    assert status_of("music", rnd(FakeIssue("V3", "auto", "a")))["V3"] == "fixed"
    assert status_of("music", rnd(FakeIssue("V6", "warn", "a")))["V6"] == "warn"
    assert (
        status_of("music", rnd(FakeIssue("V6", "warn", "a"), FakeIssue("V6", "auto", "b")))["V6"]
        == "fixed"
    )


def test_a_snap_failure_has_its_own_row() -> None:
    assert (
        status_of("music", rnd(FakeIssue("SNAP", "ai", "không khớp phách", "A")))["SNAP"] == "fail"
    )


def test_an_unreadable_answer_fails_the_first_check_and_skips_the_rest() -> None:
    r = rnd(
        FakeIssue("V1", "ai", "JSON không đúng cấu trúc yêu cầu (ValidationError)."), parsed=False
    )
    st = status_of("music", r)
    assert st["V1"] == "fail"
    assert {st[c] for c in MUSIC_CODES if c != "V1"} == {"skipped"}
    assert status_of("speech", rnd(FakeIssue("S1", "ai", "x"), parsed=False))["S1"] == "fail"
    assert status_of("speech", rnd(FakeIssue("S1", "ai", "x"), parsed=False))["S6"] == "skipped"


def test_a_code_nobody_listed_still_shows_up_at_the_end() -> None:
    rows = check_rows("music", rnd(FakeIssue("V10", "ai", "chương 3 phút", "A")))
    assert rows[-1].code == "V10" and rows[-1].status == "fail"


# ─────────────── the sentence shown for a row ───────────────


def test_detail_is_empty_for_a_clean_check_and_joins_the_messages_otherwise() -> None:
    rows = {
        r.code: r
        for r in check_rows("music", rnd(FakeIssue("V4", "ai", "a"), FakeIssue("V4", "ai", "b")))
    }
    assert check_detail(rows["V2"]) == ""
    assert check_detail(rows["V4"]) == "a; b"


def test_detail_shows_three_messages_and_counts_the_rest() -> None:
    issues = [FakeIssue("V4", "ai", f"m{i}") for i in range(5)]
    rows = {r.code: r for r in check_rows("music", rnd(*issues))}
    assert check_detail(rows["V4"]) == "m0; m1; m2 (+2 nữa)"


def test_repeated_messages_are_shown_once() -> None:
    issues = [FakeIssue("V4", "ai", "giống nhau", "A"), FakeIssue("V4", "ai", "giống nhau", "B")]
    rows = {r.code: r for r in check_rows("music", rnd(*issues))}
    assert check_detail(rows["V4"]) == "giống nhau"


# ─────────────── the file ───────────────


def test_the_report_file_round_trips_through_json() -> None:
    report = VerifyReportFile(mode="music", final_status="failed",
                              rounds=[rnd(FakeIssue("V4", "ai", "x", "A")), rnd(n=2)])  # fmt: skip
    again = VerifyReportFile.model_validate_json(report.model_dump_json())
    assert again == report and again.version == 1
    assert [r.round for r in again.rounds] == [1, 2]


@pytest.mark.parametrize(
    "raw",
    ['{"mode": "video", "final_status": "passed", "rounds": []}',
     '{"mode": "music", "final_status": "maybe", "rounds": []}',
     '{"mode": "music", "final_status": "passed"}', "[]", "not json"],
)  # fmt: skip
def test_a_damaged_report_is_rejected_not_half_read(raw: str) -> None:
    with pytest.raises(ValidationError):
        VerifyReportFile.model_validate_json(raw)


def test_finding_levels_are_limited() -> None:
    with pytest.raises(ValidationError):
        Finding(code="V1", level="nope", draft=None, msg="x")
