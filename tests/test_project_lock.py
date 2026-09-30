"""Unit tests: infrastructure/project_lock.py — one queue at a time per project folder (6d).

The lock lives in <folder>/edit/.project.lock and names the queue.db that owns it. It is alive
while that queue has a waiting, running or approval-pending job for the folder (or is younger than
a short grace, so a lock taken inside a not-yet-committed transaction is never stolen). It is
stale when the owner queue is gone or has nothing active there. A queue it cannot read is treated
as alive: refusing is safe, stealing is not.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from gani_dung_video.domain.jobs import Estimate
from gani_dung_video.infrastructure.project_lock import (
    LOCK_NAME,
    ProjectLockedError,
    acquire,
    release,
)
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb

T0 = 1_000_000.0
GRACE = 10.0
LATER = T0 + 60.0  # well past the grace


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    f = tmp_path / "footage"
    f.mkdir()
    return f


def queue(tmp_path: Path, name: str) -> QueueDb:
    return QueueDb(tmp_path / name / "queue.db")


def add_job(db: QueueDb, folder: Path, kind: str = "render", **kw: object) -> int:
    base: dict[str, object] = {
        "kind": kind,
        "project_dir": str(folder),
        "options_file": "o",
        "draft": "A" if kind == "render" else "",
        "max_attempts": 2,
    }
    return db.enqueue(NewJob(**{**base, **kw}), T0)


def lock_file(folder: Path) -> Path:
    return folder / "edit" / LOCK_NAME


def take(folder: Path, db: QueueDb, now: float = T0) -> bool:
    return acquire(folder, db.path, now=now, grace_sec=GRACE)


# ─────────────── the lock file ───────────────


def test_a_free_folder_is_locked_and_the_file_names_the_owner(tmp_path: Path, folder: Path) -> None:
    a = queue(tmp_path, "a")
    assert take(folder, a) is True
    info = json.loads(lock_file(folder).read_text(encoding="utf-8"))
    assert Path(info["queue_db"]) == a.path.resolve()
    assert info["pid"] == os.getpid() and info["created_at"] == T0


def test_the_owner_may_take_its_own_lock_again_and_refreshes_it(
    tmp_path: Path, folder: Path
) -> None:
    a = queue(tmp_path, "a")
    assert take(folder, a, T0) is True
    assert take(folder, a, T0 + 500) is False  # not new: it already held it
    assert json.loads(lock_file(folder).read_text("utf-8"))["created_at"] == T0 + 500


def test_the_owner_is_recognised_however_its_path_is_spelled(tmp_path: Path, folder: Path) -> None:
    a = queue(tmp_path, "a")
    assert take(folder, a) is True
    spelled = tmp_path / "a" / ".." / "a" / "queue.db"
    assert acquire(folder, spelled, now=LATER, grace_sec=GRACE) is False


# ─────────────── a foreign lock: alive or stale? ───────────────


def test_a_lock_younger_than_the_grace_is_never_taken_even_if_the_owner_has_no_job_yet(
    tmp_path: Path, folder: Path
) -> None:
    take(folder, queue(tmp_path, "a"), T0)  # A is between taking the lock and committing its jobs
    with pytest.raises(ProjectLockedError):
        take(folder, queue(tmp_path, "b"), T0 + GRACE - 1)


@pytest.mark.parametrize("state", ["queued", "processing", "awaiting_approval"])
def test_an_owner_with_an_active_job_keeps_the_folder_and_is_named(
    tmp_path: Path, folder: Path, state: str
) -> None:
    a = queue(tmp_path, "a")
    take(folder, a)
    jid = add_job(a, folder, "draft" if state == "awaiting_approval" else "render",
                  pipeline_id=7, max_attempts=1)  # fmt: skip
    if state != "queued":
        assert a.claim(T0, 1) is not None
    if state == "awaiting_approval":
        est = Estimate(model="m", input_tokens=1, input_only_vnd=1, worst_call_vnd=2, cap_vnd=3,
                       max_attempts=3)  # fmt: skip
        a.hold_for_approval(jid, T0, est, 86_400.0)
    with pytest.raises(ProjectLockedError) as caught:
        take(folder, queue(tmp_path, "b"), LATER)
    assert (caught.value.job_id, caught.value.pipeline_id) == (jid, 7)
    assert Path(json.loads(lock_file(folder).read_text("utf-8"))["queue_db"]) == a.path.resolve()


def test_an_owner_whose_jobs_all_ended_loses_the_lock(tmp_path: Path, folder: Path) -> None:
    a, b = queue(tmp_path, "a"), queue(tmp_path, "b")
    take(folder, a)
    jid = add_job(a, folder)
    a.request_cancel(jid, T0)
    assert take(folder, b, LATER) is True
    assert Path(json.loads(lock_file(folder).read_text("utf-8"))["queue_db"]) == b.path.resolve()


def test_an_owner_busy_on_another_folder_does_not_hold_this_one(tmp_path: Path,
                                                                 folder: Path) -> None:  # fmt: skip
    a = queue(tmp_path, "a")
    take(folder, a)
    other = tmp_path / "elsewhere"
    other.mkdir()
    add_job(a, other)  # A is busy, but not here
    assert take(folder, queue(tmp_path, "b"), LATER) is True


def test_an_owner_queue_that_no_longer_exists_loses_the_lock(tmp_path: Path, folder: Path) -> None:
    a = queue(tmp_path, "a")
    take(folder, a)
    add_job(a, folder)
    for suffix in ("", "-wal", "-shm"):
        Path(str(a.path) + suffix).unlink(missing_ok=True)
    assert take(folder, queue(tmp_path, "b"), LATER) is True


def test_an_owner_queue_that_cannot_be_read_keeps_the_lock(tmp_path: Path, folder: Path) -> None:
    a = queue(tmp_path, "a")
    take(folder, a)
    a.path.write_bytes(b"this is not an sqlite database" * 50)  # refusing is safe, stealing is not
    for suffix in ("-wal", "-shm"):
        Path(str(a.path) + suffix).unlink(missing_ok=True)
    with pytest.raises(ProjectLockedError):
        take(folder, queue(tmp_path, "b"), LATER)


@pytest.mark.parametrize("content", [b"", b"{not json", b"[]", b'{"queue_db": 5}'])
def test_a_damaged_lock_file_counts_as_being_written_when_young_and_stale_when_old(
    tmp_path: Path, folder: Path, content: bytes
) -> None:
    (folder / "edit").mkdir()
    path = lock_file(folder)
    path.write_bytes(content)
    b = queue(tmp_path, "b")
    os.utime(path, (T0, T0))
    with pytest.raises(ProjectLockedError):
        take(folder, b, T0 + GRACE - 1)  # someone is probably still writing it
    assert take(folder, b, LATER) is True
    assert Path(json.loads(path.read_text("utf-8"))["queue_db"]) == b.path.resolve()


# ─────────────── release ───────────────


def test_release_removes_only_a_lock_the_caller_owns(tmp_path: Path, folder: Path) -> None:
    a, b = queue(tmp_path, "a"), queue(tmp_path, "b")
    assert release(folder, a.path) is False  # nothing there
    take(folder, a)
    assert release(folder, b.path) is False and lock_file(folder).is_file()
    assert release(folder, a.path) is True and not lock_file(folder).exists()


# ─────────────── a race for a free folder ───────────────


def test_of_many_simultaneous_takers_exactly_one_wins(tmp_path: Path, folder: Path) -> None:
    dbs = [queue(tmp_path, f"q{i}") for i in range(10)]
    gate = threading.Barrier(len(dbs))
    won: list[int] = []
    refused: list[int] = []

    def go(i: int) -> None:
        gate.wait()
        try:
            take(folder, dbs[i], T0)
            won.append(i)
        except ProjectLockedError:
            refused.append(i)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(dbs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(won) == 1 and len(refused) == len(dbs) - 1
    assert (
        Path(json.loads(lock_file(folder).read_text("utf-8"))["queue_db"])
        == dbs[won[0]].path.resolve()
    )


def test_of_many_takers_racing_for_a_stale_lock_exactly_one_wins(
    tmp_path: Path, folder: Path
) -> None:
    take(folder, queue(tmp_path, "old"), T0)  # its owner has no jobs: stale once the grace passed
    dbs = [queue(tmp_path, f"q{i}") for i in range(8)]
    gate = threading.Barrier(len(dbs))
    won: list[int] = []

    def go(i: int) -> None:
        gate.wait()
        try:
            take(folder, dbs[i], LATER)
            won.append(i)
        except ProjectLockedError:
            pass

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(dbs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(won) == 1
    assert [f.name for f in (folder / "edit").glob(f"{LOCK_NAME}*")] == [LOCK_NAME]  # no leftovers
