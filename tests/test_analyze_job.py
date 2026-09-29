"""Integration tests: multi-stage `analyze` jobs in the daemon, with a FAKE runner (6c-1).

The runner plays what each stage command would do (write its file, print lines, exit with a
code). So resuming after a failure, the analysis stamp, dependencies, priorities and stop
requests are all tested without Whisper, OpenCV or CLIP.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.analyze_job import (
    ARTIFACTS,
    STAMP_FILE,
    footage_files,
    read_stamp,
    stage_argv,
)
from gani_dung_video.application.daemon import Daemon
from gani_dung_video.domain.jobs import ANALYZE_STAGES, Job, analyze_progress, footage_signature
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.process_runner import RunResult
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb

RULES: dict[str, Any] = {**load_rules(), "queue": {**load_rules()["queue"], "poll_sec": 0.01}}
Q = RULES["queue"]
OUT = {"inventory": "inventory.json", "transcribe": "speech.json", "scenes": "scenes.json"}


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


class Ctx:
    """What a stage scenario sees."""

    def __init__(self, stage: str, folder: Path, on_line: Callable[[str], None],
                 should_stop: Callable[[], str | None]) -> None:  # fmt: skip
        self.stage, self.folder = stage, folder
        self.on_line, self.should_stop = on_line, should_stop

    def write_output(self) -> None:
        name = OUT.get(self.stage)
        if name:
            (self.folder / "edit").mkdir(exist_ok=True)
            (self.folder / "edit" / name).write_text("{}", encoding="utf-8")


Scenario = Callable[[Ctx], RunResult]


def ok(c: Ctx) -> RunResult:
    c.on_line(f"{c.stage} chạy")
    c.write_output()
    return RunResult("exit", 0)


class FakeRunner:
    """Runs a scenario per stage; the last scenario given for a stage repeats."""

    def __init__(self, **by_stage: Scenario | list[Scenario]) -> None:
        self.by_stage = {k: list(v) if isinstance(v, list) else [v] for k, v in by_stage.items()}
        self.ran: list[str] = []
        self.argvs: list[list[str]] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: Sequence[str], cwd: Path, on_line: Callable[[str], None],
                 should_stop: Callable[[], str | None], watch: Sequence[Path], stall_sec: float,
                 timeout_sec: float, on_start: Callable[[int], None] | None = None,
                 poll_sec: float = 0.5, log_path: Path | None = None,
                 env: Mapping[str, str] | None = None) -> RunResult:  # fmt: skip
        stage, folder = argv[1], Path(argv[2])
        self.ran.append(stage)
        self.argvs.append(list(argv))
        self.calls.append({"stall": stall_sec, "timeout": timeout_sec, "watch": list(watch),
                           "env": dict(env or {})})  # fmt: skip
        if on_start:
            on_start(4321)
        scenarios = self.by_stage.get(stage, [ok])
        scenario = scenarios.pop(0) if len(scenarios) > 1 else scenarios[0]
        return scenario(Ctx(stage, folder, on_line, should_stop))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path: Path) -> QueueDb:
    return QueueDb(tmp_path / "jobs" / "queue.db")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "footage"
    d.mkdir()
    (d / "a.mp4").write_bytes(b"1" * 100)
    (d / "b.MP4").write_bytes(b"2" * 200)
    (d / "notes.txt").write_text("not a video", encoding="utf-8")
    return d


def add(db: QueueDb, folder: Path, **kw: Any) -> int:
    base: dict[str, Any] = {"kind": "analyze", "project_dir": str(folder), "options_file": "",
                            "draft": "", "max_attempts": 2}  # fmt: skip
    return db.enqueue(NewJob(**{**base, **kw}), time.time())


def make(db: QueueDb, runner: FakeRunner, clock: Clock) -> Daemon:
    return Daemon(db, RULES, runner=runner, clock=clock, pid=9999, argv_base=["py"])


def job(db: QueueDb, jid: int) -> Job:
    j = db.get(jid)
    assert j is not None
    return j


# ─────────────── the command line of a stage ───────────────


def test_a_stage_command_is_the_cli_command_of_that_name(folder: Path) -> None:
    assert stage_argv("scenes", str(folder), ["py"]) == ["py", "scenes", str(folder)]
    assert stage_argv("inventory", str(folder))[-2:] == ["inventory", str(folder)]


def test_only_the_transcribe_stage_gets_the_machines_whisper_model(folder: Path) -> None:
    assert stage_argv("transcribe", str(folder), ["py"], "small") == [
        "py", "transcribe", str(folder), "--model", "small"]  # fmt: skip
    assert stage_argv("transcribe", str(folder), ["py"]) == ["py", "transcribe", str(folder)]
    assert stage_argv("scenes", str(folder), ["py"], "small") == ["py", "scenes", str(folder)]


def test_the_daemon_passes_the_configured_whisper_model_to_transcribe_only(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    add(db, folder)
    runner = FakeRunner()
    Daemon(db, RULES, runner=runner, clock=clock, pid=9999, argv_base=["py"],
           whisper_model="small").process_next()  # fmt: skip
    by_stage = {a[1]: a[3:] for a in runner.argvs}
    assert by_stage["transcribe"] == ["--model", "small"]
    assert by_stage["inventory"] == by_stage["scenes"] == by_stage["semantics"] == []


def test_footage_files_are_the_videos_directly_inside_the_folder(folder: Path) -> None:
    (folder / ".hidden.mp4").write_bytes(b"x")
    (folder / "sub").mkdir()
    (folder / "sub" / "c.mp4").write_bytes(b"x")
    names = sorted(n for n, _s, _m in footage_files(folder, RULES))
    assert names == ["a.mp4", "b.MP4"]


# ─────────────── a whole analysis ───────────────


def test_all_four_stages_run_in_order_and_the_job_completes(db: QueueDb, folder: Path,
                                                            clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    runner = FakeRunner()
    assert make(db, runner, clock).process_next() is True
    assert runner.ran == list(ANALYZE_STAGES)
    j = job(db, jid)
    assert (j.status, j.result, j.exit_code, j.progress_pct) == ("completed", "passed", 0, 100.0)
    assert j.stages_done == list(ANALYZE_STAGES) and j.worker_pid is None
    assert all(runner.argvs[i][2] == str(folder) for i in range(4))


def test_the_console_marks_each_stage_and_keeps_the_child_lines(db: QueueDb, folder: Path,
                                                                clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    texts = [e.text for e in db.events(jid)]
    for stage in ANALYZE_STAGES:
        assert any(stage in t and "Giai đoạn" in t for t in texts)
    assert "scenes chạy" in texts


def test_analysis_uses_its_own_long_watchdog_limits(db: QueueDb, folder: Path,
                                                    clock: Clock) -> None:  # fmt: skip
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert {c["stall"] for c in runner.calls} == {Q["analyze_stall_sec"]} == {1800}
    assert all(0 < c["timeout"] <= Q["analyze_timeout_sec"] for c in runner.calls)
    assert Q["analyze_timeout_sec"] == 4 * 3600
    assert all(c["watch"] == [folder / "edit"] for c in runner.calls)
    assert all("PYTHONPATH" in c["env"] for c in runner.calls)  # works without pip install


# ─────────────── the analysis stamp ───────────────


def test_a_finished_analysis_leaves_a_stamp_of_the_footage(db: QueueDb, folder: Path,
                                                           clock: Clock) -> None:  # fmt: skip
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    stamp = read_stamp(folder)
    assert stamp is not None and stamp.stages == list(ANALYZE_STAGES)
    assert stamp.signature == footage_signature(footage_files(folder, RULES))
    assert (folder / "edit" / STAMP_FILE).is_file()


def test_unchanged_footage_reuses_the_analysis_without_running_anything(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()  # the first analysis
    again = add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    j = job(db, again)
    assert runner.ran == [] and (j.status, j.result, j.progress_pct) == ("completed", "passed",
                                                                        100.0)  # fmt: skip
    assert j.stages_done == list(ANALYZE_STAGES)
    assert any("không đổi" in e.text for e in db.events(again))


def test_changed_footage_is_analysed_again(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    (folder / "c.mp4").write_bytes(b"3" * 50)  # a new clip
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert runner.ran == list(ANALYZE_STAGES)


def test_touching_a_clip_counts_as_a_change(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    future = time.time() + 5000
    os.utime(folder / "a.mp4", (future, future))
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert runner.ran == list(ANALYZE_STAGES)


def test_force_ignores_a_fresh_stamp(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    add(db, folder, force=True)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert runner.ran == list(ANALYZE_STAGES)


def test_a_missing_result_file_forces_a_rerun_even_with_a_matching_stamp(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    (folder / "edit" / ARTIFACTS[1]).unlink()  # scenes.json deleted by hand
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert runner.ran == list(ANALYZE_STAGES)


def test_a_corrupt_stamp_is_treated_as_no_stamp(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    make(db, FakeRunner(), clock).process_next()
    (folder / "edit" / STAMP_FILE).write_text("{not json", encoding="utf-8")
    assert read_stamp(folder) is None
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert runner.ran == list(ANALYZE_STAGES)


def test_no_stamp_is_written_when_the_footage_changed_during_the_run(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    def changes_footage(c: Ctx) -> RunResult:
        (c.folder / "d.mp4").write_bytes(b"4" * 10)  # someone copied a clip in meanwhile
        return ok(c)

    jid = add(db, folder)
    make(db, FakeRunner(transcribe=changes_footage), clock).process_next()
    assert job(db, jid).status == "completed"
    assert read_stamp(folder) is None  # the next analysis must not trust this run


# ─────────────── resuming ───────────────


def fails(code: int = 2, line: str = "LỖI: không nạp được model") -> Scenario:
    def scenario(c: Ctx) -> RunResult:
        c.on_line(line)
        return RunResult("exit", code)

    return scenario


def test_a_failed_stage_is_retried_from_that_stage_not_from_the_start(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    runner = FakeRunner(transcribe=[fails(), ok])
    d = make(db, runner, clock)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts, j.stages_done) == ("queued", 1, ["inventory"])
    assert "transcribe" in (j.error_msg or "")
    clock.offset += Q["retry_delay_sec"] + 1
    assert d.process_next() is True
    assert runner.ran == ["inventory", "transcribe", "transcribe", "scenes", "semantics"]
    j = job(db, jid)
    assert (j.status, j.result, j.attempts) == ("completed", "passed", 2)


def test_when_the_attempts_run_out_the_failure_names_the_stage_and_the_last_line(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    d = make(db, FakeRunner(semantics=fails(2, "LỖI: chưa cài gói [ai]")), clock)
    d.process_next()
    clock.offset += Q["retry_delay_sec"] + 1
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts, j.exit_code) == ("failed", 2, 2)
    assert "semantics" in (j.error_msg or "") and "chưa cài gói [ai]" in (j.error_msg or "")
    assert j.stages_done == ["inventory", "transcribe", "scenes"]  # kept for a manual retry
    assert read_stamp(folder) is None


def test_a_manual_retry_of_a_failed_analysis_resumes_too(db: QueueDb, folder: Path,
                                                         clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder, max_attempts=1)
    runner = FakeRunner(scenes=[fails(), ok])
    d = make(db, runner, clock)
    d.process_next()
    assert job(db, jid).status == "failed"
    assert db.retry_manual(jid)
    d.process_next()
    assert runner.ran == ["inventory", "transcribe", "scenes", "scenes", "semantics"]
    assert job(db, jid).status == "completed"


def test_a_hung_stage_is_a_retryable_failure_that_names_the_limit(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    make(db, FakeRunner(transcribe=lambda c: RunResult("stalled", None)), clock).process_next()
    j = job(db, jid)
    assert j.status == "queued" and "1800s" in (j.error_msg or "") and "transcribe" in (
        j.error_msg or "")  # fmt: skip


# ─────────────── stop requests ───────────────


def test_cancelling_mid_stage_cancels_the_job_and_writes_no_stamp(db: QueueDb, folder: Path,
                                                                 clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)

    def cancelled(c: Ctx) -> RunResult:
        db.request_cancel(jid, time.time())
        assert c.should_stop() == "cancelled"
        return RunResult("cancelled", None)

    runner = FakeRunner(scenes=cancelled)
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "cancelled" and j.stages_done == ["inventory", "transcribe"]
    assert runner.ran == ["inventory", "transcribe", "scenes"] and read_stamp(folder) is None


def test_stop_now_gives_the_analysis_back_without_using_an_attempt(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)

    def stopped(c: Ctx) -> RunResult:
        assert c.should_stop() == "shutdown"
        return RunResult("shutdown", None)

    d = make(db, FakeRunner(transcribe=stopped), clock)
    d.request_stop(now=True)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts, j.stages_done) == ("queued", 0, ["inventory"])


# ─────────────── preconditions ───────────────


def test_a_missing_folder_fails_at_once(db: QueueDb, tmp_path: Path, clock: Clock) -> None:
    jid = add(db, tmp_path / "gone")
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and runner.ran == [] and "thư mục" in (j.error_msg or "").lower()


def test_a_folder_without_videos_fails_at_once(db: QueueDb, tmp_path: Path, clock: Clock) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "readme.txt").write_text("x", encoding="utf-8")
    jid = add(db, empty)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and runner.ran == [] and "video" in (j.error_msg or "").lower()


# ─────────────── progress ───────────────


def test_clip_progress_lines_drive_the_percentage_of_the_semantic_stage(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    seen: list[float] = []

    def clip(c: Ctx) -> RunResult:
        c.on_line("[clip 1/2] a.mp4: nhúng khung 8/16")
        seen.append(job(db, jid).progress_pct)
        c.on_line("[clip 2/2] b.mp4: nhúng khung 16/16")
        seen.append(job(db, jid).progress_pct)
        return ok(c)

    make(db, FakeRunner(semantics=clip), clock).process_next()
    expected = Q["stage_expected_sec"]
    done = ["inventory", "transcribe", "scenes"]
    assert seen[0] == pytest.approx(analyze_progress(done, "semantics", 0, expected, 0.25), abs=1.0)
    assert seen[1] > seen[0] and seen[1] < 95.0 + 1e-6


def test_progress_is_an_estimate_that_only_reaches_100_when_done(db: QueueDb, folder: Path,
                                                                clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    mid: list[float] = []

    def look(c: Ctx) -> RunResult:
        mid.append(job(db, jid).progress_pct)
        return ok(c)

    make(db, FakeRunner(scenes=look), clock).process_next()
    assert 0 < mid[0] < 100 and job(db, jid).progress_pct == 100.0


# ─────────────── scheduling: kinds, priorities, dependencies ───────────────


def test_a_render_goes_before_an_older_analysis(db: QueueDb, folder: Path, clock: Clock) -> None:
    analysis = add(db, folder)  # queued first, but the least urgent
    (folder / "edit").mkdir(exist_ok=True)
    (folder / "edit" / "drafts.json").write_text("{}", encoding="utf-8")
    render = db.enqueue(NewJob(kind="render", project_dir=str(folder), options_file="x",
                               draft="A", max_attempts=1), time.time())  # fmt: skip
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert runner.ran == ["render"]
    assert job(db, render).status != "queued" and job(db, analysis).status == "queued"


def test_a_render_waits_for_the_analysis_it_depends_on(db: QueueDb, folder: Path,
                                                       clock: Clock) -> None:  # fmt: skip
    a = add(db, folder)
    (folder / "edit").mkdir(exist_ok=True)
    (folder / "edit" / "drafts.json").write_text("{}", encoding="utf-8")
    r = db.enqueue(NewJob(kind="render", project_dir=str(folder), options_file="x", draft="A",
                          max_attempts=1, depends_on=a), time.time())  # fmt: skip
    runner = FakeRunner()
    d = make(db, runner, clock)
    d.process_next()  # the analysis (the render is not claimable yet)
    assert job(db, a).status == "completed" and job(db, r).status == "queued"
    assert runner.ran == list(ANALYZE_STAGES)
    d.process_next()  # now the render can run
    assert runner.ran[-1] == "render" and job(db, r).status != "queued"


def test_a_dependent_job_is_cancelled_when_the_analysis_fails_for_good(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    a = add(db, folder, max_attempts=1)
    r = db.enqueue(NewJob(kind="render", project_dir=str(folder), options_file="x", draft="A",
                          max_attempts=1, depends_on=a), time.time())  # fmt: skip
    d = make(db, FakeRunner(inventory=fails()), clock)
    d.process_next()
    assert job(db, a).status == "failed"
    assert d.process_next() is False  # nothing runnable; the sweep cancels the render
    j = job(db, r)
    assert j.status == "cancelled" and f"#{a}" in (j.error_msg or "")
