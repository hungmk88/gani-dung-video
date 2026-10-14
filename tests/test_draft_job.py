"""Integration tests: `draft` jobs — free stages, estimate, approval, paid call (6c-2).

A FAKE runner plays each child command: `plan`, `pick`, `phrases`, `draft --dry-run --json`
(the estimate) and the paid `draft --max-vnd N`. No network, no key, no money: what is tested
is that the daemon can never spend without an approval, and what it does around one.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.analyze_job import JobEnv
from gani_dung_video.application.daemon import Daemon
from gani_dung_video.application.draft_job import run_draft
from gani_dung_video.application.queue_service import approve_job
from gani_dung_video.domain.jobs import Estimate, Job
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.process_runner import RunResult
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb

RULES: dict[str, Any] = {**load_rules(), "queue": {**load_rules()["queue"], "poll_sec": 0.01}}
Q = RULES["queue"]
EST = Estimate(model="claude-sonnet-5-5", input_tokens=2400, input_only_vnd=122.4,
               worst_call_vnd=4207.3, cap_vnd=20_000.0, max_attempts=3)  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


class Ctx:
    """What a child command sees."""

    def __init__(self, cmd: str, argv: list[str], folder: Path, on_line: Callable[[str], None],
                 should_stop: Callable[[], str | None]) -> None:  # fmt: skip
        self.cmd, self.argv, self.folder = cmd, argv, folder
        self.on_line, self.should_stop = on_line, should_stop
        self.edit = folder / "edit"

    def log(self, *costs: float) -> None:
        """Append lines to ai_log.jsonl the way draft_service does."""
        self.edit.mkdir(exist_ok=True)
        with (self.edit / "ai_log.jsonl").open("a", encoding="utf-8") as f:
            for i, c in enumerate(costs, 1):
                f.write(json.dumps({"time": "t", "mode": "music", "attempt": i, "cost_vnd": c}))
                f.write("\n")


Scenario = Callable[[Ctx], RunResult]


def plan_ok(driver: str = "music") -> Scenario:
    def scenario(c: Ctx) -> RunResult:
        c.edit.mkdir(exist_ok=True)
        (c.edit / "plan.json").write_text(json.dumps({"driver": driver}), encoding="utf-8")
        return RunResult("exit", 0)

    return scenario


def says(est: Estimate = EST) -> Scenario:
    def scenario(c: Ctx) -> RunResult:
        c.on_line("INFO chuẩn bị ước tính")  # log noise around the answer
        c.on_line(json.dumps(est.model_dump(), ensure_ascii=False))
        return RunResult("exit", 0)

    return scenario


def paid_ok(*costs: float, name: str = "drafts.json") -> Scenario:
    def scenario(c: Ctx) -> RunResult:
        c.log(*costs)
        c.edit.mkdir(exist_ok=True)
        (c.edit / name).write_text("{}", encoding="utf-8")
        return RunResult("exit", 0)

    return scenario


def paid_ends(reason: str, code: int | None, *costs: float) -> Scenario:
    def scenario(c: Ctx) -> RunResult:
        c.log(*costs)
        c.on_line("KHÔNG ĐẠT: Sau 3 vòng Claude vẫn chưa đạt kiểm tra")
        return RunResult(reason, code)

    return scenario


class FakeRunner:
    """Scenarios per command: plan, pick, phrases, estimate (draft --dry-run), paid (draft)."""

    def __init__(self, **by: Scenario | list[Scenario]) -> None:
        defaults: dict[str, Scenario] = {
            "plan": plan_ok(), "pick": lambda c: RunResult("exit", 0),
            "phrases": lambda c: RunResult("exit", 0), "estimate": says(),
            "paid": paid_ok(1224.0)}  # fmt: skip
        self.by = {
            k: list(v) if isinstance(v, list) else [v] for k, v in {**defaults, **by}.items()
        }
        self.ran: list[str] = []
        self.argvs: list[list[str]] = []

    def __call__(self, argv: Sequence[str], cwd: Path, on_line: Callable[[str], None],
                 should_stop: Callable[[], str | None], watch: Sequence[Path], stall_sec: float,
                 timeout_sec: float, on_start: Callable[[int], None] | None = None,
                 poll_sec: float = 0.5, log_path: Path | None = None,
                 env: Mapping[str, str] | None = None) -> RunResult:  # fmt: skip
        a = list(argv)
        cmd = a[1]
        key = ("estimate" if "--dry-run" in a else "paid") if cmd == "draft" else cmd
        self.ran.append(key)
        self.argvs.append(a)
        if on_start:
            on_start(4321)
        folder = Path(a[3] if cmd in ("plan", "pick", "phrases", "draft") else a[2])
        scenarios = self.by[key]
        scenario = scenarios.pop(0) if len(scenarios) > 1 else scenarios[0]
        return scenario(Ctx(cmd, a, folder, on_line, should_stop))


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
    (d / "a.mp4").write_bytes(b"1" * 100)
    (d / "o.json").write_text("{}", encoding="utf-8")
    return d


def add(db: QueueDb, folder: Path, **kw: Any) -> int:
    base: dict[str, Any] = {"kind": "draft", "project_dir": str(folder),
                            "options_file": str(folder / "o.json"), "draft": "",
                            "max_attempts": 1}  # fmt: skip
    return db.enqueue(NewJob(**{**base, **kw}), time.time())


def make(db: QueueDb, runner: FakeRunner, clock: Clock, key: bool = True, **kw: Any) -> Daemon:
    return Daemon(
        db,
        RULES,
        runner=runner,
        clock=clock,
        pid=9999,
        argv_base=["py"],
        music_dir=kw.pop("music_dir", "E:/nhac"),
        ai_key_present=lambda: key,
        **kw,
    )


def job(db: QueueDb, jid: int) -> Job:
    j = db.get(jid)
    assert j is not None
    return j


def approve(db: QueueDb, jid: int, clock: Clock, max_vnd: float = 6000.0,
            estimate: float = 4207.3) -> None:  # fmt: skip
    approve_job(db, jid, max_vnd, estimate, rules=RULES, now=clock())


def ready_for_approval(
    db: QueueDb, folder: Path, clock: Clock, runner: FakeRunner
) -> tuple[Daemon, int]:
    jid = add(db, folder)
    d = make(db, runner, clock)
    d.process_next()
    assert job(db, jid).status == "awaiting_approval"
    return d, jid


# ─────────────── the free phase ───────────────


def test_the_free_stages_run_and_the_job_waits_for_approval(db: QueueDb, folder: Path,
                                                            clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder)
    runner = FakeRunner()
    d = make(db, runner, clock)
    assert d.process_next() is True
    assert runner.ran == ["plan", "pick", "estimate"]
    j = job(db, jid)
    assert (j.status, j.phase, j.stages_done) == ("awaiting_approval", "estimate",
                                                 ["plan", "pick", "estimate"])  # fmt: skip
    assert (j.cost_input_tokens, j.cost_worst_call_vnd, j.cost_cap_vnd) == (2400, 4207.3, 20_000.0)
    assert j.approved_vnd is None and j.worker_pid is None
    assert j.approval_expires_at is not None
    assert j.approval_expires_at == pytest.approx(clock() + Q["approval_ttl_sec"], abs=30)
    assert d.process_next() is False  # the worker is free; nothing else to do


def test_the_console_tells_the_person_exactly_how_to_approve(db: QueueDb, folder: Path,
                                                            clock: Clock) -> None:  # fmt: skip
    _d, jid = ready_for_approval(db, folder, clock, FakeRunner())
    texts = [e.text for e in db.events(jid)]
    hint = next(t for t in texts if "queue approve" in t)
    assert f"queue approve {jid}" in hint and "--estimate 4207" in hint and "--max-vnd" in hint
    assert any("4.207đ" in t and "20.000đ" in t for t in texts)


def test_speech_styles_also_extract_phrases(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    runner = FakeRunner(plan=plan_ok("speech"))
    make(db, runner, clock).process_next()
    assert runner.ran == ["plan", "pick", "phrases", "estimate"]


def test_every_command_gets_the_options_file_and_the_folder(db: QueueDb, folder: Path,
                                                            clock: Clock) -> None:  # fmt: skip
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    for argv in runner.argvs:
        assert argv[2] == str(folder / "o.json") and argv[3] == str(folder)
    assert runner.argvs[2][-2:] == ["--dry-run", "--json"]


def test_the_music_folder_goes_to_plan_only(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock, music_dir="E:/nhac").process_next()
    assert runner.argvs[0][4:] == ["--nhac-thu-muc", "E:/nhac"]
    assert all("--nhac-thu-muc" not in a for a in runner.argvs[1:])


def test_no_music_folder_means_no_flag(db: QueueDb, folder: Path, clock: Clock) -> None:
    add(db, folder)
    runner = FakeRunner()
    make(db, runner, clock, music_dir=None).process_next()
    assert "--nhac-thu-muc" not in runner.argvs[0]


def test_a_missing_options_file_fails_before_anything_runs(db: QueueDb, folder: Path,
                                                           clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder, options_file=str(folder / "gone.json"))
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and runner.ran == [] and "yêu cầu" in (j.error_msg or "")


def test_a_missing_folder_fails_before_anything_runs(db: QueueDb, tmp_path: Path,
                                                     clock: Clock) -> None:  # fmt: skip
    jid = add(db, tmp_path / "gone", options_file=str(tmp_path / "o.json"))
    runner = FakeRunner()
    make(db, runner, clock).process_next()
    assert job(db, jid).status == "failed" and runner.ran == []


# ─────────────── failures are never retried by code ───────────────


def fails(code: int = 1, line: str = "BỊ CHẶN [X5]: không đủ cảnh") -> Scenario:
    def scenario(c: Ctx) -> RunResult:
        c.on_line(line)
        return RunResult("exit", code)

    return scenario


def test_a_failed_free_stage_fails_the_job_and_is_never_retried(db: QueueDb, folder: Path,
                                                                clock: Clock) -> None:  # fmt: skip
    jid = add(db, folder, max_attempts=5)  # even with attempts to spare
    runner = FakeRunner(plan=fails())
    d = make(db, runner, clock)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts, j.exit_code) == ("failed", 1, 1)
    assert "plan" in (j.error_msg or "") and "không đủ cảnh" in (j.error_msg or "")
    clock.offset += 3600
    assert d.process_next() is False and runner.ran == ["plan"]  # nothing tried again


def test_a_manual_retry_resumes_the_free_stages_and_asks_for_a_fresh_estimate(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    runner = FakeRunner(pick=[fails(2, "LỖI: thiếu candidates"), lambda c: RunResult("exit", 0)])
    d = make(db, runner, clock)
    d.process_next()
    assert job(db, jid).status == "failed" and job(db, jid).stages_done == ["plan"]
    assert db.retry_manual(jid)
    d.process_next()
    assert runner.ran == ["plan", "pick", "pick", "estimate"]  # plan was not run again
    assert job(db, jid).status == "awaiting_approval"


def test_an_unreadable_estimate_fails_the_job(db: QueueDb, folder: Path, clock: Clock) -> None:
    jid = add(db, folder)

    def prose(c: Ctx) -> RunResult:
        c.on_line("Ước tính (không gọi API) — model x")  # the old human-readable format
        return RunResult("exit", 0)

    make(db, FakeRunner(estimate=prose), clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and "ước tính" in (j.error_msg or "").lower()


# ─────────────── approval → the paid phase ───────────────


def test_approval_then_the_paid_call_completes_the_job_and_records_the_bill(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    (folder / "edit").mkdir(exist_ok=True)
    (folder / "edit" / "ai_log.jsonl").write_text('{"cost_vnd": 999.0}\n', encoding="utf-8")  # old
    runner = FakeRunner(paid=paid_ok(1224.0, 300.5))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock, max_vnd=6000.0)
    assert d.process_next() is True
    assert runner.ran == ["plan", "pick", "estimate", "estimate", "paid"]  # re-estimated first
    paid = runner.argvs[-1]
    assert (
        paid[1] == "draft"
        and "--dry-run" not in paid
        and paid[paid.index("--max-vnd") + 1] == "6000.0"
    )
    j = job(db, jid)
    assert (j.status, j.result, j.exit_code, j.phase) == ("completed", "passed", 0, "paid")
    assert j.spent_vnd == pytest.approx(1524.5)  # only what THIS run added to the log
    assert j.output_path == str(folder / "edit" / "drafts.json") and j.progress_pct == 100.0


def test_speech_styles_report_the_speech_drafts_file(db: QueueDb, folder: Path,
                                                     clock: Clock) -> None:  # fmt: skip
    runner = FakeRunner(plan=plan_ok("speech"), paid=paid_ok(500.0, name="speech_drafts.json"))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    assert job(db, jid).output_path == str(folder / "edit" / "speech_drafts.json")


def test_a_paid_call_only_ever_runs_after_an_approval(db: QueueDb, folder: Path,
                                                      clock: Clock) -> None:  # fmt: skip
    runner = FakeRunner()
    d, jid = ready_for_approval(db, folder, clock, runner)
    for _ in range(3):
        assert d.process_next() is False  # waiting is not running
    assert "paid" not in runner.ran and job(db, jid).spent_vnd is None


def test_the_estimate_is_checked_again_and_a_change_sends_the_job_back(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    bigger = Estimate(model="m", input_tokens=2900, input_only_vnd=150.0, worst_call_vnd=5100.0,
                      cap_vnd=20_000.0, max_attempts=3)  # fmt: skip
    runner = FakeRunner(estimate=[says(), says(bigger)])  # the second answer differs
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock, max_vnd=6000.0)
    d.process_next()
    assert "paid" not in runner.ran  # not a single paid call was made
    j = job(db, jid)
    assert (j.status, j.phase, j.approved_vnd, j.approved_at) == ("awaiting_approval", "estimate",
                                                                 None, None)  # fmt: skip
    assert (j.cost_input_tokens, j.cost_worst_call_vnd) == (2900, 5100.0)
    assert any("đổi" in e.text and "5.100đ" in e.text for e in db.events(jid))
    assert j.spent_vnd is None
    # the person now approves the NEW number, and only then does it run
    approve(db, jid, clock, max_vnd=6000.0, estimate=5100.0)
    runner.by["estimate"] = [says(bigger)]
    d.process_next()
    assert runner.ran[-1] == "paid" and job(db, jid).status == "completed"


def test_a_missing_api_key_fails_at_zero_cost_before_any_call(db: QueueDb, folder: Path,
                                                              clock: Clock) -> None:  # fmt: skip
    runner = FakeRunner()
    _d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    make(db, runner, clock, key=False).process_next()
    j = job(db, jid)
    assert j.status == "failed" and "ANTHROPIC_API_KEY" in (j.error_msg or "")
    assert "paid" not in runner.ran and j.spent_vnd in (None, 0.0)


def test_an_old_ffmpeg_fails_at_zero_cost_before_any_call(
    db: QueueDb, folder: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ffmpeg < 7 cannot load the render graph: better to say so before Claude is paid.
    monkeypatch.setattr("gani_dung_video.application.draft_job.ffmpeg_problem",
                        lambda: "ffmpeg 6.1 quá cũ: app cần ffmpeg 7.0 trở lên.")  # fmt: skip
    runner = FakeRunner()
    _d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    make(db, runner, clock).process_next()
    j = job(db, jid)
    assert j.status == "failed" and "ffmpeg 6.1" in (j.error_msg or "")
    assert "0đ" in (j.error_msg or "")
    assert "paid" not in runner.ran and j.spent_vnd in (None, 0.0)


def test_the_daemon_itself_refuses_a_paid_job_without_an_approval(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    """Defence in depth: the database already won't hand such a job out."""
    jid = add(db, folder)
    claimed = db.claim(time.time(), 9999)
    assert claimed is not None and claimed.id == jid
    forged = claimed.model_copy(update={"phase": "paid", "approved_vnd": None})
    runner = FakeRunner()
    env = JobEnv(db=db, rules=RULES, runner=runner, clock=clock, pid=9999, argv_base=["py"],
                 log_dir=folder / "logs", child_env={}, should_stop=lambda j: None,
                 music_dir=None, ai_key_present=lambda: True)  # fmt: skip
    run_draft(env, forged)
    assert runner.ran == [] and job(db, jid).status == "failed"
    assert "duyệt" in (job(db, jid).error_msg or "")


