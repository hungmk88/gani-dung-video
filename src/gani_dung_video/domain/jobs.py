"""Render job queue rules (phase 6a, spec §8.3.1 Option B). Pure code, no I/O.

A job is one draft of one project. Statuses follow the spec (queued / processing / completed
/ failed / cancelled); a finished render whose technical QC failed is `completed` with
result `qc_failed` — the video exists but is not delivered, and re-rendering the same EDL
would give the same file, so it is never retried by itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Status = Literal["queued", "processing", "completed", "failed", "cancelled"]
STATUSES: tuple[str, ...] = ("queued", "processing", "completed", "failed", "cancelled")

_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"processing", "cancelled"}),
    # processing → queued: retry after a crash, or a stop request that gave the job back
    "processing": frozenset({"completed", "failed", "cancelled", "queued"}),
    "failed": frozenset({"queued"}),  # manual retry
    "cancelled": frozenset({"queued"}),  # manual retry
    "completed": frozenset({"queued"}),  # manual retry of a qc_failed result
}

# Which kinds may be put back in the queue by code after a crash or a failed run. A draft job
# spends API money: it is re-run only by a person, who first reads ai_log.jsonl.
_AUTO_RETRY = {"render": True, "analyze": True, "draft": False}

# Rough share of the expected run time; the spec asks for an estimate, not a measurement.
_PROGRESS_START, _PROGRESS_CAP = 3.0, 95.0


class Job(BaseModel):
    """One row of the `jobs` table."""

    id: int
    kind: str
    project_dir: str
    options_file: str
    draft: str
    final: bool
    no_asr: bool
    status: str
    result: str | None = None  # passed | qc_failed (only when completed)
    attempts: int
    max_attempts: int
    priority: int = 0
    not_before: float = 0.0  # epoch seconds: do not claim before (retry delay)
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    worker_pid: int | None = None
    child_pid: int | None = None
    heartbeat_at: float | None = None
    stage: str | None = None
    progress_pct: float = 0.0
    output_path: str | None = None
    error_msg: str | None = None
    exit_code: int | None = None
    cancel_requested: bool = False


class Verdict(BaseModel):
    """What to do with a job after its child process ended."""

    model_config = ConfigDict(frozen=True)

    status: str  # completed | failed | cancelled | queued (put back, attempt not used)
    result: str | None  # passed | qc_failed
    retry: bool  # a failed run that may be tried again while attempts remain
    message: str  # Vietnamese, shown to the editor


def can_transition(src: str, dst: str) -> bool:
    return dst in _TRANSITIONS.get(src, frozenset())


def may_retry(kind: str, attempts: int, max_attempts: int) -> bool:
    """Whether code may put a failed or orphaned job back in the queue."""
    return _AUTO_RETRY.get(kind, False) and attempts < max_attempts


def judge_run(
    reason: str,
    exit_code: int | None,
    report_fresh: bool,
    report_passed: bool | None,
    detail: str = "",
) -> Verdict:
    """Verdict of one finished run.

    Args:
        reason: exit | cancelled | stalled | timeout | shutdown | spawn_failed — why the
            runner returned.
        exit_code: the child's exit code (only for reason "exit").
        report_fresh: qc_report.json was written after this run started (an old report from an
            earlier run must never make this run look finished).
        report_passed: the report's `passed` field, when there is a fresh report.
        detail: extra text for the message (e.g. the limit that was exceeded).
    """
    if reason == "cancelled":
        return Verdict(status="cancelled", result=None, retry=False, message="Đã hủy job.")
    if reason == "shutdown":
        return Verdict(status="queued", result=None, retry=False,
                       message="Daemon dừng trước khi dựng xong; job trả về hàng đợi.")  # fmt: skip
    if reason == "spawn_failed":
        return Verdict(status="failed", result=None, retry=False,
                       message="Không chạy được tiến trình dựng (thiếu Python hoặc gói "
                               "gani_dung_video); kiểm tra cài đặt.")  # fmt: skip
    if reason in ("stalled", "timeout"):
        what = "không có tín hiệu nào" if reason == "stalled" else "chạy quá lâu"
        return Verdict(status="failed", result=None, retry=True,
                       message=f"Tiến trình dựng bị treo: {what} ({detail}); đã dừng.")  # fmt: skip
    if exit_code in (0, 1):
        if not report_fresh:
            return Verdict(status="failed", result=None, retry=False,
                           message="Dựng xong nhưng không có báo cáo QC mới; không tin kết quả "
                                   "này, chạy lại bằng tay.")  # fmt: skip
        if exit_code == 0 and report_passed:
            return Verdict(status="completed", result="passed", retry=False, message="")
        msg = "Video đã dựng nhưng chưa đạt soát kỹ thuật; không giao bản này."
        return Verdict(status="completed", result="qc_failed", retry=False, message=msg)
    return Verdict(status="failed", result=None, retry=True,
                   message=f"Tiến trình dựng lỗi (mã thoát {exit_code}).")  # fmt: skip


def estimate_progress(elapsed_sec: float, expected_sec: float) -> float:
    """Estimated percent while a job runs: from 3 % up to 95 %; 100 only when it is done."""
    if expected_sec <= 0 or elapsed_sec <= 0:
        return _PROGRESS_START
    share = min(elapsed_sec / expected_sec, 1.0)
    return _PROGRESS_START + (_PROGRESS_CAP - _PROGRESS_START) * share


def stage_of(line: str) -> str | None:
    """Job stage announced by a console line of the child process, if any."""
    return "dung" if "Đang dựng bản" in line else None


def line_level(line: str) -> str:
    """Console level of a child line: error | warn | info."""
    head = line.lstrip()
    if head.startswith(("LỖI", "ERROR", "Traceback")):
        return "error"
    if "KHÔNG ĐẠT" in line or head.startswith(("WARN", "CẢNH BÁO")):
        return "warn"
    return "info"
