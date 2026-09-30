"""Tests: domain/static_paths.py — which URL paths may reach a UI file (phase 6g)."""

from __future__ import annotations

import pytest

from gani_dung_video.domain.static_paths import (
    CACHE_IMMUTABLE,
    CACHE_REVALIDATE,
    cache_policy,
    content_type_for,
    etag_matches,
    etag_of,
    extension_of,
    is_api_path,
    is_spa_route,
    safe_segments,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", []),
        ("/index.html", ["index.html"]),
        ("/assets/app.js", ["assets", "app.js"]),
        ("/assets/", ["assets"]),  # a trailing slash is harmless
        ("/studio", ["studio"]),
        ("/a%20b.png", ["a b.png"]),  # decoded once
        ("/%C4%91.png", ["đ.png"]),  # UTF-8
        ("/%252e%252e", ["%2e%2e"]),  # double-encoded: decoded ONCE, so just an odd file name
    ],
)
def test_clean_paths_become_segments(path: str, expected: list[str]) -> None:
    assert safe_segments(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/../config/rules.json",
        "/assets/../../.env",
        "/%2e%2e/.env",
        "/%2E%2E/x",
        "/assets/..%5c..%5c.env",
        "/assets/%2e%2e%2fsecret",
        "/a\\b",
        "/.env",
        "/.git/config",
        "/assets/.gitkeep",
        "/%2eenv",
        "/a/%00",
        "/x%0d%0ay",
        "/x::$DATA",
        "/C:/Windows/win.ini",
        "//evil/x",
        "/a//b",
        "/%ff%fe",  # invalid UTF-8
        "/./x",
        "relative/path",
        "",
    ],
)
def test_dangerous_paths_are_refused(path: str) -> None:
    assert safe_segments(path) is None


def test_content_types_come_from_a_fixed_table() -> None:
    assert content_type_for("app.JS") == "text/javascript; charset=utf-8"
    assert content_type_for("x.css") == "text/css; charset=utf-8"
    assert content_type_for("f.woff2") == "font/woff2"
    assert content_type_for("nhap.mp4") == "video/mp4"


@pytest.mark.parametrize("name", ["README", "x.exe", "x.bat", "x.py", "x.pem", "x.key", "x.sqlite"])
def test_unknown_or_risky_extensions_have_no_type(name: str) -> None:
    assert content_type_for(name) is None


def test_extension_helper() -> None:
    assert extension_of("a.b.JS") == ".js"
    assert extension_of("noext") == ""
    assert extension_of(".hidden") == ""


@pytest.mark.parametrize(
    "segments,expected",
    [
        ([], True),
        (["studio"], True),
        (["queue", "12"], True),
        (["assets", "missing.js"], False),  # a missing FILE is a real 404
        (["api"], False),
        (["api", "nope"], False),
        (["media", "9", "video"], False),
    ],
)
def test_only_extensionless_non_api_paths_are_ui_pages(segments: list[str], expected: bool) -> None:
    assert is_spa_route(segments) is expected


def test_api_prefixes() -> None:
    assert is_api_path(["api", "jobs"]) and is_api_path(["media", "1", "video"])
    assert not is_api_path(["apix"]) and not is_api_path([])


def test_cache_policy_is_immutable_only_for_hashed_assets() -> None:
    assert cache_policy(["assets", "index-Dx1.js"]) == CACHE_IMMUTABLE
    assert cache_policy(["index.html"]) == CACHE_REVALIDATE
    assert cache_policy([]) == CACHE_REVALIDATE
    assert cache_policy(["assets"]) == CACHE_REVALIDATE  # the folder name alone is no asset


def test_etag_validation() -> None:
    tag = etag_of(255, 4096)
    assert tag == 'W/"ff-1000"'
    assert etag_matches(tag, tag)
    assert etag_matches(f'W/"other", {tag}', tag)
    assert etag_matches("*", tag)
    assert not etag_matches('W/"other"', tag)
    assert not etag_matches(None, tag) and not etag_matches("", tag)
