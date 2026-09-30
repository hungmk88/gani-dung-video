"""Tests: `serve` also hands out the built web UI (phase 6g) — a REAL server on a loopback port.

What a browser or curl would hit: page routes, hashed assets, HTTP caching (ETag / 304), Range,
the security headers on HTML, and — above all — that /api and /media are untouched and that no
path can reach a file outside the UI folder.
"""

from __future__ import annotations

import copy
import http.client
import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.api_service import ApiContext
from gani_dung_video.infrastructure import static_files
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.http_server import make_server
from gani_dung_video.infrastructure.queue_db import QueueDb
from gani_dung_video.infrastructure.settings import Settings

RULES = load_rules()
INDEX = b"<!doctype html><title>GANI</title><div id=root></div>"
JS = b"console.log('gani-ui');"
Result = tuple[int, dict[str, str], bytes]


class Ui:
    def __init__(self, server: Any, root: Path) -> None:
        self.server, self.dist = server, root
        self.port = int(server.server_address[1])

    def get(self, path: str, method: str = "GET", headers: dict[str, str] | None = None,
            host: str | None = None, body: bytes | None = None) -> Result:  # fmt: skip
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                conn.putheader("Host", host)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            if body is not None:
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()


Start = Callable[..., Ui]


@pytest.fixture
def start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Start]:
    """Build a repo-shaped folder, point the server at its frontend/dist, run it."""
    repo = tmp_path / "repo"
    servers: list[tuple[Any, threading.Thread]] = []
    monkeypatch.setattr(static_files, "REPO_ROOT", repo)

    def _start(build: bool = True, dist_setting: object = "frontend/dist") -> Ui:
        dist = repo / "frontend" / "dist"
        (dist / "assets").mkdir(parents=True, exist_ok=True)
        if build:
            (dist / "index.html").write_bytes(INDEX)
            (dist / "assets" / "app-abc123.js").write_bytes(JS)
            (dist / ".env").write_text("SECRET=1", encoding="utf-8")
        (repo / "secret.txt").write_text("outside the UI folder", encoding="utf-8")
        rules = copy.deepcopy(RULES)
        rules["api"]["frontend_dist"] = dist_setting
        root = tmp_path / "allowed"
        root.mkdir(exist_ok=True)
        ctx = ApiContext(QueueDb(tmp_path / "q" / "queue.db"), rules,
                         Settings(gani_dv_allowed_roots=str(root)))  # fmt: skip
        server = make_server(ctx, port=0)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        servers.append((server, t))
        return Ui(server, dist)

    try:
        yield _start
    finally:
        for server, t in servers:
            server.shutdown()
            server.server_close()
            t.join(timeout=10)


def error_code(body: bytes) -> str:
    return str(json.loads(body)["error"]["code"])


# ─────────────── pages and assets ───────────────


