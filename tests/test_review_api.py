"""Tests: the review routes of the local API (T-F, mục 4) on a real server, no ffmpeg."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from review_fixtures import NAME, RULES, VIDEO, Tree, build, now_s

from gani_dung_video.application.api_service import ApiContext
from gani_dung_video.domain.delivery_checks import E1Item
from gani_dung_video.infrastructure.http_server import make_server
from gani_dung_video.infrastructure.settings import Settings


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def call(self, method: str, path: str, body: object | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, Any]:  # fmt: skip
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            data = json.dumps(body).encode() if body is not None else None
            sent = dict(headers or {})
            if data is not None:
                sent["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=sent)
            r = conn.getresponse()
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
        finally:
            conn.close()


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Tree, Client]]:
    """A delivered final video, waiting, inside an allowed root."""
    tree = build(tmp_path)
    yield from _serve(tree, tmp_path)


def _serve(tree: Tree, root: Path) -> Iterator[tuple[Tree, Client]]:
    rules = {**RULES, "api": {**RULES["api"], "frontend_dist": None}}
    # A fixed clock 10 minutes after the fixture's render: with the real clock the video would
    # count as overdue once review.ttl_sec has passed since 15/10/2026 (it did on 19/10).
    ctx = ApiContext(tree.db, rules, Settings(gani_dv_allowed_roots=str(root)), clock=now_s)
    server = make_server(ctx, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield tree, Client(int(server.server_address[1]))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


# ─────────────── list ───────────────


def test_the_list_shows_the_waiting_video(served: tuple[Tree, Client]) -> None:
    t, c = served
    status, body = c.call("GET", "/api/reviews")
    assert status == 200
    [item] = body["items"]
    assert item["job_id"] == t.job.id and item["video"] == NAME
    assert item["review"]["status"] == "pending" and item["review"]["failed_checks"] == []
    assert item["review"]["overdue"] is False and item["review"]["reminded_at"] is None
    assert str(t.folder) not in json.dumps(body)  # never a path


def test_the_list_filters_by_status_and_date(served: tuple[Tree, Client]) -> None:
    _t, c = served
    assert c.call("GET", "/api/reviews?status=pending")[1]["items"] != []
    assert c.call("GET", "/api/reviews?status=approved")[1]["items"] == []
    assert c.call("GET", "/api/reviews?from=2099-01-01")[1]["items"] == []
    assert c.call("GET", "/api/reviews?from=2020-01-01")[1]["items"] != []
    assert c.call("GET", "/api/reviews?status=sai")[0] == 400
    assert c.call("GET", "/api/reviews?from=ngay-mai")[0] == 400


# ─────────────── approve / reject ───────────────


def test_approving_over_the_api_moves_the_file(served: tuple[Tree, Client]) -> None:
    t, c = served
    status, body = c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "Hùng"})
    assert status == 200 and body["review"]["status"] == "approved"
    assert body["review"]["decided_by"] == "Hùng" and body["video"] == NAME
    assert (t.root / "final" / NAME).read_bytes() == VIDEO
    assert not (t.root / "drafts" / NAME).exists()


def test_the_second_press_is_a_409_with_the_state_found(served: tuple[Tree, Client]) -> None:
    t, c = served
    c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "Hùng"})
    status, body = c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "NVH"})
    assert status == 409 and body["error"]["code"] == "already_decided"
    assert body["error"]["current_status"] == "approved"
    status2, body2 = c.call("POST", f"/api/jobs/{t.job.id}/review/reject",
                            {"reviewer": "NVH", "reason": "đổi ý"})  # fmt: skip
    assert status2 == 409 and body2["error"]["code"] == "already_decided"


def test_reject_needs_a_reason_and_keeps_the_file(served: tuple[Tree, Client]) -> None:
    t, c = served
    no = c.call("POST", f"/api/jobs/{t.job.id}/review/reject", {"reviewer": "NVH", "reason": " "})
    assert no[0] == 422 and no[1]["error"]["code"] == "reason_required"
    none = c.call("POST", f"/api/jobs/{t.job.id}/review/reject", {"reviewer": "NVH"})
    assert none[0] == 422 and none[1]["error"]["code"] == "reason_required"
    ok = c.call("POST", f"/api/jobs/{t.job.id}/review/reject",
                {"reviewer": "NVH", "reason": "chữ cuối bị che"})  # fmt: skip
    assert ok[0] == 200 and ok[1]["review"]["status"] == "changes_requested"
    assert ok[1]["review"]["reason"] == "chữ cuối bị che"
    assert (t.root / "drafts" / NAME).exists() and list((t.root / "final").iterdir()) == []


def test_a_failed_check_is_a_409_listing_it(tmp_path: Path) -> None:
    bad = [E1Item(code="E1-SAFE", name="Vùng an toàn", passed=False, detail="chữ sát mép")]
    tree = build(tmp_path, extra=bad)
    for t, c in _serve(tree, tmp_path):
        status, body = c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "Hùng"})
        assert status == 409 and body["error"]["code"] == "checks_failed"
        assert body["error"]["problems"] == ["E1-SAFE: chữ sát mép"]
        listed = c.call("GET", "/api/reviews")[1]["items"][0]["review"]["failed_checks"]
        assert listed == ["E1-SAFE: chữ sát mép"]


def test_a_preview_is_a_409(tmp_path: Path) -> None:
    tree = build(tmp_path, final=False, review=None, resolution="540x960")
    for t, c in _serve(tree, tmp_path):
        status, body = c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "Hùng"})
        assert status == 409 and body["error"]["code"] == "preview_not_reviewable"
        assert c.call("GET", "/api/reviews")[1]["items"] == []


def test_unknown_jobs_and_bad_bodies(served: tuple[Tree, Client]) -> None:
    t, c = served
    assert c.call("POST", "/api/jobs/9999/review/approve", {"reviewer": "x"})[0] == 404
    assert c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {})[0] == 400  # no reviewer key
    assert c.call("POST", f"/api/jobs/{t.job.id}/review/approve",
                  {"reviewer": "x", "path": "../../etc"})[0] == 400  # fmt: skip
    empty = c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "  "})
    assert empty[0] == 422 and empty[1]["error"]["code"] == "reviewer_required"
    assert c.call("GET", f"/api/jobs/{t.job.id}/review/approve")[0] == 405


def test_a_job_outside_the_allowed_roots_is_a_404(tmp_path: Path) -> None:
    tree = build(tmp_path / "outside")
    elsewhere = tmp_path / "root"
    elsewhere.mkdir()
    for t, c in _serve(tree, elsewhere):
        assert c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "x"})[0] == 404
        assert c.call("GET", "/api/reviews")[1]["items"] == []


def test_a_foreign_origin_cannot_press_the_button(served: tuple[Tree, Client]) -> None:
    t, c = served
    status, _body = c.call("POST", f"/api/jobs/{t.job.id}/review/approve", {"reviewer": "x"},
                           headers={"Origin": "http://evil.example"})  # fmt: skip
    assert status == 403 and (t.root / "drafts" / NAME).exists()
    assert t.read().review is not None and t.read().review.status == "pending"  # type: ignore[union-attr]


# ─────────────── the job view carries the state for the badge ───────────────


def test_the_job_view_has_the_review_state(served: tuple[Tree, Client]) -> None:
    t, c = served
    job = c.call("GET", f"/api/jobs/{t.job.id}")[1]
    assert job["review"]["status"] == "pending"
    c.call("POST", f"/api/jobs/{t.job.id}/review/reject", {"reviewer": "NVH", "reason": "sửa"})
    again = c.call("GET", f"/api/jobs/{t.job.id}")[1]
    assert again["review"]["status"] == "changes_requested" and again["review"]["reason"] == "sửa"
    listed = next(j for j in c.call("GET", "/api/jobs")[1] if j["id"] == t.job.id)
    assert listed["review"]["status"] == "changes_requested"


def test_a_job_that_was_never_a_final_render_has_no_review(tmp_path: Path) -> None:
    tree = build(tmp_path, final=False, review=None, resolution="540x960")
    for t, c in _serve(tree, tmp_path):
        assert c.call("GET", f"/api/jobs/{t.job.id}")[1]["review"] is None
