"""Tests: infrastructure/http_server.py — a REAL server on a free loopback port (6b).

Requests go through http.client, so what is tested is what a browser or curl would hit: Host /
Origin / Content-Type rules, CORS, limits, JSON errors, and HTTP Range for video.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video import cli
from gani_dung_video.application import api_service
from gani_dung_video.application.api_service import ApiContext
from gani_dung_video.domain.jobs import Estimate
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.http_server import (
    ApiServerError,
    cross_site_ok,
    host_allowed,
    make_server,
    origin_allowed,
    parse_range,
)
from gani_dung_video.infrastructure.queue_db import QueueDb
from gani_dung_video.infrastructure.settings import Settings

RULES = load_rules()
ALLOWED = RULES["api"]["allowed_origins"]
VITE = "http://localhost:5173"
VIDEO = b"0123456789" * 100  # 1000 bytes


# ─────────────── pure helpers ───────────────


@pytest.mark.parametrize(
    "host,ok",
    [("127.0.0.1:8765", True), ("localhost:8765", True), ("LOCALHOST:8765", True),
     ("127.0.0.1", False), ("localhost", False), ("127.0.0.1:9", False), ("evil.com", False),
     ("evil.com:8765", False), ("127.0.0.1.evil.com:8765", False),
     ("localhost:8765@evil.com", False),
     ("", False), (None, False), ("[::1]:8765", False), ("0.0.0.0:8765", False)],
)  # fmt: skip
def test_host_allowlist(host: str | None, ok: bool) -> None:
    assert host_allowed(host, 8765) is ok


@pytest.mark.parametrize(
    "origin,ok",
    [(None, True), (VITE, True), ("http://127.0.0.1:5173", True), ("HTTP://LOCALHOST:5173", True),
     (VITE + "/", True), ("http://localhost:8765", True), ("http://127.0.0.1:8765", True),
     ("http://evil.com", False), ("http://localhost:5174", False),
     ("https://localhost:5173", False),
     ("null", False), ("", False), ("http://localhost:5173.evil.com", False)],
)  # fmt: skip
def test_origin_allowlist(origin: str | None, ok: bool) -> None:
    assert origin_allowed(origin, ALLOWED, 8765) is ok


@pytest.mark.parametrize(
    "site,origin,referer,ok",
    [(None, None, None, True), ("same-origin", None, None, True), ("same-site", None, None, True),
     ("none", None, None, True), ("cross-site", None, None, False),
     ("cross-site", VITE, None, True), ("cross-site", "http://evil.com", None, False),
     ("cross-site", None, VITE + "/studio", True), ("cross-site", None, "http://evil.com/x", False),
     ("cross-site", None, "not a url", False)],
)  # fmt: skip
def test_cross_site_rule(site: str | None, origin: str | None, referer: str | None,
                         ok: bool) -> None:  # fmt: skip
    assert cross_site_ok(site, origin, referer, ALLOWED, 8765) is ok


@pytest.mark.parametrize(
    "header,expected",
    [(None, None), ("", None), ("bytes=0-99", (0, 99)), ("bytes=990-", (990, 999)),
     ("bytes=-5", (995, 999)), ("bytes=0-99999", (0, 999)), ("bytes=0-0", (0, 0)),
     ("bytes=-5000", (0, 999)), ("bytes=1000-", "unsatisfiable"),
     ("bytes=2000-3000", "unsatisfiable"), ("bytes=-0", "unsatisfiable"),
     ("bytes=5-2", None), ("bytes=abc", None), ("bytes=", None), ("items=0-5", None),
     ("bytes=0-1,5-6", None), ("bytes=-", None), ("bytes= 1-2", (1, 2))],
)  # fmt: skip
def test_range_parsing(header: str | None, expected: object) -> None:
    assert parse_range(header, 1000) == expected


def test_an_empty_file_has_no_satisfiable_range() -> None:
    assert parse_range("bytes=0-", 0) == "unsatisfiable"
    assert parse_range(None, 0) is None


# ─────────────── a running server ───────────────


def project(root: Path) -> Path:
    folder = root / "footage"
    (folder / "edit").mkdir(parents=True)
    opts = VideoOptions(phong_cach="ke_chuyen_nghe", muc_dich="gioi_thieu_xuong", do_dai_giay=15,
                        so_ban_nhap=2, nhac="khong_nhac")  # fmt: skip
    plan = compile_plan(opts, RULES, music=None, available_tags=set())
    (folder / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    (folder / "edit" / "drafts.json").write_text("{}", encoding="utf-8")
    return folder


class Api:
    def __init__(self, server: Any, ctx: ApiContext) -> None:
        self.server, self.ctx = server, ctx
        self.port = int(server.server_address[1])

    def call(
        self, method: str, path: str, body: object | None = None,
        headers: dict[str, str] | None = None, host: str | None = None,
        raw: bytes | None = None, json_type: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:  # fmt: skip
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                conn.putheader("Host", host)
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None
                                                else None)  # fmt: skip
            sent = dict(headers or {})
            if data is not None and json_type and "Content-Type" not in sent:
                sent["Content-Type"] = "application/json"
            for k, v in sent.items():
                conn.putheader(k, v)
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def json(self, method: str, path: str, body: object | None = None, **kw: Any) -> Any:
        status, _h, data = self.call(method, path, body, **kw)
        return status, json.loads(data) if data else None

    def finish(self, jid: int, final: bool = False) -> Path:
        job = self.ctx.db.claim(time.time(), 1)
        assert job is not None and job.id == jid
        out = Path(job.project_dir) / "edit" / "render" / job.draft
        out.mkdir(parents=True, exist_ok=True)
        name = f"video_{job.draft}_1080x1920.mp4" if final else f"nhap_{job.draft}_540p.mp4"
        (out / name).write_bytes(VIDEO)
        report = {"video": name, "passed": True, "rounds": 1, "font_px": {},
                  "loudness_two_pass": False, "fixes": [], "findings": [], "lufs": -14.0,
                  "true_peak_db": -2.0}  # fmt: skip
        (out / "qc_report.json").write_text(json.dumps(report), encoding="utf-8")
        self.ctx.db.finish(jid, time.time(), "completed", "passed", 0, None, str(out / name))
        return out


@pytest.fixture
def api(tmp_path: Path) -> Iterator[Api]:
    root = tmp_path / "allowed"
    root.mkdir()
    project(root)
    ctx = ApiContext(QueueDb(tmp_path / "q" / "queue.db"), RULES,
                     Settings(gani_dv_allowed_roots=str(root)))  # fmt: skip
    server = make_server(ctx, port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield Api(server, ctx)
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=10)


def queue(api: Api, **body: Any) -> int:
    status, data = api.json("POST", "/api/jobs", {"project_id": "0/footage", "draft": "A", **body})
    assert status == 201, data
    return int(data[0]["id"])


# ─────────────── binding ───────────────


def test_only_the_loopback_address_may_be_bound(api: Api) -> None:
    with pytest.raises(ApiServerError, match=r"127\.0\.0\.1"):
        make_server(api.ctx, host="0.0.0.0", port=0)
    with pytest.raises(ApiServerError, match=r"127\.0\.0\.1"):
        make_server(api.ctx, host="localhost", port=0)


def test_a_port_already_in_use_is_reported_not_shared(api: Api) -> None:
    # On Windows SO_REUSEADDR would let a second server silently share the port.
    with pytest.raises(ApiServerError, match=str(api.port)):
        make_server(api.ctx, port=api.port)


# ─────────────── routes ───────────────


def test_health(api: Api) -> None:
    status, headers, body = api.call("GET", "/api/health")
    assert status == 200 and headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store" and headers["x-content-type-options"] == "nosniff"
    data = json.loads(body)
    assert data["ok"] and data["allowed_roots_configured"] and data["jobs"]["queued"] == 0


def test_server_banner_does_not_reveal_versions(api: Api) -> None:
    _status, headers, _body = api.call("GET", "/api/health")
    assert "python" not in headers.get("server", "").lower()


def test_projects_have_opaque_ids_and_no_paths(api: Api) -> None:
    status, data = api.json("GET", "/api/projects")
    assert status == 200 and data[0]["id"] == "0/footage" and data[0]["ready_to_render"]
    assert "allowed" not in json.dumps(data)


def test_create_list_show_cancel_retry(api: Api) -> None:
    status, created = api.json("POST", "/api/jobs", {"project_id": "0/footage"})
    assert status == 201 and [j["draft"] for j in created] == ["A", "B"]
    jid = created[0]["id"]
    status, listing = api.json("GET", "/api/jobs?status=queued&limit=10")
    assert status == 200 and {j["id"] for j in listing} == {j["id"] for j in created}
    status, one = api.json("GET", f"/api/jobs/{jid}")
    assert status == 200 and one["frontend_status"] == "queued" and one["events"] == []
    status, cancelled = api.json("POST", f"/api/jobs/{jid}/cancel")
    assert status == 200 and cancelled["status"] == "cancelled"
    status, again = api.json("POST", f"/api/jobs/{jid}/retry")
    assert status == 200 and again["status"] == "queued"
    status, err = api.json("POST", f"/api/jobs/{jid}/retry")  # queued now: cannot retry
    assert status == 409 and err["error"]["code"] == "conflict"


def test_events_cursor(api: Api) -> None:
    jid = queue(api)
    for i in range(3):
        api.ctx.db.add_event(jid, time.time(), "info", f"dòng {i}")
    status, events = api.json("GET", f"/api/jobs/{jid}/events?after=0")
    assert status == 200 and [e["text"] for e in events] == ["dòng 0", "dòng 1", "dòng 2"]
    status, rest = api.json("GET", f"/api/jobs/{jid}/events?after={events[1]['id']}")
    assert [e["text"] for e in rest] == ["dòng 2"]


def test_preflight_route(api: Api) -> None:
    good = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 15,
            "so_ban_nhap": 1, "nhac": "khong_nhac"}  # fmt: skip
    status, ok = api.json("POST", "/api/preflight", {"options": good})
    assert status == 200 and ok["valid"] is True
    status, bad = api.json("POST", "/api/preflight", {"options": {**good, "do_dai_giay": 7}})
    assert status == 200 and bad["valid"] is False


def test_errors_are_json_with_a_code_and_a_vietnamese_message(api: Api) -> None:
    status, data = api.json("GET", "/api/jobs/999")
    assert status == 404 and set(data["error"]) == {"code", "message"}
    assert "999" in data["error"]["message"]
    status, data = api.json("POST", "/api/jobs", {"project_id": "0/ghost"})
    assert status == 404
    status, data = api.json("POST", "/api/jobs", {"project_id": "0/../etc"})
    assert status == 400 and "mã dự án" in data["error"]["message"]
    status, data = api.json("POST", "/api/jobs", {"project_id": "0/footage", "draft": "Z"})
    assert status == 400


@pytest.mark.parametrize("path", ["/", "/api", "/api/nope", "/api/jobs/abc", "/api/jobs/1/zzz",
                                  "/api/jobs/12345678901", "/media/1", "/media/1/video/x",
                                  "/../etc/passwd", "/api/jobs/1%2f..%2f"])  # fmt: skip
def test_unknown_paths_are_404_json(api: Api, path: str) -> None:
    status, data = api.json("GET", path)
    assert status == 404 and data["error"]["code"] == "not_found"


def test_wrong_methods_are_405_with_an_allow_header(api: Api) -> None:
    status, headers, body = api.call("DELETE", "/api/jobs/1")
    assert status == 405 and json.loads(body)["error"]["code"] == "method_not_allowed"
    status, headers, _ = api.call("POST", "/api/health", {})
    assert status == 405 and headers["allow"] == "GET, HEAD, OPTIONS"
    assert api.call("PUT", "/api/jobs", {})[0] == 405


@pytest.mark.parametrize("query", ["status=bogus", "limit=abc", "limit=0", "limit=-1",
                                   "limit=9999", "project_id=0/..", "project_id=9/x"])  # fmt: skip
def test_bad_query_values_are_refused_not_ignored(api: Api, query: str) -> None:
    status, data = api.json("GET", f"/api/jobs?{query}")
    assert status in (400, 404) and "error" in data


def test_bad_event_cursors_are_400(api: Api) -> None:
    jid = queue(api)
    for after in ("abc", "-1", "1.5", ""):
        assert api.call("GET", f"/api/jobs/{jid}/events?after={after}")[0] == 400


# ─────────────── security: Host, Origin, Content-Type, size ───────────────


@pytest.mark.parametrize("route", ["/api/health", "/api/projects", "/api/jobs", "/media/1/video"])
def test_a_foreign_host_is_refused_everywhere(api: Api, route: str) -> None:
    for host in ("evil.com", f"evil.com:{api.port}", "127.0.0.1"):
        status, _h, body = api.call("GET", route, host=host)
        assert status == 403 and json.loads(body)["error"]["code"] == "host_khong_hop_le"


def test_a_request_without_a_host_header_is_refused(api: Api) -> None:
    with socket.create_connection(("127.0.0.1", api.port), timeout=10) as s:
        s.sendall(b"GET /api/health HTTP/1.0\r\n\r\n")
        assert b" 403 " in s.recv(4096).split(b"\r\n")[0]


def test_a_localhost_host_header_is_fine(api: Api) -> None:
    assert api.call("GET", "/api/health", host=f"localhost:{api.port}")[0] == 200


def test_a_post_from_a_foreign_origin_is_refused_and_nothing_happens(api: Api) -> None:
    status, headers, body = api.call("POST", "/api/jobs", {"project_id": "0/footage"},
                                     headers={"Origin": "http://evil.com"})  # fmt: skip
    assert status == 403 and json.loads(body)["error"]["code"] == "origin_khong_hop_le"
    assert "access-control-allow-origin" not in headers
    assert api.ctx.db.list_jobs() == []


def test_the_vite_origin_gets_cors_headers(api: Api) -> None:
    status, headers, _ = api.call("POST", "/api/jobs", {"project_id": "0/footage", "draft": "A"},
                                  headers={"Origin": VITE})  # fmt: skip
    assert status == 201 and headers["access-control-allow-origin"] == VITE
    assert headers["vary"] == "Origin"


def test_a_post_without_an_origin_is_allowed(api: Api) -> None:
    """curl and other non-browser clients send no Origin; a browser always does on a POST."""
    assert api.call("POST", "/api/jobs", {"project_id": "0/footage", "draft": "A"})[0] == 201


def test_a_get_from_a_foreign_origin_is_refused(api: Api) -> None:
    status, headers, _ = api.call("GET", "/api/health", headers={"Origin": "http://evil.com"})
    assert status == 403 and "access-control-allow-origin" not in headers
    status, headers, _ = api.call("GET", "/api/health", headers={"Origin": VITE})
    assert status == 200 and headers["access-control-allow-origin"] == VITE


def test_cross_site_fetches_need_a_trusted_origin_or_referer(api: Api) -> None:
    site = {"Sec-Fetch-Site": "cross-site"}
    assert api.call("GET", "/api/health", headers=site)[0] == 403
    assert api.call("GET", "/api/health", headers={**site, "Referer": VITE + "/x"})[0] == 200
    assert api.call("GET", "/api/health", headers={**site, "Origin": VITE})[0] == 200
    assert api.call("GET", "/api/health", headers={**site, "Referer": "http://evil.com/"})[0] == 403


def test_preflight_options(api: Api) -> None:
    ask = {"Origin": VITE, "Access-Control-Request-Method": "POST",
           "Access-Control-Request-Headers": "content-type"}  # fmt: skip
    status, headers, body = api.call("OPTIONS", "/api/jobs", headers=ask)
    assert status == 204 and body == b""
    assert headers["access-control-allow-origin"] == VITE
    assert "POST" in headers["access-control-allow-methods"]
    assert "content-type" in headers["access-control-allow-headers"].lower()
    bad = api.call("OPTIONS", "/api/jobs", headers={**ask, "Origin": "http://evil.com"})
    assert bad[0] == 403 and "access-control-allow-origin" not in bad[1]


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data", "application/jsonx", ""])  # fmt: skip
def test_a_post_must_be_application_json(api: Api, ctype: str) -> None:
    """text/plain is what a cross-site <form> or fetch can send without a CORS preflight."""
    headers = {"Content-Type": ctype} if ctype else {}
    status, _h, body = api.call("POST", "/api/jobs", raw=b'{"project_id": "0/footage"}',
                                headers=headers, json_type=False)  # fmt: skip
    assert status == 415 and json.loads(body)["error"]["code"] == "content_type_no"
    assert api.ctx.db.list_jobs() == []


def test_json_with_a_charset_is_accepted(api: Api) -> None:
    ctype = {"Content-Type": "application/json; charset=utf-8"}
    status, _h, _b = api.call("POST", "/api/jobs", {"project_id": "0/footage", "draft": "A"},
                              headers=ctype)  # fmt: skip
    assert status == 201


def test_a_body_over_the_limit_is_refused_unread(api: Api) -> None:
    big = b'{"project_id": "' + b"x" * (RULES["api"]["max_body_bytes"] + 10) + b'"}'
    status, _h, body = api.call("POST", "/api/jobs", raw=big)
    assert status == 413 and json.loads(body)["error"]["code"] == "than_qua_lon"


def test_chunked_bodies_are_refused(api: Api) -> None:
    with socket.create_connection(("127.0.0.1", api.port), timeout=10) as s:
        head = (f"POST /api/jobs HTTP/1.1\r\nHost: 127.0.0.1:{api.port}\r\n"
                "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")  # fmt: skip
        s.sendall(head.encode() + b"2\r\n{}\r\n0\r\n\r\n")
        assert b" 411 " in s.recv(4096).split(b"\r\n")[0]


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2]", b'"text"', b"null", b"{}",
                                 b'{"project_id": "0/footage", "extra": 1}',
                                 b'{"project_id": 5}', b'{"project_id": "0/footage", "final": 1}',
                                 b'{"project_id": "0/footage", "draft": ["A"]}'])  # fmt: skip
def test_malformed_bodies_are_400_and_change_nothing(api: Api, raw: bytes) -> None:
    status, _h, body = api.call("POST", "/api/jobs", raw=raw)
    assert status == 400 and json.loads(body)["error"]["code"] == "bad_request"
    assert api.ctx.db.list_jobs() == []


def test_cancel_and_retry_accept_no_body_at_all_but_a_content_type_must_be_json(
    api: Api,
) -> None:
    jid = queue(api)
    assert api.call("POST", f"/api/jobs/{jid}/cancel")[0] == 200  # no body, no Content-Type
    status, _h, _b = api.call("POST", f"/api/jobs/{jid}/retry", raw=b"", json_type=False,
                              headers={"Content-Type": "text/plain"})  # fmt: skip
    assert status == 415  # a Content-Type that is present must be application/json


def test_an_internal_error_leaks_nothing(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("C:\\secret\\path traceback")

    monkeypatch.setattr(api_service, "list_projects", boom)
    status, _h, body = api.call("GET", "/api/projects")
    assert status == 500
    assert json.loads(body)["error"]["code"] == "loi_may_chu"
    assert b"secret" not in body and b"Traceback" not in body and b"RuntimeError" not in body


def test_a_slow_client_does_not_block_others(api: Api) -> None:
    slow = socket.create_connection(("127.0.0.1", api.port), timeout=10)
    try:
        slow.sendall(b"POST /api/jobs HTTP/1.1\r\nHost: x\r\n")  # never finishes its headers
        assert api.call("GET", "/api/health")[0] == 200  # a thread per connection
    finally:
        slow.close()


def test_keep_alive_connections_serve_several_requests(api: Api) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", api.port, timeout=10)
    try:
        for _ in range(3):
            conn.request("GET", "/api/health")
            r = conn.getresponse()
            assert r.status == 200 and json.loads(r.read())["ok"]
    finally:
        conn.close()


# ─────────────── media ───────────────


def test_video_is_served_whole_with_range_support_advertised(api: Api) -> None:
    jid = queue(api)
    api.finish(jid)
    status, headers, body = api.call("GET", f"/media/{jid}/video")
    assert status == 200 and body == VIDEO
    assert headers["content-type"] == "video/mp4" and headers["accept-ranges"] == "bytes"
    assert headers["content-length"] == "1000" and headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "rng,start,end",
    [("bytes=0-99", 0, 99), ("bytes=990-", 990, 999), ("bytes=-5", 995, 999),
     ("bytes=10-10", 10, 10)],
)  # fmt: skip
def test_range_requests_get_206_and_exactly_those_bytes(api: Api, rng: str, start: int,
                                                        end: int) -> None:  # fmt: skip
    jid = queue(api)
    api.finish(jid)
    status, headers, body = api.call("GET", f"/media/{jid}/video", headers={"Range": rng})
    assert status == 206 and body == VIDEO[start : end + 1]
    assert headers["content-range"] == f"bytes {start}-{end}/1000"
    assert headers["content-length"] == str(end - start + 1)


def test_an_unsatisfiable_range_is_416(api: Api) -> None:
    jid = queue(api)
    api.finish(jid)
    status, headers, _ = api.call("GET", f"/media/{jid}/video", headers={"Range": "bytes=5000-"})
    assert status == 416 and headers["content-range"] == "bytes */1000"


@pytest.mark.parametrize("rng", ["bytes=0-1,5-6", "garbage", "bytes=9-3"])
def test_ranges_we_do_not_serve_fall_back_to_the_whole_file(api: Api, rng: str) -> None:
    jid = queue(api)
    api.finish(jid)
    status, _h, body = api.call("GET", f"/media/{jid}/video", headers={"Range": rng})
    assert status == 200 and body == VIDEO


def test_head_returns_the_headers_only(api: Api) -> None:
    jid = queue(api)
    api.finish(jid)
    status, headers, body = api.call("HEAD", f"/media/{jid}/video")
    assert status == 200 and body == b"" and headers["content-length"] == "1000"


def test_cover_and_srt_are_served_with_fixed_types(api: Api) -> None:
    jid = queue(api)
    out = api.finish(jid)
    (out / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (out / "phude.srt").write_text("1\nxin chào\n", encoding="utf-8")
    assert api.call("GET", f"/media/{jid}/cover")[1]["content-type"] == "image/jpeg"
    status, headers, body = api.call("GET", f"/media/{jid}/srt")
    assert status == 200 and headers["content-type"].startswith("text/plain")
    assert "xin chào" in body.decode("utf-8")


def test_job_detail_links_lead_to_files_that_exist(api: Api) -> None:
    jid = queue(api)
    api.finish(jid)
    status, one = api.json("GET", f"/api/jobs/{jid}")
    assert status == 200 and one["video_url"] == f"/media/{jid}/video"
    assert one["qa"]["lufs"] == -14.0
    assert api.call("GET", one["video_url"])[0] == 200


def test_media_of_an_unfinished_job_or_a_bad_kind_is_404(api: Api) -> None:
    jid = queue(api)
    assert api.call("GET", f"/media/{jid}/video")[0] == 404
    api.finish(jid)
    for bad in ("exe", "..", "VIDEO", "video.mp4"):
        assert api.call("GET", f"/media/{jid}/{bad}")[0] == 404


def test_media_needs_a_trusted_host_and_origin_too(api: Api) -> None:
    jid = queue(api)
    api.finish(jid)
    assert api.call("GET", f"/media/{jid}/video", host="evil.com")[0] == 403
    assert api.call("GET", f"/media/{jid}/video", headers={"Origin": "http://evil.com"})[0] == 403
    site = {"Sec-Fetch-Site": "cross-site"}
    assert api.call("GET", f"/media/{jid}/video", headers=site)[0] == 403
    assert api.call("GET", f"/media/{jid}/video", headers={**site, "Referer": VITE + "/"})[0] == 200


def test_a_client_that_hangs_up_mid_video_does_not_hurt_the_server(api: Api) -> None:
    jid = queue(api)
    out = api.finish(jid)
    (out / "nhap_A_540p.mp4").write_bytes(b"x" * 20_000_000)  # far more than one socket buffer
    conn = http.client.HTTPConnection("127.0.0.1", api.port, timeout=10)
    conn.request("GET", f"/media/{jid}/video")
    r = conn.getresponse()
    r.read(10)
    conn.close()  # hang up with almost everything unsent
    assert api.call("GET", "/api/health")[0] == 200


# ─────────────── the `serve` command ───────────────


class FakeServer:
    """Stands in for the real server: 'runs' until Ctrl+C."""

    def __init__(self) -> None:
        self.server_address = ("127.0.0.1", 4321)
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_cli_serve_starts_prints_the_address_and_stops_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeServer()
    seen: dict[str, Any] = {}

    def fake_make(ctx: ApiContext, host: str | None = None, port: int | None = None) -> FakeServer:
        seen["port"] = port
        return fake

    monkeypatch.setattr(cli, "make_server", fake_make)
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "qdir"))
    assert cli.main(["serve", "--port", "0"]) == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:4321" in out and seen["port"] == 0 and fake.closed


def test_cli_serve_warns_when_no_allow_list_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "make_server", lambda ctx, host=None, port=None: FakeServer())
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "qdir"))
    monkeypatch.delenv("GANI_DV_ALLOWED_ROOTS", raising=False)
    monkeypatch.setattr("gani_dung_video.infrastructure.settings.ENV_FILE", tmp_path / "no.env")
    assert cli.main(["serve"]) == 0
    assert "GANI_DV_ALLOWED_ROOTS" in capsys.readouterr().out


def test_cli_serve_exits_2_when_the_port_is_taken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api: Api
) -> None:
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "qdir"))
    assert cli.main(["serve", "--port", str(api.port)]) == 2


# ─────────────── 6c-3: pipelines, approval and job kinds over real HTTP ───────────────

WIZARD = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 15,
          "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip


def new_pipeline(api: Api, name: str = "fresh", **body: Any) -> dict[str, Any]:
    (Path(api.ctx.settings.gani_dv_allowed_roots or "") / name).mkdir(exist_ok=True)
    status, data = api.json(
        "POST", "/api/pipelines", {"project_id": f"0/{name}", "options": WIZARD, **body}
    )
    assert status == 201, data
    return dict(data)


def hold_draft(api: Api, view: dict[str, Any]) -> int:
    analyze: int = view["jobs"][0]["id"]
    draft: int = view["jobs"][1]["id"]
    assert api.ctx.db.claim(time.time(), 1).id == analyze  # type: ignore[union-attr]
    api.ctx.db.finish(analyze, time.time(), "completed", "passed", 0, None, None)
    assert api.ctx.db.claim(time.time(), 1).id == draft  # type: ignore[union-attr]
    est = Estimate(model="m", input_tokens=1678, input_only_vnd=85.58, worst_call_vnd=4165.58,
                   cap_vnd=20000.0, max_attempts=3)  # fmt: skip
    api.ctx.db.hold_for_approval(draft, time.time(), est, 86_400.0)
    return draft


def test_posting_a_pipeline_answers_201_with_the_whole_chain(api: Api) -> None:
    view = new_pipeline(api)
    assert [j["kind"] for j in view["jobs"]] == ["analyze", "draft", "render", "render"]
    assert view["status"] == "queued" and view["needs_approval"] is False
    got, again = api.json("GET", f"/api/pipelines/{view['id']}")
    assert got == 200 and [j["id"] for j in again["jobs"]] == [j["id"] for j in view["jobs"]]


def test_a_second_pipeline_on_the_same_project_is_409_with_the_running_pipeline_id(
    api: Api,
) -> None:
    first = new_pipeline(api)
    status, body = api.json("POST", "/api/pipelines", {"project_id": "0/fresh", "options": WIZARD})
    assert status == 409 and body["error"]["code"] == "pipeline_active"
    assert body["error"]["pipeline_id"] == first["id"]
    assert body["error"]["job_id"] == first["jobs"][0]["id"]
    assert "message" in body["error"] and "\\" not in body["error"]["message"]


def test_bad_pipeline_requests_are_400_and_write_nothing(api: Api) -> None:
    root = Path(api.ctx.settings.gani_dv_allowed_roots or "")
    (root / "fresh").mkdir()
    for body in ({"project_id": "0/fresh"}, {"project_id": "0/fresh", "options": []},
                 {"project_id": "0/fresh", "options": {**WIZARD, "phong_cach": "sai"}},
                 {"project_id": "0/fresh", "options": WIZARD, "draft": "Z"},
                 {"project_id": "0/fresh", "options": WIZARD, "extra": 1}):  # fmt: skip
        status, data = api.json("POST", "/api/pipelines", body)
        assert status == 400 and data["error"]["code"] == "bad_request", body
    assert not (root / "fresh" / "edit").exists() and api.ctx.db.list_jobs() == []


def test_pipeline_routes_keep_the_json_only_rules(api: Api) -> None:
    (Path(api.ctx.settings.gani_dv_allowed_roots or "") / "fresh").mkdir()
    status, _h, _b = api.call("POST", "/api/pipelines", raw=b"project_id=0/fresh",
                              headers={"Content-Type": "text/plain"})  # fmt: skip
    assert status == 415
    status, _h, _b = api.call(
        "POST", "/api/pipelines", raw=b"x" * (RULES["api"]["max_body_bytes"] + 10)
    )
    assert status == 413
    status, _h, _b = api.call("GET", "/api/pipelines")
    assert status in (404, 405)
    body = {"project_id": "0/fresh", "options": WIZARD}
    status, _h, _b = api.call("POST", "/api/pipelines", body,
                              headers={"Origin": "http://evil.com"})  # fmt: skip
    assert status == 403


def test_an_unknown_pipeline_is_404(api: Api) -> None:
    status, body = api.json("GET", "/api/pipelines/999")
    assert status == 404 and body["error"]["code"] == "not_found"


def test_the_waiting_draft_job_shows_its_cost_over_http(api: Api) -> None:
    draft = hold_draft(api, new_pipeline(api))
    status, job = api.json("GET", f"/api/jobs/{draft}")
    assert status == 200 and job["frontend_status"] == "awaiting_approval"
    assert job["needs_approval"] is True and job["cost"]["worst_call_vnd"] == 4165.58
    assert job["cost"]["approved_vnd"] is None and job["approval_expires_at"].endswith("Z")


def test_approving_over_http(api: Api) -> None:
    draft = hold_draft(api, new_pipeline(api))
    status, job = api.json(
        "POST", f"/api/jobs/{draft}/approve", {"max_vnd": 6000, "estimate_vnd": 4166}
    )
    assert status == 200 and job["status"] == "queued" and job["cost"]["approved_vnd"] == 6000.0


def test_a_refused_approval_is_409_and_bad_numbers_are_400(api: Api) -> None:
    draft = hold_draft(api, new_pipeline(api))
    status, body = api.json(
        "POST", f"/api/jobs/{draft}/approve", {"max_vnd": 6000, "estimate_vnd": 100}
    )
    assert status == 409 and body["error"]["code"] == "approval_refused"
    for bad in ({"max_vnd": 6000}, {"max_vnd": -1, "estimate_vnd": 4166},
                {"max_vnd": "6000", "estimate_vnd": 4166}, {}):  # fmt: skip
        status, body = api.json("POST", f"/api/jobs/{draft}/approve", bad)
        assert status == 400 and body["error"]["code"] == "bad_request", bad
    assert api.ctx.db.get(draft).status == "awaiting_approval"  # type: ignore[union-attr]


def test_approve_is_post_only_and_unknown_jobs_are_404(api: Api) -> None:
    status, _h, _b = api.call("GET", "/api/jobs/1/approve")
    assert status == 405
    status, body = api.json("POST", "/api/jobs/999/approve", {"max_vnd": 6000, "estimate_vnd": 1})
    assert status == 404 and body["error"]["code"] == "not_found"


def test_the_job_list_takes_a_kind_filter(api: Api) -> None:
    new_pipeline(api)
    status, every = api.json("GET", "/api/jobs")
    assert status == 200 and len(every) == 4
    status, drafts = api.json("GET", "/api/jobs?kind=draft")
    assert status == 200 and [j["kind"] for j in drafts] == ["draft"]
    status, body = api.json("GET", "/api/jobs?kind=video")
    assert status == 400 and body["error"]["code"] == "bad_request"


def test_health_tells_the_ui_whether_a_key_exists_but_never_shows_it(api: Api) -> None:
    status, h = api.json("GET", "/api/health")
    assert status == 200 and h["ai_key_configured"] is False
    assert h["budget"] == {"limit_vnd": None, "spent_24h_vnd": 0.0, "reserved_vnd": 0.0}


def test_a_refused_body_is_read_out_before_closing_so_the_answer_is_not_lost(api: Api) -> None:
    """Closing a socket that still holds unread bytes makes the OS reset the connection, and the
    client loses the 415 (seen on Windows under load). The server lets the client finish first."""
    body = b"x" * 40_000
    for _ in range(40):
        status, _h, data = api.call(
            "POST",
            "/api/pipelines",
            raw=body,
            headers={"Content-Type": "text/plain"},
            json_type=False,
        )
        assert status == 415 and json.loads(data)["error"]["code"] == "content_type_no"
