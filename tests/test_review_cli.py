"""Tests: `review list | approve | reject` (T-F, mục 4), in-process and as two real processes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from review_fixtures import NAME, VIDEO, Tree, build

from gani_dung_video.cli import main
from gani_dung_video.domain.delivery_checks import E1Item


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tree:
    t = build(tmp_path)
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "q"))
    monkeypatch.delenv("GANI_DV_ALLOWED_ROOTS", raising=False)
    return t


def test_list_shows_the_waiting_video(tree: Tree, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["review", "list"]) == 0
    out = capsys.readouterr().out
    assert f"#{tree.job.id}" in out and NAME in out and "chờ duyệt" in out
    assert str(tree.folder) not in out  # the folder's name, never its path


def test_list_is_empty_after_the_decision_unless_asked(
    tree: Tree, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["review", "approve", str(tree.job.id), "--nguoi-duyet", "Hùng"]) == 0
    capsys.readouterr()
    assert main(["review", "list"]) == 0
    assert NAME not in capsys.readouterr().out
    assert main(["review", "list", "--trang-thai", "approved"]) == 0
    assert NAME in capsys.readouterr().out
    assert main(["review", "list", "--trang-thai", "tat-ca", "--tu-ngay", "2020-01-01"]) == 0
    assert NAME in capsys.readouterr().out


def test_approve_moves_the_file_and_exits_zero(tree: Tree) -> None:
    assert main(["review", "approve", str(tree.job.id), "--nguoi-duyet", "Hùng"]) == 0
    assert (tree.root / "final" / NAME).read_bytes() == VIDEO
    assert not (tree.root / "drafts" / NAME).exists()
    m = tree.read()
    assert m.review is not None and m.review.decided_by == "Hùng"


def test_the_reviewer_defaults_to_the_operating_system_user(tree: Tree) -> None:
    assert main(["review", "approve", str(tree.job.id)]) == 0
    m = tree.read()
    assert m.review is not None and m.review.decided_by


def test_reject_without_a_reason_exits_two_and_changes_nothing(
    tree: Tree, caplog: pytest.LogCaptureFixture
) -> None:
    before = tree.meta.read_text(encoding="utf-8")
    assert main(["review", "reject", str(tree.job.id), "--nguoi-duyet", "NVH"]) == 2
    assert main(["review", "reject", str(tree.job.id), "--nguoi-duyet", "NVH", "--ly-do", " "]) == 2
    assert "lý do" in caplog.text
    assert tree.meta.read_text(encoding="utf-8") == before


def test_reject_with_a_reason_is_recorded(tree: Tree) -> None:
    code = main(["review", "reject", str(tree.job.id), "--nguoi-duyet", "NVH",
                 "--ly-do", "chữ cuối bị che"])  # fmt: skip
    assert code == 0
    m = tree.read()
    assert m.review is not None and m.review.status == "changes_requested"
    assert m.review.reason == "chữ cuối bị che" and (tree.root / "drafts" / NAME).exists()


def test_a_refusal_prints_the_failed_checks_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bad = [E1Item(code="E1-SAFE", name="Vùng an toàn", passed=False, detail="chữ sát mép")]
    t = build(tmp_path, extra=bad)
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "q"))
    assert main(["review", "approve", str(t.job.id), "--nguoi-duyet", "Hùng"]) == 2
    assert "E1-SAFE: chữ sát mép" in caplog.text
    assert (t.root / "drafts" / NAME).exists()


def test_two_real_processes_pressing_at_once_let_exactly_one_through(
    tree: Tree, tmp_path: Path
) -> None:
    """CLI and API are separate processes: the lock file must hold between them."""
    src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONUTF8": "1",
           "GANI_DV_QUEUE_DIR": str(tmp_path / "q")}  # fmt: skip
    env.pop("GANI_DV_ALLOWED_ROOTS", None)
    cmd = [sys.executable, "-m", "gani_dung_video", "review", "approve", str(tree.job.id),
           "--nguoi-duyet"]  # fmt: skip
    procs = [subprocess.Popen([*cmd, who], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for who in ("A", "B")]  # fmt: skip
    codes = sorted(p.wait(timeout=120) for p in procs)
    for p in procs:
        for stream in (p.stdout, p.stderr):
            if stream is not None:
                stream.close()
    assert codes == [0, 2]
    assert (tree.root / "final" / NAME).read_bytes() == VIDEO
    m = tree.read()
    assert m.review is not None and m.review.decided_by in ("A", "B")
    assert not list(tree.root.glob("metadata/*.lock"))
