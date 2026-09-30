"""`draft` jobs (phase 6c-2): plan → pick (→ phrases) → estimate, wait for approval, then paid.

The only jobs that spend money, so the rules here are strict and each is also enforced
somewhere else (the database will not hand out a paid phase without an approval):
- the estimate phase is free: `draft --dry-run --json` returns before any API key is used;
- the job then WAITS (status awaiting_approval, the worker is free) until a person approves a
  maximum for it (`queue approve`), inside a time window;
- the paid phase first re-runs the free estimate: if the numbers moved, nothing is spent and
  the job goes back to waiting with the new number;
- the paid `draft --max-vnd N` cannot exceed the approved maximum (the service also checks its
  cap before every call); the real bill is read back from edit/ai_log.jsonl;
- a draft job is never retried by code, whatever happened. Any retry is a person's decision and
  starts again at a fresh estimate.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from gani_dung_video.application.analyze_job import JobEnv, run_stage, settle_stop
from gani_dung_video.domain.feasibility import INFEASIBLE_EXIT_CODE
from gani_dung_video.domain.jobs import (
    Estimate,
    Job,
    draft_stages,
    estimates_match,
    format_vnd,
    parse_estimate_line,
    remaining_stages,
    spent_in_log,
)
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR

AI_LOG = "ai_log.jsonl"
_PAID_STAGES = ("estimate", "draft")  # progress of the paid phase: re-check, then the call


def _head(env: JobEnv) -> list[str]:
    return list(env.argv_base) if env.argv_base is not None else [sys.executable, "-m",
                                                                  "gani_dung_video"]  # fmt: skip


def _argv(stage: str, job: Job, env: JobEnv) -> list[str]:
    """The CLI command of a free stage (plan gets the machine's music library, if set)."""
    common = [job.options_file, job.project_dir]
    if stage == "plan":
        music = ["--nhac-thu-muc", env.music_dir] if env.music_dir else []
        return [*_head(env), "plan", *common, *music]
    if stage == "estimate":
        return [*_head(env), "draft", *common, "--dry-run", "--json"]
    return [*_head(env), stage, *common]  # pick, phrases


def _paid_argv(job: Job, env: JobEnv) -> list[str]:
    """The paid call, capped at what the person approved (repr keeps the exact figure)."""
    assert job.approved_vnd is not None  # guarded by the caller
    return [*_head(env), "draft", job.options_file, job.project_dir, "--max-vnd",
            repr(float(job.approved_vnd))]  # fmt: skip


def _driver(folder: Path) -> str | None:
    try:
        data = json.loads((folder / EDIT_DIR / "plan.json").read_text(encoding="utf-8"))
        return str(data["driver"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _estimate_from(lines: Sequence[str]) -> Estimate | None:
    for line in reversed(lines):
        if (est := parse_estimate_line(line)) is not None:
            return est
    return None


def _stored(job: Job) -> Estimate:
    return Estimate(model="", input_tokens=job.cost_input_tokens or 0, input_only_vnd=0.0,
                    worst_call_vnd=job.cost_worst_call_vnd or 0.0,
                    cap_vnd=job.cost_cap_vnd or 0.0, max_attempts=1)  # fmt: skip


def _infeasible(exit_code: int | None, tail: Sequence[str]) -> str | None:
    """The reason `draft` gave when the footage cannot fill the plan (exit code 3), else None."""
    if exit_code != INFEASIBLE_EXIT_CODE:
        return None
    marker = "KHÔNG ĐẠT: "
    for line in reversed(tail):
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return None


def _log_size(folder: Path) -> int:
    with contextlib.suppress(OSError):
        return (folder / EDIT_DIR / AI_LOG).stat().st_size
    return 0


def _log_since(folder: Path, offset: int) -> str:
    """What this run added to ai_log.jsonl (the whole file if it was replaced meanwhile)."""
    try:
        with (folder / EDIT_DIR / AI_LOG).open("rb") as f:
            size = f.seek(0, 2)
            f.seek(offset if offset <= size else 0)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _fail(env: JobEnv, job: Job, msg: str, spent: float | None = None,
          exit_code: int | None = None) -> None:  # fmt: skip
    now = env.clock()
    env.db.finish(job.id, now, "failed", None, exit_code, msg, None, spent_vnd=spent)
    env.db.add_event(job.id, now, "error", msg)


def _hold(env: JobEnv, job: Job, est: Estimate, before: Estimate | None = None) -> None:
    """Park the job for approval and tell the person what to type."""
    cfg = env.rules["queue"]
    ttl = float(cfg["approval_ttl_sec"])
    now = env.clock()
    env.db.hold_for_approval(job.id, now, est, ttl)
    if before is not None:
        env.db.add_event(job.id, now, "warn",
                         f"Ước tính đã đổi (một lần gọi tối đa {format_vnd(before.worst_call_vnd)} "
                         f"→ {format_vnd(est.worst_call_vnd)}): duyệt cũ không còn hiệu lực, "
                         "không gọi API.")  # fmt: skip
    env.db.add_event(job.id, now, "info",
                     f"Ước tính tối đa một lần gọi: {format_vnd(est.worst_call_vnd)} "
                     f"({est.input_tokens} token vào, "
                     f"trần {format_vnd(est.cap_vnd)} cho video này).")  # fmt: skip
    env.db.add_event(job.id, now, "info",
                     f"Chờ duyệt ({ttl / 3600:g} giờ): queue approve {job.id} --max-vnd <số đồng "
                     f"tối đa> --estimate {round(est.worst_call_vnd)}")  # fmt: skip


def run_draft(env: JobEnv, job: Job) -> None:
    """Run the free estimate phase, or, for an approved job, the paid phase."""
    folder = Path(job.project_dir)
    if not folder.is_dir():
        _fail(env, job, f"Thư mục footage không còn: {folder.name}.")
        return
    if not Path(job.options_file).is_file():
        _fail(env, job, f"Không thấy tệp yêu cầu (options) của job: {Path(job.options_file).name}.")
        return
    if job.phase == "paid":
        _run_paid(env, job)
    else:
        _run_estimate(env, job)


def _run_estimate(env: JobEnv, job: Job) -> None:
    cfg = env.rules["queue"]
    folder = Path(job.project_dir)
    done = list(job.stages_done)
    tail: list[str] = []
    job_start = time.monotonic()
    while True:
        order = draft_stages(_driver(folder))  # phrases join once plan.json says "speech"
        todo = remaining_stages(order, done) or ["estimate"]
        stage = todo[0]
        capture: list[str] | None = [] if stage == "estimate" else None
        res, verdict = run_stage(
            env, job, stage, _argv(stage, job, env), stages=order, done=done,
            job_start=job_start, stall_sec=float(cfg["draft_stall_sec"]),
            timeout_sec=float(cfg["draft_timeout_sec"]), tail=tail, capture=capture,
        )  # fmt: skip
        if verdict.status != "completed":
            if stage == "estimate" and (why := _infeasible(res.exit_code, tail)) is not None:
                _fail(env, job, why, None, res.exit_code)  # nothing here fixes itself: 0đ
                return
            settle_stop(env, job, verdict, res, " | ".join(tail))
            return
        env.db.mark_stage_done(job.id, stage)
        done.append(stage)
        if stage == "estimate":
            est = _estimate_from(capture or [])
            if est is None:
                _fail(env, job, "Không đọc được ước tính chi phí từ lệnh draft --dry-run --json.")
                return
            _hold(env, job, est)
            return


def _run_paid(env: JobEnv, job: Job) -> None:
    cfg = env.rules["queue"]
    folder = Path(job.project_dir)
    if job.approved_vnd is None:  # the database never hands this out: refuse anyway
        _fail(env, job, "Job chưa được duyệt chi phí; không gọi API.")
        return
    if not env.ai_key_present():
        _fail(env, job, "Chưa có ANTHROPIC_API_KEY trong .env của máy dựng; không gọi API (0đ).")
        return
    tail: list[str] = []
    job_start = time.monotonic()
    stall, timeout = float(cfg["draft_stall_sec"]), float(cfg["draft_timeout_sec"])
    lines: list[str] = []
    res, verdict = run_stage(env, job, "estimate", _argv("estimate", job, env),
                             stages=_PAID_STAGES, done=[], job_start=job_start, stall_sec=stall,
                             timeout_sec=timeout, tail=tail, capture=lines)  # fmt: skip
    if verdict.status != "completed":
        if (why := _infeasible(res.exit_code, tail)) is not None:  # the data changed meanwhile
            _fail(env, job, why, None, res.exit_code)
            return
        settle_stop(env, job, verdict, res, " | ".join(tail))
        return
    fresh = _estimate_from(lines)
    if fresh is None:
        _fail(env, job, "Không đọc được ước tính chi phí lúc kiểm lại; không gọi API.")
        return
    before = _stored(job)
    if not estimates_match(before, fresh):
        _hold(env, job, fresh, before)  # nothing was spent: the person must look again
        return
    offset = _log_size(folder)
    res, verdict = run_stage(env, job, "draft", _paid_argv(job, env), stages=_PAID_STAGES,
                             done=["estimate"], job_start=job_start, stall_sec=stall,
                             timeout_sec=timeout, tail=tail, capture=None)  # fmt: skip
    spent = spent_in_log(_log_since(folder, offset))
    _finish_paid(env, job, folder, res.reason, res.exit_code, verdict.message, spent,
                 " | ".join(tail))  # fmt: skip


def _finish_paid(env: JobEnv, job: Job, folder: Path, reason: str, code: int | None, note: str,
                 spent: float, tail: str) -> None:  # fmt: skip
    now = env.clock()
    bill = f"Đã tốn {format_vnd(spent)} (theo ai_log.jsonl)."
    if reason == "cancelled":
        env.db.add_event(job.id, now, "warn", f"Đã hủy giữa lúc gọi API. {bill}")
        env.db.finish(job.id, now, "cancelled", None, None, None, None, spent_vnd=spent)
    elif reason == "exit" and code == 0:
        name = "speech_drafts.json" if _driver(folder) == "speech" else "drafts.json"
        out = folder / EDIT_DIR / name
        env.db.add_event(job.id, now, "info", f"Đã có kịch bản. {bill}")
        env.db.finish(job.id, now, "completed", "passed", 0, None,
                      str(out) if out.is_file() else None, spent_vnd=spent)  # fmt: skip
    elif reason == "shutdown":
        _fail(env, job, "Daemon dừng giữa lúc gọi API; không tự chạy lại vì có thể đã tốn phí. "
                        f"{bill} Xem edit/ai_log.jsonl; `queue retry` sẽ phải duyệt lại chi phí.",
              spent)  # fmt: skip
    else:
        why = note if reason != "exit" else f"Gọi API không thành công (mã thoát {code})."
        last = f" Dòng cuối của tiến trình: {tail}" if tail else ""
        _fail(env, job, f"{why} {bill}{last}", spent, code)
