"""Tests: domain/review.py — the state machine of T-F (review of a delivered final video)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gani_dung_video.domain.delivery_checks import E1Item, E1Report
from gani_dung_video.domain.review import (
    ReviewBlock,
    ReviewError,
    check_problems,
    decide,
    is_overdue,
    parse_time,
    pending_block,
    stamp,
)

T0 = datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)
TTL = 172800.0


def pending(at: datetime = T0) -> ReviewBlock:
    return pending_block(stamp(at))


def item(code: str, passed: bool, detail: str = "ok") -> E1Item:
    return E1Item(code=code, name=code, passed=passed, detail=detail)


# ─────────────── states and transitions ───────────────


def test_a_new_block_is_pending_and_has_no_decision() -> None:
    b = pending()
    assert b.status == "pending" and b.requested_at == "2026-10-15T08:00:00Z"
    assert (b.decided_at, b.decided_by, b.reason, b.reminded_at) == (None, None, None, None)


def test_approve_records_who_and_when() -> None:
    later = T0 + timedelta(hours=3)
    b = decide(pending(), "approve", "Hùng", None, later)
    assert b.status == "approved"
    assert b.decided_by == "Hùng" and b.decided_at == "2026-10-15T11:00:00Z" and b.reason is None


def test_approve_may_carry_a_note() -> None:
    assert decide(pending(), "approve", "Hùng", "  tốt  ", T0).reason == "tốt"


def test_reject_needs_a_reason() -> None:
    for bad in (None, "", "   ", "\n\t"):
        with pytest.raises(ReviewError) as e:
            decide(pending(), "reject", "Hùng", bad, T0)
        assert e.value.code == "reason_required"


def test_reject_with_a_reason_is_changes_requested() -> None:
    b = decide(pending(), "reject", "NVH", " chữ cuối bị che ", T0)
    assert (
        b.status == "changes_requested" and b.reason == "chữ cuối bị che" and b.decided_by == "NVH"
    )


def test_a_reviewer_is_required() -> None:
    for bad in ("", "   "):
        with pytest.raises(ReviewError) as e:
            decide(pending(), "approve", bad, None, T0)
        assert e.value.code == "reviewer_required"


@pytest.mark.parametrize("first", ["approve", "reject"])
@pytest.mark.parametrize("second", ["approve", "reject"])
def test_a_decided_video_cannot_be_decided_again(first: str, second: str) -> None:
    """Double click: the second press sees the decision and changes nothing."""
    done = decide(pending(), first, "Hùng", "lý do", T0)  # type: ignore[arg-type]
    with pytest.raises(ReviewError) as e:
        decide(done, second, "Hùng", "lý do", T0 + timedelta(seconds=1))  # type: ignore[arg-type]
    assert e.value.code == "already_decided" and e.value.extra["current_status"] == done.status
    assert done.decided_at == stamp(T0)  # the block itself is untouched (frozen)


def test_a_reminder_does_not_stop_a_decision() -> None:
    reminded = pending().model_copy(update={"reminded_at": stamp(T0 + timedelta(days=3))})
    assert decide(reminded, "approve", "Hùng", None, T0 + timedelta(days=4)).status == "approved"


# ─────────────── checks that stop an approval ───────────────


def test_no_problem_when_every_check_passed() -> None:
    e1 = E1Report(passed=True, items=[item("E1-DUR", True), item("C1", True)])
    assert check_problems(e1) == []


def test_every_failed_check_is_listed_with_its_detail() -> None:
    e1 = E1Report(passed=False, items=[item("E1-DUR", False, "lệch +2s"), item("E1-SAFE", True),
                                       item("C1", False, "chứa cụm cấm")])  # fmt: skip
    assert check_problems(e1) == ["E1-DUR: lệch +2s", "C1: chứa cụm cấm"]


def test_a_report_marked_failed_without_a_failed_item_still_stops() -> None:
    e1 = E1Report(passed=False, items=[item("E1-DUR", True)])
    problems = check_problems(e1)
    assert len(problems) == 1 and "E1" in problems[0]


# ─────────────── overdue ───────────────


def test_overdue_only_after_the_ttl() -> None:
    b = pending()
    assert not is_overdue(b, T0 + timedelta(seconds=TTL), TTL)  # exactly the ttl: not yet
    assert is_overdue(b, T0 + timedelta(seconds=TTL + 1), TTL)


def test_a_reminded_block_is_not_overdue_again() -> None:
    b = pending().model_copy(update={"reminded_at": stamp(T0 + timedelta(days=3))})
    assert not is_overdue(b, T0 + timedelta(days=30), TTL)


def test_a_decided_block_is_never_overdue() -> None:
    done = decide(pending(), "approve", "Hùng", None, T0)
    assert not is_overdue(done, T0 + timedelta(days=30), TTL)


def test_times_round_trip() -> None:
    assert parse_time(stamp(T0)) == T0
    assert parse_time("2026-10-15T08:00:00+00:00") == T0  # the manifest's own created_at form