def test_the_root_serves_the_built_index_with_security_headers(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/")
    assert status == 200 and body == INDEX
    assert h["content-type"] == "text/html; charset=utf-8"
    assert h["content-security-policy"] == RULES["api"]["frontend_csp"]
    assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert h["x-content-type-options"] == "nosniff"
    assert h["cache-control"] == "no-cache" and h["etag"].startswith('W/"')
    assert int(h["content-length"]) == len(INDEX)


def test_ui_page_routes_fall_back_to_the_index(start: Start) -> None:
    ui = start()
    for path in ("/studio", "/queue/12", "/index.html", "/assets/"):
        status, _h, body = ui.get(path)
        assert status == 200 and body == INDEX, path


def test_a_hashed_asset_is_cached_forever_and_has_no_page_headers(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/assets/app-abc123.js")
    assert status == 200 and body == JS
    assert h["content-type"] == "text/javascript; charset=utf-8"
    assert h["cache-control"] == "public, max-age=31536000, immutable"
    assert "content-security-policy" not in h and h["x-content-type-options"] == "nosniff"


def test_a_missing_asset_is_a_real_404_not_the_index(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/assets/missing.js")
    assert status == 404 and error_code(body) == "not_found"
    assert h["content-type"].startswith("application/json")


def test_a_matching_etag_gets_304_without_a_body(start: Start) -> None:
    ui = start()
    _s, h, _b = ui.get("/")
    status, h2, body = ui.get("/", headers={"If-None-Match": h["etag"]})
    assert status == 304 and body == b"" and h2["etag"] == h["etag"]
    status, _h, body = ui.get("/", headers={"If-None-Match": 'W/"stale"'})
    assert status == 200 and body == INDEX


def test_head_has_headers_and_no_body(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/", method="HEAD")
    assert status == 200 and body == b"" and int(h["content-length"]) == len(INDEX)


def test_an_asset_supports_one_byte_range(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/assets/app-abc123.js", headers={"Range": "bytes=0-6"})
    assert status == 206 and body == JS[:7]
    assert h["content-range"] == f"bytes 0-6/{len(JS)}" and h["accept-ranges"] == "bytes"


def test_only_get_and_head_are_allowed_on_pages(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/", method="POST", body=b"{}")
    assert status == 405 and h["allow"] == "GET, HEAD" and error_code(body) == "method_not_allowed"
    assert ui.get("/studio", method="DELETE")[0] == 405


# ─────────────── the API and media are untouched ───────────────


def test_api_and_media_paths_are_never_answered_with_the_ui(start: Start) -> None:
    ui = start()
    for path in ("/api/nope", "/api", "/api/jobs/1/zzz", "/media/9/video", "/media", "/media/x/y"):
        status, h, body = ui.get(path)
        assert status == 404, path
        assert h["content-type"].startswith("application/json") and error_code(body) == "not_found"


def test_real_api_answers_keep_their_no_store_cache_header(start: Start) -> None:
    ui = start()
    status, h, body = ui.get("/api/health")
    assert status == 200 and json.loads(body)["ok"] is True
    assert h["cache-control"] == "no-store" and "content-security-policy" not in h


# ─────────────── nothing outside the folder ───────────────


@pytest.mark.parametrize(
    "path",
    ["/.env", "/../secret.txt", "/%2e%2e/secret.txt", "/%2E%2E/secret.txt",
     "/assets/../../secret.txt", "/assets/..%5c..%5csecret.txt", "/x::$DATA",
     "/a/%00", "//evil/x", "/%2e%2e%2fsecret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"],
)  # fmt: skip
def test_no_path_reaches_a_file_outside_or_a_hidden_one(start: Start, path: str) -> None:
    ui = start()
    status, _h, body = ui.get(path)
    assert b"outside the UI folder" not in body and b"SECRET" not in body
    # "//evil/x" and "/x::$DATA" are ordinary page routes at worst (the index); the rest are 404
    assert status in (200, 404) and (status == 404 or body == INDEX)


# ─────────────── the same gate as the API ───────────────


def test_a_foreign_host_is_refused_on_the_ui_too(start: Start) -> None:
    ui = start()
    status, _h, body = ui.get("/", host="evil.com:8765")
    assert status == 403 and error_code(body) == "host_khong_hop_le"


def test_a_page_embedded_from_another_site_is_refused(start: Start) -> None:
    ui = start()
    status, _h, body = ui.get("/", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403 and error_code(body) == "yeu_cau_cheo_trang"
    assert ui.get("/", headers={"Sec-Fetch-Site": "none"})[0] == 200  # a typed address


# ─────────────── not built / switched off ───────────────


def test_without_a_build_the_server_runs_and_the_root_is_404(start: Start) -> None:
    ui = start(build=False)
    status, _h, body = ui.get("/")
    assert status == 404 and error_code(body) == "not_found"
    assert ui.get("/api/health")[0] == 200


def test_a_build_made_after_start_is_served_without_a_restart(start: Start) -> None:
    ui = start(build=False)
    assert ui.get("/")[0] == 404
    (ui.dist / "index.html").write_bytes(INDEX)
    status, _h, body = ui.get("/")
    assert status == 200 and body == INDEX


@pytest.mark.parametrize("setting", [None, "", "../outside"])
def test_the_ui_can_be_switched_off_or_misconfigured(start: Start, setting: object) -> None:
    ui = start(dist_setting=setting)
    assert ui.get("/")[0] == 404 and ui.get("/assets/app-abc123.js")[0] == 404
    assert ui.get("/api/health")[0] == 200


def test_the_missing_build_is_logged_once_at_start(
    start: Start, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        start(build=False)
    assert sum("npm run build" in r.getMessage() for r in caplog.records) == 1
