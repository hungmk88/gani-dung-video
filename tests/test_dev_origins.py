"""Tests: the API trusts no browser origin but its own unless GANI_DV_DEV_ORIGINS adds one.

The shipped list used to hold the Vite dev server (localhost:5173), so any page served on that
port (another project's dev server, a hijacked npm package) could call the API as the user,
approvals included. Audit A5, option A: no foreign origin by default; the UI developer opts in.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from http.client import HTTPConnection
from pathlib import Path

import pytest

from gani_dung_video.application.api_service import ApiContext
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.http_server import make_server
from gani_dung_video.infrastructure.queue_db import QueueDb
from gani_dung_video.infrastructure.settings import Settings, dev_origins

RULES = load_rules()
VITE = "http://localhost:5173"
Call = Callable[[str, dict[str, str]], tuple[int, dict[str, str]]]


def test_the_shipped_list_trusts_no_foreign_origin() -> None:
    assert RULES["api"]["allowed_origins"] == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("  ", []),
        ("http://localhost:5173", ["http://localhost:5173"]),
        (" http://localhost:5173 , http://127.0.0.1:5173 ",
         ["http://localhost:5173", "http://127.0.0.1:5173"]),
        ("http://localhost:5173/", ["http://localhost:5173"]),  # a trailing slash is not part of it
        ("*", []),  # no wildcard, ever
        ("null", []),
        ("localhost:5173", []),  # no scheme
        ("ftp://localhost:5173", []),
        ("http://localhost:5173/app", []),  # an origin has no path
        ("http://evil.com, http://localhost:5173", ["http://evil.com", "http://localhost:5173"]),
    ],
)  # fmt: skip
def test_the_dev_origin_list_is_parsed_strictly(raw: str | None, expected: list[str]) -> None:
    assert dev_origins(Settings(gani_dv_dev_origins=raw)) == expected


@pytest.fixture
def serve(tmp_path: Path) -> Iterator[Callable[[str | None], Call]]:
    servers: list[tuple[object, threading.Thread]] = []

    def start(origins: str | None) -> Call:
        rules = {**RULES, "api": {**RULES["api"], "frontend_dist": None}}
        ctx = ApiContext(QueueDb(tmp_path / f"q{len(servers)}" / "queue.db"), rules,
                         Settings(gani_dv_dev_origins=origins))  # fmt: skip
        server = make_server(ctx, port=0)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        servers.append((server, t))
        port = int(server.server_address[1])

        def call(path: str, headers: dict[str, str]) -> tuple[int, dict[str, str]]:
            con = HTTPConnection("127.0.0.1", port, timeout=10)
            con.request("GET", path, headers=headers)
            r = con.getresponse()
            r.read()
            out = {k.lower(): v for k, v in r.getheaders()}
            con.close()
            return r.status, out

        return call

    yield start
    for server, t in servers:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]
        t.join(timeout=10)


def test_the_vite_origin_is_refused_by_default(serve: Callable[[str | None], Call]) -> None:
    call = serve(None)
    status, headers = call("/api/health", {"Origin": VITE})
    assert status == 403 and "access-control-allow-origin" not in headers
    # a cross-site fetch that only carries a Referer from that port is refused as well
    site = {"Sec-Fetch-Site": "cross-site", "Referer": VITE + "/x"}
    assert call("/api/health", site)[0] == 403


def test_no_origin_header_still_works_for_command_line_clients(
    serve: Callable[[str | None], Call],
) -> None:
    assert serve(None)("/api/health", {})[0] == 200


def test_opting_in_trusts_exactly_the_listed_origins(serve: Callable[[str | None], Call]) -> None:
    call = serve(VITE)
    status, headers = call("/api/health", {"Origin": VITE})
    assert status == 200 and headers["access-control-allow-origin"] == VITE
    assert call("/api/health", {"Origin": "http://127.0.0.1:5173"})[0] == 403  # not listed
    assert call("/api/health", {"Origin": "http://evil.com"})[0] == 403
