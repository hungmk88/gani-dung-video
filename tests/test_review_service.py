"""Tests: application/review_service.py — approve / reject / remind a delivered final video.

The delivered tree and the queue come from `review_fixtures` (no ffmpeg).
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from review_fixtures import NAME, RULES, T_RENDER, VIDEO, build, manifest, now_s

from gani_dung_video.application import review_service as rs
from gani_dung_video.application.review_service import ReviewRefused
from gani_dung_video.domain.delivery_checks import E1Item
from gani_dung_video.domain.review import ReviewBlock
from gani_dung_video.infrastructure.queue_db import NewJob

# ─────────────── approve ───────────────


def test_approving_moves_the_file_and_records_who_and_when(tmp_path: Path) -> None:
    t = build(tmp_path)
    row = rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert row.block.status == "approved"
    assert (t.root / "final" / NAME).read_bytes() == VIDEO
    assert not (t.root / "drafts" / NAME).exists()  # moved, not copied
    m = t.read()
    assert m.review is not None and m.review.status == "approved"
    assert m.review.decided_by == "Hùng" and m.review.decided_at == "2026-10-15T08:10:00Z"
    assert t.read(t.working).review == m.review  # the working manifest says the same
    assert any(level == "info" and "Hùng" in text for level, text in t.events())


def test_a_platform_render_is_reviewed_from_its_own_folder(tmp_path: Path) -> None:
    # 14/10/2026: the TikTok render of draft A lives in edit/render/A-tiktok; the plain "A"
    # folder (another render) must not be what the review reads.
    t = build(tmp_path, platform="tiktok")
    assert t.working.parent.name == "A-tiktok"
    assert not (t.folder / "edit" / "render" / "A").exists()
    row = rs.approve(t.db, RULES, t.job.id, "NVH", None, now_s())
    assert row.block.status == "approved" and (t.root / "final" / NAME).is_file()
    assert t.read(t.working).review == t.read().review


def test_pressing_approve_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    t = build(tmp_path)
    rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    before = t.meta.read_text(encoding="utf-8")
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "NVH", None, now_s(20))
    assert e.value.code == "already_decided"
    assert t.meta.read_text(encoding="utf-8") == before  # nobody was recorded over the first
    assert (t.root / "final" / NAME).read_bytes() == VIDEO
    assert any(level == "warn" and "NVH" in text for level, text in t.events())  # the attempt


def test_a_preview_is_never_reviewed(tmp_path: Path) -> None:
    t = build(tmp_path, final=False, review=None, resolution="540x960")
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "preview_not_reviewable"
    assert (t.root / "drafts" / NAME).exists() and not list((t.root / "final").iterdir())


def test_every_failed_check_is_listed_and_the_file_stays(tmp_path: Path) -> None:
    bad = [E1Item(code="E1-SAFE", name="Vùng an toàn", passed=False, detail="chữ sát mép")]
    t = build(tmp_path, extra=bad)
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "checks_failed"
    assert e.value.extra["problems"] == ["E1-SAFE: chữ sát mép"]
    assert "E1-SAFE" in str(e.value)
    assert (t.root / "drafts" / NAME).exists() and not list((t.root / "final").iterdir())
    assert t.read().review is not None and t.read().review.status == "pending"  # type: ignore[union-attr]


def test_a_video_that_was_not_delivered_cannot_be_approved(tmp_path: Path) -> None:
    t = build(tmp_path, review=None, delivered_as=None)
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "not_delivered"


def test_a_missing_file_is_refused_not_invented(tmp_path: Path) -> None:
    t = build(tmp_path, with_file=False)
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "file_missing"


def test_a_job_that_is_not_a_render_is_refused(tmp_path: Path) -> None:
    t = build(tmp_path)
    other = t.db.enqueue(NewJob(kind="analyze", project_dir=str(t.folder), options_file="",
                                draft=""), now_s())  # fmt: skip
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, other, "Hùng", None, now_s())
    assert e.value.code == "not_a_render_job"
    with pytest.raises(ReviewRefused) as e2:
        rs.approve(t.db, RULES, 999, "Hùng", None, now_s())
    assert e2.value.code == "not_found"


def test_a_render_that_was_overwritten_by_a_newer_one_is_not_this_job(tmp_path: Path) -> None:
    """The working manifest belongs to the newest render of the letter; an older job must not
    approve (or even show) a video that is not its own."""
    t = build(tmp_path)
    newer = manifest(review=ReviewBlock(requested_at="2026-10-16T08:00:00Z"))
    newer = newer.model_copy(update={"created_at": "2026-10-16T08:00:00+00:00"})
    t.working.write_text(newer.model_dump_json(indent=1), encoding="utf-8")
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "not_delivered"


# ─────────────── reject ───────────────


def test_reject_keeps_the_file_in_drafts_and_the_reason_in_the_manifest(tmp_path: Path) -> None:
    t = build(tmp_path)
    row = rs.reject(t.db, RULES, t.job.id, "NVH", "chữ cuối bị che", now_s())
    assert row.block.status == "changes_requested"
    assert (t.root / "drafts" / NAME).exists() and not list((t.root / "final").iterdir())
    m = t.read()
    assert m.review is not None and m.review.reason == "chữ cuối bị che"
    assert m.review.decided_by == "NVH"
    assert any(level == "warn" and "chữ cuối bị che" in text for level, text in t.events())


def test_reject_without_a_reason_is_refused_and_nothing_changes(tmp_path: Path) -> None:
    t = build(tmp_path)
    before = t.meta.read_text(encoding="utf-8")
    with pytest.raises(ReviewRefused) as e:
        rs.reject(t.db, RULES, t.job.id, "NVH", "   ", now_s())
    assert e.value.code == "reason_required"
    assert t.meta.read_text(encoding="utf-8") == before


def test_reject_is_allowed_even_when_a_check_failed(tmp_path: Path) -> None:
    bad = [E1Item(code="E1-SAFE", name="x", passed=False, detail="chữ sát mép")]
    t = build(tmp_path, extra=bad)
    assert (
        rs.reject(t.db, RULES, t.job.id, "NVH", "bỏ", now_s()).block.status == "changes_requested"
    )


# ─────────────── old files, listing ───────────────


def test_a_final_delivered_before_t_f_is_pending(tmp_path: Path) -> None:
    t = build(tmp_path, review=None)  # a manifest without the block, 1080x1920
    [row] = rs.list_reviews(t.db, RULES, now=now_s())
    assert row.block.status == "pending" and row.block.requested_at == "2026-10-15T08:00:00Z"
    assert rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s()).block.status == "approved"


def test_the_list_filters_by_status_and_by_date(tmp_path: Path) -> None:
    t = build(tmp_path)
    assert [r.job.id for r in rs.list_reviews(t.db, RULES, now=now_s())] == [t.job.id]
    assert rs.list_reviews(t.db, RULES, status="approved", now=now_s()) == []
    assert rs.list_reviews(t.db, RULES, since=T_RENDER.date() + timedelta(days=1),
                           now=now_s()) == []  # fmt: skip
    assert len(rs.list_reviews(t.db, RULES, since=T_RENDER.date(), now=now_s())) == 1
    rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert len(rs.list_reviews(t.db, RULES, status="approved", now=now_s())) == 1
    assert rs.list_reviews(t.db, RULES, status="pending", now=now_s()) == []


def test_the_list_skips_previews_and_flags_overdue(tmp_path: Path) -> None:
    t = build(tmp_path)
    ttl = float(RULES["review"]["ttl_sec"])
    [soon] = rs.list_reviews(t.db, RULES, now=now_s(60))
    assert soon.overdue is False
    [late] = rs.list_reviews(t.db, RULES, now=T_RENDER.timestamp() + ttl + 5)
    assert late.overdue is True
    p = build(tmp_path / "other", final=False, review=None, resolution="540x960")
    assert rs.list_reviews(p.db, RULES, now=now_s()) == []


# ─────────────── crash between the steps (copy → write → delete) ───────────────


def test_a_crash_after_the_copy_leaves_a_pending_video_that_can_be_approved_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = build(tmp_path)

    def boom(path: Path, text: str) -> None:
        raise OSError("sập giữa chừng")

    monkeypatch.setattr(rs, "write_text_atomic", boom)
    with pytest.raises(OSError, match="sập"):
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    monkeypatch.undo()
    assert (t.root / "drafts" / NAME).exists()  # the original was not touched
    assert t.read().review is not None and t.read().review.status == "pending"  # type: ignore[union-attr]
    # the copy may already be in final/: the retry must not mind
    assert rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s(20)).block.status == "approved"
    assert (t.root / "final" / NAME).read_bytes() == VIDEO
    assert not (t.root / "drafts" / NAME).exists()


def test_a_crash_before_the_delete_is_tidied_by_the_next_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = build(tmp_path)
    real_unlink = Path.unlink

    def no_delete(self: Path, missing_ok: bool = False) -> None:
        if self.name == NAME and self.parent.name == "drafts":
            raise OSError("không xoá được")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", no_delete)
    rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())  # approved, the delete failed
    monkeypatch.undo()
    assert t.read().review is not None and t.read().review.status == "approved"  # type: ignore[union-attr]
    assert (t.root / "drafts" / NAME).exists() and (t.root / "final" / NAME).exists()
    rs.list_reviews(t.db, RULES, now=now_s(30))  # looking at it settles the leftover
    assert not (t.root / "drafts" / NAME).exists() and (t.root / "final" / NAME).exists()


def test_a_copy_that_comes_out_a_different_size_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = build(tmp_path)

    def short_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(src.read_bytes()[:10])

    monkeypatch.setattr(rs, "_copy_file", short_copy)
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "copy_failed"
    assert (t.root / "drafts" / NAME).read_bytes() == VIDEO
    assert not list((t.root / "final").iterdir())  # no half file left behind
    assert t.read().review is not None and t.read().review.status == "pending"  # type: ignore[union-attr]


# ─────────────── two at once (CLI + API are two processes; here two threads) ───────────────


def test_two_simultaneous_approvals_let_exactly_one_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first approval is held in the middle of the copy; the second one starts then. Without
    the lock both would copy and both would be "ok"; with it the second waits, then sees the
    decision."""
    t = build(tmp_path)
    inside, release = threading.Event(), threading.Event()
    real_copy = rs._copy_file

    def slow_copy(src: Path, dst: Path) -> None:
        inside.set()
        release.wait(5)
        real_copy(src, dst)

    monkeypatch.setattr(rs, "_copy_file", slow_copy)
    results: dict[str, str] = {}

    def press(who: str) -> None:
        try:
            rs.approve(t.db, RULES, t.job.id, who, None, now_s())
            results[who] = "ok"
        except ReviewRefused as e:
            results[who] = e.code

    first = threading.Thread(target=press, args=("A",))
    first.start()
    assert inside.wait(5)  # A is inside the critical section
    second = threading.Thread(target=press, args=("B",))
    second.start()
    time.sleep(0.4)  # B has had every chance to get in
    release.set()
    first.join(timeout=30)
    second.join(timeout=30)
    assert results == {"A": "ok", "B": "already_decided"}
    assert (t.root / "final" / NAME).read_bytes() == VIDEO
    m = t.read()
    assert m.review is not None and m.review.decided_by == "A"  # B did not write over A


