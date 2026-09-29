"""The background worker (phase 6a, spec §8.3.1): takes render jobs one at a time.

Loop: every poll_sec seconds show a heartbeat, claim the next queued job and run it as a child
process (`python -m gani_dung_video render …`). What happens next is decided from how the child
ended (domain.jobs.judge_run): finished / QC failed / retry / failed / cancelled / handed back.

Safety rules:
- one daemon at a time (a row in the queue database, refreshed by the heartbeat);
- on start-up, jobs a dead daemon left `processing` are put back (or failed), and the ffmpeg
  tree it left behind is stopped so it cannot race the retry;
- a stop request (Ctrl+C, SIGTERM, `queue stop`) lets the running job finish within
  shutdown_grace_sec, then gives it back to the queue without using an attempt; `now` gives it
  back at once. Half-written videos are deleted, so a partial MP4 is never mistaken for a result.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from gani_dung_video.application.analyze_job import JobEnv, run_analyze
from gani_dung_video.application.draft_job import run_draft
from gani_dung_video.application.render_service import RENDER_DIR, _video_name
from gani_dung_video.domain.jobs import (
    Job,
    Verdict,
    estimate_progress,
    judge_run,
    line_level,
    may_retry,
    stage_of,
)
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.config_files import CONFIG_DIR
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.process_runner import RunResult, kill_tree, pid_alive, run_child
from gani_dung_video.infrastructure.queue_db import QueueDb

logger = logging.getLogger(__name__)
REPO_ROOT = CONFIG_DIR.parent
# The child imports this same package even when it is not pip-installed (tests, a fresh clone).
_PACKAGE_PARENT = Path(__file__).resolve().parents[2]
_TAIL_LINES = 3  # last console lines kept to explain a failure
_REPORT = "qc_report.json"
_FILE_TIME_SLACK_SEC = 2.0  # file systems round modification times


class DaemonError(RuntimeError):
    """The daemon cannot start (message in Vietnamese)."""


def render_argv(job: Job, base: Sequence[str] | None = None) -> list[str]:
    """The CLI command that renders one job's draft."""
    head = list(base) if base is not None else [sys.executable, "-m", "gani_dung_video"]
    argv = [*head, "render", job.project_dir, "--ban", job.draft]
    if not job.final:
        argv.append("--preview")
    if job.no_asr:
        argv.append("--no-asr")
    return argv


