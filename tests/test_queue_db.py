"""Unit tests: infrastructure/queue_db.py — SQLite job queue, claim lock, crash recovery (6a)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb, QueueDbError

T0 = 1_000_000.0


def new_job(draft: str = "A", kind: str = "render", **kw: object) -> NewJob:
    base: dict[str, object] = {"kind": kind, "project_dir": "E:/footage", "options_file": "o.md",
                               "draft": draft, "final": False, "no_asr": False,
                               "max_attempts": 2, "priority": 0}  # fmt: skip
    return NewJob(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture
def db(tmp_path: Path) -> QueueDb:
    return QueueDb(tmp_path / "jobs" / "queue.db")


def alive_none(pid: int) -> bool:
    return False


def alive_all(pid: int) -> bool:
    return True


# ─────────────── schema ───────────────


def test_schema_is_created_with_wal_and_a_version(db: QueueDb, tmp_path: Path) -> None:
    assert db.schema_version() == 2
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


def test_paid_draft_jobs_are_never_requeued_after_a_crash(db: QueueDb) -> None:
    jid = running(db, kind="draft", max_attempts=5)
    [r] = db.recover_orphans(T0 + 500, alive_none, stale_sec=120, retry_delay=30)
    assert (r.job_id, r.action) == (jid, "failed")
    job = db.get(jid)
    assert job is not None and "ai_log" in (job.error_msg or "")


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


def test_a_v1_database_is_upgraded_with_every_job_and_event_kept(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    make_v1(path)
    db = QueueDb(path)
    assert db.schema_version() == 2
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
    assert db.schema_version() == 2 and len(db.list_jobs()) == 2


def test_a_fresh_database_starts_at_v2_without_any_backup(tmp_path: Path) -> None:
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