def test_a_crash_while_putting_the_copy_in_place_leaves_no_half_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    t = build(tmp_path)

    def boom(src: object, dst: object) -> None:
        raise OSError("mất điện")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="mất điện"):
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    monkeypatch.undo()
    assert list((t.root / "final").iterdir()) == []  # neither the file nor its .part
    assert (t.root / "drafts" / NAME).read_bytes() == VIDEO
    assert t.read().review is not None and t.read().review.status == "pending"  # type: ignore[union-attr]
    assert rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s(5)).block.status == "approved"


def test_a_stale_lock_left_by_a_dead_process_is_taken_over(tmp_path: Path) -> None:
    t = build(tmp_path)
    lock = t.meta.with_name(t.meta.name + ".review.lock")
    lock.write_text("pid 1", encoding="utf-8")
    old = time.time() - 3600
    import os

    os.utime(lock, (old, old))
    assert rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s()).block.status == "approved"
    assert not lock.exists()


def test_a_fresh_lock_held_by_someone_else_is_reported_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = build(tmp_path)
    t.meta.with_name(t.meta.name + ".review.lock").write_text("pid 1", encoding="utf-8")
    monkeypatch.setattr(rs, "LOCK_WAIT_SEC", 0.2)
    with pytest.raises(ReviewRefused) as e:
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, now_s())
    assert e.value.code == "lock_busy"


