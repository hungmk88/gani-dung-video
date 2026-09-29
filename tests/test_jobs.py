"""Unit tests: domain/jobs.py — job states, verdict of a finished run, retry, progress (6a)."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.jobs import (
    STATUSES,
    can_transition,
    estimate_progress,
    judge_run,
    line_level,
    may_retry,
    stage_of,
)

# ─────────────── state machine ───────────────


def test_statuses_are_the_spec_vocabulary() -> None:
    assert STATUSES == ("queued", "processing", "completed", "failed", "cancelled")


@pytest.mark.parametrize(
    "src,dst",
    [("queued", "processing"), ("queued", "cancelled"), ("processing", "completed"),
     ("processing", "failed"), ("processing", "cancelled"), ("processing", "queued"),
     ("failed", "queued"), ("cancelled", "queued"), ("completed", "queued")],
)  # fmt: skip
def test_allowed_transitions(src: str, dst: str) -> None:
    assert can_transition(src, dst)


@pytest.mark.parametrize(
    "src,dst",
    [("queued", "completed"), ("queued", "failed"), ("completed", "processing"),
     ("failed", "completed"), ("cancelled", "processing"), ("processing", "processing"),
     ("queued", "queued"), ("nonsense", "queued")],
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
