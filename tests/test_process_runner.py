"""Tests: infrastructure/process_runner.py — real child processes, streaming, kill (6a).

Children are tiny `python -c` scripts, so what is tested is the real thing: pipes, process
groups and killing a whole process tree on Windows and POSIX alike.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gani_dung_video.infrastructure import process_runner
from gani_dung_video.infrastructure.process_runner import (
    RunResult,
    kill_tree,
    pid_alive,
    run_child,
)

PY = sys.executable


def run(
    code: str,
    tmp_path: Path,
    *,
    lines: list[str] | None = None,
    stop: object = None,
    stall: float = 30.0,
    timeout: float = 30.0,
    watch: list[Path] | None = None,
    started: list[int] | None = None,
    log: Path | None = None,
) -> RunResult:
    got = lines if lines is not None else []
    return run_child(
        [PY, "-c", code], tmp_path, got.append,
        stop if callable(stop) else (lambda: None),
        watch or [], stall, timeout,
        on_start=started.append if started is not None else None, poll_sec=0.05, log_path=log,
    )  # fmt: skip


def wait_dead(pid: int, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


# ─────────────── normal runs ───────────────


def test_lines_are_streamed_and_the_exit_code_is_returned(tmp_path: Path) -> None:
    got: list[str] = []
    started: list[int] = []
    res = run("print('một'); print('hai')", tmp_path, lines=got, started=started)
    assert (res.reason, res.exit_code) == ("exit", 0)
    assert got == ["một", "hai"] and len(started) == 1 and started[0] > 0


def test_a_failing_child_reports_its_code(tmp_path: Path) -> None:
    res = run("import sys; sys.exit(3)", tmp_path)
    assert (res.reason, res.exit_code) == ("exit", 3)


def test_stderr_is_merged_into_the_same_stream(tmp_path: Path) -> None:
    got: list[str] = []
    run("import sys; print('lỗi ở stderr', file=sys.stderr)", tmp_path, lines=got)
    assert got == ["lỗi ở stderr"]


def test_lines_are_written_to_the_log_file(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "1.log"
    run("print('Đang dựng bản A')", tmp_path, log=log)
    assert "Đang dựng bản A" in log.read_text(encoding="utf-8")


def test_a_missing_program_is_reported_not_raised(tmp_path: Path) -> None:
    res = run_child(["definitely-not-a-program-xyz"], tmp_path, lambda _l: None, lambda: None,
                    [], 5, 5, poll_sec=0.05)  # fmt: skip
    assert res.reason == "spawn_failed" and res.exit_code is None


# ─────────────── watchdog ───────────────


def test_a_silent_child_is_killed_as_stalled(tmp_path: Path) -> None:
    started: list[int] = []
    t0 = time.monotonic()
    res = run("import time; time.sleep(60)", tmp_path, stall=1.0, started=started)
    assert res.reason == "stalled" and res.exit_code is None
    assert time.monotonic() - t0 < 20 and wait_dead(started[0])


def test_console_output_counts_as_activity(tmp_path: Path) -> None:
    code = "import time\nfor i in range(12):\n    print(i, flush=True)\n    time.sleep(0.4)"
    res = run(code, tmp_path, stall=3.0)
    assert (res.reason, res.exit_code) == ("exit", 0)  # 5 s of work, never 3 s of silence


def test_files_growing_in_a_watched_folder_count_as_activity(tmp_path: Path) -> None:
    out = tmp_path / "render"
    out.mkdir()
    target = out / "video.mp4"
    code = (
        f"import time\nf = open(r'{target}', 'ab')\n"
        "for i in range(12):\n    f.write(b'x' * 1000); f.flush(); time.sleep(0.4)"
    )
    res = run(code, tmp_path, stall=3.0, watch=[out])
    assert (res.reason, res.exit_code) == ("exit", 0)


def test_a_chatty_but_endless_child_hits_the_overall_timeout(tmp_path: Path) -> None:
    code = "import time\nwhile True:\n    print('.', flush=True)\n    time.sleep(0.1)"
    started: list[int] = []
    res = run(code, tmp_path, timeout=1.5, started=started)
    assert res.reason == "timeout" and wait_dead(started[0])


# ─────────────── stop requests ───────────────


@pytest.mark.parametrize("why", ["cancelled", "shutdown"])
def test_a_stop_request_kills_the_child_and_says_why(tmp_path: Path, why: str) -> None:
    got: list[str] = []
    started: list[int] = []

    def stop() -> str | None:
        return why if got else None  # ask to stop once the child has said something

    code = "import time; print('bắt đầu', flush=True); time.sleep(60)"
    res = run(code, tmp_path, lines=got, stop=stop, started=started)
    assert res.reason == why and wait_dead(started[0])


def test_the_whole_process_tree_is_killed_not_only_the_child(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pidfile}', 'w').write(str(g.pid))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)"
    )  # fmt: skip
    got: list[str] = []
    started: list[int] = []
    res = run(code, tmp_path, lines=got, stop=lambda: "cancelled" if got else None,
              started=started)  # fmt: skip
    assert res.reason == "cancelled"
    grandchild = int(pidfile.read_text())
    assert wait_dead(started[0]) and wait_dead(grandchild)  # ffmpeg is a grandchild in real runs


# ─────────────── pid helpers ───────────────


def test_pid_alive_knows_itself_and_a_finished_process() -> None:
    assert pid_alive(os.getpid())
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait(timeout=30)
    assert wait_dead(p.pid)


def test_pid_alive_never_kills_the_process_it_asks_about() -> None:
    # On Windows os.kill(pid, 0) would TERMINATE the process; pid_alive must only look.
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        assert pid_alive(p.pid) and pid_alive(p.pid)
        assert p.poll() is None
    finally:
        kill_tree(p.pid)
        p.wait(timeout=30)


def test_pid_alive_rejects_nonsense() -> None:
    assert not pid_alive(0) and not pid_alive(-5)


def test_kill_tree_on_a_dead_pid_is_harmless() -> None:
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait(timeout=30)
    kill_tree(p.pid)  # already gone: must not raise


# ─────────────── POSIX group kill (faked: no real group signals are sent) ───────────────

SIGTERM, SIGKILL = int(signal.SIGTERM), 9


class _Signals:
    """Records what the POSIX branch of kill_tree sends, and to whom."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, pgid: int, dies_on: int | None) -> None:
        self.mp = monkeypatch
        self.calls: list[tuple[str, int, int]] = []
        self.dies_on = dies_on  # the signal after which the process is gone (None: never)
        self.gone = False
        monkeypatch.setattr(os, "getpgid", lambda pid: pgid, raising=False)
        monkeypatch.setattr(os, "killpg", lambda g, sig: self._send("killpg", g, sig),
                            raising=False)  # fmt: skip
        monkeypatch.setattr(os, "kill", lambda pid, sig: self._send("kill", pid, sig))
        monkeypatch.setattr(process_runner, "pid_alive", lambda pid: not self.gone)
        monkeypatch.setattr(process_runner, "_SIGKILL", SIGKILL)

    def _send(self, how: str, target: int, sig: int) -> None:
        self.calls.append((how, target, int(sig)))
        if sig == self.dies_on:
            self.gone = True

    def kill(self, pid: int, grace_sec: float) -> None:
        with self.mp.context() as m:  # only for the call: pytest itself reads sys.platform
            m.setattr(sys, "platform", "linux")
            process_runner._kill_posix(pid, grace_sec)


