"""T-F: the reviewer's decision on a delivered final video (Dev Package 03, 8B). Pure code.

A final video that passed every check sits in `delivery/<campaign>/drafts/` as `pending`. A
person approves it (the file moves to `final/`) or asks for changes (a reason is required and
kept). The states and what may follow what live here; moving files and writing manifests is
`application/review_service.py`.

    pending ──approve──▶ approved
       └─────reject───▶ changes_requested

A decided video is final: pressing the button twice, or two people pressing it at once, changes
nothing the second time. Waiting too long never decides anything: the video is reminded of once
(`reminded_at`) and stays `pending`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.delivery_checks import E1Report

ReviewStatus = Literal["pending", "approved", "changes_requested"]
ReviewAction = Literal["approve", "reject"]


class ReviewError(ValueError):
    """A decision that must not go through. `code` is for programs, the message (Vietnamese) is
    for people, `extra` carries what the caller may show (the status that was found)."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:  # Any: error body fields
        super().__init__(message)
        self.code, self.message = code, message
        self.extra: dict[str, Any] = extra


class ReviewBlock(BaseModel):
    """`review` in manifest.json: where one video stands with its reviewer."""

    model_config = ConfigDict(frozen=True)

    status: ReviewStatus = "pending"
    requested_at: str  # when the video was delivered and became waiting (ISO 8601 UTC)
    decided_at: str | None = None
    decided_by: str | None = None  # who pressed the button (a name or a Telegram id)
    reason: str | None = None  # required for changes_requested, optional note on approval
    reminded_at: str | None = None  # the one reminder; None until it is sent


def stamp(at: datetime) -> str:
    """The form used inside `review`: UTC, whole seconds, `Z`."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: str) -> datetime:
    """Both forms found in manifests: `...Z` (review) and `...+00:00` (created_at)."""
    value = datetime.fromisoformat(text)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def pending_block(requested_at: str) -> ReviewBlock:
    return ReviewBlock(status="pending", requested_at=requested_at)


def decide(
    block: ReviewBlock, action: ReviewAction, reviewer: str, reason: str | None, now: datetime
) -> ReviewBlock:
    """The block after `reviewer` pressed approve/reject.

    Raises:
        ReviewError: reviewer_required, already_decided (carries the status found) or
            reason_required (reject without a reason).
    """
    who = (reviewer or "").strip()
    if not who:
        raise ReviewError("reviewer_required", "Thiếu người duyệt: ghi tên hoặc mã của người bấm.")
    if block.status != "pending":
        raise ReviewError(
            "already_decided",
            f"Video này đã được quyết định ('{block.status}'"
            + (f" bởi {block.decided_by}" if block.decided_by else "")
            + "): không đổi được nữa.",
            current_status=block.status,
        )  # fmt: skip
    note = (reason or "").strip() or None
    if action == "reject":
        if note is None:
            raise ReviewError("reason_required", "Yêu cầu sửa phải kèm một dòng lý do.")
        status: ReviewStatus = "changes_requested"
    else:
        status = "approved"
    return block.model_copy(update={"status": status, "decided_by": who, "reason": note,
                                    "decided_at": stamp(now)})  # fmt: skip


def check_problems(e1: E1Report) -> list[str]:
    """One line per failed check ("CODE: detail"); empty when the video may be approved.

    Every check in the manifest counts: a video is approved only when all of them passed. A
    report flagged failed without a failed item (hand-edited file) still stops."""
    lines = [f"{i.code}: {i.detail}" for i in e1.items if not i.passed]
    if not lines and not e1.passed:
        lines.append("E1: báo cáo kiểm tra ghi là không đạt.")
    return lines


def is_overdue(block: ReviewBlock, now: datetime, ttl_sec: float) -> bool:
    """Waiting longer than `ttl_sec`, not decided, and not yet reminded."""
    if block.status != "pending" or block.reminded_at is not None:
        return False
    return (now - parse_time(block.requested_at)).total_seconds() > ttl_sec
