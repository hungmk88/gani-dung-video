"""Integration tests: application/daemon.py with a FAKE child-process runner (6a).

The runner is a plain function with run_child's signature that plays a scenario: it writes the
files a real `render` would, calls the callbacks, and returns how the child "ended". So the
daemon's decisions (retry, cancel, graceful stop, recovery) are tested without ffmpeg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.daemon import Daemon, DaemonError, render_argv
from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.render_service import SpeechDraftsFile
from gani_dung_video.domain.edl import SpeechDraft
from gani_dung_video.domain.jobs import Job
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.speech_edit import SpeechCut
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.process_runner import RunResult
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb

RULES: dict[str, Any] = {**load_rules(), "queue": {**load_rules()["queue"], "poll_sec": 0.01}}
Q = RULES["queue"]


class Clock:
    """Real time plus an adjustable offset, so 'ten minutes later' costs nothing."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


class Ctx:
    """What a scenario sees."""

    def __init__(self, job_folder: Path, letter: str, final: bool, on_line: Callable[[str], None],
                 should_stop: Callable[[], str | None]) -> None:  # fmt: skip
        self.dir = job_folder / "edit" / "render" / letter
        name = f"video_{letter}_1080x1920.mp4" if final else f"nhap_{letter}_540p.mp4"
        self.video = self.dir / name
        self.on_line, self.should_stop = on_line, should_stop

    def write_video(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.video.write_bytes(b"x" * 100)

    def write_report(self, passed: bool) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "qc_report.json").write_text(json.dumps({"passed": passed}), encoding="utf-8")


Scenario = Callable[[Ctx], RunResult]


class FakeRunner:
    def __init__(self, *scenarios: Scenario) -> None:
        self.scenarios = list(scenarios)
        self.argvs: list[list[str]] = []
        self.limits: list[tuple[float, float]] = []  # (stall_sec, timeout_sec) of each call
        self.calls = 0

    def __call__(self, argv: Sequence[str], cwd: Path, on_line: Callable[[str], None],
                 should_stop: Callable[[], str | None], watch: Sequence[Path], stall_sec: float,
                 timeout_sec: float, on_start: Callable[[int], None] | None = None,
                 poll_sec: float = 0.5, log_path: Path | None = None,
                 env: Mapping[str, str] | None = None) -> RunResult:  # fmt: skip
        self.calls += 1
        self.limits.append((stall_sec, timeout_sec))
        self.argvs.append(list(argv))
        if on_start:
            on_start(4321)
        folder = Path(argv[argv.index("render") + 1])
        letter = argv[argv.index("--ban") + 1]
        ctx = Ctx(folder, letter, "--preview" not in argv, on_line, should_stop)
        scenario = self.scenarios.pop(0) if len(self.scenarios) > 1 else self.scenarios[0]
        return scenario(ctx)


def passes(c: Ctx) -> RunResult:
    c.on_line("Đang dựng bản A (video)…")
    c.write_video()
    c.write_report(True)
    c.on_line("Bản A: video — QC: ĐẠT")
    return RunResult("exit", 0)


def qc_fails(c: Ctx) -> RunResult:
    c.write_video()
    c.write_report(False)
    return RunResult("exit", 1)


def crashes(c: Ctx) -> RunResult:
    c.write_video()  # a partial file
    c.on_line("LỖI: ffmpeg lỗi: test")
    return RunResult("exit", 2)


def stalls(c: Ctx) -> RunResult:
    c.write_video()
    return RunResult("stalled", None)


def lingers(c: Ctx) -> RunResult:
    """Runs until the daemon asks it to stop; if never asked, it finishes normally."""
    c.write_video()
    for _ in range(50):
        if (why := c.should_stop()) is not None:
            return RunResult(why, None)
    return passes(c)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path: Path) -> QueueDb:
    return QueueDb(tmp_path / "jobs" / "queue.db")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "footage"
    (d / "edit").mkdir(parents=True)
    (d / "edit" / "drafts.json").write_text("{}", encoding="utf-8")
    return d


