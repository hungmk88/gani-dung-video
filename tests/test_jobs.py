"""Unit tests: domain/jobs.py — job states, verdict of a finished run, retry, progress (6a)."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.jobs import (
    ANALYZE_STAGES,
    KIND_PRIORITY,
    STATUSES,
    AnalysisStamp,
    analyze_progress,
    blocked_reason,
    can_transition,
    dependency_state,
    estimate_progress,
    footage_signature,
    join_stages,
    judge_run,
    judge_stage,
    line_level,
    may_retry,
    parse_clip_progress,
    parse_stages,
    remaining_stages,
    stage_of,
    stamp_is_fresh,
)

# ─────────────── state machine ───────────────


def test_statuses_are_the_spec_vocabulary_plus_the_approval_gate() -> None:
    assert STATUSES == ("queued", "processing", "awaiting_approval", "completed", "failed",
                        "cancelled")  # fmt: skip


@pytest.mark.parametrize(
    "src,dst",
    [("queued", "processing"), ("queued", "cancelled"), ("processing", "completed"),
     ("processing", "failed"), ("processing", "cancelled"), ("processing", "queued"),
     ("failed", "queued"), ("cancelled", "queued"), ("completed", "queued"),
     ("processing", "awaiting_approval"), ("awaiting_approval", "queued"),
     ("awaiting_approval", "cancelled"), ("awaiting_approval", "failed")],
)  # fmt: skip
def test_allowed_transitions(src: str, dst: str) -> None:
    assert can_transition(src, dst)


@pytest.mark.parametrize(
    "src,dst",
    [("queued", "completed"), ("queued", "failed"), ("completed", "processing"),
     ("failed", "completed"), ("cancelled", "processing"), ("processing", "processing"),
     ("queued", "queued"), ("nonsense", "queued"), ("queued", "awaiting_approval"),
     ("awaiting_approval", "processing"), ("awaiting_approval", "completed"),
     ("completed", "awaiting_approval")],
)  # fmt: skip
def test_forbidden_transitions(src: str, dst: str) -> None:
    assert not can_transition(src, dst)


# ─────────────── verdict of a finished run ───────────────


def test_clean_exit_with_a_fresh_passing_report_is_completed_passed() -> None:
    v = judge_run("exit", 0, report_fresh=True, report_passed=True)
    assert (v.status, v.result, v.retry) == ("completed", "passed", False)


def test_exit_1_is_qc_failed_and_is_never_retried() -> None:
    v = judge_run("exit", 1, report_fresh=True, report_passed=False)
    assert (v.status, v.result, v.retry) == ("completed", "qc_failed", False)


def test_a_passing_exit_without_a_fresh_report_is_not_believed() -> None:
    # an old qc_report.json from an earlier run must not make this run look finished
    v = judge_run("exit", 0, report_fresh=False, report_passed=True)
    assert (v.status, v.result, v.retry) == ("failed", None, False)
    assert "báo cáo" in v.message.lower()


def test_exit_1_without_a_report_is_a_failure_not_a_qc_verdict() -> None:
    v = judge_run("exit", 1, report_fresh=False, report_passed=None)
    assert (v.status, v.retry) == ("failed", False)


@pytest.mark.parametrize("code", [2, 3, -9, -11, 255])
def test_other_exit_codes_are_retryable_failures(code: int) -> None:
    v = judge_run("exit", code, report_fresh=False, report_passed=None)
    assert (v.status, v.result, v.retry) == ("failed", None, True)


def test_cancelled_run_is_cancelled_without_retry() -> None:
    v = judge_run("cancelled", None, report_fresh=False, report_passed=None)
    assert (v.status, v.retry) == ("cancelled", False)


def test_shutdown_puts_the_job_back_in_the_queue() -> None:
    v = judge_run("shutdown", None, report_fresh=False, report_passed=None)
    assert v.status == "queued" and v.retry is False


def test_a_program_that_cannot_start_fails_without_retry() -> None:
    v = judge_run("spawn_failed", None, report_fresh=False, report_passed=None)
    assert (v.status, v.retry) == ("failed", False)


@pytest.mark.parametrize("reason", ["stalled", "timeout"])
def test_hung_or_overlong_runs_fail_but_may_be_retried(reason: str) -> None:
    v = judge_run(reason, None, report_fresh=False, report_passed=None, detail="600s")
    assert (v.status, v.retry) == ("failed", True) and "600s" in v.message


# ─────────────── retry policy ───────────────


def test_render_is_retried_until_the_attempts_run_out() -> None:
    assert may_retry("render", attempts=1, max_attempts=2)
    assert not may_retry("render", attempts=2, max_attempts=2)


def test_paid_draft_jobs_are_never_retried_automatically() -> None:
    assert not may_retry("draft", attempts=1, max_attempts=5)


def test_unknown_kinds_are_not_retried() -> None:
    assert not may_retry("mystery", attempts=0, max_attempts=5)


# ─────────────── progress ───────────────


def test_progress_starts_low_and_grows_with_time() -> None:
    assert estimate_progress(0, 100) == pytest.approx(3.0)
    assert 3.0 < estimate_progress(30, 100) < estimate_progress(60, 100)


def test_progress_never_reaches_100_before_the_job_is_really_done() -> None:
    assert estimate_progress(100, 100) == pytest.approx(95.0)
    assert estimate_progress(10_000, 100) == pytest.approx(95.0)


def test_progress_survives_a_bad_estimate() -> None:
    assert estimate_progress(50, 0) == pytest.approx(3.0)
    assert estimate_progress(-5, 100) == pytest.approx(3.0)


# ─────────────── console lines ───────────────


def test_stage_follows_the_known_log_lines() -> None:
    assert stage_of("Đang dựng bản A (video_A_1080x1920.mp4)…") == "dung"
    assert stage_of("một dòng bất kỳ") is None


def test_line_levels() -> None:
    assert line_level("LỖI: không thấy ffmpeg") == "error"
    assert line_level("Bản A: x.mp4 (30s) — QC: KHÔNG ĐẠT — không giao bản này") == "warn"
    assert line_level("Đang dựng bản A") == "info"


# ─────────────── 6c-1: dependencies between jobs ───────────────


@pytest.mark.parametrize(
    "status,result,expected",
    [(None, None, "ready"), ("completed", "passed", "ready"), ("completed", None, "ready"),
     ("queued", None, "wait"), ("processing", None, "wait"), ("awaiting_approval", None, "wait"),
     ("failed", None, "blocked"), ("cancelled", None, "blocked"),
     ("completed", "qc_failed", "blocked"), ("mystery", None, "blocked")],
)  # fmt: skip
def test_dependency_state(status: str | None, result: str | None, expected: str) -> None:
    assert dependency_state(status, result) == expected


def test_blocked_reason_names_the_job_and_what_happened_to_it() -> None:
    assert "#7" in blocked_reason(7, "failed", None) and "lỗi" in blocked_reason(7, "failed", None)
    assert "hủy" in blocked_reason(7, "cancelled", None)
    assert "QC" in blocked_reason(7, "completed", "qc_failed")


def test_priorities_run_short_paid_work_before_long_analysis() -> None:
    assert KIND_PRIORITY["render"] > KIND_PRIORITY["draft"] > KIND_PRIORITY["analyze"]


# ─────────────── 6c-1: resumable stages ───────────────


def test_the_analysis_stages_are_in_pipeline_order() -> None:
    assert ANALYZE_STAGES == ("inventory", "transcribe", "scenes", "semantics")


def test_stage_lists_round_trip() -> None:
    assert parse_stages(None) == [] and parse_stages("") == []
    assert parse_stages("inventory,transcribe") == ["inventory", "transcribe"]
    assert join_stages(["inventory", "transcribe"]) == "inventory,transcribe"
    assert parse_stages(join_stages(["scenes"])) == ["scenes"]


def test_unknown_or_blank_stage_names_are_dropped() -> None:
    assert parse_stages("inventory,,bogus, scenes ") == ["inventory", "scenes"]


def test_a_retry_resumes_at_the_first_stage_that_is_not_done() -> None:
    assert remaining_stages(ANALYZE_STAGES, []) == list(ANALYZE_STAGES)
    assert remaining_stages(ANALYZE_STAGES, ["inventory", "transcribe"]) == ["scenes", "semantics"]
    assert remaining_stages(ANALYZE_STAGES, list(ANALYZE_STAGES)) == []


def test_a_gap_in_the_done_list_reruns_everything_after_the_gap() -> None:
    # scenes was never finished, so a recorded "semantics" cannot be trusted
    assert remaining_stages(ANALYZE_STAGES, ["inventory", "semantics"]) == [
        "transcribe", "scenes", "semantics"]  # fmt: skip


EXPECTED = {"inventory": 10.0, "transcribe": 300.0, "scenes": 100.0, "semantics": 590.0}


def test_analysis_progress_counts_finished_stages_and_the_current_one() -> None:
    start = analyze_progress([], "inventory", 0.0, EXPECTED)
    assert start == pytest.approx(3.0)
    after_two = analyze_progress(["inventory", "transcribe"], "scenes", 0.0, EXPECTED)
    assert after_two == pytest.approx(3.0 + 92.0 * 310 / 1000)
    half = analyze_progress(["inventory", "transcribe"], "scenes", 50.0, EXPECTED)
    assert half > after_two


def test_analysis_progress_never_reaches_100_before_the_job_is_done() -> None:
    late = analyze_progress(list(ANALYZE_STAGES[:-1]), "semantics", 99_999.0, EXPECTED)
    assert 90.0 < late < 95.0 + 1e-9
    assert analyze_progress(list(ANALYZE_STAGES), None, 0.0, EXPECTED) <= 95.0


def test_analysis_progress_survives_bad_estimates() -> None:
    assert analyze_progress([], "inventory", 5.0, {}) == pytest.approx(3.0)
    assert analyze_progress([], "inventory", -5.0, EXPECTED) == pytest.approx(3.0)


def test_clip_progress_lines_give_a_real_fraction() -> None:
    assert parse_clip_progress("[clip 1/4] a.mp4: nhúng khung 8/16") == pytest.approx(0.125)
    assert parse_clip_progress("[clip 3/4] b.mp4: nhúng khung 16/16") == pytest.approx(0.75)
    assert parse_clip_progress("[clip 4/4] c.mp4: nhúng khung 16/16") == pytest.approx(1.0)
    assert parse_clip_progress("Đang nhúng CLIP: a.mp4 (16 khung)") is None
    assert parse_clip_progress("[clip 0/0] x: nhúng khung 0/0") is None
    assert parse_clip_progress("") is None


def test_the_clip_fraction_replaces_the_time_estimate_inside_its_stage() -> None:
    by_time = analyze_progress(["inventory", "transcribe", "scenes"], "semantics", 0.0, EXPECTED)
    by_lines = analyze_progress(["inventory", "transcribe", "scenes"], "semantics", 0.0, EXPECTED,
                                fraction=0.5)  # fmt: skip
    assert by_lines > by_time


# ─────────────── 6c-1: when a stage command ends ───────────────


def test_a_stage_that_exits_zero_is_done() -> None:
    v = judge_stage("exit", 0, stage="scenes")
    assert (v.status, v.result, v.retry) == ("completed", "passed", False)


@pytest.mark.parametrize("code", [1, 2, -9])
def test_a_stage_that_fails_names_itself_and_may_be_retried(code: int) -> None:
    v = judge_stage("exit", code, stage="semantics")
    assert (v.status, v.retry) == ("failed", True) and "semantics" in v.message


def test_stage_stops_map_like_a_render() -> None:
    assert judge_stage("cancelled", None, stage="scenes").status == "cancelled"
    assert judge_stage("shutdown", None, stage="scenes").status == "queued"
    hung = judge_stage("stalled", None, stage="transcribe", detail="1800s")
    assert (hung.status, hung.retry) == ("failed", True) and "1800s" in hung.message
    assert judge_stage("spawn_failed", None, stage="inventory").retry is False


# ─────────────── 6c-1: the analysis stamp ───────────────


def files(*specs: tuple[str, int, int]) -> list[tuple[str, int, int]]:
    return list(specs)


def test_the_footage_signature_ignores_order_and_case_of_names() -> None:
    a = footage_signature(files(("A.mp4", 10, 1), ("b.mp4", 20, 2)))
    b = footage_signature(files(("b.mp4", 20, 2), ("a.MP4", 10, 1)))
    assert a == b and len(a) == 64


@pytest.mark.parametrize("changed", [
    [("a.mp4", 11, 1), ("b.mp4", 20, 2)],  # size
    [("a.mp4", 10, 9), ("b.mp4", 20, 2)],  # mtime
    [("a2.mp4", 10, 1), ("b.mp4", 20, 2)],  # name
    [("a.mp4", 10, 1)],  # a clip removed
    [("a.mp4", 10, 1), ("b.mp4", 20, 2), ("c.mp4", 1, 1)],  # a clip added
])  # fmt: skip
def test_any_change_to_the_footage_changes_the_signature(
    changed: list[tuple[str, int, int]],
) -> None:
    base = footage_signature(files(("a.mp4", 10, 1), ("b.mp4", 20, 2)))
    assert footage_signature(changed) != base


def test_an_empty_folder_has_a_stable_signature() -> None:
    assert footage_signature([]) == footage_signature([])


def stamp(sig: str) -> AnalysisStamp:
    return AnalysisStamp(signature=sig, stages=list(ANALYZE_STAGES), created_at=1.0,
                         rules_version="2.0")  # fmt: skip


def test_a_stamp_is_reused_only_when_nothing_changed_and_no_force() -> None:
    assert stamp_is_fresh(stamp("x"), "x", force=False, artifacts_present=True)
    assert not stamp_is_fresh(stamp("x"), "y", force=False, artifacts_present=True)
    assert not stamp_is_fresh(stamp("x"), "x", force=True, artifacts_present=True)
    assert not stamp_is_fresh(stamp("x"), "x", force=False, artifacts_present=False)
    assert not stamp_is_fresh(None, "x", force=False, artifacts_present=True)


def test_a_stamp_from_an_incomplete_analysis_is_not_fresh() -> None:
    partial = stamp("x").model_copy(update={"stages": ["inventory", "transcribe"]})
    assert not stamp_is_fresh(partial, "x", force=False, artifacts_present=True)