class Daemon:
    """Runs the queue; every outside dependency is injectable so tests need no process."""

    def __init__(
        self, db: QueueDb, rules: Rules, *, runner: Callable[..., RunResult] = run_child,
        clock: Callable[[], float] = time.time, alive: Callable[[int], bool] = pid_alive,
        killer: Callable[[int], None] = kill_tree, pid: int | None = None,
        argv_base: Sequence[str] | None = None, log_dir: Path | None = None,
        whisper_model: str | None = None, music_dir: str | None = None,
        ai_key_present: Callable[[], bool] | None = None,
    ) -> None:  # fmt: skip
        self.db, self._cfg, self._rules = db, rules["queue"], rules
        self._runner, self._clock, self._alive, self._killer = runner, clock, alive, killer
        self._pid = pid if pid is not None else os.getpid()
        self._argv_base = argv_base
        self._whisper_model = whisper_model
        self._music_dir = music_dir
        self._ai_key_present = ai_key_present if ai_key_present is not None else (lambda: False)
        self._log_dir = log_dir if log_dir is not None else db.path.parent / "logs"
        self._local_stop: str | None = None  # "graceful" | "now", from a signal
        self._grace_until: float | None = None
        self._stop_event = threading.Event()

    # ─────────────── stop requests ───────────────

    def request_stop(self, now: bool = False) -> None:
        """Ask this daemon to stop (a second request, or `now`, is the stronger one)."""
        self._local_stop = "now" if now or self._local_stop else "graceful"

    def _stop_mode(self) -> str | None:
        modes = {self._local_stop, self.db.stop_request()} - {None}
        return "now" if "now" in modes else ("graceful" if modes else None)

    def _should_stop(self, job: Job) -> str | None:
        """Polled while a child runs: why it must be stopped now, or None."""
        if self.db.cancel_requested(job.id):
            return "cancelled"
        mode = self._stop_mode()
        if mode == "now":
            return "shutdown"
        if mode == "graceful":
            now = self._clock()
            if self._grace_until is None:
                self._grace_until = now + float(self._cfg["shutdown_grace_sec"])
            elif now > self._grace_until:
                return "shutdown"
        return None

    # ─────────────── the loop ───────────────

    def run(self, stop: threading.Event | None = None) -> None:
        """Serve the queue until `stop` is set or a stop request arrives.

        Raises:
            DaemonError: another daemon is already running.
        """
        self._stop_event = stop if stop is not None else threading.Event()
        stale = float(self._cfg["daemon_stale_sec"])
        if not self.db.register_daemon(self._pid, self._clock(), stale, self._alive):
            other = self.db.daemon_status(self._clock(), stale)
            raise DaemonError(f"Đã có daemon khác đang chạy (pid {other.pid}). Mỗi máy chỉ chạy "
                              "một daemon; dừng nó trước bằng `queue stop`.")  # fmt: skip
        try:
            self._start_up()
            while not self._stopping():
                self.db.daemon_heartbeat(self._pid, self._clock())
                if not self.process_next():
                    self._stop_event.wait(float(self._cfg["poll_sec"]))
        finally:
            self.db.unregister_daemon(self._pid)

    def _stopping(self) -> bool:
        return self._stop_event.is_set() or self._stop_mode() is not None

    def _start_up(self) -> None:
        with contextlib.suppress(Exception):  # a failed backup must not stop the queue
            self.db.backup(self.db.path.with_name(self.db.path.name + ".bak"))
        now = self._clock()
        orphans = self.db.recover_orphans(now, self._alive, float(self._cfg["job_stale_sec"]),
                                          float(self._cfg["retry_delay_sec"]))  # fmt: skip
        max_age = float(self._cfg["leftover_kill_max_age_sec"])
        for r in orphans:
            logger.warning("Khôi phục job %d sau sập: %s", r.job_id, r.action)
            self.db.add_event(r.job_id, now, "warn", f"Khôi phục sau khi daemon sập: {r.action}.")
            if r.child_pid and r.heartbeat_age <= max_age and self._alive(r.child_pid):
                self._killer(r.child_pid)  # its ffmpeg would race the retry for the same file

    # ─────────────── one job ───────────────

    def _child_env(self) -> dict[str, str]:
        return {"PYTHONPATH": os.pathsep.join(
            [str(_PACKAGE_PARENT), *filter(None, [os.environ.get("PYTHONPATH")])])}  # fmt: skip

    def _job_env(self) -> JobEnv:
        return JobEnv(db=self.db, rules=self._rules, runner=self._runner, clock=self._clock,
                      pid=self._pid, argv_base=self._argv_base, log_dir=self._log_dir,
                      child_env=self._child_env(), should_stop=self._should_stop,
                      whisper_model=self._whisper_model, music_dir=self._music_dir,
                      ai_key_present=self._ai_key_present)  # fmt: skip

    def process_next(self) -> bool:
        """Claim and run one job. False when nothing was runnable."""
        for jid in self.db.cascade_blocked(self._clock()):  # jobs whose dependency ended badly
            logger.warning("Job %d bị hủy vì job nó phụ thuộc không hoàn thành", jid)
        for jid in self.db.expire_approvals(self._clock()):  # nobody approved in time
            logger.warning("Job %d hết hạn duyệt chi phí", jid)
            self.db.add_event(jid, self._clock(), "warn",
                              "Hết hạn duyệt chi phí: không gọi API. `queue retry` để ước tính "
                              "lại.")  # fmt: skip
        job = self.db.claim(self._clock(), self._pid)
        if job is None:
            return False
        try:
            if job.kind == "analyze":
                run_analyze(self._job_env(), job)
            elif job.kind == "draft":
                run_draft(self._job_env(), job)
            elif job.kind == "render":
                self._run_job(job)
            else:
                self._fail(job, f"Loại job '{job.kind}' chưa được hỗ trợ (giai đoạn 6c-2).")
        except Exception as e:  # a bug here must fail the job, never kill the daemon
            logger.exception("Job %d gặp lỗi bất ngờ", job.id)
            self._fail(job, f"Lỗi bất ngờ trong daemon: {type(e).__name__}: {e}")
        return True

    def _fail(self, job: Job, msg: str) -> None:
        with contextlib.suppress(Exception):
            self.db.finish(job.id, self._clock(), "failed", None, None, msg, None)
            self.db.add_event(job.id, self._clock(), "error", msg)

    def _precheck(self, job: Job) -> str | None:
        folder = Path(job.project_dir)
        if not folder.is_dir():
            return f"Thư mục footage không còn: {folder}."
        edit = folder / EDIT_DIR
        if not any((edit / n).is_file() for n in ("drafts.json", "speech_drafts.json")):
            return (
                "Chưa có edit/drafts.json hoặc edit/speech_drafts.json — chạy lệnh `draft` trước."
            )
        return None

    def _run_job(self, job: Job) -> None:
        if (problem := self._precheck(job)) is not None:
            self._fail(job, problem)  # nothing here fixes itself: no retry
            return
        folder = Path(job.project_dir)
        out = folder / EDIT_DIR / RENDER_DIR / job.draft
        video = out / _video_name(job.draft, preview=not job.final)
        kind = "render_final" if job.final else "render_preview"
        started = job.started_at if job.started_at is not None else self._clock()
        wall_start = time.time()  # file times are wall-clock: compared with this, not the clock
        expected = float(self._cfg["expected_sec"][kind])
        tail: list[str] = []
        stage_box, last_box = ["khoi_dong"], [0.0]  # current stage; monotonic time of last write

        def tick(force: bool = False) -> None:
            mono = time.monotonic()
            if not force and mono - last_box[0] < 1.0:
                return
            last_box[0] = mono
            now = self._clock()
            pct = estimate_progress(now - started, expected)
            self.db.progress(job.id, now, stage_box[0], pct)
            self.db.daemon_heartbeat(self._pid, now)

        def on_line(line: str) -> None:
            if line.strip():
                tail.append(line.strip())
                del tail[:-_TAIL_LINES]
            if (stage := stage_of(line)) is not None:
                stage_box[0] = stage
            self.db.add_event(job.id, self._clock(), line_level(line), line,
                              keep=int(self._cfg["max_events_per_job"]))  # fmt: skip
            tick(force=stage is not None)

        def should_stop() -> str | None:
            tick()
            return self._should_stop(job)

        first = f"Bắt đầu dựng bản {job.draft} (lần {job.attempts}/{job.max_attempts})."
        self.db.add_event(job.id, self._clock(), "info", first)
        res = self._runner(
            render_argv(job, self._argv_base), REPO_ROOT, on_line, should_stop, [out],
            float(self._cfg["stall_sec"]), float(self._cfg["timeout_sec"][kind]),
            on_start=lambda pid: self.db.set_child(job.id, pid),
            poll_sec=min(float(self._cfg["poll_sec"]), 0.5),
            log_path=self._log_dir / f"{job.id}.log",
            env=self._child_env(),
        )  # fmt: skip
        fresh, passed = self._report(out, video, wall_start)
        limit = (
            self._cfg["stall_sec"] if res.reason == "stalled" else self._cfg["timeout_sec"][kind]
        )
        verdict = judge_run(res.reason, res.exit_code, fresh, passed, detail=f"{limit}s")
        self._settle(job, verdict, res, video, wall_start, " | ".join(tail))

    @staticmethod
    def _report(out: Path, video: Path, started: float) -> tuple[bool, bool | None]:
        """(is there a QC report written by THIS run?, its `passed` field)."""
        report = out / _REPORT
        try:
            if not (video.is_file() and report.stat().st_mtime >= started - _FILE_TIME_SLACK_SEC):
                return False, None
            return True, bool(json.loads(report.read_text(encoding="utf-8")).get("passed"))
        except (OSError, ValueError):
            return False, None

    @staticmethod
    def _remove_partial(video: Path, started: float) -> None:
        """Delete the video only if this run wrote it (an older result is left alone)."""
        with contextlib.suppress(OSError):
            if video.is_file() and video.stat().st_mtime >= started - _FILE_TIME_SLACK_SEC:
                video.unlink()

    def _settle(
        self, job: Job, v: Verdict, res: RunResult, video: Path, started: float, tail: str = ""
    ) -> None:
        now = self._clock()
        level = "info" if v.status == "completed" and v.result == "passed" else "warn"
        if v.message:
            self.db.add_event(job.id, now, level, v.message)
        if v.status == "completed":
            self.db.finish(job.id, now, "completed", v.result, res.exit_code,
                           v.message or None, str(video))  # fmt: skip
            return
        self._remove_partial(video, started)
        if v.status == "cancelled":
            self.db.finish(job.id, now, "cancelled", None, None, None, None)
        elif v.status == "queued":  # a stop request gave the job back: the attempt is not used
            self.db.requeue(job.id, now, v.message, consume_attempt=False)
        elif v.retry and may_retry(job.kind, job.attempts, job.max_attempts):
            self.db.requeue(job.id, now + float(self._cfg["retry_delay_sec"]), v.message,
                            consume_attempt=True)  # fmt: skip
        else:
            tried = f" (đã thử {job.attempts}/{job.max_attempts} lần)" if v.retry else ""
            last = f" Dòng cuối của tiến trình: {tail}" if tail else ""
            self.db.finish(job.id, now, "failed", None, res.exit_code,
                           v.message + tried + last, None)  # fmt: skip
