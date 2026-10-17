"""Use case: the reviewer's decision on a delivered final video (Dev Package 03, T-F).

A final video that passed every check waits in `edit/delivery/<campaign>/drafts/` with
`review: pending` in its manifest. Here a person approves it (the file moves to `final/`) or asks
for changes (a reason is required), and the daemon reminds once of videos left waiting too long.

Which manifest is the truth: `metadata/<name>.manifest.json` is written once per delivered video
and never overwritten by a later render. The working manifest in `edit/render/<draft>/` is
replaced by every render of that letter, so it is only kept in step while it still describes the
same video.

Order of one approval (a crash between two steps never loses the video or lies about it):
    1. take the file lock  2. copy drafts/ → final/  3. write the manifest(s)  4. delete drafts/
After 2 the state is still `pending` and a retry simply goes on; after 3 it is `approved` and the
leftover in drafts/ is removed the next time anyone looks.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from gani_dung_video.domain.jobs import Job
from gani_dung_video.domain.manifest import Manifest
from gani_dung_video.domain.platforms import render_dir_name
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.review import (
    ReviewAction,
    ReviewBlock,
    ReviewError,
    check_problems,
    decide,
    is_overdue,
    parse_time,
    pending_block,
    stamp,
)
from gani_dung_video.infrastructure.atomic_file import write_text_atomic
from gani_dung_video.infrastructure.config_files import ConfigError
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.queue_db import QueueDb

logger = logging.getLogger(__name__)

LOCK_WAIT_SEC = 10.0  # how long a second person waits for the first to finish
LOCK_STALE_SEC = 120.0  # a lock older than this was left by a process that died
REMIND_WINDOW_DAYS = 30  # videos delivered longer ago than this are not looked at for reminders
_SLACK_SEC = 10.0  # clock slack between a job's times and its manifest's created_at


class ReviewRefused(ConfigError):
    """A review action that was not carried out. `code` is stable for programs (the API turns it
    into a status), the message is Vietnamese for people, `extra` is for the response body."""

    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.extra: dict[str, object] = extra


@dataclass(frozen=True)
class Delivered:
    """One delivered final video and where its files and manifests are."""

    job: Job
    root: Path  # delivery/<campaign>
    name: str  # the E3 file name
    meta_path: Path  # metadata/<stem>.manifest.json: the manifest that counts
    working_path: Path  # edit/render/<draft>/manifest.json: replaced by every render
    manifest: Manifest  # the one in metadata/

    @property
    def draft_file(self) -> Path:
        return self.root / "drafts" / self.name

    @property
    def final_file(self) -> Path:
        return self.root / "final" / self.name


@dataclass(frozen=True)
class ReviewRow:
    job: Job
    video: Delivered
    block: ReviewBlock
    problems: list[str]  # failed checks, empty when the video may be approved
    overdue: bool


# ─────────────── finding the video of a job ───────────────


def _read(path: Path) -> Manifest | None:
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def final_resolution(rules: Rules) -> str:
    size = rules["render"]["final"]
    return f"{int(size['width'])}x{int(size['height'])}"


def locate(job: Job, rules: Rules) -> Delivered:
    """The delivered final video that `job` produced.

    Raises:
        ReviewRefused: not a render job, a preview, not finished, not delivered, or its video
            was replaced by a newer render of the same letter.
    """
    if job.kind != "render":
        raise ReviewRefused("not_a_render_job", f"Job #{job.id} không phải job dựng video.")
    if not job.final:
        raise ReviewRefused(
            "preview_not_reviewable",
            f"Job #{job.id} là bản nháp 540p: chỉ bản cuối 1080×1920 mới được duyệt.",
        )
    if job.status != "completed" or job.finished_at is None or job.started_at is None:
        raise ReviewRefused("not_delivered", f"Job #{job.id} chưa dựng xong.")
    folder = Path(job.project_dir)
    working = (
        folder / EDIT_DIR / "render" / render_dir_name(job.draft, job.platform) / "manifest.json"
    )
    wm = _read(working)
    if wm is None:
        raise ReviewRefused("not_delivered", f"Job #{job.id} không có manifest đọc được.")
    made = parse_time(wm.created_at).timestamp()
    if not (job.started_at - _SLACK_SEC <= made <= job.finished_at + _SLACK_SEC):
        raise ReviewRefused(
            "not_delivered",
            f"Video của job #{job.id} đã bị bản dựng mới hơn của bản {job.draft}"
            f"{f' ({job.platform})' if job.platform else ''} thay thế: "
            "duyệt job mới nhất.",
        )
    if wm.delivered_as is None:
        raise ReviewRefused(
            "not_delivered", f"Job #{job.id} không qua kiểm tra nên không được giao, không duyệt."
        )
    stem = Path(wm.delivered_as).stem
    for candidate in sorted((folder / EDIT_DIR / str(rules["delivery"]["root_dir"])).glob(
            f"*/metadata/{stem}.manifest.json")):  # fmt: skip
        meta = _read(candidate)
        if meta is not None:
            return Delivered(job=job, root=candidate.parent.parent, name=wm.delivered_as,
                             meta_path=candidate, working_path=working, manifest=meta)  # fmt: skip
    raise ReviewRefused("not_delivered", f"Không tìm thấy bản giao của job #{job.id}.")


def effective_review(m: Manifest, rules: Rules) -> ReviewBlock | None:
    """The review block of a manifest; a final delivered before T-F (no block) counts as pending
    from the day it was made. None for anything that is not a delivered final video."""
    if m.review is not None:
        return m.review
    if m.delivered_as is not None and m.resolution == final_resolution(rules):
        return pending_block(stamp(parse_time(m.created_at)))
    return None


def _row(video: Delivered, rules: Rules, now: datetime) -> ReviewRow:
    block = effective_review(video.manifest, rules)
    if block is None:
        raise ReviewRefused("preview_not_reviewable", f"Video {video.name} không phải bản cuối.")
    ttl = float(rules["review"]["ttl_sec"])
    # `overdue` here is about waiting, whether or not the reminder was already sent
    waited = (now - parse_time(block.requested_at)).total_seconds()
    return ReviewRow(job=video.job, video=video, block=block, problems=check_problems(
        video.manifest.e1), overdue=block.status == "pending" and waited > ttl)  # fmt: skip


# ─────────────── file lock, copy, write ───────────────


@contextlib.contextmanager
def _locked(meta_path: Path) -> Iterator[None]:
    """One decision at a time per video, across threads and processes (CLI and API)."""
    lock = meta_path.with_name(meta_path.name + ".review.lock")
    deadline = time.monotonic() + LOCK_WAIT_SEC
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SEC:
                    lock.unlink()
                    continue
            if time.monotonic() > deadline:
                raise ReviewRefused(
                    "lock_busy", "Video này đang được người khác duyệt: thử lại sau ít giây."
                ) from None
            time.sleep(0.05)
            continue
        os.write(fd, f"pid {os.getpid()}".encode())
        os.close(fd)
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def _copy_file(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)


def _copy_into_final(video: Delivered) -> None:
    """drafts/<name> → final/<name> through a temp file, so final/ never holds half a video."""
    src, dst = video.draft_file, video.final_file
    if dst.exists() and not src.exists():
        return  # an earlier attempt already copied it
    if not src.exists():
        raise ReviewRefused("file_missing", f"Không còn tệp {video.name} trong drafts/.")
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.part")
    try:
        _copy_file(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise ReviewRefused("copy_failed", f"Chép {video.name} sang final/ bị thiếu byte.")
        os.replace(tmp, dst)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _write_block(video: Delivered, block: ReviewBlock) -> Manifest:
    """The new block into the manifest that counts, and into the working one while it is still
    the same video."""
    updated = video.manifest.model_copy(update={"review": block})
    text = updated.model_dump_json(indent=1)
    write_text_atomic(video.meta_path, text)
    working = _read(video.working_path)
    if working is not None and working.delivered_as == video.name:
        write_text_atomic(video.working_path, working.model_copy(update={"review": block})
                          .model_dump_json(indent=1))  # fmt: skip
    return updated


def _tidy(video: Delivered, block: ReviewBlock) -> None:
    """An approved video must not also sit in drafts/ (a crash after the manifest was written)."""
    if block.status == "approved" and video.final_file.exists() and video.draft_file.exists():
        with contextlib.suppress(OSError):
            video.draft_file.unlink()


def _event(db: QueueDb, job: Job, now: float, level: str, text: str) -> None:
    with contextlib.suppress(Exception):  # the log must never undo a decision
        db.add_event(job.id, now, level, text)


# ─────────────── what the callers use ───────────────


def _when(now: float | None) -> tuple[float, datetime]:
    epoch = time.time() if now is None else now
    return epoch, datetime.fromtimestamp(epoch, UTC)


def _job(db: QueueDb, job_id: int) -> Job:
    job = db.get(job_id)
    if job is None:
        raise ReviewRefused("not_found", f"Không có job #{job_id}.")
    return job


def list_reviews(
    db: QueueDb, rules: Rules, *, status: str | None = None, since: date | None = None,
    limit: int = 200, now: float | None = None,
) -> list[ReviewRow]:  # fmt: skip
    """Final videos delivered by finished render jobs, newest job first.

    Args:
        status: only this state ("pending", "approved", "changes_requested"); None = all.
        since: only videos that started waiting on or after this day (UTC).
    """
    epoch, at = _when(now)
    rows: list[ReviewRow] = []
    seen: set[Path] = set()
    for job in db.list_jobs(status="completed", kind="render", limit=500):
        if not job.final or job.result not in (None, "passed"):
            continue
        try:
            video = locate(job, rules)
            row = _row(video, rules, at)
        except ReviewRefused:
            continue  # a preview, a replaced render, a blocked video: not in the list
        if video.meta_path in seen:
            continue
        seen.add(video.meta_path)
        _tidy(video, row.block)
        if status is not None and row.block.status != status:
            continue
        if since is not None and parse_time(row.block.requested_at).date() < since:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    del epoch
    return rows


def review_of_job(job: Job, rules: Rules, now: float | None = None) -> ReviewRow | None:
    """The review state of a finished final render, or None when it has none (a preview, a
    video that was not delivered, a render replaced by a newer one). Read-only."""
    _, at = _when(now)
    try:
        return _row(locate(job, rules), rules, at)
    except ReviewRefused:
        return None


def _decide(
    db: QueueDb, rules: Rules, job_id: int, action: ReviewAction, reviewer: str,
    reason: str | None, now: float | None,
) -> ReviewRow:  # fmt: skip
    epoch, at = _when(now)
    job = _job(db, job_id)
    video = locate(job, rules)
    who = (reviewer or "").strip() or "?"
    try:
        with _locked(video.meta_path):
            video = locate(job, rules)  # again, inside the lock: someone may have just decided
            row = _row(video, rules, at)
            new = decide(row.block, action, reviewer, reason, at)  # may raise ReviewError
            if action == "approve":
                if row.problems:
                    raise ReviewRefused(
                        "checks_failed",
                        "Không duyệt được: video chưa đạt " + "; ".join(row.problems),
                        problems=row.problems,
                    )
                _copy_into_final(video)
            updated = _write_block(video, new)
            if action == "approve":
                _tidy(video, new)
    except ReviewError as e:
        _event(db, job, epoch, "warn", f"Bị từ chối ({e.code}) khi {who} bấm {action}: {e.message}")
        raise ReviewRefused(e.code, e.message, **e.extra) from e
    except ReviewRefused as e:
        _event(db, job, epoch, "warn", f"Bị từ chối ({e.code}) khi {who} bấm {action}: {e}")
        raise
    if action == "approve":
        _event(db, job, epoch, "info", f"{who} đã duyệt: {video.name} sang final/.")
    else:
        _event(db, job, epoch, "warn", f"{who} yêu cầu sửa: {new.reason}")
    done = Delivered(job=job, root=video.root, name=video.name, meta_path=video.meta_path,
                     working_path=video.working_path, manifest=updated)  # fmt: skip
    return _row(done, rules, at)


def approve(
    db: QueueDb, rules: Rules, job_id: int, reviewer: str, note: str | None = None,
    now: float | None = None,
) -> ReviewRow:  # fmt: skip
    """Approve: the file moves to final/, the manifest records who and when.

    Raises:
        ReviewRefused: with a `code` (not_found, not_a_render_job, preview_not_reviewable,
            not_delivered, already_decided, checks_failed, file_missing, copy_failed,
            lock_busy, reviewer_required).
    """
    return _decide(db, rules, job_id, "approve", reviewer, note, now)


def reject(
    db: QueueDb, rules: Rules, job_id: int, reviewer: str, reason: str, now: float | None = None
) -> ReviewRow:  # fmt: skip
    """Ask for changes: the reason is required and stays in the manifest; the file stays in
    drafts/. Raises ReviewRefused like `approve` (and reason_required)."""
    return _decide(db, rules, job_id, "reject", reviewer, reason, now)


def remind_overdue(db: QueueDb, rules: Rules, now: float | None = None) -> list[int]:
    """Remind, once, of every pending video waiting longer than `review.ttl_sec`.

    The reminder is a `warn` event on the job and `reminded_at` in the manifest; the video stays
    pending and can still be decided. A manifest that cannot be read is skipped.

    Returns:
        The ids of the jobs reminded now.
    """
    epoch, at = _when(now)
    ttl = float(rules["review"]["ttl_sec"])
    cutoff = epoch - REMIND_WINDOW_DAYS * 86400
    reminded: list[int] = []
    seen: set[Path] = set()
    for job in db.list_jobs(status="completed", kind="render", limit=500):
        if not job.final or (job.finished_at or 0.0) < cutoff:
            continue
        try:
            video = locate(job, rules)
            if video.meta_path in seen:
                continue
            seen.add(video.meta_path)
            block = effective_review(video.manifest, rules)
            if block is None or not is_overdue(block, at, ttl):
                continue
            with _locked(video.meta_path):
                video = locate(job, rules)  # the state may have changed while waiting
                block = effective_review(video.manifest, rules)
                if block is None or not is_overdue(block, at, ttl):
                    continue
                _write_block(video, block.model_copy(update={"reminded_at": stamp(at)}))
        except (ReviewRefused, OSError, ValueError) as e:
            logger.warning("Nhắc duyệt: bỏ qua job #%d (%s)", job.id, e)
            continue
        days = (at - parse_time(block.requested_at)).total_seconds() / 86400
        _event(db, job, epoch, "warn",
               f"Video {video.name} chờ duyệt quá hạn ({days:.1f} ngày, hạn {ttl / 3600:g} giờ). "
               "Nhắc một lần; vẫn chờ duyệt.")  # fmt: skip
        reminded.append(job.id)
    return reminded


# The names the command line uses (the module's own `approve`/`reject` read better inside it).
approve_review = approve
reject_review = reject