def test_kill_posix_signals_the_group_when_the_pid_leads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _Signals(monkeypatch, pgid=4321, dies_on=SIGTERM)
    sent.kill(4321, grace_sec=1.0)
    assert sent.calls == [("killpg", 4321, SIGTERM)]


def test_kill_posix_never_signals_a_group_the_pid_does_not_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pid read from the DB after a crash may now be an unrelated process (or our own child)
    # that shares a group with a terminal, a shell or pytest: killing the group would kill them.
    sent = _Signals(monkeypatch, pgid=1, dies_on=SIGTERM)
    sent.kill(4321, grace_sec=1.0)
    assert sent.calls == [("kill", 4321, SIGTERM)]


def test_kill_posix_escalates_to_sigkill_after_the_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _Signals(monkeypatch, pgid=4321, dies_on=SIGKILL)
    sent.kill(4321, grace_sec=0.0)
    assert sent.calls == [("killpg", 4321, SIGTERM), ("killpg", 4321, SIGKILL)]


def test_kill_posix_escalates_a_non_leader_with_kill_not_killpg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _Signals(monkeypatch, pgid=1, dies_on=SIGKILL)
    sent.kill(4321, grace_sec=0.0)
    assert sent.calls == [("kill", 4321, SIGTERM), ("kill", 4321, SIGKILL)]


def test_kill_posix_ignores_a_process_that_vanished(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(pid: int) -> int:
        raise ProcessLookupError

    sent = _Signals(monkeypatch, pgid=4321, dies_on=None)
    monkeypatch.setattr(os, "getpgid", gone, raising=False)
    sent.kill(4321, grace_sec=0.0)  # must not raise
    assert sent.calls == []