# ─────────────── the one reminder ───────────────


def overdue_at() -> float:
    return T_RENDER.timestamp() + float(RULES["review"]["ttl_sec"]) + 60


def test_an_overdue_video_is_reminded_once_and_stays_pending(tmp_path: Path) -> None:
    t = build(tmp_path)
    assert rs.remind_overdue(t.db, RULES, overdue_at()) == [t.job.id]
    m = t.read()
    assert m.review is not None and m.review.status == "pending"
    assert m.review.reminded_at is not None
    assert t.read(t.working).review == m.review
    warns = [text for level, text in t.events() if level == "warn" and "quá hạn" in text]
    assert len(warns) == 1
    # a second pass, an hour later: silence
    assert rs.remind_overdue(t.db, RULES, overdue_at() + 3600) == []
    assert len([1 for lv, tx in t.events() if lv == "warn" and "quá hạn" in tx]) == 1
    # and it can still be approved after the reminder
    assert (
        rs.approve(t.db, RULES, t.job.id, "Hùng", None, overdue_at() + 7200).block.status
        == "approved"
    )


def test_nothing_is_reminded_before_the_ttl_or_after_a_decision(tmp_path: Path) -> None:
    t = build(tmp_path)
    assert rs.remind_overdue(t.db, RULES, now_s(60)) == []
    rs.reject(t.db, RULES, t.job.id, "NVH", "sửa", now_s(5))
    assert rs.remind_overdue(t.db, RULES, overdue_at()) == []


