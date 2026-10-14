"""Run one job as a child process (phase 6a): stream its lines, watch it, kill its whole tree.

Each job is the CLI in a child process (`python -m gani_dung_video render …`), so a hung
ffmpeg or a crashed model cannot take the daemon down and memory is freed after every job.
The child's stdout+stderr are merged and passed to `on_line`; a watchdog kills it when it
has been silent (no line, no file changing under the watched folders) for `stall_sec`, or
has run longer than `timeout_sec`, or when `should_stop` asks (cancel, shutdown).

Windows note: `os.kill(pid, 0)` does NOT probe a process there — any signal other than
CTRL_C/CTRL_BREAK terminates it — so `pid_alive` asks the OS through ctypes instead.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STILL_ACTIVE = 259
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # none on Windows (unused there)
_SCAN_EVERY_SEC = 1.0


@dataclass(frozen=True)
class RunResult:
    reason: str  # exit | cancelled | shutdown | stalled | timeout | spawn_failed
    exit_code: int | None


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid is running (never signals or kills it)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists, we may not look
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Every job child is `python -m gani_dung_video …` (application/daemon.py, analyze_job.py).
CLI_MARKER = "gani_dung_video"


def pid_command(pid: int) -> str | None:
    """The command line of `pid`, or None when the OS will not say (gone, no access, no tool).

    Linux reads /proc; macOS asks `ps`; Windows asks CIM through PowerShell. Only used after
    a crash, to make sure a pid read back from the queue is still OUR child before killing it.
    """
    if pid <= 0:
        return None
    proc = Path(f"/proc/{pid}/cmdline")
    if sys.platform.startswith("linux"):
        try:
            raw = proc.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return None
        return raw or None  # a zombie has an empty command line: unknown, not "someone else"
    if sys.platform == "win32":
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
        ]
    else:
        argv = ["ps", "-o", "command=", "-p", str(pid)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def is_our_child(pid: int) -> bool:
    """False only when the OS shows `pid` running something that is not our CLI.

    A pid stored before a crash may have been reused by an unrelated program (a shell, an
    editor) and must not be killed. When the command line cannot be read, the old behaviour
    stands (kill): the heartbeat-age limit of the caller already makes reuse unlikely.
    """
    cmd = pid_command(pid)
    return cmd is None or CLI_MARKER in cmd


def kill_tree(pid: int, grace_sec: float = 5.0) -> None:
    """Stop a process and everything it started (ffmpeg is a grandchild). Never raises."""
    if not pid_alive(pid):
        return
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           timeout=30, check=False)  # fmt: skip
        return
    _kill_posix(pid, grace_sec)


def _kill_posix(pid: int, grace_sec: float) -> None:
    """SIGTERM, then SIGKILL after `grace_sec`; the group only if `pid` leads it.

    `run_child` starts every job in its own session, so a job's pid is its group leader and
    the group holds ffmpeg too. A pid that does NOT lead its group (a stale pid read back from
    the DB that the OS has since reused, or a child that shares our group) must not take the
    group down with it: that would signal a terminal, a shell, or the daemon itself.
    """
    if sys.platform == "win32":  # POSIX only: Windows goes through taskkill /T
        return
    try:
        send = os.killpg if os.getpgid(pid) == pid else os.kill
        send(pid, signal.SIGTERM)
        deadline = time.monotonic() + grace_sec
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.1)
        if pid_alive(pid):
            send(pid, _SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _snapshot(paths: Sequence[Path]) -> tuple[int, int, float]:
    """(files, total bytes, newest mtime) under the watched folders: changes = activity."""
    files, size, newest = 0, 0, 0.0
    for root in paths:
        stack = [root]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            # os.stat, not DirEntry.stat: on Windows the latter reports stale
                            # sizes for a file another process still has open (ffmpeg's video)
                            st = os.stat(entry.path, follow_symlinks=False)
                            files, size, newest = (
                                files + 1,
                                size + st.st_size,
                                max(newest, st.st_mtime),
                            )
            except OSError:
                continue
    return files, size, newest


def _reader(stream: object, out: queue.Queue[str | None]) -> None:
    try:
        for line in stream:  # type: ignore[attr-defined]
            out.put(line.rstrip("\r\n"))
    finally:
        out.put(None)


def run_child(
    argv: Sequence[str], cwd: Path, on_line: Callable[[str], None],
    should_stop: Callable[[], str | None], watch: Sequence[Path], stall_sec: float,
    timeout_sec: float, on_start: Callable[[int], None] | None = None, poll_sec: float = 0.5,
    log_path: Path | None = None, env: Mapping[str, str] | None = None,
) -> RunResult:  # fmt: skip
    """Run `argv` and supervise it until it ends or must be stopped."""
    child_env = {**os.environ, **(env or {}), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    flags: dict[str, Any]
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        flags = {"start_new_session": True}
    try:
        proc = subprocess.Popen(
            list(argv), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=child_env, **flags,
        )  # fmt: skip
    except OSError as e:
        logger.error("Không chạy được tiến trình con: %s", e)
        return RunResult("spawn_failed", None)
    if on_start is not None:
        on_start(proc.pid)
    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True).start()
    log = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8")
    try:
        return _supervise(proc, lines, on_line, should_stop, watch, stall_sec, timeout_sec,
                          poll_sec, log)  # fmt: skip
    finally:
        if log is not None:
            log.close()


def _supervise(
    proc: subprocess.Popen[str], lines: queue.Queue[str | None],
    on_line: Callable[[str], None], should_stop: Callable[[], str | None],
    watch: Sequence[Path], stall_sec: float, timeout_sec: float, poll_sec: float, log: Any,
) -> RunResult:  # fmt: skip
    start = last_activity = time.monotonic()
    snap, next_scan = _snapshot(watch), start + _SCAN_EVERY_SEC
    eof = False

    def drain() -> bool:
        nonlocal eof
        got = False
        while True:
            try:
                item = lines.get_nowait()
            except queue.Empty:
                return got
            if item is None:
                eof = True
                continue
            got = True
            if log is not None:
                log.write(item + "\n")
                log.flush()
            on_line(item)

    def stop(reason: str) -> RunResult:
        kill_tree(proc.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=15)
        drain()
        return RunResult(reason, None)

    while True:
        now = time.monotonic()
        if drain():
            last_activity = now
        if proc.poll() is not None:
            deadline = now + 1.0  # a grandchild may keep the pipe open: do not wait for EOF
            while not eof and time.monotonic() < deadline:
                drain()
                time.sleep(0.02)
            drain()
            return RunResult("exit", proc.returncode)
        if (why := should_stop()) is not None:
            return stop(why)
        if now - start > timeout_sec:
            return stop("timeout")
        if watch and now >= next_scan:
            next_scan = now + _SCAN_EVERY_SEC
            if (fresh := _snapshot(watch)) != snap:
                snap, last_activity = fresh, now
        if now - last_activity > stall_sec:
            return stop("stalled")
        time.sleep(poll_sec)
