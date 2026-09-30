"""Rules for serving the built web UI from `serve` (phase 6g). Pure: no file or network I/O.

Why strict: the UI files come from a folder next to code and secrets (`.env`, `.git`), and a URL
is attacker-shaped text. So a path is accepted only when every segment is plain, the file type
is one we know by extension, and hidden names never leave the folder. The filesystem side
(resolving symlinks, checking the file is really inside the folder) is in infrastructure.
"""

from __future__ import annotations

from urllib.parse import unquote

# Fixed table: never the OS registry (`mimetypes` reads it on Windows and gets `.js` wrong).
STATIC_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
}
HTML = ".html"
INDEX = "index.html"
# Vite gives files under /assets a content hash in the name: safe to cache for a year.
IMMUTABLE_DIR = "assets"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"  # keep a copy but ask (ETag) before every use
_API_PREFIXES = ("api", "media")


def safe_segments(url_path: str) -> list[str] | None:
    """URL path → clean path segments, or None when the path must not be served at all.

    Each segment is percent-decoded exactly once, then refused when it could leave the folder or
    reach something hidden: a separator (`/`, `\\`), a drive/stream colon (`x::$DATA` on
    Windows), NUL or another control character, `.` / `..`, a leading dot (`.env`, `.git`), or
    invalid UTF-8. An empty segment in the middle (`//`) is refused too; a trailing slash is fine.

    Example:
        >>> safe_segments("/assets/app.js")
        ['assets', 'app.js']
        >>> safe_segments("/%2e%2e/.env") is None
        True
    """
    if not url_path.startswith("/") or url_path.startswith("//"):
        return None
    raw = url_path[1:].split("/")
    if raw and raw[-1] == "":
        raw = raw[:-1]  # trailing slash
    out: list[str] = []
    for part in raw:
        if part == "":
            return None
        try:
            seg = unquote(part, errors="strict")
        except UnicodeDecodeError:
            return None
        if _unsafe(seg):
            return None
        out.append(seg)
    return out


def _unsafe(seg: str) -> bool:
    if seg in {"", ".", ".."} or seg.startswith("."):
        return True
    if any(c in seg for c in "/\\:"):
        return True
    return any(ord(c) < 32 or ord(c) == 127 for c in seg)


def extension_of(name: str) -> str:
    """Lower-case extension including the dot ('' when there is none)."""
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def content_type_for(name: str) -> str | None:
    """Fixed Content-Type by extension; None for an unknown type (the caller answers 404)."""
    return STATIC_TYPES.get(extension_of(name))


def is_api_path(segments: list[str]) -> bool:
    """/api/... and /media/... belong to the API and are never answered with a UI file."""
    return bool(segments) and segments[0] in _API_PREFIXES


def is_spa_route(segments: list[str]) -> bool:
    """A page route of the UI (no file extension, not an API path): it gets index.html so a
    reload or a typed URL works. A missing file such as /assets/x.js is a real 404."""
    if is_api_path(segments):
        return False
    return not segments or extension_of(segments[-1]) == ""


def cache_policy(segments: list[str]) -> str:
    """Cache-Control for a UI file: hashed build assets are immutable, the rest revalidates."""
    if len(segments) >= 2 and segments[0] == IMMUTABLE_DIR:
        return CACHE_IMMUTABLE
    return CACHE_REVALIDATE


def etag_of(mtime_ns: int, size: int) -> str:
    """Weak validator from modification time and size (cheap, changes on every rebuild)."""
    return f'W/"{mtime_ns:x}-{size:x}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """`If-None-Match` names this ETag (or `*`): the client copy is current → answer 304."""
    if not if_none_match:
        return False
    tags = [t.strip() for t in if_none_match.split(",")]
    return "*" in tags or etag in tags