def test_a_broken_manifest_does_not_stop_the_reminder_pass(tmp_path: Path) -> None:
    a = build(tmp_path / "a")
    b = build(tmp_path / "b")
    a.meta.write_text("{ không phải json", encoding="utf-8")
    assert rs.remind_overdue(b.db, RULES, overdue_at()) == [b.job.id]
    assert rs.remind_overdue(a.db, RULES, overdue_at()) == []  # skipped, not raised


# ─────────────── the daemon runs the reminder pass, at most once a minute ───────────────


def test_the_daemon_reminds_overdue_videos_and_not_more_often_than_every_minute(
    tmp_path: Path,
) -> None:
    from gani_dung_video.application.daemon import REVIEW_CHECK_SEC, Daemon

    t = build(tmp_path)
    clock = {"now": overdue_at()}
    daemon = Daemon(t.db, RULES, clock=lambda: clock["now"], pid=4242, argv_base=["py"])
    assert daemon.process_next() is False  # nothing to run, but the pass happened
    assert len([1 for lv, tx in t.events() if lv == "warn" and "quá hạn" in tx]) == 1
    # the file is changed back to unreminded: inside the minute the daemon must not look
    meta = t.read()
    assert meta.review is not None and meta.review.reminded_at is not None
    t.meta.write_text(
        meta.model_copy(update={"review": meta.review.model_copy(update={"reminded_at": None})})
        .model_dump_json(indent=1), encoding="utf-8")  # fmt: skip
    clock["now"] += REVIEW_CHECK_SEC - 1
    daemon.process_next()
    assert len([1 for lv, tx in t.events() if lv == "warn" and "quá hạn" in tx]) == 1
    clock["now"] += 2  # a minute has passed
    daemon.process_next()
    assert len([1 for lv, tx in t.events() if lv == "warn" and "quá hạn" in tx]) == 2


def test_an_error_in_the_reminder_pass_never_stops_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gani_dung_video.application import daemon as daemon_module

    t = build(tmp_path)

    def boom(*a: object, **k: object) -> list[int]:
        raise RuntimeError("hỏng")

    monkeypatch.setattr(daemon_module, "remind_overdue", boom)
    d = daemon_module.Daemon(t.db, RULES, clock=time.time, pid=1, argv_base=["py"])
    assert d.process_next() is False  # logged, not raised
