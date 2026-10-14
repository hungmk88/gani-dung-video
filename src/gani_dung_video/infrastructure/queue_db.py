"""SQLite job queue (phase 6a, spec §8.3.1): jobs, console events, the daemon's own row.

One short-lived connection per operation (WAL + busy_timeout), so the CLI, the daemon and the
future HTTP API can all touch the same file at once. Every write runs in BEGIN IMMEDIATE:
claiming a job is select-then-update inside one exclusive transaction, so two workers can
never take the same job. All values go in as query parameters, never spliced into SQL.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.jobs import (
    ALL_STAGES,
    KIND_PRIORITY,
    STATUSES,
    Estimate,
    Job,
    blocked_reason,
    can_transition,
    dependency_state,
    join_stages,
    may_retry,
    parse_stages,
)

SCHEMA_VERSION = 3
STOP_MODES = ("graceful", "now")

_STATUS_LIST = "'queued', 'processing', 'awaiting_approval', 'completed', 'failed', 'cancelled'"
_V1_COLUMNS = (
    "id, kind, project_dir, options_file, draft, final, no_asr, status, result, attempts, "
    "max_attempts, priority, not_before, created_at, started_at, finished_at, worker_pid, "
    "child_pid, heartbeat_at, stage, progress_pct, output_path, error_msg, exit_code, "
    "cancel_requested"
)


def _jobs_ddl(name: str) -> str:
    """The jobs table (v2). SQLite cannot alter a CHECK, so the v1 → v2 upgrade builds this
    under another name, copies the rows and renames it."""
    return f"""CREATE TABLE {name} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('render', 'draft', 'analyze')),
  project_dir TEXT NOT NULL,
  options_file TEXT NOT NULL,
  draft TEXT NOT NULL,
  final INTEGER NOT NULL DEFAULT 0,
  no_asr INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ({_STATUS_LIST})),
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
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  pipeline_id INTEGER,
  depends_on INTEGER REFERENCES jobs (id) ON DELETE SET NULL,
  phase TEXT CHECK (phase IN ('estimate', 'paid')),
  stages_done TEXT NOT NULL DEFAULT '',
  force INTEGER NOT NULL DEFAULT 0,
  cost_input_tokens INTEGER,
  cost_worst_call_vnd REAL,
  cost_cap_vnd REAL,
  approved_vnd REAL,
  approved_at REAL,
  spent_vnd REAL,
  approval_expires_at REAL
)"""


_JOBS_INDEXES = [
    "CREATE INDEX idx_jobs_claim ON jobs (status, priority DESC, id)",
    "CREATE INDEX idx_jobs_depends ON jobs (depends_on)",
    "CREATE INDEX idx_jobs_pipeline ON jobs (pipeline_id)",
]
_OTHER_TABLES = [
    """CREATE TABLE job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
  ts REAL NOT NULL,
  level TEXT NOT NULL,
  text TEXT NOT NULL
)""",
    "CREATE INDEX idx_events_job ON job_events (job_id, id)",
    """CREATE TABLE daemon (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pid INTEGER NOT NULL,
  started_at REAL NOT NULL,
  heartbeat_at REAL NOT NULL,
  stop_request TEXT CHECK (stop_request IN ('graceful', 'now'))
)""",
]

_BOOLS = ("final", "no_asr", "cancel_requested", "force")


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
    priority: int | None = None  # None: by kind (render before draft before analyze)
    pipeline_id: int | None = None
    depends_on: int | None = None  # job that must complete (passed) first
    force: bool = False  # analyze: ignore the analysis stamp


class FolderBusyError(QueueDbError):
    """A pipeline was refused because the folder already has an active job."""

    def __init__(self, job_id: int, pipeline_id: int | None) -> None:
        super().__init__(f"Thư mục đang có job #{job_id} chạy hoặc chờ.")
        self.job_id, self.pipeline_id = job_id, pipeline_id


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


def _spend_summary(con: sqlite3.Connection, now: float, window_sec: float) -> tuple[float, float]:
    """(spent in the window, reserved) read on `con`: see QueueDb.spend_summary."""
    spent = con.execute(
        "SELECT COALESCE(SUM(COALESCE(spent_vnd, approved_vnd, 0)), 0) FROM jobs "
        "WHERE kind = 'draft' AND phase = 'paid' AND finished_at IS NOT NULL "
        "AND finished_at >= ?", (now - window_sec,)).fetchone()[0]  # fmt: skip
    reserved = con.execute(
        "SELECT COALESCE(SUM(approved_vnd), 0) FROM jobs WHERE kind = 'draft' "
        "AND phase = 'paid' AND status IN ('queued', 'processing') "
        "AND approved_vnd IS NOT NULL").fetchone()[0]  # fmt: skip
    return float(spent), float(reserved)


def _job(row: sqlite3.Row) -> Job:
    data: dict[str, Any] = dict(row)
    for k in _BOOLS:
        data[k] = bool(data[k])
    data["stages_done"] = parse_stages(data["stages_done"])
    return Job(**data)


def _crash_message(job: Job) -> str:
    if job.kind != "draft":
        return "Daemon sập khi đang dựng và đã hết số lần thử."
    if job.phase == "paid":
        return (
            "Daemon sập khi đang gọi API tốn phí; không tự chạy lại. Xem edit/ai_log.jsonl để "
            "biết đã tốn bao nhiêu; chạy lại bằng `queue retry` thì phải duyệt lại chi phí."
        )
    return (
        "Daemon sập khi đang chuẩn bị kịch bản (chưa tốn phí API); job kịch bản không tự chạy "
        "lại — dùng `queue retry`."
    )


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
        hint = (f"Không đọc được {self.path.name}: nếu file hỏng, đổi tên nó (hoặc dùng bản "
                f"{self.path.name}.bak) rồi chạy lại.")  # fmt: skip
        try:
            with self._conn() as con:
                version = int(con.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise QueueDbError(
                    f"{self.path.name} thuộc phiên bản mới hơn ({version}) chương trình này "
                    f"biết ({SCHEMA_VERSION}); cập nhật chương trình."
                )
            if version == 0:
                self._create()
            elif version == 1:
                self._migrate_from_v1()
            elif version == 2:
                self._migrate_2_to_3()
        except sqlite3.DatabaseError as e:
            raise QueueDbError(f"{hint} ({e})") from e

    def _create(self) -> None:
        with self._conn() as con:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("BEGIN IMMEDIATE")
            try:
                if int(con.execute("PRAGMA user_version").fetchone()[0]) == 0:  # not raced
                    for stmt in [_jobs_ddl("jobs"), *_JOBS_INDEXES, *_OTHER_TABLES]:
                        con.execute(stmt)
                    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def _migrate_from_v1(self) -> None:
        """Phase 6c: rebuild `jobs` with the new columns and the awaiting_approval status
        (straight to the newest layout, so a v1 file never passes through v2).

        SQLite procedure for changing a table with foreign keys pointing at it: foreign keys
        OFF (so dropping the old table does not cascade-delete every event), one exclusive
        transaction, copy, drop, rename, integrity check. A copy of the old file is kept first
        (queue.db.v1.bak), and a process that loses the race finds version 2 and does nothing.
        """
        con = sqlite3.connect(self.path, timeout=self._busy_ms / 1000, isolation_level=None)
        try:
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute(f"PRAGMA busy_timeout = {int(self._busy_ms)}")
            con.execute("BEGIN IMMEDIATE")
            try:
                if int(con.execute("PRAGMA user_version").fetchone()[0]) != 1:
                    con.execute("ROLLBACK")  # another process already upgraded it
                    return
                self.backup(self.path.with_name(self.path.name + ".v1.bak"))
                con.execute(_jobs_ddl("jobs_v2"))
                con.execute(f"INSERT INTO jobs_v2 ({_V1_COLUMNS}) SELECT {_V1_COLUMNS} FROM jobs")
                con.execute("DROP TABLE jobs")
                con.execute("ALTER TABLE jobs_v2 RENAME TO jobs")
                for stmt in _JOBS_INDEXES:
                    con.execute(stmt)
                if con.execute("PRAGMA foreign_key_check").fetchall():
                    raise QueueDbError(
                        "Nâng cấp queue.db bị hủy: dữ liệu cũ không khớp khóa ngoại."
                    )
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()

    def _migrate_2_to_3(self) -> None:
        """Phase 6c-2: one new column, the deadline of an approval window. A plain ADD COLUMN
        (no rebuild), inside one exclusive transaction, after a copy of the v2 file."""
        con = sqlite3.connect(self.path, timeout=self._busy_ms / 1000, isolation_level=None)
        try:
            con.execute(f"PRAGMA busy_timeout = {int(self._busy_ms)}")
            con.execute("BEGIN IMMEDIATE")
            try:
                if int(con.execute("PRAGMA user_version").fetchone()[0]) != 2:
                    con.execute("ROLLBACK")  # another process already upgraded it
                    return
                self.backup(self.path.with_name(self.path.name + ".v2.bak"))
                con.execute("ALTER TABLE jobs ADD COLUMN approval_expires_at REAL")
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()

    def schema_version(self) -> int:
        with self._conn() as con:
            return int(con.execute("PRAGMA user_version").fetchone()[0])

    # ─────────────── jobs ───────────────

    @staticmethod
    def _insert(con: sqlite3.Connection, job: NewJob, now: float) -> int:
        priority = job.priority if job.priority is not None else KIND_PRIORITY[job.kind]
        dep = job.depends_on
        if dep is not None and con.execute("SELECT 1 FROM jobs WHERE id = ?",
                                           (dep,)).fetchone() is None:  # fmt: skip
            raise QueueDbError(f"Job phụ thuộc #{dep} không tồn tại.")
        cur = con.execute(
            "INSERT INTO jobs (kind, project_dir, options_file, draft, final, no_asr, "
            "max_attempts, priority, created_at, pipeline_id, depends_on, force) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.kind, job.project_dir, job.options_file, job.draft, int(job.final),
             int(job.no_asr), job.max_attempts, priority, now, job.pipeline_id,
             job.depends_on, int(job.force)),
        )  # fmt: skip
        return int(cur.lastrowid or 0)

    def enqueue(self, job: NewJob, now: float) -> int:
        with self._write() as con:
            return self._insert(con, job, now)

    def enqueue_chain(
        self, steps: Sequence[tuple[NewJob, int | None]], now: float, *,
        guard_folder: str | None = None, before_insert: Callable[[], None] | None = None,
    ) -> tuple[int, list[int]]:  # fmt: skip
        """Create a pipeline: every job of the chain in ONE transaction, or none at all.

        Each step is (job, index of the earlier step it waits for, or None). All jobs get one
        new pipeline id. With `guard_folder`, the chain is refused while any job of that
        folder waits, runs or awaits approval. `before_insert` runs after that check and
        inside the transaction (a caller writes the options file there: if it raises, nothing
        is queued, and a refused chain never touches the file).

        Returns:
            (pipeline id, job ids in step order).

        Raises:
            QueueDbError: empty chain, or a step waiting for one that is not before it.
            FolderBusyError: `guard_folder` has an active job.
        """
        if not steps:
            raise QueueDbError("Chuỗi job rỗng.")
        for i, (_, after) in enumerate(steps):
            if after is not None and not 0 <= after < i:
                raise QueueDbError(f"Bước {i + 1} của chuỗi phụ thuộc vào một bước không đứng "
                                   "trước nó.")  # fmt: skip
        with self._write() as con:
            if guard_folder is not None:
                busy = con.execute(
                    "SELECT id, pipeline_id FROM jobs WHERE project_dir = ? AND status IN "
                    "('queued', 'processing', 'awaiting_approval') ORDER BY id LIMIT 1",
                    (guard_folder,)).fetchone()  # fmt: skip
                if busy is not None:
                    raise FolderBusyError(int(busy["id"]), busy["pipeline_id"])
            if before_insert is not None:
                before_insert()
            pipeline = int(con.execute("SELECT COALESCE(MAX(pipeline_id), 0) + 1 FROM jobs"
                                       ).fetchone()[0])  # fmt: skip
            ids: list[int] = []
            for job, after in steps:
                link = {"pipeline_id": pipeline, "depends_on": ids[after] if after is not None
                        else None}  # fmt: skip
                ids.append(self._insert(con, job.model_copy(update=link), now))
        return pipeline, ids

    def jobs_of_pipeline(self, pipeline_id: int) -> list[Job]:
        """The jobs of one pipeline in creation order (empty for an unknown pipeline)."""
        with self._conn() as con:
            rows = con.execute("SELECT * FROM jobs WHERE pipeline_id = ? ORDER BY id",
                               (pipeline_id,)).fetchall()  # fmt: skip
        return [_job(r) for r in rows]

    def next_pipeline_id(self) -> int:
        with self._conn() as con:
            return int(con.execute("SELECT COALESCE(MAX(pipeline_id), 0) + 1 FROM jobs"
                                   ).fetchone()[0])  # fmt: skip

    def find_active(self, kind: str, project_dir: str, draft: str, final: bool) -> int | None:
        """Id of a waiting or running job for the same render, if any (no duplicates)."""
        with self._conn() as con:
            row = con.execute(
                "SELECT id FROM jobs WHERE kind = ? AND project_dir = ? AND draft = ? "
                "AND final = ? AND status IN ('queued', 'processing', 'awaiting_approval') "
                "ORDER BY id LIMIT 1",
                (kind, project_dir, draft, int(final))).fetchone()  # fmt: skip
        return int(row["id"]) if row else None

    def get(self, job_id: int) -> Job | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list_jobs(
        self, status: str | None = None, limit: int = 100, project_dir: str | None = None,
        kind: str | None = None,
    ) -> list[Job]:  # fmt: skip
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if project_dir is not None:
            where.append("project_dir = ?")
            args.append(project_dir)
        if kind is not None:
            where.append("kind = ?")
            args.append(kind)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "")
        with self._conn() as con:
            rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [_job(r) for r in rows]

    def counts(self) -> dict[str, int]:
        """Number of jobs per status (every status present, zero when none)."""
        out = dict.fromkeys(STATUSES, 0)
        with self._conn() as con:
            for row in con.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
                out[str(row["status"])] = int(row["n"])
        return out

    def claim(self, now: float, worker_pid: int) -> Job | None:
        """Take the next runnable job (highest priority, then oldest) or None."""
        with self._write() as con:
            row = con.execute(
                "SELECT j.id FROM jobs j LEFT JOIN jobs d ON d.id = j.depends_on "
                "WHERE j.status = 'queued' AND j.not_before <= ? AND (j.depends_on IS NULL OR "
                "(d.status = 'completed' AND (d.result IS NULL OR d.result = 'passed'))) "
                # the paid phase of a draft job (it spends money) needs an approval, always
                "AND (j.kind != 'draft' OR j.phase IS NULL OR j.phase = 'estimate' "
                "OR j.approved_vnd IS NOT NULL) "
                "ORDER BY j.priority DESC, j.id LIMIT 1", (now,)).fetchone()  # fmt: skip
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
        spent_vnd: float | None = None,
    ) -> None:  # fmt: skip
        with self._write() as con:
            self._must_be_processing(con, job_id, status)
            con.execute(
                "UPDATE jobs SET status = ?, result = ?, exit_code = ?, error_msg = ?, "
                "output_path = ?, finished_at = ?, worker_pid = NULL, child_pid = NULL, "
                "progress_pct = CASE WHEN ? = 'completed' THEN 100 ELSE progress_pct END, "
                "spent_vnd = COALESCE(?, spent_vnd) WHERE id = ?",
                (status, result, exit_code, error, output_path, now, status, spent_vnd, job_id),
            )

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
            if status in ("queued", "awaiting_approval"):
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
            self._reset_draft(con, job_id)
            return True

    @staticmethod
    def _reset_draft(con: sqlite3.Connection, job_id: int) -> None:
        """A draft job tried again starts at a fresh estimate: no old approval, no old numbers
        (the free stages already done stay; what was spent stays on record)."""
        row = con.execute("SELECT kind, stages_done FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or row["kind"] != "draft":
            return
        keep = [s for s in parse_stages(row["stages_done"]) if s != "estimate"]
        con.execute(
            "UPDATE jobs SET phase = 'estimate', approved_vnd = NULL, approved_at = NULL, "
            "cost_input_tokens = NULL, cost_worst_call_vnd = NULL, cost_cap_vnd = NULL, "
            "approval_expires_at = NULL, stages_done = ? WHERE id = ?",
            (join_stages(keep), job_id))  # fmt: skip

    # ─────────────── phase 6c: dependencies and stages ───────────────

    def cascade_blocked(self, now: float) -> list[int]:
        """Cancel jobs that can never run because the job they wait for failed, was cancelled
        or needs QC review — and, in turn, the jobs waiting for those. Returns their ids."""
        changed: list[int] = []
        with self._write() as con:
            while True:
                rows = con.execute(
                    "SELECT j.id, d.id AS did, d.status AS dstatus, d.result AS dresult "
                    "FROM jobs j JOIN jobs d ON d.id = j.depends_on "
                    "WHERE j.status IN ('queued', 'awaiting_approval')").fetchall()  # fmt: skip
                blocked = [
                    r for r in rows if dependency_state(r["dstatus"], r["dresult"]) == "blocked"
                ]
                if not blocked:
                    return changed
                for r in blocked:
                    msg = blocked_reason(int(r["did"]), str(r["dstatus"]), r["dresult"])
                    con.execute("UPDATE jobs SET status = 'cancelled', finished_at = ?, "
                                "error_msg = ? WHERE id = ?", (now, msg, r["id"]))  # fmt: skip
                    changed.append(int(r["id"]))

    def mark_stage_done(self, job_id: int, stage: str) -> None:
        """Record a finished stage of a running multi-stage job (a retry resumes after it)."""
        if stage not in ALL_STAGES:
            raise QueueDbError(f"Giai đoạn '{stage}' không hợp lệ.")
        with self._write() as con:
            row = con.execute("SELECT status, stages_done FROM jobs WHERE id = ?",
                              (job_id,)).fetchone()  # fmt: skip
            if row is None:
                raise QueueDbError(f"Không có job {job_id}.")
            if row["status"] != "processing":
                raise QueueDbError(f"Job {job_id} đang '{row['status']}', không phải processing.")
            done = parse_stages(row["stages_done"])
            if stage not in done:
                con.execute("UPDATE jobs SET stages_done = ? WHERE id = ?",
                            (join_stages([*done, stage]), job_id))  # fmt: skip

    # ─────────────── phase 6c-2: approving what costs money ───────────────

    def hold_for_approval(self, job_id: int, now: float, est: Estimate, ttl_sec: float) -> None:
        """A running draft job has its free stages done: park it with its estimate until a
        person approves. Any earlier approval is void (the estimate it covered is gone)."""
        with self._write() as con:
            self._must_be_processing(con, job_id, "awaiting_approval")
            con.execute(
                "UPDATE jobs SET status = 'awaiting_approval', phase = 'estimate', "
                "cost_input_tokens = ?, cost_worst_call_vnd = ?, cost_cap_vnd = ?, "
                "approved_vnd = NULL, approved_at = NULL, approval_expires_at = ?, "
                "worker_pid = NULL, child_pid = NULL, heartbeat_at = NULL, stage = 'cho_duyet', "
                "cancel_requested = 0 WHERE id = ?",
                (est.input_tokens, est.worst_call_vnd, est.cap_vnd, now + ttl_sec, job_id),
            )  # fmt: skip

    def approve(self, job_id: int, now: float, max_vnd: float,
                check: Callable[[Job, float, float], None] | None = None) -> None:  # fmt: skip
        """Record an approval and queue the job's paid phase.

        `check(job, spent_24h, reserved)` runs inside the same exclusive transaction that
        writes the approval, on the rows as they are at that moment (it raises to refuse).
        The daily budget has to be judged here: two approvals judged on numbers read before
        either was written would both pass and together cross the limit.
        """
        with self._write() as con:
            row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise QueueDbError(f"Không có job {job_id}.")
            if check is not None:
                spent, reserved = _spend_summary(con, now, 86_400.0)
                check(_job(row), spent, reserved)
            if row["status"] != "awaiting_approval":
                raise QueueDbError(f"Job {job_id} đang '{row['status']}', không phải chờ duyệt.")
            con.execute(
                "UPDATE jobs SET status = 'queued', phase = 'paid', approved_vnd = ?, "
                "approved_at = ?, approval_expires_at = NULL, not_before = ?, stage = NULL "
                "WHERE id = ?", (max_vnd, now, now, job_id))  # fmt: skip

    def expire_approvals(self, now: float) -> list[int]:
        """Cancel waiting jobs whose approval window closed; returns their ids."""
        with self._write() as con:
            rows = con.execute(
                "SELECT id FROM jobs WHERE status = 'awaiting_approval' "
                "AND approval_expires_at IS NOT NULL AND approval_expires_at < ?",
                (now,)).fetchall()  # fmt: skip
            msg = ("Hết hạn duyệt chi phí: không ai duyệt kịp nên không gọi API. Dùng "
                   "`queue retry` để ước tính lại.")  # fmt: skip
            for r in rows:
                con.execute("UPDATE jobs SET status = 'cancelled', finished_at = ?, "
                            "error_msg = ? WHERE id = ?", (now, msg, r["id"]))  # fmt: skip
        return [int(r["id"]) for r in rows]

    def spend_summary(self, now: float, window_sec: float = 86_400.0) -> tuple[float, float]:
        """(spent in the window, reserved) for the daily budget. Spent: paid draft jobs finished
        in the window, by the bill measured from ai_log — or, when nobody could measure it
        (a crash), the whole approval. Reserved: approvals of jobs that may still spend."""
        with self._conn() as con:
            return _spend_summary(con, now, window_sec)

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
                    msg = _crash_message(job)
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
