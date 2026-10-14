"""The local web API server (phase 6b): standard-library http.server on 127.0.0.1 only.

Every request passes the same gate before anything else runs:
1. Host must be 127.0.0.1:<port> or localhost:<port> (stops DNS rebinding);
2. an Origin header, when present, must be a trusted origin (the dev UI, or this server itself);
3. a `Sec-Fetch-Site: cross-site` request needs a trusted Origin or Referer;
4. a POST body must be JSON (Content-Type application/json, size-limited, Content-Length).
Errors are always {"error": {"code", "message"}} in Vietnamese and never carry paths or
tracebacks. Threads serve connections independently, so a stalled client blocks nobody; at
most `rules.api.max_concurrent_requests` at once, a connection past that is answered 503.

From phase 6g the same server also hands out the built web UI (`rules.api.frontend_dist`) for
any GET/HEAD path that is not /api/... or /media/...; see domain/static_paths.py for the rules.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal, cast
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, ValidationError

from gani_dung_video.application import api_service
from gani_dung_video.application.api_service import ApiContext, ApiError
from gani_dung_video.domain.api_models import (
    ApproveRequest,
    JobCreate,
    PipelineCreate,
    PreflightRequest,
)
from gani_dung_video.domain.static_paths import etag_matches, is_api_path, safe_segments
from gani_dung_video.infrastructure.settings import dev_origins
from gani_dung_video.infrastructure.static_files import (
    StaticFile,
    dist_root,
    has_index,
    resolve_static,
)

logger = logging.getLogger(__name__)
_LINGER_BYTES = 1 << 20  # most unread body we will drain before closing a refused request
_LINGER_SEC = 2.0
_LOOPBACK = "127.0.0.1"
_ID = r"(\d{1,9})"
# (path pattern, methods, handler name); HEAD is served wherever GET is.
_ROUTES: list[tuple[re.Pattern[str], tuple[str, ...], str]] = [
    (re.compile(p), m, h)
    for p, m, h in [
        (r"^/api/health$", ("GET",), "health"),
        (r"^/api/projects$", ("GET",), "projects"),
        (r"^/api/jobs$", ("GET", "POST"), "jobs"),
        (rf"^/api/jobs/{_ID}$", ("GET",), "job"),
        (rf"^/api/jobs/{_ID}/events$", ("GET",), "events"),
        (rf"^/api/jobs/{_ID}/cancel$", ("POST",), "cancel"),
        (rf"^/api/jobs/{_ID}/retry$", ("POST",), "retry"),
        (rf"^/api/jobs/{_ID}/approve$", ("POST",), "approve"),
        (r"^/api/pipelines$", ("POST",), "pipelines"),
        (rf"^/api/pipelines/{_ID}$", ("GET",), "pipeline"),
        (r"^/api/preflight$", ("POST",), "preflight"),
        (r"^/api/music$", ("GET",), "music"),
        (r"^/media/music/([^/]+)$", ("GET",), "music_media"),
        (rf"^/media/{_ID}/([a-z]{{1,10}})$", ("GET",), "media"),
    ]
]
_METHOD_ORDER = ("GET", "HEAD", "POST", "OPTIONS")


class ApiServerError(RuntimeError):
    """The server cannot start (message in Vietnamese)."""


# ─────────────── pure request rules ───────────────


def _norm(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def _own_origins(port: int) -> list[str]:
    return [f"http://{_LOOPBACK}:{port}", f"http://localhost:{port}"]


def host_allowed(host: str | None, port: int) -> bool:
    """Only 127.0.0.1:<port> and localhost:<port>: anything else is a rebinding attempt."""
    return host is not None and host.strip().lower() in {f"{_LOOPBACK}:{port}", f"localhost:{port}"}


def origin_allowed(origin: str | None, allowed: Sequence[str], port: int) -> bool:
    """No Origin (curl, same-origin GET) is fine; otherwise it must be listed or our own."""
    if origin is None:
        return True
    return _norm(origin) in {*(_norm(o) for o in allowed), *_own_origins(port)}


def cross_site_ok(site: str | None, origin: str | None, referer: str | None,
                  allowed: Sequence[str], port: int) -> bool:  # fmt: skip
    """A cross-site request (a page on another site fetching us, or embedding a <video>) is
    accepted only when it names a trusted origin, in Origin or in the Referer's origin."""
    if site is None or site.lower() != "cross-site":
        return True
    trusted = {*(_norm(o) for o in allowed), *_own_origins(port)}
    if origin is not None and _norm(origin) in trusted:
        return True
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}".lower() in trusted
    return False


