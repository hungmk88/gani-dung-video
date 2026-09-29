"""Unit tests: infrastructure/queue_db.py — SQLite job queue, claim lock, crash recovery (6a)."""

from __future__ import annotations

import sqlite3
import threading
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
    assert db.schema_version() == 1
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
    assert db.counts() == {"queued": 1, "processing": 0, "completed": 0, "failed": 1,
                           "cancelled": 0}  # fmt: skip


def test_list_can_be_limited_to_one_project(db: QueueDb) -> None:
    mine = db.enqueue(new_job("A", project_dir="E:/one"), T0)
    db.enqueue(new_job("A", project_dir="E:/two"), T0)
    assert [j.id for j in db.list_jobs(project_dir="E:/one")] == [mine]
    assert db.list_jobs(project_dir="E:/none") == []
