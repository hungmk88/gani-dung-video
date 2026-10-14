"""Tests: infrastructure/static_files.py — the UI file behind a URL, inside the dist folder."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gani_dung_video.domain.static_paths import CACHE_IMMUTABLE, CACHE_REVALIDATE
from gani_dung_video.infrastructure.static_files import dist_root, has_index, resolve_static


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>GANI</title>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "assets" / "app-abc123.css").write_text("body{}", encoding="utf-8")
    (root / "assets" / "notes.exe").write_bytes(b"MZ")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return root


def test_the_root_and_page_routes_get_index_html(dist: Path) -> None:
    for url in ("/", "/studio", "/queue/12", "/assets/"):
        f = resolve_static(dist, url)
        assert f is not None and f.path.name == "index.html", url
        assert f.is_html and f.cache_control == CACHE_REVALIDATE
        assert f.content_type == "text/html; charset=utf-8"


def test_hashed_assets_are_typed_and_cached_forever(dist: Path) -> None:
    js = resolve_static(dist, "/assets/app-abc123.js")
    css = resolve_static(dist, "/assets/app-abc123.css")
    assert js is not None and css is not None
    assert js.content_type == "text/javascript; charset=utf-8" and not js.is_html
    assert css.content_type == "text/css; charset=utf-8"
    assert js.cache_control == CACHE_IMMUTABLE
    assert js.size == len("console.log(1)") and js.etag.startswith('W/"')


def test_files_outside_the_page_routes_revalidate(dist: Path) -> None:
    f = resolve_static(dist, "/favicon.svg")
    assert f is not None and f.cache_control == CACHE_REVALIDATE


def test_a_missing_file_is_not_replaced_by_the_index(dist: Path) -> None:
    assert resolve_static(dist, "/assets/missing.js") is None
    assert resolve_static(dist, "/nope.png") is None


@pytest.mark.parametrize(
    "url",
    ["/.env", "/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt",
     "/assets/..%5c..%5csecret.txt", "/x::$DATA", "//evil/x", "/a/%00"],
)  # fmt: skip
def test_nothing_outside_or_hidden_is_served(dist: Path, url: str) -> None:
    assert resolve_static(dist, url) is None


def test_an_unknown_file_type_is_never_served(dist: Path) -> None:
    assert resolve_static(dist, "/assets/notes.exe") is None


def test_a_symlink_out_of_the_folder_is_refused(dist: Path, tmp_path: Path) -> None:
    link = dist / "assets" / "leak.txt"
    try:
        os.symlink(tmp_path / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege on this Windows account")
    assert resolve_static(dist, "/assets/leak.txt") is None


def test_a_symlink_inside_the_folder_is_fine(dist: Path) -> None:
    link = dist / "assets" / "alias.js"
    try:
        os.symlink(dist / "assets" / "app-abc123.js", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege on this Windows account")
    f = resolve_static(dist, "/assets/alias.js")
    assert f is not None and f.content_type.startswith("text/javascript")


def test_without_a_build_nothing_is_found(tmp_path: Path) -> None:
    empty = tmp_path / "dist"
    empty.mkdir()
    assert not has_index(empty)
    assert resolve_static(empty, "/") is None
    assert resolve_static(empty, "/studio") is None
    assert resolve_static(tmp_path / "does-not-exist", "/") is None


def test_dist_root_is_taken_from_the_repo_root(tmp_path: Path) -> None:
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    assert (
        dist_root({"frontend_dist": "frontend/dist"}, tmp_path)
        == (tmp_path / "frontend" / "dist").resolve()
    )


@pytest.mark.parametrize("value", [None, "", "   ", 7])
def test_dist_root_off_when_unset(tmp_path: Path, value: object) -> None:
    assert dist_root({"frontend_dist": value}, tmp_path) is None
    assert dist_root({}, tmp_path) is None


@pytest.mark.parametrize("value", [
    "../elsewhere", "frontend/../../up", "/etc",
    # A drive path is absolute only on Windows; on POSIX "C:/Windows" is a folder named "C:"
    # inside the repo, so it is rightly served there (CI runs on Linux).
    pytest.param("C:/Windows", marks=pytest.mark.skipif(os.name != "nt", reason="Windows path")),
])  # fmt: skip
def test_dist_root_outside_the_repo_is_refused(tmp_path: Path, value: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert dist_root({"frontend_dist": value}, repo) is None


def _junction(link: Path, target: Path) -> bool:
    """A Windows directory junction (no privilege needed). False when not on Windows or it fails."""
    if os.name != "nt":
        return False
    done = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False
    )
    return done.returncode == 0


def test_a_junction_out_of_the_folder_is_refused(dist: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.js").write_text("stolen", encoding="utf-8")
    if not _junction(dist / "assets" / "jump", outside):
        pytest.skip("directory junctions are a Windows feature")
    assert resolve_static(dist, "/assets/jump/leak.js") is None