def parse_range(header: str | None, size: int) -> tuple[int, int] | Literal["unsatisfiable"] | None:
    """One HTTP byte range → (first, last) inclusive. None: serve the whole file (no header,
    something we do not handle such as several ranges, or invalid syntax, which RFC 9110 says
    to ignore). "unsatisfiable": answer 416."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if m is None:
        return None
    first, last = m.group(1), m.group(2)
    if not first and not last:
        return None
    if not first:  # suffix: the last N bytes
        n = int(last)
        return "unsatisfiable" if n == 0 or size == 0 else (max(size - n, 0), size - 1)
    start = int(first)
    end = int(last) if last else size - 1
    if last and end < start:
        return None
    if start >= size:
        return "unsatisfiable"
    return start, min(end, size - 1)


# ─────────────── the server ───────────────


_BUSY_MSG = "Máy chủ đang bận (quá nhiều kết nối cùng lúc); thử lại sau giây lát."
_BUSY_BODY = json.dumps({"error": {"code": "may_chu_ban", "message": _BUSY_MSG}},
                        ensure_ascii=False).encode()  # fmt: skip
_BUSY = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\n"
         b"Content-Length: " + str(len(_BUSY_BODY)).encode() + b"\r\nRetry-After: 1\r\n"
         b"Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n"
         b"\r\n" + _BUSY_BODY)  # fmt: skip


def _refuse_busy(sock: socket.socket) -> None:
    """Answer 503 on a connection past the cap without a thread: the accept loop sends a few
    hundred bytes, never waits, and reads out what already arrived so closing does not reset
    the connection under the answer."""
    try:
        sock.settimeout(0.2)
        sock.sendall(_BUSY)
        sock.setblocking(False)
        left = _LINGER_BYTES
        while left > 0 and (data := sock.recv(65536)):
            left -= len(data)
    except OSError:
        return  # nothing more to read (BlockingIOError), or the client left


class _Server(ThreadingHTTPServer):
    # Windows lets a second server bind a port already in use when SO_REUSEADDR is on.
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 max_handlers: int) -> None:  # fmt: skip
        super().__init__(address, handler)
        # One thread per connection: without a cap, stalled connections would use them all up.
        self._max_handlers = max_handlers
        self._slots = threading.BoundedSemaphore(max_handlers)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            logger.warning("api: quá %s kết nối cùng lúc, từ chối %s", self._max_handlers,
                           client_address)  # fmt: skip
            _refuse_busy(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()  # no thread started: it will never give the place back
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that hangs up or stalls is routine; anything else goes to the log (the
        default prints a traceback to stderr)."""
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError | TimeoutError):
            logger.debug("api: client %s left: %s", client_address, exc)
        else:
            logger.error("api: lỗi ở kết nối %s", client_address, exc_info=exc)


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str, close: bool = False) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.close = status, code, message, close


