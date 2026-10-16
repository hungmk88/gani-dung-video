"""Render job queue rules (phase 6a, spec §8.3.1 Option B). Pure code, no I/O.

A job is one draft of one project. Statuses follow the spec (queued / processing / completed
/ failed / cancelled); a finished render whose technical QC failed is `completed` with
result `qc_failed` — the video exists but is not delivered, and re-rendering the same EDL
would give the same file, so it is never retried by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Status = Literal["queued", "processing", "awaiting_approval", "completed", "failed", "cancelled"]
# awaiting_approval (phase 6c): a draft job has its free stages and its cost estimate done and
# waits for a person to approve spending; nothing paid can start from any other status.
STATUSES: tuple[str, ...] = ("queued", "processing", "awaiting_approval", "completed", "failed",
                             "cancelled")  # fmt: skip

_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"processing", "cancelled"}),
    # processing → queued: retry after a crash, or a stop request that gave the job back
    "processing": frozenset({"completed", "failed", "cancelled", "queued", "awaiting_approval"}),
    "awaiting_approval": frozenset({"queued", "cancelled", "failed"}),  # approved / dropped
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
    # phase 6c
    pipeline_id: int | None = None
    depends_on: int | None = None  # runs only after that job completed (passed)
    phase: str | None = None  # draft jobs: estimate | paid
    stages_done: list[str] = []  # analyze jobs: finished stages, so a retry resumes
    force: bool = False  # analyze: ignore the analysis stamp
    cost_input_tokens: int | None = None
    cost_worst_call_vnd: float | None = None
    cost_cap_vnd: float | None = None
    approved_vnd: float | None = None
    approved_at: float | None = None
    spent_vnd: float | None = None
    approval_expires_at: float | None = None  # awaiting_approval: the window closes at this time
    platform: str = ""  # render: tiktok | facebook (14/10/2026); "" = no platform profile


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


def render_limits(cfg: Mapping[str, Any], final: bool, video_sec: float | None
                  ) -> tuple[float, float, float]:  # fmt: skip
    """(expected, stall, timeout) seconds of a render job; they grow with the video (phase 7d).

    The fixed `expected_sec` fit 15–30 s videos. A 182 s video measured 304 s in preview and
    999 s in final (two render rounds each) on the Dell, and ffmpeg prints nothing for minutes
    during a round, so the stall window cannot stay at 600 s: a healthy job would be killed.
    """
    kind = "render_final" if final else "render_preview"
    base = float(cfg["expected_sec"][kind])
    per = float(cfg.get("render_sec_per_video_sec", {}).get(kind, 0.0))
    expected = max(base, per * (video_sec or 0.0))
    stall = max(float(cfg["stall_sec"]), expected)
    timeout = max(float(cfg["timeout_sec"][kind]), 2.0 * expected)
    return expected, stall, timeout


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
    if "KHÔNG ĐẠT" in line or head.upper().startswith(("WARN", "CẢNH BÁO")):
        return "warn"
    return "info"


# ─────────────── phase 6c-1: dependencies, priorities, resumable stages ───────────────

# Short work first: a render must not wait behind a half-hour analysis (never pre-empted).
KIND_PRIORITY: dict[str, int] = {"render": 30, "draft": 20, "analyze": 10}
ANALYZE_STAGES: tuple[str, ...] = ("inventory", "transcribe", "scenes", "semantics")
# Drafting (6c-2): plan → pick → (phrases, speech styles) → estimate; then, once approved, the
# paid `draft` call itself, which is a phase of its own rather than a recorded stage.
DRAFT_STAGES: tuple[str, ...] = ("plan", "pick", "phrases", "estimate")
ALL_STAGES: tuple[str, ...] = (*ANALYZE_STAGES, *DRAFT_STAGES)
_CLIP_LINE = re.compile(r"^\[clip (\d+)/(\d+)\].*\s(\d+)/(\d+)\s*$")


def dependency_state(dep_status: str | None, dep_result: str | None) -> str:
    """ready: no dependency, or it finished fine; wait: it is still to come; blocked: it ended
    badly (failed, cancelled, or a render whose QC failed), so this job can never run."""
    if dep_status is None:
        return "ready"
    if dep_status == "completed":
        return "ready" if dep_result in (None, "passed") else "blocked"
    if dep_status in ("queued", "processing", "awaiting_approval"):
        return "wait"
    return "blocked"


def blocked_reason(dep_id: int, dep_status: str, dep_result: str | None) -> str:
    """Why a job was cancelled without running (shown in its error)."""
    what = {"failed": "bị lỗi", "cancelled": "đã bị hủy"}.get(dep_status, "không hoàn thành")
    if dep_status == "completed" and dep_result == "qc_failed":
        what = "chưa đạt soát kỹ thuật (QC)"
    return f"Bị chặn: job #{dep_id} {what} nên job này không chạy."


def parse_stages(text: str | None) -> list[str]:
    """The `stages_done` column: comma-separated known stage names, in the order written."""
    if not text:
        return []
    return [x for x in (p.strip() for p in text.split(",")) if x in ALL_STAGES]


def join_stages(stages: Iterable[str]) -> str:
    return ",".join(stages)


def remaining_stages(all_stages: Sequence[str], done: Sequence[str]) -> list[str]:
    """Stages still to run: from the first one not done. A later stage that is recorded as
    done but follows an unfinished one is not trusted (each stage reads the one before)."""
    for i, stage in enumerate(all_stages):
        if stage not in done:
            return list(all_stages[i:])
    return []


def parse_clip_progress(line: str) -> float | None:
    """`[clip 2/4] a.mp4: nhúng khung 8/16` → overall fraction of the CLIP stage (0–1)."""
    m = _CLIP_LINE.match(line.strip())
    if m is None:
        return None
    clip, clips, frame, frames = (int(x) for x in m.groups())
    if clips <= 0 or frames <= 0 or clip <= 0:
        return None
    return min(1.0, ((clip - 1) + min(frame / frames, 1.0)) / clips)


def analyze_progress(
    done: Sequence[str], current: str | None, elapsed_sec: float,
    expected: Mapping[str, float], fraction: float | None = None,
    stages: Sequence[str] = ANALYZE_STAGES,
) -> float:  # fmt: skip
    """Estimated percent of an analysis: finished stages by their expected duration, plus the
    running stage by the real `fraction` when its log gives one, else by elapsed time.
    Between 3 % and 95 %; only the finished job shows 100."""
    total = sum(float(expected.get(s, 0.0)) for s in stages)
    if total <= 0:
        return _PROGRESS_START
    finished = sum(float(expected.get(s, 0.0)) for s in done if s in stages)
    running = 0.0
    if current is not None and current not in done:
        exp = float(expected.get(current, 0.0))
        if fraction is not None:
            share = min(max(fraction, 0.0), 1.0)
        else:
            share = min(max(elapsed_sec, 0.0) / exp, 1.0) if exp > 0 else 0.0
        running = share * 0.95 * exp  # never quite "done" until the stage really ends
    span = _PROGRESS_CAP - _PROGRESS_START
    return min(_PROGRESS_CAP, _PROGRESS_START + span * (finished + running) / total)


def judge_stage(reason: str, exit_code: int | None, stage: str = "", detail: str = "") -> Verdict:
    """What to do when one stage command of a multi-stage job ended: on "completed" go to the
    next stage (the job finishes after the last one); the other verdicts end the job as for a
    render, except that a failed stage is retried from that stage, not from the start."""
    if reason == "cancelled":
        return Verdict(status="cancelled", result=None, retry=False, message="Đã hủy job.")
    if reason == "shutdown":
        msg = f"Daemon dừng giữa giai đoạn {stage}; job trả về hàng đợi."
        return Verdict(status="queued", result=None, retry=False, message=msg)
    if reason == "spawn_failed":
        return Verdict(status="failed", result=None, retry=False,
                       message="Không chạy được tiến trình (thiếu Python hoặc gói "
                               "gani_dung_video); kiểm tra cài đặt.")  # fmt: skip
    if reason in ("stalled", "timeout"):
        what = "không có tín hiệu nào" if reason == "stalled" else "chạy quá lâu"
        msg = f"Giai đoạn {stage} bị treo: {what} ({detail}); đã dừng."
        return Verdict(status="failed", result=None, retry=True, message=msg)
    if exit_code == 0:
        return Verdict(status="completed", result="passed", retry=False, message="")
    return Verdict(status="failed", result=None, retry=True,
                   message=f"Giai đoạn {stage} lỗi (mã thoát {exit_code}).")  # fmt: skip


class AnalysisStamp(BaseModel):
    """edit/analysis_stamp.json: the footage an analysis was done on."""

    signature: str
    stages: list[str]
    created_at: float
    rules_version: str


def footage_signature(files: Iterable[tuple[str, int, int]]) -> str:
    """Fingerprint of a footage folder from (name, size, mtime_ns) per clip: any clip added,
    removed, renamed, resized or touched changes it. Order and name case do not matter."""
    h = hashlib.sha256()
    for name, size, mtime_ns in sorted((n.lower(), sz, mt) for n, sz, mt in files):
        h.update(f"{name}\0{size}\0{mtime_ns}\n".encode())
    return h.hexdigest()


def stamp_is_fresh(stamp: AnalysisStamp | None, signature: str, force: bool,
                   artifacts_present: bool) -> bool:  # fmt: skip
    """Reuse an earlier analysis: same footage, complete, its files still there, not forced."""
    return (
        not force
        and artifacts_present
        and stamp is not None
        and stamp.signature == signature
        and set(ANALYZE_STAGES) <= set(stamp.stages)
    )


# ─────────────── phase 6c-2: drafting, estimates and cost approval ───────────────


def draft_stages(driver: str | None) -> tuple[str, ...]:
    """The free stages of a draft job; speech-driven styles also extract phrases."""
    if driver == "speech":
        return ("plan", "pick", "phrases", "estimate")
    return ("plan", "pick", "estimate")


class Estimate(BaseModel):
    """What `draft --dry-run --json` prints: the worst cost of one Claude call, no API used."""

    model_config = ConfigDict(frozen=True)

    model: str
    input_tokens: int = Field(ge=0)
    input_only_vnd: float = Field(ge=0)
    worst_call_vnd: float = Field(ge=0)  # input + max output tokens, one call
    cap_vnd: float = Field(ge=0)  # the video's cost cap from rules.json
    max_attempts: int = Field(ge=1)


def parse_estimate_line(line: str) -> Estimate | None:
    """An estimate is a whole line of JSON; anything else (log noise, prose) is not one."""
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        return Estimate.model_validate(json.loads(text))
    except (ValueError, ValidationError):
        return None


def estimates_match(a: Estimate, b: Estimate) -> bool:
    """Same input size and the same amounts to the whole đồng: nothing changed in between."""
    return (a.input_tokens == b.input_tokens
            and round(a.worst_call_vnd) == round(b.worst_call_vnd)
            and round(a.cap_vnd) == round(b.cap_vnd))  # fmt: skip


def spent_in_log(text: str) -> float:
    """Total `cost_vnd` of the lines of an ai_log.jsonl chunk (bad lines are skipped)."""
    total = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        cost = entry.get("cost_vnd") if isinstance(entry, dict) else None
        if isinstance(cost, int | float) and not isinstance(cost, bool) and math.isfinite(cost) \
                and cost >= 0:  # fmt: skip
            total += float(cost)
    return total


class BudgetState(BaseModel):
    """The daily budget check's inputs (limit None = no daily limit)."""

    model_config = ConfigDict(frozen=True)

    limit: float | None
    spent_24h: float  # paid `draft` jobs finished in the last 24 hours
    reserved: float  # approved but not yet finished: money that may still be spent


