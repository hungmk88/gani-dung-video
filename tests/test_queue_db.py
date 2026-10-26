"""Unit tests: infrastructure/queue_db.py — SQLite job queue, claim lock, crash recovery (6a)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.domain.jobs import Estimate
from gani_dung_video.infrastructure.queue_db import (
    FolderBusyError,
    NewJob,
    QueueDb,
    QueueDbError,
)

T0 = 1_000_000.0


def new_job(draft: str = "A", **kw: Any) -> NewJob:
    base: dict[str, Any] = {"kind": "render", "project_dir": "E:/footage", "options_file": "o.md",
                               "draft": draft, "final": False, "no_asr": False,
                               "max_attempts": 2, "priority": 0}  # fmt: skip
    return NewJob(**{**base, **kw})


@pytest.fixture
def db(tmp_path: Path) -> QueueDb:
    return QueueDb(tmp_path / "jobs" / "queue.db")


def alive_none(pid: int) -> bool:
    return False


def alive_all(pid: int) -> bool:
    return True


# ─────────────── schema ───────────────


def test_schema_is_created_with_wal_and_a_version(db: QueueDb, tmp_path: Path) -> None:
    assert db.schema_version() == 5
    con = sqlite3.connect(tmp_path / "jobs" / "queue.db")
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert {"jobs", "job_events", "daemon"} <= tables


def test_reopening_keeps_the_data(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    jid = QueueDb(path).enqueue(new_job(), T0)
    assert QueueDb(path).get(jid) is not None


def test_a_corrupt_file_is_refused_with_an_actionable_message(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(QueueDbError, match=r"queue\.db"):
        QueueDb(path)


def test_a_database_from_a_newer_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    QueueDb(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    with pytest.raises(QueueDbError, match="mới hơn"):
        QueueDb(path)


# ─────────────── enqueue / read ───────────────


def test_enqueue_round_trip_keeps_every_field_verbatim(db: QueueDb) -> None:
    nasty = 'E:/it\'s a "folder"; DROP TABLE jobs;--'
    jid = db.enqueue(new_job(project_dir=nasty, final=True, no_asr=True), T0)
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.attempts, job.max_attempts) == ("queued", 0, 2)
    assert job.project_dir == nasty and job.final and job.no_asr
    assert job.created_at == T0 and job.started_at is None and job.result is None
    assert db.get(jid + 100) is None
    assert len(db.list_jobs()) == 1  # the table is still there


def test_list_is_newest_first_and_can_filter(db: QueueDb) -> None:
    a, b, c = (db.enqueue(new_job(x), T0 + i) for i, x in enumerate("ABC"))
    db.claim(T0 + 10, 111)  # A is now processing
    assert [j.id for j in db.list_jobs()] == [c, b, a]
    assert [j.id for j in db.list_jobs("queued")] == [c, b]
    assert [j.id for j in db.list_jobs(limit=2)] == [c, b]


# ─────────────── claim ───────────────


def test_claim_takes_the_highest_priority_then_the_oldest(db: QueueDb) -> None:
    low = db.enqueue(new_job("A"), T0)
    later = db.enqueue(new_job("B"), T0 + 1)
    urgent = db.enqueue(new_job("C", priority=5), T0 + 2)
    order = [db.claim(T0 + 10, 42) for _ in range(3)]
    assert [j.id for j in order if j] == [urgent, low, later]


def test_claim_marks_the_job_as_processing(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    job = db.claim(T0 + 5, 4242)
    assert job is not None and job.id == jid
    assert (job.status, job.attempts, job.worker_pid) == ("processing", 1, 4242)
    assert job.started_at == T0 + 5 and job.heartbeat_at == T0 + 5 and job.stage == "khoi_dong"
    assert db.claim(T0 + 6, 4242) is None  # nothing left


def test_claim_on_an_empty_queue_returns_nothing(db: QueueDb) -> None:
    assert db.claim(T0, 1) is None


def test_a_job_waiting_out_its_retry_delay_is_not_claimed_yet(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    db.requeue(jid, not_before=T0 + 30, error="lỗi", consume_attempt=True)
    assert db.claim(T0 + 29, 1) is None
    again = db.claim(T0 + 30, 1)
    assert again is not None and again.attempts == 2


def test_two_workers_never_claim_the_same_job(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    first = QueueDb(path)
    ids = {first.enqueue(new_job(str(i)), T0) for i in range(6)}
    got: list[int] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        mine = QueueDb(path)
        while (j := mine.claim(T0 + 1, 1000 + n)) is not None:
            with lock:
                got.append(j.id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(got) == sorted(ids)  # every job exactly once


# ─────────────── progress, events ───────────────


def test_progress_updates_the_heartbeat(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    db.set_child(jid, 777)
    db.progress(jid, T0 + 9, "dung", 42.5)
    job = db.get(jid)
    assert job is not None
    assert (job.child_pid, job.stage, job.progress_pct, job.heartbeat_at) == (777, "dung", 42.5,
                                                                             T0 + 9)  # fmt: skip


def test_events_are_read_in_order_after_a_cursor(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    for i in range(3):
        db.add_event(jid, T0 + i, "info", f"dòng {i}")
    got = db.events(jid)
    assert [e.text for e in got] == ["dòng 0", "dòng 1", "dòng 2"]
    assert [e.text for e in db.events(jid, after_id=got[0].id)] == ["dòng 1", "dòng 2"]


def test_only_the_last_events_of_a_job_are_kept(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    other = db.enqueue(new_job("B"), T0)
    db.add_event(other, T0, "info", "của job khác")
    for i in range(10):
        db.add_event(jid, T0 + i, "info", f"dòng {i}", keep=4)
    assert [e.text for e in db.events(jid)] == ["dòng 6", "dòng 7", "dòng 8", "dòng 9"]
    assert len(db.events(other)) == 1


# ─────────────── finishing ───────────────


def test_finish_records_the_outcome(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    db.finish(jid, T0 + 60, "completed", "passed", 0, None, "E:/x/video_A.mp4")
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.result, job.exit_code, job.progress_pct) == ("completed", "passed", 0,
                                                                        100.0)  # fmt: skip
    assert job.finished_at == T0 + 60 and job.output_path == "E:/x/video_A.mp4"
    assert job.worker_pid is None and job.child_pid is None


def test_a_failed_job_keeps_its_progress_and_error(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    db.progress(jid, T0 + 1, "dung", 40.0)
    db.finish(jid, T0 + 2, "failed", None, 2, "ffmpeg lỗi", None)
    job = db.get(jid)
    assert job is not None and job.progress_pct == 40.0 and job.error_msg == "ffmpeg lỗi"


def test_a_job_that_is_not_processing_cannot_be_finished(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    with pytest.raises(QueueDbError, match="processing"):
        db.finish(jid, T0, "completed", "passed", 0, None, None)


def test_requeue_can_give_the_attempt_back(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    db.set_child(jid, 5)
    db.requeue(jid, not_before=T0, error="dừng daemon", consume_attempt=False)
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.attempts, job.worker_pid, job.child_pid) == ("queued", 0, None, None)
    assert job.error_msg == "dừng daemon" and job.started_at is None


# ─────────────── cancel and manual retry ───────────────


def test_cancelling_a_waiting_job_is_immediate(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    assert db.request_cancel(jid, T0 + 1) == "cancelled"
    job = db.get(jid)
    assert job is not None and job.status == "cancelled" and job.finished_at == T0 + 1


def test_cancelling_a_running_job_only_raises_a_flag(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    assert not db.cancel_requested(jid)
    assert db.request_cancel(jid, T0 + 1) == "processing"
    assert db.cancel_requested(jid)


def test_cancelling_a_finished_job_changes_nothing(db: QueueDb) -> None:
    jid = db.enqueue(new_job(), T0)
    db.claim(T0, 1)
    db.finish(jid, T0 + 1, "completed", "passed", 0, None, None)
    assert db.request_cancel(jid, T0 + 2) == "completed"
    assert db.request_cancel(999, T0) == "missing"


def test_manual_retry_is_for_failed_cancelled_and_qc_failed_jobs_only(db: QueueDb) -> None:
    failed = db.enqueue(new_job("A"), T0)
    passed = db.enqueue(new_job("B"), T0)
    qc_bad = db.enqueue(new_job("C"), T0)
    for _ in (failed, passed, qc_bad):
        db.claim(T0, 1)
    db.finish(failed, T0, "failed", None, 2, "lỗi", None)
    db.finish(passed, T0, "completed", "passed", 0, None, None)
    db.finish(qc_bad, T0, "completed", "qc_failed", 1, None, None)
    assert db.retry_manual(failed) and db.retry_manual(qc_bad)
    assert not db.retry_manual(passed) and not db.retry_manual(999)
    job = db.get(failed)
    assert job is not None
    assert (job.status, job.attempts, job.error_msg, job.result) == ("queued", 0, None, None)


# ─────────────── recovery after a crash ───────────────


def running(db: QueueDb, draft: str = "A", pid: int = 100, at: float = T0, **kw: object) -> int:
    jid = db.enqueue(new_job(draft, **kw), at)
    db.claim(at, pid)
    return jid


def test_a_job_whose_worker_died_goes_back_to_the_queue(db: QueueDb) -> None:
    jid = running(db)
    out = db.recover_orphans(T0 + 500, alive_none, stale_sec=120, retry_delay=30)
    assert [(r.job_id, r.action) for r in out] == [(jid, "requeued")]
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.attempts, job.worker_pid) == ("queued", 1, None)  # attempt stays used
    assert "sập" in (job.error_msg or "") and db.claim(T0 + 510, 1) is None  # waits 30 s
    assert db.claim(T0 + 531, 1) is not None


def test_a_job_out_of_attempts_is_failed_instead_of_requeued(db: QueueDb) -> None:
    jid = running(db, max_attempts=1)
    out = db.recover_orphans(T0 + 500, alive_none, stale_sec=120, retry_delay=30)
    assert [(r.job_id, r.action) for r in out] == [(jid, "failed")]
    job = db.get(jid)
    assert job is not None and job.status == "failed" and job.finished_at == T0 + 500


def test_draft_jobs_are_never_requeued_after_a_crash(db: QueueDb) -> None:
    jid = running(db, kind="draft", max_attempts=5)
    [r] = db.recover_orphans(T0 + 500, alive_none, stale_sec=120, retry_delay=30)
    assert (r.job_id, r.action) == (jid, "failed")
    job = db.get(jid)
    assert job is not None and "queue retry" in (job.error_msg or "")  # the message fits the phase


def test_a_live_worker_with_a_fresh_heartbeat_is_left_alone(db: QueueDb) -> None:
    running(db)
    assert db.recover_orphans(T0 + 60, alive_all, stale_sec=120, retry_delay=30) == []


def test_a_live_pid_with_a_stale_heartbeat_is_still_recovered(db: QueueDb) -> None:
    # the pid may have been reused by an unrelated process after a reboot
    jid = running(db)
    out = db.recover_orphans(T0 + 500, alive_all, stale_sec=120, retry_delay=30)
    assert [r.job_id for r in out] == [jid]


# ─────────────── daemon registration and stop requests ───────────────


def test_only_one_live_daemon_may_run(db: QueueDb) -> None:
    assert db.register_daemon(10, T0, stale_sec=15, alive=alive_all)
    assert not db.register_daemon(11, T0 + 5, stale_sec=15, alive=alive_all)


def test_a_dead_or_silent_daemon_can_be_replaced(db: QueueDb) -> None:
    assert db.register_daemon(10, T0, stale_sec=15, alive=alive_all)
    assert db.register_daemon(11, T0 + 5, stale_sec=15, alive=alive_none)  # pid gone
    assert db.register_daemon(12, T0 + 100, stale_sec=15, alive=alive_all)  # heartbeat old


def test_unregistering_frees_the_slot_but_only_for_the_owner(db: QueueDb) -> None:
    db.register_daemon(10, T0, stale_sec=15, alive=alive_all)
    db.unregister_daemon(99)  # not the owner: ignored
    assert not db.register_daemon(11, T0 + 1, stale_sec=15, alive=alive_all)
    db.unregister_daemon(10)
    assert db.register_daemon(11, T0 + 2, stale_sec=15, alive=alive_all)


def test_daemon_status_reports_online_only_while_the_heartbeat_is_fresh(db: QueueDb) -> None:
    assert not db.daemon_status(T0, 15).online
    db.register_daemon(10, T0, stale_sec=15, alive=alive_all)
    assert db.daemon_status(T0 + 10, 15).online
    db.daemon_heartbeat(10, T0 + 12)
    assert db.daemon_status(T0 + 20, 15).online
    st = db.daemon_status(T0 + 40, 15)
    assert not st.online and st.pid == 10


def test_stop_requests_are_stored_and_cleared_on_the_next_start(db: QueueDb) -> None:
    db.register_daemon(10, T0, stale_sec=15, alive=alive_all)
    assert db.stop_request() is None
    db.set_stop_request("graceful")
    assert db.stop_request() == "graceful"
    db.set_stop_request("now")
    assert db.stop_request() == "now"
    db.unregister_daemon(10)
    db.register_daemon(11, T0 + 1, stale_sec=15, alive=alive_all)
    assert db.stop_request() is None


def test_an_invalid_stop_mode_is_refused(db: QueueDb) -> None:
    with pytest.raises(QueueDbError):
        db.set_stop_request("whenever")


# ─────────────── backup ───────────────


def test_backup_is_a_complete_readable_copy(db: QueueDb, tmp_path: Path) -> None:
    jid = db.enqueue(new_job(), T0)
    dest = tmp_path / "queue.db.bak"
    db.backup(dest)
    copy = QueueDb(dest)
    assert copy.get(jid) is not None


# ─────────────── duplicates ───────────────


def test_find_active_sees_waiting_and_running_jobs_only(db: QueueDb) -> None:
    jid = db.enqueue(new_job("A"), T0)
    assert db.find_active("render", "E:/footage", "A", False) == jid
    assert db.find_active("render", "E:/footage", "A", True) is None  # other size
    assert db.find_active("render", "E:/footage", "B", False) is None
    assert db.find_active("render", "E:/other", "A", False) is None
    db.claim(T0, 1)
    assert db.find_active("render", "E:/footage", "A", False) == jid  # running counts
    db.finish(jid, T0 + 1, "completed", "passed", 0, None, None)
    assert db.find_active("render", "E:/footage", "A", False) is None


# ─────────────── counts, project filter (6b) ───────────────


def test_counts_by_status_include_every_status(db: QueueDb) -> None:
    a = db.enqueue(new_job("A"), T0)
    db.enqueue(new_job("B"), T0)
    db.claim(T0, 1)
    db.finish(a, T0 + 1, "failed", None, 2, "lỗi", None)
    assert db.counts() == {"queued": 1, "processing": 0, "awaiting_approval": 0, "completed": 0,
                           "failed": 1, "cancelled": 0}  # fmt: skip


def test_list_can_be_limited_to_one_project(db: QueueDb) -> None:
    mine = db.enqueue(new_job("A", project_dir="E:/one"), T0)
    db.enqueue(new_job("A", project_dir="E:/two"), T0)
    assert [j.id for j in db.list_jobs(project_dir="E:/one")] == [mine]
    assert db.list_jobs(project_dir="E:/none") == []


# ─────────────── 6c-1: schema v2 migration ───────────────

_V1 = """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('render', 'draft', 'analyze')),
  project_dir TEXT NOT NULL, options_file TEXT NOT NULL, draft TEXT NOT NULL,
  final INTEGER NOT NULL DEFAULT 0, no_asr INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
  result TEXT CHECK (result IN ('passed', 'qc_failed')),
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
  priority INTEGER NOT NULL DEFAULT 0, not_before REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, worker_pid INTEGER,
  child_pid INTEGER, heartbeat_at REAL, stage TEXT, progress_pct REAL NOT NULL DEFAULT 0,
  output_path TEXT, error_msg TEXT, exit_code INTEGER,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_jobs_claim ON jobs (status, priority DESC, id);