def add(db: QueueDb, folder: Path, draft: str = "A", **kw: Any) -> int:
    base: dict[str, Any] = {"kind": "render", "project_dir": str(folder),
                            "options_file": str(folder / "o.md"), "draft": draft,
                            "max_attempts": 2}  # fmt: skip
    return db.enqueue(NewJob(**{**base, **kw}), time.time())


def make(db: QueueDb, runner: FakeRunner, clock: Clock, **kw: Any) -> Daemon:
    return Daemon(db, RULES, runner=runner, clock=clock, pid=9999, **kw)


def job(db: QueueDb, jid: int) -> Job:
    j = db.get(jid)
    assert j is not None
    return j


# ─────────────── the command line the child gets ───────────────


def test_render_argv_for_a_preview_and_a_final_without_asr(folder: Path) -> None:
    base = job_stub(folder, final=False, no_asr=False)
    a = render_argv(base, ["py"])
    assert a == ["py", "render", str(folder), "--ban", "A", "--preview"]
    b = render_argv(job_stub(folder, final=True, no_asr=True), ["py"])
    assert b == ["py", "render", str(folder), "--ban", "A", "--no-asr"]


def job_stub(folder: Path, final: bool, no_asr: bool) -> Job:
    return Job(id=1, kind="render", project_dir=str(folder), options_file="o.md", draft="A",
               final=final, no_asr=no_asr, status="processing", attempts=1, max_attempts=2,
               created_at=0.0)  # fmt: skip


# ─────────────── one job ───────────────


def test_an_empty_queue_does_nothing(db: QueueDb, clock: Clock) -> None:
    runner = FakeRunner(passes)
    assert make(db, runner, clock).process_next() is False and runner.calls == 0


def test_a_passing_render_completes_the_job(db: QueueDb, folder: Path, clock: Clock) -> None:
    jid = add(db, folder)
    d = make(db, FakeRunner(passes), clock)
    assert d.process_next() is True
    j = job(db, jid)
    assert (j.status, j.result, j.exit_code, j.progress_pct) == ("completed", "passed", 0, 100.0)
    assert j.output_path == str(folder / "edit" / "render" / "A" / "nhap_A_540p.mp4")
    assert j.worker_pid is None and j.child_pid is None
    texts = [e.text for e in db.events(jid)]
    assert "Đang dựng bản A (video)…" in texts and any("QC: ĐẠT" in t for t in texts)


