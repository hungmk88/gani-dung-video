"""SQLite job queue (phase 6a, spec §8.3.1): jobs, console events, the daemon's own row.

One short-lived connection per operation (WAL + busy_timeout), so the CLI, the daemon and the
future HTTP API can all touch the same file at once. Every write runs in BEGIN IMMEDIATE:
claiming a job is select-then-update inside one exclusive transaction, so two workers can
never take the same job. All values go in as query parameters, never spliced into SQL.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.jobs import Job, can_transition, may_retry

SCHEMA_VERSION = 1
STOP_MODES = ("graceful", "now")

_SCHEMA = """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('render', 'draft', 'analyze')),
  project_dir TEXT NOT NULL,
  options_file TEXT NOT NULL,
  draft TEXT NOT NULL,
  final INTEGER NOT NULL DEFAULT 0,
  no_asr INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
  result TEXT CHECK (result IN ('passed', 'qc_failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
  priority INTEGER NOT NULL DEFAULT 0,
  not_before REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  worker_pid INTEGER,
  child_pid INTEGER,
  heartbeat_at REAL,
  stage TEXT,
  progress_pct REAL NOT NULL DEFAULT 0,
  output_path TEXT,
  error_msg TEXT,
  exit_code INTEGER,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_jobs_claim ON jobs (status, priority DESC, id);
CREATE TABLE job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
  ts REAL NOT NULL,
  level TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX idx_events_job ON job_events (job_id, id);
CREATE TABLE daemon (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pid INTEGER NOT NULL,
  started_at REAL NOT NULL,
  heartbeat_at REAL NOT NULL,
  stop_request TEXT CHECK (stop_request IN ('graceful', 'now'))
);
"""

_BOOLS = ("final", "no_asr", "cancel_requested")


class QueueDbError(RuntimeError):
    """The queue database cannot be used (message in Vietnamese, says what to do)."""


class NewJob(BaseModel):
    """What a caller provides to put a job in the queue."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["render", "draft", "analyze"]
    project_dir: str
    options_file: str
    draft: str
    final: bool = False
    no_asr: bool = False
    max_attempts: int = 2
    priority: int = 0


class JobEvent(BaseModel):
    id: int
    job_id: int
    ts: float
    level: str
    text: str


class DaemonStatus(BaseModel):
    online: bool
    pid: int | None = None
    heartbeat_at: float | None = None
    stop_request: str | None = None


class Recovered(BaseModel):
    """A job found orphaned after a crash and what was done with it."""

    job_id: int
    action: str  # requeued | failed
    child_pid: int | None  # the dead worker's child, which may still be running
    heartbeat_age: float


def _job(row: sqlite3.Row) -> Job:
    data: dict[str, Any] = dict(row)
    for k in _BOOLS:
        data[k] = bool(data[k])
    return Job(**data)


class QueueDb:
    """Handle on queue.db; creates the file and schema on first use."""

    def __init__(self, path: Path, busy_ms: int = 5000) -> None:
        self.path = path
        self._busy_ms = busy_ms
        path.parent.mkdir(parents=True, exist_ok=True)
        self._open_or_create()

    # ─────────────── connections ───────────────

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=self._busy_ms / 1000, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout = {int(self._busy_ms)}")
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One exclusive transaction: committed on success, rolled back on any error."""
        with self._conn() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def _open_or_create(self) -> None:
        hint = (
            f"Không đọc được {self.path.name}: nếu file hỏng, đổi tên nó (hoặc dùng bản "
            f"{self.path.name}.bak) rồi chạy lại."
        )
        try:
            with self._conn() as con:
                version = int(con.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise QueueDbError(
                        f"{self.path.name} thuộc phiên bản mới hơn ({version}) chương trình này "
                        f"biết ({SCHEMA_VERSION}); cập nhật chương trình."
                    )
                if version == 0:
                    con.execute("PRAGMA journal_mode = WAL")
                    con.execute("BEGIN IMMEDIATE")
                    if int(con.execute("PRAGMA user_version").fetchone()[0]) == 0:
                        for stmt in _SCHEMA.split(";\n"):
                            if stmt.strip():
                                con.execute(stmt)
                        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    con.execute("COMMIT")
        except sqlite3.DatabaseError as e:
            raise QueueDbError(f"{hint} ({e})") from e

    def schema_version(self) -> int:
        with self._conn() as con:
            return int(con.execute("PRAGMA user_version").fetchone()[0])

    # ─────────────── jobs ───────────────

    def enqueue(self, job: NewJob, now: float) -> int:
        with self._write() as con:
            cur = con.execute(
                "INSERT INTO jobs (kind, project_dir, options_file, draft, final, no_asr, "
                "max_attempts, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job.kind, job.project_dir, job.options_file, job.draft, int(job.final),
                 int(job.no_asr), job.max_attempts, job.priority, now),
            )  # fmt: skip
            return int(cur.lastrowid or 0)

    def find_active(self, kind: str, project_dir: str, draft: str, final: bool) -> int | None:
        """Id of a waiting or running job for the same render, if any (no duplicates)."""
        with self._conn() as con:
            row = con.execute(
                "SELECT id FROM jobs WHERE kind = ? AND project_dir = ? AND draft = ? "
                "AND final = ? AND status IN ('queued', 'processing') ORDER BY id LIMIT 1",
                (kind, project_dir, draft, int(final))).fetchone()  # fmt: skip
        return int(row["id"]) if row else None

    def get(self, job_id: int) -> Job | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list_jobs(self, status: str | None = None, limit: int = 100) -> list[Job]:
        sql, args = "SELECT * FROM jobs", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        with self._conn() as con:
            rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [_job(r) for r in rows]

    def claim(self, now: float, worker_pid: int) -> Job | None:
        """Take the next runnable job (highest priority, then oldest) or None."""
        with self._write() as con:
            row = con.execute(
                "SELECT id FROM jobs WHERE status = 'queued' AND not_before <= ? "
                "ORDER BY priority DESC, id LIMIT 1", (now,)).fetchone()  # fmt: skip
            if row is None:
                return None
            con.execute(
                "UPDATE jobs SET status = 'processing', attempts = attempts + 1, "
                "started_at = ?, finished_at = NULL, heartbeat_at = ?, worker_pid = ?, "
                "child_pid = NULL, stage = 'khoi_dong', progress_pct = 0, error_msg = NULL, "
                "exit_code = NULL, cancel_requested = 0 WHERE id = ?",
                (now, now, worker_pid, row["id"]),
            )  # fmt: skip
            claimed = con.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return _job(claimed)

    def _status_of(self, con: sqlite3.Connection, job_id: int) -> str:
        row = con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise QueueDbError(f"Không có job {job_id}.")
        return str(row["status"])

    def _must_be_processing(self, con: sqlite3.Connection, job_id: int, dst: str) -> None:
        status = self._status_of(con, job_id)
        if status != "processing" or not can_transition(status, dst):
            raise QueueDbError(f"Job {job_id} đang '{status}', không phải processing.")

    def set_child(self, job_id: int, pid: int) -> None:
        with self._write() as con:
            con.execute("UPDATE jobs SET child_pid = ? WHERE id = ?", (pid, job_id))

    def progress(self, job_id: int, now: float, stage: str | None, pct: float) -> None:
        with self._write() as con:
            con.execute(
                "UPDATE jobs SET heartbeat_at = ?, stage = COALESCE(?, stage), progress_pct = ? "
                "WHERE id = ? AND status = 'processing'", (now, stage, pct, job_id))  # fmt: skip

    def finish(
        self, job_id: int, now: float, status: str, result: str | None,
        exit_code: int | None, error: str | None, output_path: str | None,
    ) -> None:  # fmt: skip
        with self._write() as con:
            self._must_be_processing(con, job_id, status)
            con.execute(
                "UPDATE jobs SET status = ?, result = ?, exit_code = ?, error_msg = ?, "
                "output_path = ?, finished_at = ?, worker_pid = NULL, child_pid = NULL, "
                "progress_pct = CASE WHEN ? = 'completed' THEN 100 ELSE progress_pct END "
                "WHERE id = ?",
                (status, result, exit_code, error, output_path, now, status, job_id),
            )  # fmt: skip

    def requeue(self, job_id: int, not_before: float, error: str | None,
                consume_attempt: bool) -> None:  # fmt: skip
        """Put a running job back in the queue (retry, or a stop request gave it back)."""
        with self._write() as con:
            self._must_be_processing(con, job_id, "queued")
            self._requeue(con, job_id, not_before, error, consume_attempt)

    @staticmethod
    def _requeue(con: sqlite3.Connection, job_id: int, not_before: float, error: str | None,
                 consume_attempt: bool) -> None:  # fmt: skip
        give_back = 0 if consume_attempt else 1
        con.execute(
            "UPDATE jobs SET status = 'queued', attempts = MAX(attempts - ?, 0), not_before = ?, "
            "error_msg = ?, worker_pid = NULL, child_pid = NULL, started_at = NULL, "
            "heartbeat_at = NULL, stage = NULL, progress_pct = 0, cancel_requested = 0 "
            "WHERE id = ?", (give_back, not_before, error, job_id))  # fmt: skip

    # ─────────────── cancel, manual retry ───────────────

    def request_cancel(self, job_id: int, now: float) -> str:
        """Cancel a waiting job at once; ask a running one to stop. Returns the job's status
        afterwards ("missing" when there is no such job)."""
        with self._write() as con:
            row = con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return "missing"
            status = str(row["status"])
            if status == "queued":
                con.execute("UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                            (now, job_id))  # fmt: skip
                return "cancelled"
            if status == "processing":
                con.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
            return status

    def cancel_requested(self, job_id: int) -> bool:
        with self._conn() as con:
            row = con.execute("SELECT cancel_requested FROM jobs WHERE id = ?",
                              (job_id,)).fetchone()  # fmt: skip
        return bool(row and row["cancel_requested"])

    def retry_manual(self, job_id: int) -> bool:
        """Queue a failed, cancelled or qc_failed job again (attempts start over)."""
        with self._write() as con:
            row = con.execute("SELECT status, result FROM jobs WHERE id = ?",
                              (job_id,)).fetchone()  # fmt: skip
            if row is None:
                return False
            ok = row["status"] in ("failed", "cancelled") or (
                row["status"] == "completed" and row["result"] == "qc_failed")  # fmt: skip
            if not ok:
                return False
            con.execute(
                "UPDATE jobs SET status = 'queued', result = NULL, attempts = 0, error_msg = NULL, "
                "exit_code = NULL, not_before = 0, started_at = NULL, finished_at = NULL, "
                "heartbeat_at = NULL, stage = NULL, progress_pct = 0, output_path = NULL, "
                "cancel_requested = 0 WHERE id = ?", (job_id,))  # fmt: skip
            return True

    # ─────────────── events ───────────────

    def add_event(self, job_id: int, now: float, level: str, text: str, keep: int = 500) -> None:
        with self._write() as con:
            con.execute("INSERT INTO job_events (job_id, ts, level, text) VALUES (?, ?, ?, ?)",
                        (job_id, now, level, text))  # fmt: skip
            con.execute(
                "DELETE FROM job_events WHERE job_id = ? AND id NOT IN "
                "(SELECT id FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT ?)",
                (job_id, job_id, keep),
            )  # fmt: skip

    def events(self, job_id: int, after_id: int = 0) -> list[JobEvent]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id",
                (job_id, after_id)).fetchall()  # fmt: skip
        return [JobEvent(**dict(r)) for r in rows]

    # ─────────────── crash recovery ───────────────

    def recover_orphans(self, now: float, alive: Callable[[int], bool], stale_sec: float,
                        retry_delay: float) -> list[Recovered]:  # fmt: skip
        """Deal with jobs left `processing` by a daemon that died: back to the queue while
        attempts remain (waiting `retry_delay`), otherwise failed. Paid kinds never retry."""
        out: list[Recovered] = []
        with self._write() as con:
            rows = con.execute("SELECT * FROM jobs WHERE status = 'processing'").fetchall()
            for row in rows:
                job = _job(row)
                age = now - job.heartbeat_at if job.heartbeat_at is not None else float("inf")
                dead = job.worker_pid is None or not alive(job.worker_pid)
                if not dead and age <= stale_sec:
                    continue  # its worker is alive and still reporting
                if may_retry(job.kind, job.attempts, job.max_attempts):
                    msg = "Daemon sập hoặc máy tắt khi đang dựng; tự dựng lại."
                    self._requeue(con, job.id, now + retry_delay, msg, consume_attempt=True)
                    action = "requeued"
                else:
                    msg = ("Daemon sập khi đang chạy job tốn phí API; không tự chạy lại — xem "
                           "ai_log.jsonl rồi chạy lại bằng tay."
                           if job.kind == "draft" else
                           "Daemon sập khi đang dựng và đã hết số lần thử.")  # fmt: skip
                    con.execute(
                        "UPDATE jobs SET status = 'failed', error_msg = ?, finished_at = ?, "
                        "worker_pid = NULL, child_pid = NULL WHERE id = ?",
                        (msg, now, job.id),
                    )
                    action = "failed"
                out.append(Recovered(job_id=job.id, action=action, child_pid=job.child_pid,
                                     heartbeat_age=min(age, 1e12)))  # fmt: skip
        return out

    # ─────────────── the daemon's own row ───────────────

    def register_daemon(self, pid: int, now: float, stale_sec: float,
                        alive: Callable[[int], bool]) -> bool:  # fmt: skip
        """Claim the single daemon slot. False when another live daemon holds it."""
        with self._write() as con:
            row = con.execute("SELECT pid, heartbeat_at FROM daemon WHERE id = 1").fetchone()
            if row and row["pid"] != pid and now - row["heartbeat_at"] <= stale_sec \
                    and alive(int(row["pid"])):  # fmt: skip
                return False
            con.execute(
                "INSERT OR REPLACE INTO daemon (id, pid, started_at, heartbeat_at, stop_request) "
                "VALUES (1, ?, ?, ?, NULL)", (pid, now, now))  # fmt: skip
            return True

    def daemon_heartbeat(self, pid: int, now: float) -> None:
        with self._write() as con:
            con.execute("UPDATE daemon SET heartbeat_at = ? WHERE id = 1 AND pid = ?", (now, pid))

    def unregister_daemon(self, pid: int) -> None:
        with self._write() as con:
            con.execute("DELETE FROM daemon WHERE id = 1 AND pid = ?", (pid,))

    def daemon_status(self, now: float, stale_sec: float) -> DaemonStatus:
        with self._conn() as con:
            row = con.execute("SELECT * FROM daemon WHERE id = 1").fetchone()
        if row is None:
            return DaemonStatus(online=False)
        beat = row["heartbeat_at"]
        return DaemonStatus(
            online=now - beat <= stale_sec,
            pid=row["pid"],
            heartbeat_at=beat,
            stop_request=row["stop_request"],
        )

    def set_stop_request(self, mode: str) -> bool:
        """Ask the running daemon to stop (graceful: finish the job; now: give it back)."""
        if mode not in STOP_MODES:
            raise QueueDbError(f"Chế độ dừng '{mode}' không hợp lệ ({', '.join(STOP_MODES)}).")
        with self._write() as con:
            cur = con.execute("UPDATE daemon SET stop_request = ? WHERE id = 1", (mode,))
            return cur.rowcount > 0

    def stop_request(self) -> str | None:
        with self._conn() as con:
            row = con.execute("SELECT stop_request FROM daemon WHERE id = 1").fetchone()
        return row["stop_request"] if row else None

    # ─────────────── backup ───────────────

    def backup(self, dest: Path) -> None:
        """A consistent copy of the database (SQLite online backup, safe while in use)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as src:
            out = sqlite3.connect(dest)
            try:
                src.backup(out)
            finally:
                out.close()