CREATE TABLE job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
  ts REAL NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX idx_events_job ON job_events (job_id, id);
CREATE TABLE daemon (
  id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER NOT NULL, started_at REAL NOT NULL,
  heartbeat_at REAL NOT NULL, stop_request TEXT CHECK (stop_request IN ('graceful', 'now'))
);
"""


def make_v1(path: Path) -> None:
    """A queue.db exactly as phase 6a/6b wrote it, with two jobs and three events."""
    con = sqlite3.connect(path)
    for stmt in _V1.split(";\n"):
        if stmt.strip():
            con.execute(stmt)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute(
        "INSERT INTO jobs (kind, project_dir, options_file, draft, final, status, result, "
        "attempts, max_attempts, created_at, started_at, finished_at, progress_pct, output_path, "
        "exit_code) VALUES ('render', 'E:/a', 'o', 'A', 1, 'completed', 'passed', 1, 2, 10, 11, "
        "12, 100, 'E:/a/v.mp4', 0)")  # fmt: skip
    con.execute(
        "INSERT INTO jobs (kind, project_dir, options_file, draft, status, attempts, max_attempts,"
        " created_at, started_at, worker_pid, heartbeat_at, stage) VALUES ('render', 'E:/b', 'o', "
        "'B', 'processing', 1, 2, 20, 21, 99, 22, 'dung')")  # fmt: skip
    for i in range(3):
        con.execute("INSERT INTO job_events (job_id, ts, level, text) VALUES (1, ?, 'info', ?)",
                    (30 + i, f"dòng {i}"))  # fmt: skip
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()


def test_a_v1_database_is_upgraded_to_the_newest_schema_with_everything_kept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    db = QueueDb(path)
    assert db.schema_version() == 5
    done, running = db.get(1), db.get(2)
    assert done is not None and running is not None
    assert (done.status, done.result, done.final, done.output_path) == ("completed", "passed", True,
                                                                       "E:/a/v.mp4")  # fmt: skip
    assert (running.status, running.worker_pid, running.stage) == ("processing", 99, "dung")
    assert [e.text for e in db.events(1)] == ["dòng 0", "dòng 1", "dòng 2"]  # not cascaded away


def test_upgraded_jobs_get_neutral_values_for_the_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    job = QueueDb(path).get(1)
    assert job is not None
    assert (job.pipeline_id, job.depends_on, job.phase, job.stages_done, job.force) == (
        None, None, None, [], False)  # fmt: skip
    assert job.approved_vnd is None and job.spent_vnd is None and job.cost_cap_vnd is None


def test_the_upgrade_keeps_a_copy_of_the_old_file_and_touches_it_only_once(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    QueueDb(path)
    backup = tmp_path / "queue.db.v1.bak"
    assert backup.is_file()
    con = sqlite3.connect(backup)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    finally:
        con.close()
    stamp = backup.stat().st_mtime_ns
    time.sleep(0.05)
    QueueDb(path)  # already v2: nothing to do, the old copy is not replaced
    assert backup.stat().st_mtime_ns == stamp


def test_ids_keep_counting_after_the_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    db = QueueDb(path)
    assert db.enqueue(new_job("C"), T0) == 3


def test_the_upgraded_table_knows_the_new_status_and_refuses_bad_ones(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    QueueDb(path)
    con = sqlite3.connect(path)
    try:
        con.execute("UPDATE jobs SET status = 'awaiting_approval' WHERE id = 2")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE jobs SET status = 'sleeping' WHERE id = 2")
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


def test_two_processes_opening_an_old_file_upgrade_it_once(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    errors: list[BaseException] = []

    def opener() -> None:
        try:
            QueueDb(path)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=opener) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)
    assert errors == []
    db = QueueDb(path)
    assert db.schema_version() == 5 and len(db.list_jobs()) == 2


def test_a_fresh_database_starts_at_the_newest_version_without_any_backup(tmp_path: Path) -> None:
    QueueDb(tmp_path / "queue.db")
    assert not (tmp_path / "queue.db.v1.bak").exists()


# ─────────────── 6c-1: dependencies and priorities ───────────────


def analyze_job(**kw: object) -> NewJob:
    return new_job("", **{"kind": "analyze", "priority": None, **kw})


def test_a_job_waits_for_the_job_it_depends_on(db: QueueDb) -> None:
    first = db.enqueue(analyze_job(), T0)
    second = db.enqueue(new_job("A", depends_on=first, priority=None), T0)
    got = db.claim(T0 + 1, 1)
    assert got is not None and got.id == first
    assert db.claim(T0 + 2, 1) is None  # the analysis is running: the render must wait
    db.finish(first, T0 + 3, "completed", "passed", 0, None, None)
    nxt = db.claim(T0 + 4, 1)
    assert nxt is not None and nxt.id == second


def test_a_job_is_never_claimed_after_its_dependency_failed(db: QueueDb) -> None:
    first = db.enqueue(analyze_job(), T0)
    db.enqueue(new_job("A", depends_on=first, priority=None), T0)
    db.claim(T0, 1)
    db.finish(first, T0 + 1, "failed", None, 2, "lỗi", None)
    assert db.claim(T0 + 2, 1) is None


def test_a_dependency_that_does_not_exist_is_refused(db: QueueDb) -> None:
    with pytest.raises(QueueDbError, match="99"):
        db.enqueue(new_job("A", depends_on=99), T0)


def test_priority_follows_the_kind_unless_given(db: QueueDb) -> None:
    slow = db.enqueue(analyze_job(), T0)  # queued first, but least urgent
    paid = db.enqueue(new_job("A", kind="draft", priority=None), T0 + 1)
    quick = db.enqueue(new_job("B", priority=None), T0 + 2)
    order = [db.claim(T0 + 10, 1) for _ in range(3)]
    assert [j.id for j in order if j] == [quick, paid, slow]
    assert [j.priority for j in order if j] == [30, 20, 10]


def test_an_explicit_priority_still_wins(db: QueueDb) -> None:
    a = db.enqueue(analyze_job(priority=99), T0)
    db.enqueue(new_job("B", priority=None), T0)
    got = db.claim(T0, 1)
    assert got is not None and got.id == a


# ─────────────── 6c-1: cancelling what can never run ───────────────


def test_cascade_cancels_a_whole_chain_behind_a_failed_job(db: QueueDb) -> None:
    a = db.enqueue(analyze_job(), T0)
    b = db.enqueue(new_job("A", kind="draft", depends_on=a, priority=None), T0)
    c = db.enqueue(new_job("A", depends_on=b, priority=None), T0)
    db.claim(T0, 1)
    db.finish(a, T0 + 1, "failed", None, 2, "lỗi", None)
    assert sorted(db.cascade_blocked(T0 + 2)) == [b, c]
    for jid in (b, c):
        job = db.get(jid)
        assert job is not None and job.status == "cancelled" and job.finished_at == T0 + 2
        assert "Bị chặn" in (job.error_msg or "")
    assert "#1" in (db.get(b).error_msg or "")  # type: ignore[union-attr]
    assert db.cascade_blocked(T0 + 3) == []  # nothing left to do


def test_cascade_leaves_jobs_whose_dependency_is_still_to_come(db: QueueDb) -> None:
    a = db.enqueue(analyze_job(), T0)
    b = db.enqueue(new_job("A", depends_on=a, priority=None), T0)
    assert db.cascade_blocked(T0) == []
    db.claim(T0, 1)  # the analysis is running
    assert db.cascade_blocked(T0) == []
    job = db.get(b)
    assert job is not None and job.status == "queued"


def test_cascade_also_covers_a_dependency_that_was_cancelled_or_needs_qc_review(
    db: QueueDb,
) -> None:
    a = db.enqueue(analyze_job(), T0)
    b = db.enqueue(new_job("A", depends_on=a, priority=None), T0)
    db.request_cancel(a, T0 + 1)
    assert db.cascade_blocked(T0 + 2) == [b]
    assert "hủy" in (db.get(b).error_msg or "")  # type: ignore[union-attr]


def test_cascade_never_touches_a_job_that_already_started(db: QueueDb) -> None:
    a = db.enqueue(analyze_job(), T0)
    b = db.enqueue(new_job("A", depends_on=a, priority=None), T0)
    db.claim(T0, 1)
    db.finish(a, T0 + 1, "completed", "passed", 0, None, None)
    got = db.claim(T0 + 2, 1)
    assert got is not None and got.id == b
    assert db.cascade_blocked(T0 + 3) == []


# ─────────────── 6c-1: resumable stages, force, pipelines, kinds ───────────────


def test_finished_stages_are_recorded_once_and_in_order(db: QueueDb) -> None:
    jid = db.enqueue(analyze_job(), T0)
    db.claim(T0, 1)
    db.mark_stage_done(jid, "inventory")
    db.mark_stage_done(jid, "transcribe")
    db.mark_stage_done(jid, "transcribe")  # again: no duplicate
    job = db.get(jid)
    assert job is not None and job.stages_done == ["inventory", "transcribe"]


def test_stages_survive_a_retry_a_stop_and_a_manual_retry(db: QueueDb) -> None:
    jid = db.enqueue(analyze_job(), T0)
    db.claim(T0, 1)
    db.mark_stage_done(jid, "inventory")
    db.requeue(jid, T0, "lỗi", consume_attempt=True)  # automatic retry
    assert db.get(jid).stages_done == ["inventory"]  # type: ignore[union-attr]
    db.claim(T0 + 1, 1)
    db.requeue(jid, T0, "dừng", consume_attempt=False)  # a stop request
    assert db.get(jid).stages_done == ["inventory"]  # type: ignore[union-attr]
    db.claim(T0 + 2, 1)
    db.finish(jid, T0 + 3, "failed", None, 2, "lỗi", None)
    assert db.retry_manual(jid)  # a person retries: resume, do not redo
    assert db.get(jid).stages_done == ["inventory"]  # type: ignore[union-attr]


def test_marking_a_stage_of_a_job_that_is_not_running_is_refused(db: QueueDb) -> None:
    jid = db.enqueue(analyze_job(), T0)
    with pytest.raises(QueueDbError, match="processing"):
        db.mark_stage_done(jid, "inventory")
    db.claim(T0, 1)
    with pytest.raises(QueueDbError, match="bogus"):
        db.mark_stage_done(jid, "bogus")


def test_the_force_flag_is_stored(db: QueueDb) -> None:
    jid = db.enqueue(analyze_job(force=True), T0)
    other = db.enqueue(analyze_job(), T0)
    assert db.get(jid).force and not db.get(other).force  # type: ignore[union-attr]


def test_pipeline_ids_count_up(db: QueueDb) -> None:
    assert db.next_pipeline_id() == 1
    db.enqueue(analyze_job(pipeline_id=1), T0)
    assert db.next_pipeline_id() == 2


def test_list_can_be_limited_to_one_kind(db: QueueDb) -> None:
    a = db.enqueue(analyze_job(), T0)
    r = db.enqueue(new_job("A"), T0)
    assert [j.id for j in db.list_jobs(kind="analyze")] == [a]
    assert [j.id for j in db.list_jobs(kind="render")] == [r]
    assert {j.id for j in db.list_jobs()} == {a, r}


def test_find_active_works_for_an_analysis_of_a_project(db: QueueDb) -> None:
    a = db.enqueue(analyze_job(), T0)
    assert db.find_active("analyze", "E:/footage", "", False) == a
    assert db.find_active("render", "E:/footage", "", False) is None


def test_a_crashed_analysis_is_requeued_but_keeps_what_it_finished(db: QueueDb) -> None:
    jid = db.enqueue(analyze_job(), T0)
    db.claim(T0, 1)
    db.mark_stage_done(jid, "inventory")
    [r] = db.recover_orphans(T0 + 500, alive_none, stale_sec=120, retry_delay=30)
    assert (r.job_id, r.action) == (jid, "requeued")
    assert db.get(jid).stages_done == ["inventory"]  # type: ignore[union-attr]


# ─────────────── 6c-2: schema v3 (approval deadline) ───────────────

# the jobs table exactly as phase 6c-1 created it (v2): no approval_expires_at yet
_V2_JOBS = """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('render', 'draft', 'analyze')),
  project_dir TEXT NOT NULL, options_file TEXT NOT NULL, draft TEXT NOT NULL,
  final INTEGER NOT NULL DEFAULT 0, no_asr INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing',
    'awaiting_approval', 'completed', 'failed', 'cancelled')),
  result TEXT CHECK (result IN ('passed', 'qc_failed')),
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
  priority INTEGER NOT NULL DEFAULT 0, not_before REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, worker_pid INTEGER,
  child_pid INTEGER, heartbeat_at REAL, stage TEXT, progress_pct REAL NOT NULL DEFAULT 0,
  output_path TEXT, error_msg TEXT, exit_code INTEGER,
  cancel_requested INTEGER NOT NULL DEFAULT 0, pipeline_id INTEGER,
  depends_on INTEGER REFERENCES jobs (id) ON DELETE SET NULL,
  phase TEXT CHECK (phase IN ('estimate', 'paid')), stages_done TEXT NOT NULL DEFAULT '',
  force INTEGER NOT NULL DEFAULT 0, cost_input_tokens INTEGER, cost_worst_call_vnd REAL,
  cost_cap_vnd REAL, approved_vnd REAL, approved_at REAL, spent_vnd REAL
)
"""


def make_v2(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(_V2_JOBS)
    for stmt in _V1.split(";\n")[1:]:  # the v1 script minus its jobs table: same other tables
        if stmt.strip() and "CREATE TABLE jobs" not in stmt and "idx_jobs_claim" not in stmt:
            con.execute(stmt)
    con.execute("CREATE INDEX idx_jobs_claim ON jobs (status, priority DESC, id)")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute(
        "INSERT INTO jobs (kind, project_dir, options_file, draft, status, attempts, max_attempts,"
        " created_at, phase, stages_done, cost_worst_call_vnd, approved_vnd) VALUES ('draft', "
        "'E:/a', 'o', '', 'awaiting_approval', 1, 1, 10, 'estimate', 'plan,pick', 4207.3, NULL)"
    )
    con.execute(
        "INSERT INTO job_events (job_id, ts, level, text) VALUES (1, 11, 'info', 'xin chào')"
    )
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()


def test_a_v2_database_gets_the_deadline_column_and_keeps_everything(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v2(path)
    db = QueueDb(path)
    assert db.schema_version() == 5
    job = db.get(1)
    assert job is not None
    assert (job.status, job.phase, job.stages_done, job.cost_worst_call_vnd) == (
        "awaiting_approval", "estimate", ["plan", "pick"], 4207.3)  # fmt: skip
    assert job.approval_expires_at is None
    assert [e.text for e in db.events(1)] == ["xin chào"]
    con = sqlite3.connect(path)
    try:
        con.execute("UPDATE jobs SET approval_expires_at = 123.5 WHERE id = 1")
        cols = [r[1] for r in con.execute("PRAGMA table_info(jobs)")]
        assert cols.count("approval_expires_at") == 1
    finally:
        con.close()


def test_the_v2_upgrade_keeps_its_own_backup_and_leaves_the_v1_one_alone(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v2(path)
    QueueDb(path)
    backup = tmp_path / "queue.db.v2.bak"
    assert backup.is_file()
    con = sqlite3.connect(backup)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        con.close()
    assert not (tmp_path / "queue.db.v1.bak").exists()


def test_a_v1_database_goes_straight_to_v3_without_a_v2_backup(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    QueueDb(path)
    assert (tmp_path / "queue.db.v1.bak").is_file()
    assert not (tmp_path / "queue.db.v2.bak").exists()
    con = sqlite3.connect(path)
    try:
        assert "approval_expires_at" in [r[1] for r in con.execute("PRAGMA table_info(jobs)")]
    finally:
        con.close()


def test_two_processes_opening_a_v2_file_upgrade_it_once(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v2(path)
    errors: list[BaseException] = []

    def opener() -> None:
        try:
            QueueDb(path)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=opener) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)
    assert errors == [] and QueueDb(path).schema_version() == 5


# ─────────────── 16/10/2026: schema v4 (platform of a render) ───────────────


def make_v3(path: Path) -> None:
    make_v2(path)
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE jobs ADD COLUMN approval_expires_at REAL")
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()


def columns(path: Path) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(jobs)")]
    finally:
        con.close()


def test_a_v3_database_gets_the_platform_column_and_old_rows_read_no_platform(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queue.db"
    make_v3(path)
    db = QueueDb(path)
    assert db.schema_version() == 5 and columns(path).count("platform") == 1
    job = db.get(1)
    assert job is not None and job.platform == "" and job.status == "awaiting_approval"
    assert (tmp_path / "queue.db.v3.bak").is_file()


def test_a_v2_database_passes_through_v3_and_gets_both_new_columns(tmp_path: Path) -> None:
    # Before 14/10/2026 the 2 → 3 step stamped the newest version itself: a v2 file would have
    # skipped every later step. Each step now stamps its own number.
    path = tmp_path / "queue.db"
    make_v2(path)
    QueueDb(path)
    cols = columns(path)
    assert "approval_expires_at" in cols and "platform" in cols


def test_a_new_database_and_a_v1_one_have_the_platform_column(tmp_path: Path) -> None:
    QueueDb(tmp_path / "new.db")
    assert "platform" in columns(tmp_path / "new.db")
    make_v1(tmp_path / "old.db")
    QueueDb(tmp_path / "old.db")
    assert "platform" in columns(tmp_path / "old.db")


def test_the_tiktok_and_the_facebook_render_of_one_draft_are_not_duplicates(
    db: QueueDb,
) -> None:
    def render(platform: str) -> NewJob:
        return NewJob(kind="render", project_dir="E:/a", options_file="o", draft="A",
                      final=True, platform=platform)  # fmt: skip

    tiktok = db.enqueue(render("tiktok"), 1.0)
    assert db.find_active("render", "E:/a", "A", True, "tiktok") == tiktok
    assert db.find_active("render", "E:/a", "A", True, "facebook") is None
    assert db.find_active("render", "E:/a", "A", True) is None  # "" = no platform
    facebook = db.enqueue(render("facebook"), 2.0)
    assert {db.get(tiktok).platform, db.get(facebook).platform} == {"tiktok", "facebook"}  # type: ignore[union-attr]


# ─────────────── 6c-2: holding a draft job for approval ───────────────

TTL = 86_400.0


def estimate(**kw: object) -> Estimate:
    base: dict[str, object] = {"model": "claude-sonnet-5-5", "input_tokens": 2400,
                               "input_only_vnd": 122.4, "worst_call_vnd": 4207.3,
                               "cap_vnd": 20_000.0, "max_attempts": 3}  # fmt: skip
    return Estimate(**{**base, **kw})


def draft_job(**kw: object) -> NewJob:
    return new_job("", **{"kind": "draft", "priority": None, "max_attempts": 1, **kw})


def held(db: QueueDb, at: float = T0, **kw: object) -> int:
    """A draft job that ran its free stages and now waits for approval."""
    jid = db.enqueue(draft_job(**kw), at)
    db.claim(at, 1)
    db.hold_for_approval(jid, at + 5, estimate(), TTL)
    return jid


def test_a_draft_job_is_held_with_its_estimate_and_a_deadline(db: QueueDb) -> None:
    jid = db.enqueue(draft_job(), T0)
    db.claim(T0, 1)
    db.set_child(jid, 55)
    db.hold_for_approval(jid, T0 + 10, estimate(), TTL)
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.phase, job.worker_pid, job.child_pid) == (
        "awaiting_approval",
        "estimate",
        None,
        None,
    )
    assert (job.cost_input_tokens, job.cost_worst_call_vnd, job.cost_cap_vnd) == (
        2400,
        4207.3,
        20_000.0,
    )
    assert job.approval_expires_at == T0 + 10 + TTL and job.approved_vnd is None
    assert job.finished_at is None  # it is not finished, only waiting


def test_only_a_running_job_can_be_held(db: QueueDb) -> None:
    jid = db.enqueue(draft_job(), T0)
    with pytest.raises(QueueDbError, match="processing"):
        db.hold_for_approval(jid, T0, estimate(), TTL)


def test_a_held_job_frees_the_worker_for_other_jobs(db: QueueDb) -> None:
    held(db)
    other = db.enqueue(new_job("A", priority=None), T0)
    got = db.claim(T0 + 20, 1)
    assert got is not None and got.id == other  # the waiting draft is not claimable


def test_approving_makes_the_job_claimable_for_its_paid_phase(db: QueueDb) -> None:
    jid = held(db)
    db.approve(jid, T0 + 100, 6000.0)
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.phase, job.approved_vnd, job.approved_at) == (
        "queued",
        "paid",
        6000.0,
        T0 + 100,
    )
    assert job.approval_expires_at is None
    got = db.claim(T0 + 101, 1)
    assert got is not None and got.id == jid and got.phase == "paid"


def test_only_a_waiting_job_can_be_approved(db: QueueDb) -> None:
    jid = db.enqueue(draft_job(), T0)
    with pytest.raises(QueueDbError, match="chờ duyệt"):
        db.approve(jid, T0, 6000.0)
    with pytest.raises(QueueDbError, match="99"):
        db.approve(99, T0, 6000.0)


def test_a_paid_job_without_an_approval_can_never_be_claimed(db: QueueDb) -> None:
    """The database refuses even if code above it is wrong."""
    jid = db.enqueue(draft_job(), T0)
    con = sqlite3.connect(db.path)
    try:
        con.execute("UPDATE jobs SET phase = 'paid' WHERE id = ?", (jid,))
        con.commit()
    finally:
        con.close()
    assert db.claim(T0 + 1, 1) is None
    con = sqlite3.connect(db.path)
    try:
        con.execute("UPDATE jobs SET approved_vnd = 5000 WHERE id = ?", (jid,))
        con.commit()
    finally:
        con.close()
    got = db.claim(T0 + 2, 1)
    assert got is not None and got.id == jid


def test_the_estimate_phase_of_a_draft_job_is_claimable_without_approval(db: QueueDb) -> None:
    jid = db.enqueue(draft_job(), T0)
    got = db.claim(T0, 1)
    assert got is not None and got.id == jid and got.priority == 20


def test_an_estimate_that_changed_puts_an_approved_job_back_on_hold(db: QueueDb) -> None:
    jid = held(db)
    db.approve(jid, T0 + 10, 6000.0)
    db.claim(T0 + 11, 1)  # the paid phase starts and finds the estimate changed
    db.hold_for_approval(jid, T0 + 12, estimate(worst_call_vnd=5100.0, input_tokens=2900), TTL)
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.phase, job.approved_vnd, job.approved_at) == (
        "awaiting_approval",
        "estimate",
        None,
        None,
    )  # the old approval is void
    assert job.cost_worst_call_vnd == 5100.0 and job.cost_input_tokens == 2900


# ─────────────── 6c-2: the approval window ───────────────


def test_a_waiting_job_expires_after_its_window(db: QueueDb) -> None:
    jid = held(db)
    fresh = held(db, T0 + 50_000)
    assert db.expire_approvals(T0 + TTL) == []  # not yet (window ends at held-time + TTL)
    assert db.expire_approvals(T0 + 5 + TTL + 1) == [jid]
    job = db.get(jid)
    assert job is not None and job.status == "cancelled" and job.finished_at == T0 + 5 + TTL + 1
    assert "hết hạn" in (job.error_msg or "").lower() and "queue retry" in (job.error_msg or "")
    assert db.get(fresh).status == "awaiting_approval"  # type: ignore[union-attr]


def test_expiring_never_touches_other_statuses(db: QueueDb) -> None:
    approved = held(db)
    db.approve(approved, T0 + 10, 6000.0)
    queued = db.enqueue(draft_job(), T0)
    assert db.expire_approvals(T0 + 10 * TTL) == []
    assert db.get(approved).status == "queued" and db.get(queued).status == "queued"  # type: ignore[union-attr]


def test_a_waiting_job_can_be_cancelled_at_once(db: QueueDb) -> None:
    jid = held(db)
    assert db.request_cancel(jid, T0 + 30) == "cancelled"
    job = db.get(jid)
    assert job is not None and job.status == "cancelled" and job.finished_at == T0 + 30


def test_duplicates_include_a_draft_waiting_for_approval(db: QueueDb) -> None:
    jid = held(db)
    assert db.find_active("draft", "E:/footage", "", False) == jid


# ─────────────── 6c-2: how much has been spent, how much is promised ───────────────


def paid_job(db: QueueDb, at: float, approved: float, spent: float | None,
             outcome: str = "completed") -> int:  # fmt: skip
    jid = held(db, at, project_dir=f"E:/p{at}")
    db.approve(jid, at + 10, approved)
    db.claim(at + 11, 1)
    if outcome == "completed":
        db.finish(jid, at + 20, "completed", "passed", 0, None, None, spent_vnd=spent)
    elif outcome == "failed":
        db.finish(jid, at + 20, "failed", None, 2, "lỗi", None, spent_vnd=spent)
    return jid


def test_finish_stores_what_was_spent(db: QueueDb) -> None:
    jid = paid_job(db, T0, 6000.0, 1224.0)
    assert db.get(jid).spent_vnd == 1224.0  # type: ignore[union-attr]


def test_spent_counts_finished_paid_jobs_of_the_last_24_hours(db: QueueDb) -> None:
    paid_job(db, T0, 6000.0, 1224.0)
    paid_job(db, T0 + 100, 6000.0, 300.0, outcome="failed")
    spent, reserved = db.spend_summary(T0 + 3600)
    assert (spent, reserved) == (1524.0, 0.0)
    assert db.spend_summary(T0 + 100_000)[0] == 0.0  # outside the 24 h window


def test_reserved_is_the_approval_of_jobs_that_may_still_spend(db: QueueDb) -> None:
    waiting = held(db, T0, project_dir="E:/c")  # not approved: reserves nothing
    queued = held(db, T0, project_dir="E:/a")
    db.approve(queued, T0 + 10, 6000.0)  # approved, waiting for the worker
    running = held(db, T0, project_dir="E:/b")
    db.approve(running, T0 + 10, 7000.0)
    got = db.claim(T0 + 20, 1)  # one of the two approved jobs starts
    assert got is not None and got.status == "processing"
    assert db.get(waiting).status == "awaiting_approval"  # type: ignore[union-attr]
    assert db.spend_summary(T0 + 30) == (0.0, 13_000.0)  # both count: one running, one queued


def test_a_paid_job_whose_spending_is_unknown_counts_its_whole_approval(db: QueueDb) -> None:
    paid_job(db, T0, 6000.0, None, outcome="failed")  # crashed: nobody measured the bill
    assert db.spend_summary(T0 + 100) == (6000.0, 0.0)


def test_render_and_analysis_jobs_never_count_as_spending(db: QueueDb) -> None:
    r = db.enqueue(new_job("A", priority=None), T0)
    db.claim(T0, 1)
    db.finish(r, T0 + 1, "completed", "passed", 0, None, None, spent_vnd=999.0)
    assert db.spend_summary(T0 + 2) == (0.0, 0.0)


# ─────────────── 6c-2: retrying a draft job, and what a crash means ───────────────


def test_a_manual_retry_starts_again_from_a_fresh_estimate(db: QueueDb) -> None:
    jid = db.enqueue(draft_job(), T0)
    db.claim(T0, 1)
    for stage in ("plan", "pick", "estimate"):
        db.mark_stage_done(jid, stage)
    db.hold_for_approval(jid, T0 + 1, estimate(), TTL)
    db.approve(jid, T0 + 2, 6000.0)
    db.claim(T0 + 3, 1)
    db.finish(jid, T0 + 4, "failed", None, 2, "API lỗi", None, spent_vnd=100.0)
    assert db.retry_manual(jid)
    job = db.get(jid)
    assert job is not None
    assert (job.status, job.phase, job.approved_vnd, job.cost_worst_call_vnd) == (
        "queued", "estimate", None, None)  # fmt: skip
    assert job.stages_done == ["plan", "pick"]  # the estimate is redone, the free work is kept
    assert job.approval_expires_at is None and job.spent_vnd == 100.0  # the bill stays on record


def test_a_cancelled_waiting_job_can_be_retried_too(db: QueueDb) -> None:
    jid = held(db)
    db.request_cancel(jid, T0 + 30)
    assert db.retry_manual(jid)
    assert db.get(jid).phase == "estimate"  # type: ignore[union-attr]


def test_a_crash_never_requeues_a_draft_job_and_the_message_fits_the_phase(db: QueueDb) -> None:
    free = db.enqueue(draft_job(project_dir="E:/free"), T0)
    db.claim(T0, 77)  # dies while planning: nothing was spent
    paid = held(db, T0, project_dir="E:/paid")
    db.approve(paid, T0 + 1, 6000.0)
    db.claim(T0 + 2, 78)  # dies during the API call: money may have been spent
    out = db.recover_orphans(T0 + 900, alive_none, stale_sec=120, retry_delay=30)
    assert {(r.job_id, r.action) for r in out} == {(free, "failed"), (paid, "failed")}
    free_msg = db.get(free).error_msg or ""  # type: ignore[union-attr]
    paid_msg = db.get(paid).error_msg or ""  # type: ignore[union-attr]
    assert "queue retry" in free_msg and "ai_log" not in free_msg
    assert "ai_log" in paid_msg and "duyệt lại" in paid_msg


def test_the_stage_vocabulary_of_drafting_is_accepted(db: QueueDb) -> None:
    jid = db.enqueue(draft_job(), T0)
    db.claim(T0, 1)
    for stage in ("plan", "pick", "phrases", "estimate"):
        db.mark_stage_done(jid, stage)
    assert db.get(jid).stages_done == ["plan", "pick", "phrases", "estimate"]  # type: ignore[union-attr]


# ─────────────── 6c-3: atomic job chains ───────────────


def chain_jobs(folder: str = "E:/footage") -> list[tuple[NewJob, int | None]]:
    """analyze → draft → two renders: the shape of a web pipeline."""
    return [
        (new_job("", kind="analyze", project_dir=folder), None),
        (new_job("", kind="draft", project_dir=folder, max_attempts=1), 0),
        (new_job("A", project_dir=folder), 1),
        (new_job("B", project_dir=folder), 1),
    ]


def test_a_chain_is_created_in_order_with_one_pipeline_and_real_dependencies(db: QueueDb) -> None:
    pid, ids = db.enqueue_chain(chain_jobs(), T0)
    jobs = [db.get(i) for i in ids]
    assert all(j is not None for j in jobs) and ids == sorted(ids) and len(ids) == 4
    a, d, ra, rb = (j for j in jobs if j is not None)
    assert {j.pipeline_id for j in (a, d, ra, rb)} == {pid}
    assert (a.depends_on, d.depends_on, ra.depends_on, rb.depends_on) == (None, a.id, d.id, d.id)
    assert (a.kind, d.kind, ra.kind, rb.kind) == ("analyze", "draft", "render", "render")
    assert all(j.status == "queued" for j in (a, d, ra, rb))


def test_each_chain_gets_its_own_pipeline_number(db: QueueDb) -> None:
    first, _ = db.enqueue_chain(chain_jobs("E:/a"), T0)
    second, _ = db.enqueue_chain(chain_jobs("E:/b"), T0)
    assert second == first + 1


def test_a_chain_step_may_only_wait_for_an_earlier_step(db: QueueDb) -> None:
    for bad in (2, 1, 9, -1):
        jobs = chain_jobs()
        jobs[1] = (jobs[1][0], bad)
        with pytest.raises(QueueDbError, match="phụ thuộc"):
            db.enqueue_chain(jobs, T0)
    assert db.counts()["queued"] == 0  # nothing got in


def test_an_empty_chain_is_refused(db: QueueDb) -> None:
    with pytest.raises(QueueDbError):
        db.enqueue_chain([], T0)


def test_a_failure_while_creating_a_chain_leaves_nothing_behind(db: QueueDb) -> None:
    def boom() -> None:
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        db.enqueue_chain(chain_jobs(), T0, before_insert=boom)
    assert db.counts()["queued"] == 0
    assert db.list_jobs() == []
    pid, _ = db.enqueue_chain(chain_jobs(), T0)  # and the queue still works
    assert pid == 1


def test_the_hook_runs_once_after_the_folder_check_and_before_the_first_insert(db: QueueDb) -> None:
    seen: list[int] = []

    def hook() -> None:
        seen.append(len(db.list_jobs()))  # a second connection sees the committed state only

    db.enqueue_chain(chain_jobs(), T0, guard_folder="E:/footage", before_insert=hook)
    assert seen == [0]


def busy_chain(db: QueueDb, status: str) -> tuple[int, list[int]]:
    """A one-step chain (a draft job) in the given active status."""
    pid, ids = db.enqueue_chain([(new_job("", kind="draft", max_attempts=1), None)], T0)
    if status != "queued":
        assert db.claim(T0 + 1, 1) is not None  # processing
    if status == "awaiting_approval":
        est = Estimate(model="m", input_tokens=1, input_only_vnd=1, worst_call_vnd=2, cap_vnd=3,
                       max_attempts=3)  # fmt: skip
        db.hold_for_approval(ids[0], T0 + 2, est, 86_400.0)
    assert db.get(ids[0]).status == status  # type: ignore[union-attr]
    return pid, ids


@pytest.mark.parametrize("status", ["queued", "processing", "awaiting_approval"])
def test_a_busy_folder_refuses_a_second_chain_and_names_the_running_one(
    db: QueueDb, status: str
) -> None:
    pid, ids = busy_chain(db, status)
    called: list[int] = []
    with pytest.raises(FolderBusyError) as caught:
        db.enqueue_chain(chain_jobs(), T0 + 3, guard_folder="E:/footage",
                         before_insert=lambda: called.append(1))  # fmt: skip
    assert caught.value.pipeline_id == pid and caught.value.job_id == ids[0]
    assert called == []  # the hook (which writes options.json) never ran
    assert len(db.list_jobs()) == 1  # nothing was added


def test_finished_jobs_and_other_folders_do_not_block_a_chain(db: QueueDb) -> None:
    _, ids = db.enqueue_chain(chain_jobs("E:/old"), T0, guard_folder="E:/old")
    for jid in ids:
        db.request_cancel(jid, T0 + 1)
    db.enqueue_chain(chain_jobs("E:/old"), T0 + 2, guard_folder="E:/old")  # all ended: fine
    db.enqueue_chain(chain_jobs("E:/other"), T0 + 2, guard_folder="E:/other")  # another project


def test_the_jobs_of_a_pipeline_come_back_in_creation_order(db: QueueDb) -> None:
    pid, ids = db.enqueue_chain(chain_jobs(), T0)
    db.enqueue_chain(chain_jobs("E:/other"), T0)
    assert [j.id for j in db.jobs_of_pipeline(pid)] == ids
    assert db.jobs_of_pipeline(999) == []


# ─────────────── 16/10/2026: schema v5 (render without music) ───────────────


def make_v4(path: Path) -> None:
    make_v3(path)
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE jobs ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()


def test_a_v4_database_gets_the_no_music_column_and_old_rows_keep_their_music(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queue.db"
    make_v4(path)
    db = QueueDb(path)
    assert db.schema_version() == 5 and columns(path).count("no_music") == 1
    job = db.get(1)
    assert job is not None and job.no_music is False and job.status == "awaiting_approval"
    assert (tmp_path / "queue.db.v4.bak").is_file()


def test_every_older_database_ends_with_the_no_music_column(tmp_path: Path) -> None:
    for name, make in (("v1.db", make_v1), ("v2.db", make_v2), ("v3.db", make_v3)):
        make(tmp_path / name)
        assert QueueDb(tmp_path / name).schema_version() == 5
        assert {"platform", "no_music"} <= set(columns(tmp_path / name)), name
    QueueDb(tmp_path / "new.db")
    assert "no_music" in columns(tmp_path / "new.db")


def test_a_render_without_music_is_stored_and_read_back(db: QueueDb) -> None:
    job_id = db.enqueue(NewJob(kind="render", project_dir="E:/a", options_file="o", draft="A",
                               final=True, platform="tiktok", no_music=True), 1.0)  # fmt: skip
    job = db.get(job_id)
    assert job is not None and job.no_music is True and job.platform == "tiktok"