def test_console_lines_move_the_stage_and_progress(db: QueueDb, folder: Path,
                                                   clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    seen: list[tuple[str | None, float]] = []

    def watch(c: Ctx) -> RunResult:
        c.on_line("Đang dựng bản A (video)…")
        j = job(db, jid)
        seen.append((j.stage, j.progress_pct))
        c.write_video()
        c.write_report(True)
        return RunResult("exit", 0)

    make(db, FakeRunner(watch), clock).process_next()
    assert seen[0][0] == "dung" and 0 < seen[0][1] < 100


def test_console_errors_are_recorded_with_their_level(db: QueueDb, folder: Path,
                                                      clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    make(db, FakeRunner(crashes), clock).process_next()
    levels = {e.text: e.level for e in db.events(jid)}
    assert levels["LỖI: ffmpeg lỗi: test"] == "error"


def test_qc_failure_keeps_the_video_and_is_not_retried(db: QueueDb, folder: Path,
                                                       clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    runner = FakeRunner(qc_fails)
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert (j.status, j.result) == ("completed", "qc_failed")
    assert (folder / "edit" / "render" / "A" / "nhap_A_540p.mp4").is_file()  # evidence stays
    assert make(db, runner, clock).process_next() is False  # nothing queued again


def test_an_old_report_does_not_make_a_new_run_look_finished(db: QueueDb, folder: Path,
                                                             clock: Clock) -> None:  # fmt: skip
    old = folder / "edit" / "render" / "A"
    old.mkdir(parents=True)
    (old / "qc_report.json").write_text('{"passed": true}', encoding="utf-8")
    past = time.time() - 3600
    import os

    os.utime(old / "qc_report.json", (past, past))

    def exits_ok_without_writing(c: Ctx) -> RunResult:
        return RunResult("exit", 0)

    jid = add(db, folder)
    make(db, FakeRunner(exits_ok_without_writing), clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and "báo cáo" in (j.error_msg or "")


# ─────────────── retries ───────────────


def test_a_crash_is_retried_after_the_delay_and_the_partial_video_is_removed(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    runner = FakeRunner(crashes, passes)
    d = make(db, runner, clock)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts) == ("queued", 1) and "mã thoát 2" in (j.error_msg or "")
    assert j.not_before >= clock() + Q["retry_delay_sec"] - 5
    assert not (folder / "edit" / "render" / "A" / "nhap_A_540p.mp4").exists()
    assert d.process_next() is False  # still waiting out the 30 s
    clock.offset += Q["retry_delay_sec"] + 1
    assert d.process_next() is True
    assert job(db, jid).result == "passed" and job(db, jid).attempts == 2


def test_after_the_last_attempt_the_job_fails_for_good(db: QueueDb, folder: Path,
                                                       clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    d = make(db, FakeRunner(crashes), clock)
    d.process_next()
    clock.offset += Q["retry_delay_sec"] + 1
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts, j.exit_code) == ("failed", 2, 2)
    assert "mã thoát 2" in (j.error_msg or "")
    assert "ffmpeg lỗi: test" in (j.error_msg or "")  # the child's last line explains it


def test_a_hung_child_is_a_retryable_failure(db: QueueDb, folder: Path, clock: Clock) -> None:
    jid = add(db, folder)
    make(db, FakeRunner(stalls), clock).process_next()
    j = job(db, jid)
    assert j.status == "queued" and "treo" in (j.error_msg or "")


def test_a_program_that_cannot_start_fails_at_once(db: QueueDb, folder: Path,
                                                   clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    make(db, FakeRunner(lambda c: RunResult("spawn_failed", None)), clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and "tiến trình" in (j.error_msg or "").lower()


# ─────────────── checks before starting ───────────────


def test_a_missing_project_folder_fails_without_starting_anything(
    db: QueueDb, tmp_path: Path, clock: Clock
) -> None:
    jid = add(db, tmp_path / "gone")
    runner = FakeRunner(passes)
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and runner.calls == 0 and "thư mục" in (j.error_msg or "").lower()


def test_a_folder_without_any_drafts_fails_without_starting(db: QueueDb, tmp_path: Path,
                                                            clock: Clock) -> None:  # fmt: skip
    bare = tmp_path / "bare"
    (bare / "edit").mkdir(parents=True)
    jid = add(db, bare)
    runner = FakeRunner(passes)
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and runner.calls == 0 and "draft" in (j.error_msg or "").lower()


def test_speech_drafts_are_accepted_too(db: QueueDb, tmp_path: Path, clock: Clock) -> None:
    d = tmp_path / "speech"
    (d / "edit").mkdir(parents=True)
    (d / "edit" / "speech_drafts.json").write_text("{}", encoding="utf-8")
    jid = add(db, d)
    make(db, FakeRunner(passes), clock).process_next()
    assert job(db, jid).status == "completed"


# ─────────────── cancel ───────────────


def test_cancelling_a_running_job_stops_the_child_and_cleans_up(db: QueueDb, folder: Path,
                                                                clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)

    def cancelled_midway(c: Ctx) -> RunResult:
        c.write_video()
        db.request_cancel(jid, time.time())
        assert c.should_stop() == "cancelled"
        return RunResult("cancelled", None)

    make(db, FakeRunner(cancelled_midway), clock).process_next()
    j = job(db, jid)
    assert j.status == "cancelled"
    assert not (folder / "edit" / "render" / "A" / "nhap_A_540p.mp4").exists()


# ─────────────── graceful stop ───────────────


def test_a_graceful_stop_lets_the_running_job_finish_within_the_grace(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    d = make(db, FakeRunner(lingers), clock)
    d.request_stop(now=False)
    d.process_next()  # nobody asks it to stop before the 600 s are up: it just finishes
    assert job(db, jid).result == "passed"


def test_a_graceful_stop_gives_the_job_back_once_the_grace_is_over(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)

    def slow(c: Ctx) -> RunResult:
        c.write_video()
        assert c.should_stop() is None  # grace just started
        clock.offset += Q["shutdown_grace_sec"] + 1
        assert c.should_stop() == "shutdown"
        return RunResult("shutdown", None)

    d = make(db, FakeRunner(slow), clock)
    d.request_stop(now=False)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts) == ("queued", 0)  # the attempt is given back
    assert not (folder / "edit" / "render" / "A" / "nhap_A_540p.mp4").exists()


def test_stop_now_gives_the_job_back_immediately(db: QueueDb, folder: Path,
                                                 clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)

    def stopped(c: Ctx) -> RunResult:
        c.write_video()
        assert c.should_stop() == "shutdown"
        return RunResult("shutdown", None)

    d = make(db, FakeRunner(stopped), clock)
    d.request_stop(now=True)
    d.process_next()
    assert job(db, jid).status == "queued" and job(db, jid).attempts == 0


def test_a_stop_request_written_in_the_database_is_honoured_too(db: QueueDb, folder: Path,
                                                                clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    db.register_daemon(9999, time.time(), 15, lambda p: True)

    def stopped(c: Ctx) -> RunResult:
        db.set_stop_request("now")
        return RunResult(c.should_stop() or "exit", None)

    make(db, FakeRunner(stopped), clock).process_next()
    assert job(db, jid).status == "queued"


# ─────────────── the loop ───────────────


def run_in_thread(d: Daemon, stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=d.run, args=(stop,), daemon=True)
    t.start()
    return t


def wait_for(cond: Callable[[], bool], seconds: float = 20.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_loop_works_through_the_queue_then_stops_on_request(db: QueueDb, folder: Path,
                                                                clock: Clock) -> None:  # fmt: skip
    ids = [add(db, folder, x) for x in "ABC"]
    stop = threading.Event()
    t = run_in_thread(make(db, FakeRunner(passes), clock), stop)
    assert wait_for(lambda: all(job(db, i).status == "completed" for i in ids))
    assert db.daemon_status(time.time(), 15).online
    db.set_stop_request("graceful")
    t.join(timeout=20)
    assert not t.is_alive()
    assert not db.daemon_status(time.time(), 15).online  # it unregistered itself


def test_the_event_stops_the_loop_too(db: QueueDb, clock: Clock) -> None:
    stop = threading.Event()
    t = run_in_thread(make(db, FakeRunner(passes), clock), stop)
    assert wait_for(lambda: db.daemon_status(time.time(), 15).online)
    stop.set()
    t.join(timeout=20)
    assert not t.is_alive()


def test_a_second_daemon_is_refused(db: QueueDb, clock: Clock) -> None:
    db.register_daemon(1234, time.time(), 15, lambda p: True)
    with pytest.raises(DaemonError, match="1234"):
        make(db, FakeRunner(passes), clock, alive=lambda p: True).run(threading.Event())


def test_start_up_recovers_jobs_a_dead_daemon_left_running(db: QueueDb, folder: Path,
                                                           clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    db.claim(time.time(), 55555)  # a daemon that then died
    db.set_child(jid, 66666)
    killed: list[int] = []
    stop = threading.Event()
    stop.set()  # start, recover, and leave at once
    d = Daemon(db, RULES, runner=FakeRunner(passes), clock=clock, pid=9999,
               alive=lambda p: p == 66666, killer=killed.append)  # fmt: skip
    d.run(stop)
    j = job(db, jid)
    assert j.status == "queued" and j.attempts == 1
    assert killed == [66666]  # the orphaned ffmpeg tree is stopped so it cannot race the retry


def test_start_up_makes_a_backup_of_the_queue(db: QueueDb, clock: Clock) -> None:
    add_nothing = threading.Event()
    add_nothing.set()
    make(db, FakeRunner(passes), clock).run(add_nothing)
    assert (db.path.parent / "queue.db.bak").is_file()


# ─────────────── the real thing: a real child process runs the real CLI ───────────────


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
def test_a_real_child_process_renders_a_job_end_to_end(tmp_path: Path) -> None:
    d = tmp_path / "footage"
    d.mkdir()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=360x640:r=30:d=9", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(d / "a.mp4")],
                   check=True, timeout=120)  # fmt: skip
    build_inventory(d, load_rules())
    opts = VideoOptions(phong_cach="ban_hang_co_loi", muc_dich="ban_hang", do_dai_giay=15,
                        so_ban_nhap=1, nhac="khong_nhac")  # fmt: skip
    plan = compile_plan(opts, load_rules(), music=None, available_tags=set())
    (d / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    word = SpokenWord(text="Giò", start=0.1, end=0.5, prob=0.9)
    # 9 s of speech for a "15 s" option: inside the voice limit (E1-DUR allows ±7 s)
    cut = SpeechCut(phrase_id="a_p0", clip="a.mp4", src_in=0.0, src_out=9.0, t_start=0.0,
                    t_end=9.0, words=[word])  # fmt: skip
    sd = SpeechDraftsFile(model="THU-TAY", drafts=[SpeechDraft(draft="A", label="x", cuts=[cut])])
    (d / "edit" / "speech_drafts.json").write_text(sd.model_dump_json(), encoding="utf-8")

    db = QueueDb(tmp_path / "jobs" / "queue.db")
    jid = add(db, d)
    Daemon(db, load_rules()).process_next()  # real run_child, real `python -m gani_dung_video`
    j = job(db, jid)
    assert (j.status, j.result) == ("completed", "passed"), j.error_msg
    assert j.output_path and Path(j.output_path).is_file()
    assert any("QC: ĐẠT" in e.text for e in db.events(jid))  # the child's console reached the log
    assert (tmp_path / "jobs" / "logs" / f"{jid}.log").is_file()


# ─────────────── limits that follow the length of the video (phase 7d) ───────────────


def test_a_short_video_keeps_the_fixed_stall_and_timeout(db: QueueDb, folder: Path,
                                                         clock: Clock) -> None:  # fmt: skip
    (folder / "edit" / "plan.json").write_text(json.dumps({"options": {"duration": 15}}),
                                               encoding="utf-8")  # fmt: skip
    add(db, folder, final=True)
    runner = FakeRunner(passes)
    make(db, runner, clock).process_next()
    assert runner.limits == [(Q["stall_sec"], Q["timeout_sec"]["render_final"])]


def test_a_three_minute_final_gets_a_longer_stall_window(db: QueueDb, folder: Path,
                                                         clock: Clock) -> None:  # fmt: skip
    # ffmpeg prints nothing for ~8 minutes per round on the Dell: 600 s would kill a healthy job
    (folder / "edit" / "plan.json").write_text(json.dumps({"options": {"duration": 180}}),
                                               encoding="utf-8")  # fmt: skip
    add(db, folder, final=True)
    runner = FakeRunner(passes)
    make(db, runner, clock).process_next()
    stall, timeout = runner.limits[0]
    assert stall > Q["stall_sec"] and timeout >= Q["timeout_sec"]["render_final"]


def test_a_missing_or_broken_plan_falls_back_to_the_fixed_limits(db: QueueDb, folder: Path,
                                                                 clock: Clock) -> None:  # fmt: skip
    (folder / "edit" / "plan.json").write_text("{not json", encoding="utf-8")
    add(db, folder)
    runner = FakeRunner(passes)
    make(db, runner, clock).process_next()
    assert runner.limits == [(Q["stall_sec"], Q["timeout_sec"]["render_preview"])]
