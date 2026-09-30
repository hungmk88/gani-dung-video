"""One queue at a time per project folder (phase 6d).

Two queues (two queue.db files) working on one footage folder overwrite each other's
options.json, plan.json and render files, and neither queue can see the other's jobs. The
lock <folder>/edit/.project.lock names the queue.db that owns the folder.

It is alive while the owner queue has a waiting, running or approval-pending job for the
folder — not while some process lives: whoever created it (`serve`, a CLI command) exits at
once, the daemon is another process, and a draft job may wait a day for its approval. It is
stale when the owner queue is gone or has nothing active for the folder. A lock younger than
the grace is always alive (its owner takes it inside a transaction whose jobs are not visible
yet), and a queue that cannot be read counts as alive: refusing is safe, stealing is not.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_NAME = ".project.lock"
_GUARD_NAME = ".project.lock.takeover"  # held for milliseconds while a stale lock is replaced
_ACTIVE = "('queued', 'processing', 'awaiting_approval')"


class ProjectLockedError(RuntimeError):
    """Another queue owns the folder. `job_id` / `pipeline_id` name its job when known."""

    def __init__(self, job_id: int | None = None, pipeline_id: int | None = None) -> None:
        super().__init__("Thư mục dự án đang được một hàng đợi khác dùng.")
        self.job_id, self.pipeline_id = job_id, pipeline_id


@dataclass(frozen=True)
class _Owner:
    queue_db: str
    pid: int
    created_at: float


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _read(path: Path) -> _Owner | None:
    """The lock's content, or None when the file is empty, damaged or of the wrong shape."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        queue_db = data["queue_db"]
        if not isinstance(queue_db, str):
            return None
        return _Owner(queue_db, int(data["pid"]), float(data["created_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(path: Path, queue_db: Path, now: float) -> None:
    body = {"queue_db": str(Path(queue_db).resolve()), "pid": os.getpid(), "created_at": now}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _refresh(path: Path, queue_db: Path, now: float) -> None:
    """Rewrite our own lock atomically (a reader never sees a half-written file)."""
    body = {"queue_db": str(Path(queue_db).resolve()), "pid": os.getpid(), "created_at": now}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)


def _active_job(owner_db: str, folder: Path) -> tuple[bool, int | None, int | None]:
    """(is the owner busy here, its job id, its pipeline id). An unreadable queue is busy."""
    path = Path(owner_db)
    if not path.is_file():
        return False, None, None
    try:
        con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2.0)
        try:
            row = con.execute(
                f"SELECT id, pipeline_id FROM jobs WHERE project_dir = ? AND status IN {_ACTIVE} "
                "ORDER BY id LIMIT 1", (str(folder),)).fetchone()  # fmt: skip
        finally:
            con.close()
    except sqlite3.Error:
        logger.warning("Không đọc được hàng đợi giữ khóa dự án; coi là còn hiệu lực.")
        return True, None, None
    return (True, int(row[0]), row[1]) if row else (False, None, None)


def _verdict(path: Path, folder: Path, queue_db: Path, now: float, grace: float
             ) -> tuple[str, int | None, int | None]:  # fmt: skip
    """'ours', 'alive' (with the owner's job) or 'stale' for the lock that exists."""
    owner = _read(path)
    if owner is None:  # empty or damaged: probably still being written, unless it is old
        try:
            young = now - path.stat().st_mtime < grace
        except OSError:
            return "stale", None, None  # vanished meanwhile
        return ("alive", None, None) if young else ("stale", None, None)
    if _same(owner.queue_db, queue_db):
        return "ours", None, None
    if now - owner.created_at < grace:
        return "alive", None, None
    busy, job, pipeline = _active_job(owner.queue_db, folder)
    return ("alive", job, pipeline) if busy else ("stale", None, None)


def acquire(folder: Path, queue_db: Path, *, now: float | None = None,
            grace_sec: float = 10.0) -> bool:  # fmt: skip
    """Take the folder for `queue_db`.

    Returns:
        True if the lock was created (or taken from a stale owner); False if `queue_db`
        already held it (its time is refreshed).

    Raises:
        ProjectLockedError: another queue owns the folder.
    """
    stamp = now if now is not None else time.time()
    edit = folder / "edit"
    edit.mkdir(parents=True, exist_ok=True)
    path = edit / LOCK_NAME
    try:
        _write(path, queue_db, stamp)
        return True
    except FileExistsError:
        pass
    verdict, job, pipeline = _verdict(path, folder, queue_db, stamp, grace_sec)
    if verdict == "ours":
        _refresh(path, queue_db, stamp)
        return False
    if verdict == "alive":
        raise ProjectLockedError(job, pipeline)
    return _take_over(path, edit / _GUARD_NAME, folder, queue_db, stamp, grace_sec)


def _take_over(path: Path, guard: Path, folder: Path, queue_db: Path, now: float,
               grace: float) -> bool:  # fmt: skip
    """Replace a stale lock. Only one taker at a time (the guard), and it looks again inside."""
    try:
        fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        with contextlib.suppress(OSError):  # a guard left by a crash is cleared once it is old
            if now - guard.stat().st_mtime > grace:
                guard.unlink()
        raise ProjectLockedError() from None
    os.close(fd)
    try:
        verdict, job, pipeline = _verdict(path, folder, queue_db, now, grace)
        if verdict == "alive":
            raise ProjectLockedError(job, pipeline)
        if verdict == "ours":
            _refresh(path, queue_db, now)
            return False
        path.unlink(missing_ok=True)
        try:
            _write(path, queue_db, now)
        except FileExistsError:  # a third queue found the folder free in this instant
            raise ProjectLockedError() from None
        return True
    finally:
        guard.unlink(missing_ok=True)


def release(folder: Path, queue_db: Path) -> bool:
    """Remove the lock if `queue_db` owns it. Returns whether it was removed."""
    path = folder / "edit" / LOCK_NAME
    owner = _read(path)
    if owner is None or not _same(owner.queue_db, queue_db):
        return False
    path.unlink(missing_ok=True)
    return True