class ApprovalError(ValueError):
    """An approval that must not go through (message in Vietnamese, with the right numbers)."""


def format_vnd(x: float) -> str:
    """4207.3 → '4.207đ' (Vietnamese thousands separator, whole đồng)."""
    return f"{x:,.0f}".replace(",", ".") + "đ"


_vnd = format_vnd


def validate_approval(job: Job, max_vnd: float, estimate_vnd: float, now: float,
                      budget: BudgetState) -> None:  # fmt: skip
    """Raise ApprovalError unless approving `max_vnd` for this job is safe.

    The person must have seen the estimate (`estimate_vnd` echoes it), the maximum must cover
    at least one call and stay under the video's cap, the window must still be open, and the
    daily budget (spent + reserved + this maximum) must hold."""
    if job.kind != "draft":
        raise ApprovalError("Chỉ job kịch bản (draft) mới cần duyệt chi phí.")
    if job.status != "awaiting_approval":
        raise ApprovalError(f"Job không đang chờ duyệt (đang '{job.status}').")
    worst, cap = job.cost_worst_call_vnd, job.cost_cap_vnd
    if worst is None or cap is None:
        raise ApprovalError("Job chưa có ước tính chi phí; chưa thể duyệt.")
    if job.approval_expires_at is not None and now > job.approval_expires_at:
        raise ApprovalError("Đã hết hạn duyệt; thử lại job để có ước tính mới.")
    if not math.isfinite(max_vnd) or max_vnd <= 0:
        raise ApprovalError("Số tiền tối đa phải là số dương hợp lệ.")
    if not math.isfinite(estimate_vnd) or round(estimate_vnd) != round(worst):
        raise ApprovalError(f"Ước tính bạn nhập ({_vnd(estimate_vnd)}) không khớp ước tính hiện "
                            f"tại của job ({_vnd(worst)}): ước tính đã đổi hoặc nhập sai; xem lại "
                            "rồi duyệt.")  # fmt: skip
    if max_vnd < worst:
        raise ApprovalError(f"Số tiền tối đa ({_vnd(max_vnd)}) thấp hơn một lần gọi tối đa "
                            f"({_vnd(worst)}): không thể gọi lần nào.")  # fmt: skip
    if max_vnd > cap:
        raise ApprovalError(f"Số tiền tối đa ({_vnd(max_vnd)}) vượt trần {_vnd(cap)} của video "
                            "này.")  # fmt: skip
    if (
        budget.limit is not None
        and budget.spent_24h + budget.reserved + max_vnd > budget.limit + 1e-6
    ):
        raise ApprovalError(
            f"Duyệt này vượt hạn mức ngày {_vnd(budget.limit)}: đã tiêu {_vnd(budget.spent_24h)} "
            f"+ đã đặt cọc {_vnd(budget.reserved)} + duyệt mới {_vnd(max_vnd)}.")  # fmt: skip
