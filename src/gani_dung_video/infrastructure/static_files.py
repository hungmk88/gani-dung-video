"""Find the file for a URL inside the built UI folder (phase 6g). Read-only file access.

The folder is `rules.api.frontend_dist` (default `frontend/dist`, relative to the repo root, not to
the current directory). A file is served only when its real location, after following symlinks and
junctions, is still inside that folder.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gani_dung_video.domain.static_paths import (
    HTML,
    INDEX,
    cache_policy,
    content_type_for,
    etag_of,
    extension_of,
    is_spa_route,
    safe_segments,
)
from gani_dung_video.infrastructure.config_files import CONFIG_DIR

logger = logging.getLogger(__name__)
REPO_ROOT = CONFIG_DIR.parent


@dataclass(frozen=True)
class StaticFile:
    path: Path
    size: int
    content_type: str
    etag: str
    cache_control: str
    is_html: bool


def dist_root(cfg: Mapping[str, Any], repo_root: Path | None = None) -> Path | None:
    """The configured UI folder, or None when serving the UI is off or the setting is unsafe.

    `frontend_dist` empty/absent turns it off. A relative value is taken from the repo root; a
    value that lands outside the repo is refused (logged) so a typo cannot publish another folder.
    """
    raw = cfg.get("frontend_dist")
    if not isinstance(raw, str) or not raw.strip():
        return None
    base = (repo_root if repo_root is not None else REPO_ROOT).resolve()
    root = (base / raw).resolve()
    if not root.is_relative_to(base):
        logger.warning("api.frontend_dist nằm ngoài thư mục dự án; không phục vụ giao diện.")
        return None
    return root


def has_index(root: Path) -> bool:
    return (root / INDEX).is_file()


def resolve_static(root: Path, url_path: str) -> StaticFile | None:
    """The UI file to answer `url_path` with, or None (the caller answers 404).

    A path with an unsafe segment, an unknown extension or a real location outside `root` is
    None. A page route without a file extension (the UI's own routes) falls back to index.html.
    """
    segments = safe_segments(url_path)
    if segments is None:
        return None
    found = _locate(root, segments)
    if found is None and is_spa_route(segments):
        found = _locate(root, [INDEX])
        segments = [INDEX]
    if found is None:
        return None
    ctype = content_type_for(found.name)
    if ctype is None:
        return None
    st = found.stat()
    return StaticFile(
        path=found, size=st.st_size, content_type=ctype, etag=etag_of(st.st_mtime_ns, st.st_size),
        cache_control=cache_policy(segments), is_html=extension_of(found.name) == HTML,
    )  # fmt: skip


def _locate(root: Path, segments: list[str]) -> Path | None:
    target = root.joinpath(*(segments or [INDEX]))
    try:
        real = target.resolve()
        base = root.resolve()
    except OSError:
        return None
    if not real.is_relative_to(base):
        return None  # a symlink or junction pointing out of the UI folder
    return real if real.is_file() else None
