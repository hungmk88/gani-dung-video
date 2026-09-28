"""CLI + infrastructure tests: exit codes, file checks, Vietnamese output on Windows consoles."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gani_dung_video.cli import main
from gani_dung_video.infrastructure.config_files import (
    ConfigError,
    load_glossary,
    load_rules,
    read_options_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "yeu-cau"
SRC = Path(__file__).resolve().parents[1] / "src"


def test_exit_0_when_valid(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preflight", str(FIXTURES / "00-hop-le-phu-luc-d.md")]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_exit_1_when_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preflight", str(FIXTURES / "X01-tvc-3-phut.md")]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "X1"


def test_json_options_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "o.json"
    f.write_text(json.dumps({"phong_cach": "sang_trong", "muc_dich": "ke_chuyen_thuong_hieu",
                             "do_dai_giay": 30, "so_ban_nhap": 1, "nhac": "ai_tu_chon"}),
                 encoding="utf-8")  # fmt: skip
    assert main(["preflight", str(f)]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("name", ["missing.md", "notes.txt"])
def test_exit_2_on_unreadable_file(tmp_path: Path, name: str) -> None:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert main(["preflight", str(tmp_path / name)]) == 2


def test_exit_2_on_bad_usage() -> None:
    assert main([]) == 2


def test_read_options_rejects_oversized_file(tmp_path: Path) -> None:
    f = tmp_path / "big.md"
    f.write_bytes(b"x" * 1_000_001)
    with pytest.raises(ConfigError, match="quá lớn"):
        read_options_file(f)


def test_broken_json_is_reported_with_line(tmp_path: Path) -> None:
    f = tmp_path / "rules.json"
    f.write_text('{\n "a": ,\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match="dòng 2"):
        load_rules(f)


def test_rules_missing_section_is_named(tmp_path: Path) -> None:
    f = tmp_path / "rules.json"
    f.write_text('{"rules_version": "2.0"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="styles"):
        load_rules(f)


def test_bad_glossary_shape_is_reported(tmp_path: Path) -> None:
    f = tmp_path / "glossary.json"
    f.write_text('{"terms": [{"correct": 1}], "banned_claims": []}', encoding="utf-8")
    with pytest.raises(ConfigError, match="sai cấu trúc"):
        load_glossary(f)


def test_cp1252_console_does_not_crash() -> None:
    # Regression: a Windows console (cp1252) crashed on Vietnamese output.
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(SRC)}
    p = subprocess.run(
        [sys.executable, "-m", "gani_dung_video", "preflight", str(FIXTURES / "X01-tvc-3-phut.md")],
        capture_output=True, env=env, timeout=60, check=False,
    )  # fmt: skip
    assert p.returncode == 1, p.stderr.decode("utf-8", "replace")
    assert "TVC cắt nhanh" in json.loads(p.stdout.decode("utf-8"))["error_msg"]
