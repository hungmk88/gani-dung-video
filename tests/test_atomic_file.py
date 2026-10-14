"""Tests: infrastructure/atomic_file.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gani_dung_video.infrastructure.atomic_file import write_text_atomic


def test_it_creates_and_replaces_a_file_without_leaving_a_temp(tmp_path: Path) -> None:
    f = tmp_path / "manifest.json"
    write_text_atomic(f, "một")
    assert f.read_text(encoding="utf-8") == "một"
    write_text_atomic(f, "hai")
    assert f.read_text(encoding="utf-8") == "hai"
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


def test_a_failure_keeps_the_old_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "manifest.json"
    f.write_text("cũ", encoding="utf-8")

    def boom(src: object, dst: object) -> None:
        raise OSError("đĩa đầy")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="đĩa đầy"):
        write_text_atomic(f, "mới")
    assert f.read_text(encoding="utf-8") == "cũ"
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]