# ─────────────── what happens when the paid call goes wrong ───────────────


@pytest.mark.parametrize("code", [1, 2])
def test_a_failed_paid_call_fails_the_job_keeps_the_bill_and_is_never_retried(
    db: QueueDb, folder: Path, clock: Clock, code: int
) -> None:
    runner = FakeRunner(paid=paid_ends("exit", code, 1224.0, 800.0))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.exit_code) == ("failed", code)
    assert j.spent_vnd == pytest.approx(2024.0) and "chưa đạt kiểm tra" in (j.error_msg or "")
    clock.offset += 3600
    ran = list(runner.ran)
    assert d.process_next() is False and runner.ran == ran  # no second paid attempt


def test_cancelling_during_the_paid_call_stops_it_and_still_records_the_bill(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    runner = FakeRunner(paid=paid_ends("cancelled", None, 700.0))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    j = job(db, jid)
    # 700đ was logged; the call in flight when it was cut may have cost up to one worst call
    assert j.status == "cancelled" and j.spent_vnd == pytest.approx(700.0 + WORST_CALL)
    assert "trần" in note_of(db, jid)


def test_a_stop_request_during_the_paid_call_never_requeues_it(db: QueueDb, folder: Path,
                                                               clock: Clock) -> None:  # fmt: skip
    """A second run under the same approval could spend the whole cap again."""
    runner = FakeRunner(paid=paid_ends("shutdown", None, 900.0))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    j = job(db, jid)
    assert j.status == "failed" and j.spent_vnd == pytest.approx(900.0 + WORST_CALL)
    assert "ai_log" in (j.error_msg or "") and "duyệt lại" in (j.error_msg or "")


def test_a_hung_paid_call_is_a_failure_not_a_retry(db: QueueDb, folder: Path,
                                                   clock: Clock) -> None:  # fmt: skip
    runner = FakeRunner(paid=paid_ends("stalled", None, 300.0))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    j = job(db, jid)
    assert j.status == "failed" and j.spent_vnd == pytest.approx(300.0 + WORST_CALL)


# ─────────────── audit A4: a call cut in flight is not "free" ───────────────

WORST_CALL = EST.worst_call_vnd


def note_of(db: QueueDb, jid: int) -> str:
    return " ".join(e.text for e in db.events(jid))


@pytest.mark.parametrize("reason", ["cancelled", "shutdown", "stalled", "timeout"])
def test_a_call_cut_before_anything_was_logged_still_counts_as_one_worst_call(
    db: QueueDb, folder: Path, clock: Clock, reason: str
) -> None:
    """Cancel, retry, repeat used to record 0đ each time: the daily cap never filled."""
    runner = FakeRunner(paid=paid_ends(reason, None))  # nothing reached ai_log.jsonl
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    assert job(db, jid).spent_vnd == pytest.approx(WORST_CALL)
    assert db.spend_summary(clock())[0] == pytest.approx(WORST_CALL)  # the budget sees it


def test_the_in_flight_allowance_never_goes_above_what_was_approved(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    runner = FakeRunner(paid=paid_ends("cancelled", None, 5000.0))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock, max_vnd=6000.0)
    d.process_next()
    assert job(db, jid).spent_vnd == pytest.approx(6000.0)  # 5000 + 4207 is capped at the approval


def test_a_call_that_ended_by_itself_is_billed_by_the_log_alone(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    runner = FakeRunner(paid=paid_ends("exit", 1, 2024.0))
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock)
    d.process_next()
    assert job(db, jid).spent_vnd == pytest.approx(2024.0)  # no allowance: nothing was cut


def test_a_stop_during_the_free_phase_gives_the_job_back_at_no_cost(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)

    def stopped(c: Ctx) -> RunResult:
        assert c.should_stop() == "shutdown"
        return RunResult("shutdown", None)

    d = make(db, FakeRunner(pick=stopped), clock)
    d.request_stop(now=True)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.attempts, j.stages_done) == ("queued", 0, ["plan"])