def make_server(ctx: ApiContext, host: str | None = None, port: int | None = None
                ) -> ThreadingHTTPServer:  # fmt: skip
    """Bind the API (default 127.0.0.1:8765; port 0 = any free port, for tests).

    Raises:
        ApiServerError: a host other than 127.0.0.1, or the port is already taken.
    """
    cfg = ctx.rules["api"]
    host = host if host is not None else str(cfg["host"])
    port = int(cfg["port"]) if port is None else port
    if host != _LOOPBACK:
        raise ApiServerError(f"API chỉ được mở ở {_LOOPBACK} (nhận '{host}'): không bao giờ mở "
                             "ra mạng.")  # fmt: skip
    ui = dist_root(cfg)
    if ui is not None and not has_index(ui):
        logger.warning("Chưa build giao diện (%s không có index.html): chạy `npm run build:real` "
                       "trong frontend để `serve` phục vụ luôn giao diện.", ui.name)  # fmt: skip
    try:
        server = _Server((host, port), _make_handler(ctx, cfg),
                         int(cfg["max_concurrent_requests"]))  # fmt: skip
    except OSError as e:
        raise ApiServerError(f"Không mở được cổng {port}: {e.strerror or e}. Có thể một lệnh "
                             "`serve` khác đang chạy.") from e  # fmt: skip
    return server