# ─────────────── the approval window ───────────────


def test_an_unapproved_job_is_cancelled_when_its_window_closes(db: QueueDb, folder: Path,
                                                               clock: Clock) -> None:  # fmt: skip
    d, jid = ready_for_approval(db, folder, clock, FakeRunner())
    clock.offset += Q["approval_ttl_sec"] + 60
    assert d.process_next() is False
    j = job(db, jid)
    assert j.status == "cancelled" and "hết hạn" in (j.error_msg or "").lower()
    assert any("hết hạn" in e.text.lower() for e in db.events(jid))


def test_the_window_is_24_hours_by_default() -> None:
    assert Q["approval_ttl_sec"] == 86_400


def test_an_approval_after_the_window_is_refused(db: QueueDb, folder: Path,
                                                 clock: Clock) -> None:  # fmt: skip
    _d, jid = ready_for_approval(db, folder, clock, FakeRunner())
    clock.offset += Q["approval_ttl_sec"] + 60
    with pytest.raises(Exception, match="hết hạn"):
        approve(db, jid, clock)
    assert job(db, jid).approved_vnd is None


# ─────────────── 6e: an impossible plan ends the job at 0đ, with the reason ───────────────

WHY = "Dừng trước khi gọi Claude (0đ): khối 'main' cần 12 phách nhưng chỉ có 1 cảnh ứng viên."


def test_an_impossible_plan_fails_the_job_with_the_reason_and_no_wrapper(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    runner = FakeRunner(estimate=fails(3, "KHÔNG ĐẠT: " + WHY))
    d = make(db, runner, clock)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.exit_code, j.spent_vnd) == ("failed", 3, None)
    assert j.error_msg == WHY  # the person reads the reason, not "mã thoát 3"
    assert runner.ran == ["plan", "pick", "estimate"] and "paid" not in runner.ran
    clock.offset += 3600
    assert d.process_next() is False  # never tried again on its own


def test_an_ordinary_estimate_failure_keeps_its_technical_message(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    make(db, FakeRunner(estimate=fails(1, "LỖI: candidates.json hỏng")), clock).process_next()
    msg = job(db, jid).error_msg or ""
    assert "mã thoát 1" in msg and "candidates.json hỏng" in msg


def test_a_plan_that_became_impossible_before_the_paid_call_stops_without_spending(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    runner = FakeRunner(estimate=[says(), fails(3, "KHÔNG ĐẠT: " + WHY)])
    d, jid = ready_for_approval(db, folder, clock, runner)
    approve(db, jid, clock, max_vnd=6000.0)
    d.process_next()
    j = job(db, jid)
    assert j.status == "failed" and j.error_msg == WHY and j.spent_vnd is None
    assert "paid" not in runner.ran


# ─────────────── ticket 13/10/2026: a track whose BPM cannot build the video ───────────────

BPM_WHY = "Video không dựng được với cặp nhạc + phong cách + độ dài này. Lý do: bài ở BPM 100."


def test_a_track_the_plan_step_refuses_fails_the_job_with_the_reason_before_any_pick(
    db: QueueDb, folder: Path, clock: Clock
) -> None:
    jid = add(db, folder)
    runner = FakeRunner(plan=fails(4, "BPM BỊ CHẶN: " + BPM_WHY))
    d = make(db, runner, clock)
    d.process_next()
    j = job(db, jid)
    assert (j.status, j.exit_code, j.spent_vnd) == ("failed", 4, None)
    assert j.error_msg == BPM_WHY  # the person reads the reason, not "mã thoát 4"
    assert runner.ran == ["plan"]  # nothing after it ran, nothing was spent
    clock.offset += 3600
    assert d.process_next() is False  # never tried again on its own