def _make_handler(ctx: ApiContext, cfg: Mapping[str, Any]) -> type[BaseHTTPRequestHandler]:
    allowed = [*cfg["allowed_origins"], *dev_origins(ctx.settings)]
    max_body = int(cfg["max_body_bytes"])
    chunk = int(cfg["media_chunk_bytes"])
    timeout_sec = float(cfg["socket_timeout_sec"])
    ui_root = dist_root(cfg)
    csp = str(cfg.get("frontend_csp") or "")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "GaniDV"
        sys_version = ""
        # Socket timeout per operation: a stalled client is dropped, not waited for forever.
        timeout = timeout_sec

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("api %s", format % args)

        @property
        def _port(self) -> int:
            return int(cast(ThreadingHTTPServer, self.server).server_port)

        # ─────────────── sending ───────────────

        def _cors(self) -> list[tuple[str, str]]:
            origin = self.headers.get("Origin")
            if origin is None or not origin_allowed(origin, allowed, self._port):
                return []
            return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                    ("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges")]  # fmt: skip

        def _head(self, status: int, headers: Sequence[tuple[str, str]]) -> None:
            self.send_response(status)
            overridden = {k.lower() for k, _ in headers}
            defaults = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]
            for k, v in [*(d for d in defaults if d[0].lower() not in overridden),
                         *self._cors(), *headers]:  # fmt: skip
                self.send_header(k, v)
            self.end_headers()

        def _send_bytes(self, status: int, body: bytes, ctype: str,
                        extra: Sequence[tuple[str, str]] = ()) -> None:  # fmt: skip
            self._head(status, [("Content-Type", ctype), ("Content-Length", str(len(body))),
                                *extra])  # fmt: skip
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, status: int, payload: object,
                       extra: Sequence[tuple[str, str]] = ()) -> None:  # fmt: skip
            if isinstance(payload, BaseModel):
                payload = payload.model_dump(mode="json")
            elif isinstance(payload, list):
                payload = [p.model_dump(mode="json") if isinstance(p, BaseModel) else p
                           for p in payload]  # fmt: skip
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, data, "application/json; charset=utf-8", extra)

        def _send_error(self, status: int, code: str, message: str,
                        extra: Sequence[tuple[str, str]] = (),
                        fields: Mapping[str, Any] | None = None) -> None:  # fmt: skip
            body = {"code": code, "message": message, **(fields or {})}
            self._send_json(status, {"error": body}, extra)

        # ─────────────── the gate ───────────────

        def _gate(self) -> None:
            if not host_allowed(self.headers.get("Host"), self._port):
                raise _HttpError(403, "host_khong_hop_le", "Địa chỉ truy cập không được phép.",
                                 close=True)  # fmt: skip
            origin = self.headers.get("Origin")
            if not origin_allowed(origin, allowed, self._port):
                raise _HttpError(403, "origin_khong_hop_le", "Trang gọi vào không được phép.",
                                 close=True)  # fmt: skip
            if not cross_site_ok(self.headers.get("Sec-Fetch-Site"), origin,
                                 self.headers.get("Referer"), allowed, self._port):  # fmt: skip
                raise _HttpError(403, "yeu_cau_cheo_trang",
                                 "Yêu cầu từ trang khác không được phép.", close=True)  # fmt: skip

        def _read_json(self) -> dict[str, Any]:
            """The POST body as a JSON object ({} when there is none)."""
            ctype = self.headers.get("Content-Type")
            if self.headers.get("Transfer-Encoding") is not None:
                msg = "Chỉ nhận thân yêu cầu có Content-Length."
                raise _HttpError(411, "can_content_length", msg, close=True)
            raw_len = self.headers.get("Content-Length")
            if raw_len is None:
                length = 0
            elif raw_len.isdigit():
                length = int(raw_len)
            else:
                raise _HttpError(400, "bad_request", "Content-Length không hợp lệ.", close=True)
            if ctype is not None and ctype.split(";")[0].strip().lower() != "application/json":
                raise _HttpError(415, "content_type_no",
                                 "Content-Type phải là application/json.", close=True)  # fmt: skip
            if length and ctype is None:
                raise _HttpError(415, "content_type_no",
                                 "Content-Type phải là application/json.", close=True)  # fmt: skip
            if length > max_body:
                raise _HttpError(413, "than_qua_lon",
                                 f"Thân yêu cầu tối đa {max_body} byte.", close=True)  # fmt: skip
            if not length:
                return {}
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as e:  # deep nesting
                raise _HttpError(400, "bad_request", "Thân yêu cầu không phải JSON hợp lệ.") from e
            if not isinstance(data, dict):
                raise _HttpError(400, "bad_request", "Thân yêu cầu phải là một đối tượng JSON.")
            return data

        # ─────────────── dispatch ───────────────

        def _handle(self) -> None:
            try:
                self._gate()
                self._route()
            except _HttpError as e:
                if e.close:
                    self.close_connection = True  # an unread body must not poison the socket
                self._send_error(e.status, e.code, e.message)
                if e.close:
                    self._linger()
            except ApiError as e:
                self._send_error(e.status, e.code, e.message, fields=e.extra)
            except (BrokenPipeError, ConnectionError, TimeoutError):
                self.close_connection = True  # the client left; nothing to tell it
            except Exception:
                logger.exception("Lỗi không lường trước khi xử lý %s %s", self.command,
                                 self.path.split("?")[0])  # fmt: skip
                self.close_connection = True
                with_body = self.command != "HEAD"
                if with_body:
                    self._send_error(500, "loi_may_chu", "Máy chủ gặp lỗi; xem log của lệnh serve.")

        def _linger(self) -> None:
            """Let the client finish sending before the socket closes. Closing with unread bytes
            makes the OS reset the connection, and the client would lose the answer just sent."""
            try:
                self.connection.shutdown(socket.SHUT_WR)  # the answer is complete: say so
                deadline, left = time.monotonic() + _LINGER_SEC, _LINGER_BYTES
                while left > 0 and (wait := deadline - time.monotonic()) > 0:
                    self.connection.settimeout(wait)
                    data = self.connection.recv(min(65536, left))
                    if not data:
                        return
                    left -= len(data)
            except OSError:
                return  # the client left or stalled: nothing more to do

        def _route(self) -> None:
            parts = urlsplit(self.path)
            method = self.command
            for pattern, methods, name in _ROUTES:
                m = pattern.match(parts.path)
                if m is None:
                    continue
                allowed_methods = [x for x in _METHOD_ORDER
                                   if x in methods or (x == "HEAD" and "GET" in methods)
                                   or x == "OPTIONS"]  # fmt: skip
                allow = ", ".join(allowed_methods)
                if method == "OPTIONS":
                    self._send_bytes(204, b"", "text/plain", [
                        ("Allow", allow), ("Access-Control-Allow-Methods", allow),
                        ("Access-Control-Allow-Headers", "Content-Type"),
                        ("Access-Control-Max-Age", "600")])  # fmt: skip
                    return
                if method not in allowed_methods:
                    self.close_connection = True
                    self._send_error(405, "method_not_allowed", "Phương thức không được hỗ trợ.",
                                     [("Allow", allow)])  # fmt: skip
                    self._linger()  # the refused request may carry a body we never read
                    return
                query = parse_qs(parts.query, keep_blank_values=True)
                getattr(self, f"_r_{name}")(query, *m.groups())
                return
            if ui_root is not None and not self._is_api_url(parts.path):
                self._route_static(parts.path)
                return
            raise ApiError(404, "not_found", "Không có đường dẫn này.")

        def _is_api_url(self, path: str) -> bool:
            segments = safe_segments(path)
            return path == "/api" or path.startswith("/api/") or (
                segments is not None and is_api_path(segments))  # fmt: skip

        def _route_static(self, path: str) -> None:
            """The built UI: GET/HEAD only. Anything unsafe or unknown is the same 404 as ever."""
            assert ui_root is not None
            if self.command not in ("GET", "HEAD"):
                self.close_connection = True
                self._send_error(405, "method_not_allowed", "Phương thức không được hỗ trợ.",
                                 [("Allow", "GET, HEAD")])  # fmt: skip
                self._linger()
                return
            found = resolve_static(ui_root, path)
            if found is None:
                raise ApiError(404, "not_found", "Không có đường dẫn này.")
            self._send_static(found)

        def _send_static(self, f: StaticFile) -> None:
            extra = [("ETag", f.etag), ("Cache-Control", f.cache_control)]
            if f.is_html:
                extra += [("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer")]
                if csp:
                    extra.append(("Content-Security-Policy", csp))
            if etag_matches(self.headers.get("If-None-Match"), f.etag):
                self._head(304, extra)
                return
            self._send_file(f.path, f.size, [("Content-Type", f.content_type), *extra])

        def do_GET(self) -> None:
            self._handle()

        do_HEAD = do_POST = do_OPTIONS = do_PUT = do_DELETE = do_PATCH = do_GET

        # ─────────────── routes ───────────────

        def _one(self, query: Mapping[str, list[str]], key: str) -> str | None:
            vals = query.get(key)
            return vals[-1] if vals else None

        def _r_health(self, query: Mapping[str, list[str]]) -> None:
            self._send_json(200, api_service.health(ctx))

        def _r_projects(self, query: Mapping[str, list[str]]) -> None:
            self._send_json(200, api_service.list_projects(ctx))

        def _r_jobs(self, query: Mapping[str, list[str]]) -> None:
            if self.command == "POST":
                try:
                    req = JobCreate.model_validate(self._read_json())
                except ValidationError as e:
                    raise _HttpError(400, "bad_request", _validation_text(e)) from e
                self._send_json(201, api_service.create_jobs(ctx, req))
                return
            raw_limit = self._one(query, "limit")
            if raw_limit is not None and not (raw_limit.isdigit() and 1 <= int(raw_limit) <= 200):
                raise ApiError(400, "bad_request", "limit phải là số từ 1 đến 200.")
            self._send_json(200, api_service.list_jobs(
                ctx, self._one(query, "status"), self._one(query, "project_id"),
                int(raw_limit) if raw_limit else 50, self._one(query, "kind")))  # fmt: skip

        def _r_job(self, query: Mapping[str, list[str]], job_id: str) -> None:
            self._send_json(200, api_service.get_job(ctx, int(job_id)))

        def _r_events(self, query: Mapping[str, list[str]], job_id: str) -> None:
            after = self._one(query, "after")
            if after is not None and not after.isdigit():
                raise ApiError(400, "bad_request", "after phải là số nguyên không âm.")
            self._send_json(200, api_service.job_events(ctx, int(job_id), int(after or 0)))

        def _r_cancel(self, query: Mapping[str, list[str]], job_id: str) -> None:
            self._read_json()  # enforces the body rules even though nothing is read from it
            self._send_json(200, api_service.cancel(ctx, int(job_id)))

        def _r_retry(self, query: Mapping[str, list[str]], job_id: str) -> None:
            self._read_json()
            self._send_json(200, api_service.retry(ctx, int(job_id)))

        def _r_pipelines(self, query: Mapping[str, list[str]]) -> None:
            try:
                req = PipelineCreate.model_validate(self._read_json())
            except ValidationError as e:
                raise _HttpError(400, "bad_request", _validation_text(e)) from e
            self._send_json(201, api_service.create_pipeline(ctx, req))

        def _r_pipeline(self, query: Mapping[str, list[str]], pipeline_id: str) -> None:
            self._send_json(200, api_service.get_pipeline(ctx, int(pipeline_id)))

        def _r_approve(self, query: Mapping[str, list[str]], job_id: str) -> None:
            try:
                req = ApproveRequest.model_validate(self._read_json())
            except ValidationError as e:
                raise _HttpError(400, "bad_request", _validation_text(e)) from e
            self._send_json(200, api_service.approve(ctx, int(job_id), req))

        def _r_preflight(self, query: Mapping[str, list[str]]) -> None:
            try:
                req = PreflightRequest.model_validate(self._read_json())
            except ValidationError as e:
                raise _HttpError(400, "bad_request", _validation_text(e)) from e
            self._send_json(200, api_service.preflight(ctx, req))

        def _r_music(self, query: Mapping[str, list[str]]) -> None:
            self._send_json(200, api_service.list_music(ctx))

        def _r_music_media(self, query: Mapping[str, list[str]], name: str) -> None:
            media = api_service.resolve_music(ctx, unquote(name))
            headers = [("Content-Type", media.content_type), ("Content-Disposition", "inline")]
            self._send_file(media.path, media.size, headers)

        def _r_media(self, query: Mapping[str, list[str]], job_id: str, kind: str) -> None:
            media = api_service.resolve_media(ctx, int(job_id), kind)
            headers = [("Content-Type", media.content_type), ("Content-Disposition", "inline")]
            self._send_file(media.path, media.size, headers)

        def _send_file(self, path: Any, size: int, headers: Sequence[tuple[str, str]]) -> None:
            """A file with HTTP Range (one range), for job videos and for UI files alike."""
            rng = parse_range(self.headers.get("Range"), size)
            base = [*headers, ("Accept-Ranges", "bytes")]
            if rng == "unsatisfiable":
                self._head(416, [*base, ("Content-Range", f"bytes */{size}"),
                                 ("Content-Length", "0")])  # fmt: skip
                return
            start, end = rng if rng is not None else (0, size - 1)
            length = end - start + 1 if size else 0
            status = 206 if rng is not None else 200
            extra = [("Content-Range", f"bytes {start}-{end}/{size}")] if rng else []
            self._head(status, [*base, *extra, ("Content-Length", str(length))])
            if self.command == "HEAD" or not length:
                return
            sent = _stream(path, self.wfile, start, length, chunk)
            if sent != length:
                self.close_connection = True  # the file changed under us: length is now a lie

    return Handler


def _stream(path: Any, out: Any, start: int, length: int, chunk: int) -> int:
    """Copy `length` bytes of the file from `start`; returns how many were really sent."""
    sent = 0
    with open(path, "rb") as f:
        f.seek(start)
        while sent < length:
            data = f.read(min(chunk, length - sent))
            if not data:
                break
            out.write(data)
            sent += len(data)
    return sent


def _validation_text(err: ValidationError) -> str:
    parts = []
    for e in err.errors()[:3]:
        where = ".".join(str(x) for x in e["loc"]) or "dữ liệu"
        parts.append(f"{where}: {str(e['msg']).removeprefix('Value error, ')}")
    return "Dữ liệu gửi lên không đúng — " + "; ".join(parts)


ServerFactory = Callable[[ApiContext], ThreadingHTTPServer]
