"""Tests: application/queue_service.py + the queue/daemon CLI commands (6a)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.queue_service import (
    ApprovalRefused,
    NotReadyError,
    PipelineBusyError,
    ProjectLockedError,
    ai_key_configured,
    allowed_roots,
    approve_job,
    cancel_job,
    enqueue_analyze,
    enqueue_draft,
    enqueue_pipeline,
    enqueue_project_render,
    enqueue_render,
    music_dir,
    queue_dir,
    retry_job,
)
from gani_dung_video.cli import main
from gani_dung_video.domain.jobs import Estimate
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.project_lock import acquire
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb, QueueDbError
from gani_dung_video.infrastructure.settings import Settings

RULES = load_rules()
OPTS: dict[str, Any] = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong",
                        "do_dai_giay": 15, "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "allowed"
    (r / "footage").mkdir(parents=True)
    return r


@pytest.fixture
def opt(tmp_path: Path) -> Path:
    p = tmp_path / "o.json"
    p.write_text(json.dumps(OPTS), encoding="utf-8")
    return p


@pytest.fixture
def db(tmp_path: Path) -> QueueDb:
    return QueueDb(tmp_path / "jobs" / "queue.db")


def cfg(root: Path | None) -> Settings:
    return Settings(gani_dv_allowed_roots=str(root) if root else None)


def add(db: QueueDb, opt: Path, folder: Path, root: Path | None, **kw: Any) -> Any:
    return enqueue_render(opt, folder, db, rules=RULES, settings=cfg(root), **kw)


# ─────────────── settings ───────────────


def test_allowed_roots_are_split_resolved_and_blanks_ignored(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    s = Settings(gani_dv_allowed_roots=f"{a}{os.pathsep}{os.pathsep}{b}{os.pathsep}")
    assert allowed_roots(s) == [a.resolve(), b.resolve()]
    assert allowed_roots(Settings()) == []


def test_queue_dir_defaults_to_the_project_jobs_folder(tmp_path: Path) -> None:
    assert queue_dir(Settings()).name == "jobs"
    assert queue_dir(Settings(gani_dv_queue_dir=str(tmp_path / "q"))) == tmp_path / "q"


# ─────────────── enqueue: safety first ───────────────


def test_nothing_is_accepted_until_the_allowed_roots_are_configured(
    db: QueueDb, opt: Path, root: Path
) -> None:
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        add(db, opt, root / "footage", None)
    assert db.list_jobs() == []


def test_a_folder_outside_the_allowed_roots_is_refused(
    db: QueueDb, opt: Path, root: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ConfigError, match="không nằm trong"):
        add(db, opt, elsewhere, root)


def test_dot_dot_cannot_escape_the_allowed_root(
    db: QueueDb, opt: Path, root: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret").mkdir()
    sneaky = root / "footage" / ".." / ".." / "secret"
    with pytest.raises(ConfigError, match="không nằm trong"):
        add(db, opt, sneaky, root)


def test_a_sibling_with_the_same_prefix_is_not_inside_the_root(
    db: QueueDb, opt: Path, root: Path
) -> None:
    lookalike = root.parent / (root.name + "-evil")
    lookalike.mkdir()
    with pytest.raises(ConfigError, match="không nằm trong"):
        add(db, opt, lookalike, root)


def test_a_missing_folder_or_options_file_is_refused(db: QueueDb, opt: Path, root: Path) -> None:
    with pytest.raises(ConfigError, match="Không thấy thư mục"):
        add(db, opt, root / "gone", root)
    with pytest.raises(ConfigError):
        add(db, root / "no-such-options.json", root / "footage", root)


def test_options_that_preflight_blocks_are_refused_before_queueing(
    db: QueueDb, root: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**OPTS, "do_dai_giay": 7}), encoding="utf-8")
    with pytest.raises(ConfigError, match="bị chặn"):
        add(db, bad, root / "footage", root)
    assert db.list_jobs() == []


# ─────────────── enqueue: jobs ───────────────


def test_all_drafts_become_one_job_each(db: QueueDb, opt: Path, root: Path) -> None:
    out = add(db, opt, root / "footage", root, final=True, no_asr=True)
    assert [(r.draft, r.duplicate) for r in out] == [("A", False), ("B", False)]
    jobs = {j.draft: j for j in db.list_jobs()}
    assert set(jobs) == {"A", "B"}
    a = jobs["A"]
    assert (a.kind, a.status, a.final, a.no_asr) == ("render", "queued", True, True)
    assert a.max_attempts == RULES["queue"]["max_attempts"]
    assert a.project_dir == str((root / "footage").resolve())


def test_one_named_draft_only(db: QueueDb, opt: Path, root: Path) -> None:
    out = add(db, opt, root / "footage", root, ban="b")
    assert [r.draft for r in out] == ["B"]


def test_a_draft_the_request_does_not_have_is_refused(db: QueueDb, opt: Path, root: Path) -> None:
    with pytest.raises(ConfigError, match="'C'"):
        add(db, opt, root / "footage", root, ban="C")


def test_the_same_render_is_not_queued_twice(db: QueueDb, opt: Path, root: Path) -> None:
    first = add(db, opt, root / "footage", root, ban="A")
    again = add(db, opt, root / "footage", root, ban="A")
    assert again[0].duplicate and again[0].job_id == first[0].job_id
    assert len(db.list_jobs()) == 1
    other_size = add(db, opt, root / "footage", root, ban="A", final=True)
    assert not other_size[0].duplicate and len(db.list_jobs()) == 2


# ─────────────── cancel / retry ───────────────


def test_cancel_and_retry_wrappers(db: QueueDb, opt: Path, root: Path) -> None:
    [r] = add(db, opt, root / "footage", root, ban="A")
    assert cancel_job(db, r.job_id) == "cancelled"
    assert retry_job(db, r.job_id, rules=RULES, settings=cfg(root)) is True
    assert cancel_job(db, 999) == "missing"
    assert retry_job(db, 999, rules=RULES, settings=cfg(root)) is False


# ─────────────── CLI ───────────────


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path) -> Path:
    monkeypatch.setenv("GANI_DV_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "qdir"))
    return tmp_path / "qdir" / "queue.db"


def status_of(path: Path, jid: int) -> str:
    job = QueueDb(path).get(jid)
    assert job is not None
    return job.status


def test_cli_queue_add_list_show_cancel_retry(
    env: Path, opt: Path, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = str(root / "footage")
    assert main(["queue", "add", str(opt), folder, "--ban", "A", "--final"]) == 0
    assert "Đã đưa vào hàng đợi" in capsys.readouterr().out
    assert main(["queue", "list"]) == 0
    out = capsys.readouterr().out
    assert "#1" in out and "queued" in out and "daemon" in out.lower()
    assert main(["queue", "show", "1"]) == 0
    assert "bản A" in capsys.readouterr().out
    assert main(["queue", "cancel", "1"]) == 0
    assert status_of(env, 1) == "cancelled"
    assert main(["queue", "retry", "1"]) == 0
    assert status_of(env, 1) == "queued"


def test_cli_queue_add_without_roots_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, opt: Path, root: Path
) -> None:
    monkeypatch.delenv("GANI_DV_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("GANI_DV_QUEUE_DIR", str(tmp_path / "qdir"))
    monkeypatch.setattr("gani_dung_video.infrastructure.settings.ENV_FILE", tmp_path / "no.env")
    assert main(["queue", "add", str(opt), str(root / "footage")]) == 2


def test_cli_show_unknown_job_exits_2(env: Path) -> None:
    assert main(["queue", "show", "77"]) == 2


def test_cli_stop_reaches_a_registered_daemon(env: Path) -> None:
    db = QueueDb(env)
    db.register_daemon(os.getpid(), time.time(), 15, lambda p: True)
    assert main(["queue", "stop", "--now"]) == 0
    assert db.stop_request() == "now"
    assert main(["queue", "stop"]) == 0 and db.stop_request() == "graceful"


def test_cli_stop_without_a_daemon_says_so(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["queue", "stop"]) == 0
    assert "không có daemon" in capsys.readouterr().out.lower()


def test_cli_daemon_refuses_to_start_a_second_one(env: Path) -> None:
    db = QueueDb(env)
    other = os.getppid()  # a live process that is not this one
    db.register_daemon(other, time.time(), 15, lambda p: True)  # "another" live daemon
    assert main(["daemon"]) == 2


# ─────────────── by project, from plan.json (6b) ───────────────


def ready_project(root: Path, speech: bool = False, count: int = 2) -> Path:
    folder = root / "footage"
    (folder / "edit").mkdir(parents=True, exist_ok=True)
    style = "ban_hang_co_loi" if speech else "ke_chuyen_nghe"
    purpose = "ban_hang" if speech else "gioi_thieu_xuong"
    opts = VideoOptions(phong_cach=style, muc_dich=purpose, do_dai_giay=15, so_ban_nhap=count,
                        nhac="khong_nhac")  # fmt: skip
    plan = compile_plan(opts, RULES, music=None, available_tags=set())
    (folder / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    name = "speech_drafts.json" if speech else "drafts.json"
    (folder / "edit" / name).write_text("{}", encoding="utf-8")
    return folder


def by_project(db: QueueDb, folder: Path, root: Path | None, **kw: Any) -> Any:
    return enqueue_project_render(folder, db, rules=RULES, settings=cfg(root), **kw)


def test_project_render_queues_every_draft_the_plan_has(db: QueueDb, root: Path) -> None:
    out = by_project(db, ready_project(root, count=3), root, final=True)
    assert [r.draft for r in out] == ["A", "B", "C"]
    a = db.list_jobs()[-1]
    assert a.final and a.options_file.endswith("plan.json")  # the plan is the options source


def test_project_render_names_one_draft_and_refuses_unknown_ones(db: QueueDb, root: Path) -> None:
    folder = ready_project(root, count=2)
    assert [r.draft for r in by_project(db, folder, root, draft="b")] == ["B"]
    with pytest.raises(ConfigError, match="'C'"):
        by_project(db, folder, root, draft="C")


def test_project_render_is_not_queued_twice(db: QueueDb, root: Path) -> None:
    folder = ready_project(root)
    by_project(db, folder, root, draft="A")
    assert by_project(db, folder, root, draft="A")[0].duplicate


def test_a_project_without_a_plan_or_drafts_is_not_ready(db: QueueDb, root: Path) -> None:
    bare = root / "footage"
    with pytest.raises(NotReadyError, match="plan"):
        by_project(db, bare, root)
    ready_project(root)
    (bare / "edit" / "drafts.json").unlink()  # the plan is there, the drafts are not
    with pytest.raises(NotReadyError, match="draft"):
        by_project(db, bare, root)
    assert db.list_jobs() == []


def test_speech_projects_need_speech_drafts(db: QueueDb, root: Path) -> None:
    folder = ready_project(root, speech=True)
    assert len(by_project(db, folder, root)) == 2
    (folder / "edit" / "speech_drafts.json").unlink()
    (folder / "edit" / "drafts.json").write_text("{}", encoding="utf-8")  # the wrong file
    with pytest.raises(NotReadyError, match="speech_drafts"):
        by_project(db, folder, root, draft="A")


def test_project_render_keeps_the_allow_list_rules(db: QueueDb, root: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        by_project(db, ready_project(root), None)
    other = tmp_path / "other"
    (other / "edit").mkdir(parents=True)
    with pytest.raises(ConfigError, match="không nằm trong"):
        by_project(db, other, root)


# ─────────────── analysis jobs and dependencies (6c-1) ───────────────


def analyse(db: QueueDb, folder: Path, root: Path | None, **kw: Any) -> Any:
    return enqueue_analyze(folder, db, rules=RULES, settings=cfg(root), **kw)


def test_an_analysis_is_queued_with_the_lowest_priority(db: QueueDb, root: Path) -> None:
    r = analyse(db, root / "footage", root)
    job = db.get(r.job_id)
    assert job is not None and not r.duplicate
    assert (job.kind, job.status, job.priority, job.force) == ("analyze", "queued", 10, False)
    assert job.project_dir == str((root / "footage").resolve()) and job.draft == ""
    assert job.max_attempts == RULES["queue"]["max_attempts"]


def test_force_is_kept_and_a_second_request_returns_the_first_job(db: QueueDb,
                                                                  root: Path) -> None:  # fmt: skip
    first = analyse(db, root / "footage", root, force=True)
    again = analyse(db, root / "footage", root)
    assert again.duplicate and again.job_id == first.job_id
    assert db.get(first.job_id).force  # type: ignore[union-attr]


def test_an_analysis_keeps_the_allow_list_rules(db: QueueDb, root: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        analyse(db, root / "footage", None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ConfigError, match="không nằm trong"):
        analyse(db, elsewhere, root)
    with pytest.raises(ConfigError, match="Không thấy thư mục"):
        analyse(db, root / "gone", root)
    assert db.list_jobs() == []


def test_a_render_can_wait_for_an_analysis(db: QueueDb, opt: Path, root: Path) -> None:
    a = analyse(db, root / "footage", root)
    out = add(db, opt, root / "footage", root, ban="A", after=a.job_id)
    assert db.get(out[0].job_id).depends_on == a.job_id  # type: ignore[union-attr]
    project = ready_project(root)
    by = by_project(db, project, root, draft="B", after=a.job_id)
    assert db.get(by[0].job_id).depends_on == a.job_id  # type: ignore[union-attr]


def test_waiting_for_a_job_that_does_not_exist_is_refused(db: QueueDb, opt: Path,
                                                         root: Path) -> None:  # fmt: skip
    with pytest.raises(ConfigError, match="99"):
        add(db, opt, root / "footage", root, ban="A", after=99)
    assert db.list_jobs() == []


def test_cli_queue_analyze_and_after(env: Path, opt: Path, root: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    folder = str(root / "footage")
    assert main(["queue", "analyze", folder, "--force"]) == 0
    assert "phân tích" in capsys.readouterr().out.lower()
    assert main(["queue", "add", str(opt), folder, "--ban", "A", "--after", "1"]) == 0
    capsys.readouterr()
    assert main(["queue", "list"]) == 0
    out = capsys.readouterr().out
    assert "phân tích" in out.lower() and "#2" in out and "sau job #1" in out
    assert main(["queue", "show", "1"]) == 0
    assert "phân tích" in capsys.readouterr().out.lower()
    assert QueueDb(env).get(2).depends_on == 1  # type: ignore[union-attr]


def test_cli_queue_analyze_outside_the_allow_list_exits_2(env: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert main(["queue", "analyze", str(elsewhere)]) == 2


# ─────────────── 6c-2: draft jobs and cost approval ───────────────


def draft_for(db: QueueDb, opt: Path, folder: Path, root: Path | None, **kw: Any) -> Any:
    return enqueue_draft(opt, folder, db, rules=RULES, settings=cfg(root), **kw)


def test_a_draft_job_is_queued_for_the_estimate_with_one_attempt(db: QueueDb, opt: Path,
                                                                 root: Path) -> None:  # fmt: skip
    r = draft_for(db, opt, root / "footage", root)
    job = db.get(r.job_id)
    assert job is not None and not r.duplicate
    assert (job.kind, job.status, job.priority, job.max_attempts) == ("draft", "queued", 20, 1)
    assert job.options_file == str(opt.resolve())
    assert job.project_dir == str((root / "footage").resolve()) and job.phase is None


def test_a_draft_is_not_queued_twice_even_while_it_waits_for_approval(
    db: QueueDb, opt: Path, root: Path
) -> None:
    first = draft_for(db, opt, root / "footage", root)
    db.claim(time.time(), 1)
    db.hold_for_approval(first.job_id, time.time(), estimate(), 86_400.0)
    again = draft_for(db, opt, root / "footage", root)
    assert again.duplicate and again.job_id == first.job_id


def test_a_draft_can_wait_for_an_analysis(db: QueueDb, opt: Path, root: Path) -> None:
    a = analyse(db, root / "footage", root)
    r = draft_for(db, opt, root / "footage", root, after=a.job_id)
    assert db.get(r.job_id).depends_on == a.job_id  # type: ignore[union-attr]
    with pytest.raises(ConfigError, match="99"):
        draft_for(db, opt, root / "footage", root, after=99)


def test_a_draft_keeps_every_allow_list_and_preflight_rule(db: QueueDb, opt: Path, root: Path,
                                                           tmp_path: Path) -> None:  # fmt: skip
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        draft_for(db, opt, root / "footage", None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ConfigError, match="không nằm trong"):
        draft_for(db, opt, elsewhere, root)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**OPTS, "do_dai_giay": 7}), encoding="utf-8")
    with pytest.raises(ConfigError, match="bị chặn"):
        draft_for(db, bad, root / "footage", root)
    assert db.list_jobs() == []


def estimate(**kw: Any) -> Estimate:
    base: dict[str, Any] = {
        "model": "m",
        "input_tokens": 2400,
        "input_only_vnd": 122.4,
        "worst_call_vnd": 4207.3,
        "cap_vnd": 20_000.0,
        "max_attempts": 3,
    }
    return Estimate(**{**base, **kw})


def waiting_job(db: QueueDb, root: Path, name: str = "footage", at: float | None = None) -> int:
    (root / name).mkdir(exist_ok=True)
    from gani_dung_video.infrastructure.queue_db import NewJob

    stamp = at if at is not None else time.time()
    jid = db.enqueue(NewJob(kind="draft", project_dir=str((root / name).resolve()),
                            options_file="o", draft="", max_attempts=1), stamp)  # fmt: skip
    got = db.claim(stamp, 1)
    assert got is not None and got.id == jid
    db.hold_for_approval(jid, stamp, estimate(), 86_400.0)
    return jid


def approve(db: QueueDb, jid: int, max_vnd: float = 6000.0, est: float = 4207.3,
            rules: Any = None, now: float | None = None) -> Any:  # fmt: skip
    return approve_job(db, jid, max_vnd, est, rules=rules or RULES, now=now)


def test_approving_queues_the_paid_phase_with_the_agreed_maximum(db: QueueDb, root: Path) -> None:
    jid = waiting_job(db, root)
    job = approve(db, jid, 6000.0, 4207.0)
    assert (job.status, job.phase, job.approved_vnd) == ("queued", "paid", 6000.0)
    assert db.get(jid).approved_at is not None  # type: ignore[union-attr]


def test_a_wrong_estimate_a_low_maximum_or_a_high_one_are_refused_and_change_nothing(
    db: QueueDb, root: Path
) -> None:
    jid = waiting_job(db, root)
    for args, needle in [((6000.0, 3000.0), "không khớp"), ((4000.0, 4207.3), "thấp hơn"),
                         ((25_000.0, 4207.3), "vượt trần")]:  # fmt: skip
        with pytest.raises(ApprovalRefused, match=needle):
            approve(db, jid, *args)
    job = db.get(jid)
    assert job is not None and job.status == "awaiting_approval" and job.approved_vnd is None


def test_a_refusal_is_a_config_error_so_the_cli_can_report_it(db: QueueDb, root: Path) -> None:
    jid = waiting_job(db, root)
    with pytest.raises(ConfigError):
        approve(db, jid, 1.0, 4207.3)


def test_approving_an_unknown_job_or_a_render_is_refused(db: QueueDb, root: Path,
                                                         opt: Path) -> None:  # fmt: skip
    with pytest.raises(ConfigError, match="99"):
        approve(db, 99)
    [r] = add(db, opt, root / "footage", root, ban="A")
    with pytest.raises(ApprovalRefused, match="kịch bản"):
        approve(db, r.job_id)


def test_an_approval_after_the_window_is_refused(db: QueueDb, root: Path) -> None:
    jid = waiting_job(db, root, at=time.time() - 100_000)
    with pytest.raises(ApprovalRefused, match="hết hạn"):
        approve(db, jid)


def paid_and_done(db: QueueDb, root: Path, name: str, approved: float, spent: float,
                  rules: Any = None) -> None:  # fmt: skip
    jid = waiting_job(db, root, name)
    approve(db, jid, approved, 4207.3, rules=rules)
    db.claim(time.time(), 1)
    db.finish(jid, time.time(), "completed", "passed", 0, None, None, spent_vnd=spent)


BUDGET = {**RULES, "queue": {**RULES["queue"], "daily_budget_vnd": 100_000}}


NO_LIMIT = {**RULES, "queue": {**RULES["queue"], "daily_budget_vnd": None}}


def test_the_shipped_configuration_limits_spending_to_100k_a_day() -> None:
    assert RULES["queue"]["daily_budget_vnd"] == 100_000


def test_without_a_daily_budget_nothing_limits_the_total(db: QueueDb, root: Path) -> None:
    for n in range(6):
        paid_and_done(db, root, f"p{n}", 20_000.0, 20_000.0, rules=NO_LIMIT)
    approve(db, waiting_job(db, root, "last"), 20_000.0, rules=NO_LIMIT)


def test_the_shipped_limit_refuses_the_approval_that_would_cross_it(
    db: QueueDb, root: Path
) -> None:
    for n in range(4):
        paid_and_done(db, root, f"p{n}", 20_000.0, 20_000.0)  # 80.000đ spent today
    first, second = waiting_job(db, root, "a"), waiting_job(db, root, "b")
    assert approve(db, first, 20_000.0).status == "queued"  # exactly 100.000đ is allowed
    with pytest.raises(ApprovalRefused, match="hạn mức ngày"):
        approve(db, second, 6_000.0)


def test_the_daily_budget_counts_spent_reserved_and_the_new_maximum(db: QueueDb,
                                                                   root: Path) -> None:  # fmt: skip
    paid_and_done(db, root, "a", 20_000.0, 20_000.0)
    paid_and_done(db, root, "b", 20_000.0, 20_000.0)
    paid_and_done(db, root, "c", 20_000.0, 20_000.0)  # 60.000đ spent today
    queued, last, over = (waiting_job(db, root, n) for n in ("d", "e", "f"))
    approve(db, queued, 20_000.0, rules=BUDGET)  # 20.000đ reserved (approved, not yet run)
    approve(db, last, 20_000.0, rules=BUDGET)  # 60 + 20 + 20 = 100.000đ: exactly the limit
    with pytest.raises(ApprovalRefused, match="hạn mức ngày"):
        approve(db, over, 6000.0, rules=BUDGET)
    assert db.get(over).status == "awaiting_approval"  # type: ignore[union-attr]


def test_spending_older_than_a_day_no_longer_counts(db: QueueDb, root: Path) -> None:
    old = waiting_job(db, root, "old", at=time.time() - 200_000)
    approve(db, old, 20_000.0, rules=BUDGET, now=time.time() - 200_000)
    db.claim(time.time() - 200_000, 1)
    db.finish(old, time.time() - 190_000, "completed", "passed", 0, None, None, spent_vnd=99_000.0)
    approve(db, waiting_job(db, root, "new"), 20_000.0, rules=BUDGET)


def test_two_approvals_that_interleave_cannot_both_pass_the_daily_budget(
    db: QueueDb, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit A4: the budget was read in one transaction and the approval written in another.

    Another request that approves in that gap went unseen, so two approvals that were each fine
    alone (80.000đ + 15.000đ) were both accepted and together crossed 100.000đ.
    """
    for n in range(4):
        paid_and_done(db, root, f"p{n}", 20_000.0, 20_000.0)  # 80.000đ spent today
    first, second = waiting_job(db, root, "a"), waiting_job(db, root, "b")
    real_approve = db.approve
    raced = {"done": False}

    def approve_with_a_rival_in_the_gap(*args: Any, **kwargs: Any) -> None:
        if not raced["done"]:
            raced["done"] = True
            approve(db, second, 15_000.0)  # the other request lands first, completely
        real_approve(*args, **kwargs)

    monkeypatch.setattr(db, "approve", approve_with_a_rival_in_the_gap)
    with pytest.raises(ApprovalRefused, match="hạn mức ngày"):
        approve(db, first, 15_000.0)  # 80 + 15 reserved by the rival + 15 > 100
    assert db.get(first).status == "awaiting_approval"  # type: ignore[union-attr]
    assert db.get(second).status == "queued"  # type: ignore[union-attr]


def test_a_refused_budget_check_leaves_the_job_untouched(db: QueueDb, root: Path) -> None:
    for n in range(4):
        paid_and_done(db, root, f"p{n}", 20_000.0, 20_000.0)
    jid = waiting_job(db, root, "x")
    with pytest.raises(ApprovalRefused):
        approve(db, jid, 25_000.0)  # 80 + 25 > 100
    j = db.get(jid)
    assert j is not None and j.status == "awaiting_approval" and j.approved_vnd is None


# ─────────────── 6c-2: the API key and the music library (settings) ───────────────


def test_the_key_check_says_yes_or_no_and_never_the_key() -> None:
    assert ai_key_configured(Settings(anthropic_api_key="sk-secret-value")) is True
    assert ai_key_configured(Settings()) is False
    assert ai_key_configured(Settings(anthropic_api_key="   ")) is False
    assert ai_key_configured(Settings(anthropic_api_key="")) is False


def test_the_music_folder_comes_from_the_settings(tmp_path: Path) -> None:
    assert music_dir(Settings()) is None
    assert music_dir(Settings(gani_dv_music_dir="  ")) is None
    assert music_dir(Settings(gani_dv_music_dir=str(tmp_path))) == str(tmp_path)


# ─────────────── 6c-2: the CLI ───────────────


def held_via_cli(env: Path) -> int:
    db = QueueDb(env)
    jid = db.enqueue(NewJobForCli(), time.time())
    db.claim(time.time(), 1)
    db.hold_for_approval(jid, time.time(), estimate(), 86_400.0)
    return jid


def NewJobForCli() -> Any:
    from gani_dung_video.infrastructure.queue_db import NewJob

    return NewJob(kind="draft", project_dir="E:/footage", options_file="o", draft="",
                  max_attempts=1)  # fmt: skip


def test_cli_queue_draft_then_show_and_list(
    env: Path, opt: Path, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = str(root / "footage")
    assert main(["queue", "draft", str(opt), folder]) == 0
    assert "kịch bản" in capsys.readouterr().out.lower()
    assert main(["queue", "list"]) == 0
    assert "kịch bản" in capsys.readouterr().out.lower()


def test_cli_show_prints_the_cost_block_of_a_waiting_job(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jid = held_via_cli(env)
    assert main(["queue", "show", str(jid)]) == 0
    out = capsys.readouterr().out
    assert "chờ duyệt" in out and "4.207đ" in out and "20.000đ" in out
    assert f"queue approve {jid}" in out


def test_cli_approve_needs_both_numbers(env: Path) -> None:
    jid = held_via_cli(env)
    assert main(["queue", "approve", str(jid), "--max-vnd", "6000"]) == 2  # no --estimate
    assert main(["queue", "approve", str(jid), "--estimate", "4207"]) == 2  # no --max-vnd
    assert QueueDb(env).get(jid).status == "awaiting_approval"  # type: ignore[union-attr]


def test_cli_approve_success_and_refusals(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jid = held_via_cli(env)
    assert main(["queue", "approve", str(jid), "--max-vnd", "6000", "--estimate", "3000"]) == 2
    assert main(["queue", "approve", str(jid), "--max-vnd", "4000", "--estimate", "4207"]) == 2
    assert QueueDb(env).get(jid).status == "awaiting_approval"  # type: ignore[union-attr]
    capsys.readouterr()
    assert main(["queue", "approve", str(jid), "--max-vnd", "6000", "--estimate", "4207"]) == 0
    assert "6.000đ" in capsys.readouterr().out
    job = QueueDb(env).get(jid)
    assert job is not None and (job.status, job.phase, job.approved_vnd) == ("queued", "paid",
                                                                            6000.0)  # fmt: skip


def test_cli_approving_a_job_that_is_not_waiting_exits_2(env: Path) -> None:
    assert main(["queue", "approve", "99", "--max-vnd", "6000", "--estimate", "4207"]) == 2


# ─────────────── 6c-3: pipelines (analyze → draft → render in one go) ───────────────


def pipe(db: QueueDb, root: Path, folder: str = "footage", options: Any = None, **kw: Any) -> Any:
    return enqueue_pipeline(options if options is not None else OPTS, root / folder, db,
                            rules=RULES, settings=cfg(root), **kw)  # fmt: skip


def test_a_pipeline_queues_analyze_then_draft_then_one_render_per_draft(
    db: QueueDb, root: Path
) -> None:
    r = pipe(db, root)
    jobs = db.jobs_of_pipeline(r.pipeline_id)
    assert [j.kind for j in jobs] == ["analyze", "draft", "render", "render"]
    assert [j.draft for j in jobs] == ["", "", "A", "B"]
    assert r.analyze_id == jobs[0].id and r.draft_id == jobs[1].id
    assert r.render_ids == [jobs[2].id, jobs[3].id] and r.drafts == ["A", "B"]
    assert [j.depends_on for j in jobs] == [None, jobs[0].id, jobs[1].id, jobs[1].id]
    assert all(j.project_dir == str((root / "footage").resolve()) for j in jobs)


def test_the_draft_job_never_retries_but_the_others_use_the_normal_limit(
    db: QueueDb, root: Path
) -> None:
    a, d, ra, _ = db.jobs_of_pipeline(pipe(db, root).pipeline_id)
    assert d.max_attempts == 1
    assert a.max_attempts == ra.max_attempts == int(RULES["queue"]["max_attempts"])


def test_options_are_saved_next_to_the_project_edit_files_and_every_job_points_at_them(
    db: QueueDb, root: Path
) -> None:
    r = pipe(db, root)
    saved = root / "footage" / "edit" / "options.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == OPTS
    assert not list((root / "footage" / "edit").glob("*.tmp"))  # written atomically
    _, d, ra, _ = db.jobs_of_pipeline(r.pipeline_id)
    assert Path(d.options_file) == saved.resolve() and Path(ra.options_file) == saved.resolve()


def test_vietnamese_text_in_options_is_stored_readable(db: QueueDb, root: Path) -> None:
    pipe(db, root, options={**OPTS, "ghi_chu": "Xưởng sạch, viên bò tròn đều"})
    text = (root / "footage" / "edit" / "options.json").read_text(encoding="utf-8")
    assert "Xưởng sạch" in text


def test_a_single_draft_letter_or_final_size_and_no_asr_are_carried_to_the_renders(
    db: QueueDb, root: Path
) -> None:
    r = pipe(db, root, draft="b", final=True, no_asr=True)
    assert r.drafts == ["B"]
    *_, render = db.jobs_of_pipeline(r.pipeline_id)
    assert (render.draft, render.final, render.no_asr) == ("B", True, True)


def test_force_analyze_is_carried_to_the_analysis(db: QueueDb, root: Path) -> None:
    (root / "two").mkdir()
    forced, *_ = db.jobs_of_pipeline(pipe(db, root, force_analyze=True).pipeline_id)
    plain, *_ = db.jobs_of_pipeline(pipe(db, root, folder="two").pipeline_id)
    assert forced.force is True and plain.force is False


def test_a_draft_letter_the_options_do_not_have_is_refused_and_nothing_is_written(
    db: QueueDb, root: Path
) -> None:
    with pytest.raises(ConfigError, match="C"):
        pipe(db, root, draft="C")  # so_ban_nhap is 2
    assert db.list_jobs() == [] and not (root / "footage" / "edit" / "options.json").exists()


def test_options_that_fail_preflight_write_and_queue_nothing(db: QueueDb, root: Path) -> None:
    bad = {**OPTS, "phong_cach": "khong_co_phong_cach_nay"}
    with pytest.raises(ConfigError, match="chặn"):
        pipe(db, root, options=bad)
    assert db.list_jobs() == [] and not (root / "footage" / "edit").exists()


def test_a_project_outside_the_allowed_roots_or_missing_is_refused(
    db: QueueDb, root: Path, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        enqueue_pipeline(OPTS, tmp_path / "elsewhere", db, rules=RULES, settings=cfg(root))
    with pytest.raises(ConfigError):
        pipe(db, root, folder="no-such-folder")
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        enqueue_pipeline(OPTS, root / "footage", db, rules=RULES, settings=cfg(None))
    assert db.list_jobs() == []


def test_a_busy_project_gets_a_conflict_that_names_the_pipeline_and_keeps_its_options(
    db: QueueDb, root: Path
) -> None:
    first = pipe(db, root)
    saved = root / "footage" / "edit" / "options.json"
    before = saved.read_text(encoding="utf-8")
    with pytest.raises(PipelineBusyError) as caught:
        pipe(db, root, options={**OPTS, "do_dai_giay": 30})
    assert caught.value.pipeline_id == first.pipeline_id
    assert caught.value.job_id == first.analyze_id
    assert saved.read_text(encoding="utf-8") == before  # the running pipeline's options survive
    assert len(db.list_jobs()) == 4


def test_a_plain_render_job_in_the_folder_also_blocks_a_pipeline(db: QueueDb, root: Path,
                                                                   opt: Path) -> None:  # fmt: skip
    add(db, opt, root / "footage", root, ban="A")
    with pytest.raises(PipelineBusyError) as caught:
        pipe(db, root)
    assert caught.value.pipeline_id is None  # that render was not part of any pipeline


def test_once_everything_ended_a_new_pipeline_may_start(db: QueueDb, root: Path) -> None:
    first = pipe(db, root)
    for jid in [first.analyze_id, first.draft_id, *first.render_ids]:
        cancel_job(db, jid)
    second = pipe(db, root, options={**OPTS, "do_dai_giay": 30})
    assert second.pipeline_id == first.pipeline_id + 1
    saved = json.loads((root / "footage" / "edit" / "options.json").read_text(encoding="utf-8"))
    assert saved["do_dai_giay"] == 30


def test_a_cancelled_analysis_takes_its_waiting_dependents_down_with_it(
    db: QueueDb, root: Path
) -> None:
    r = pipe(db, root)
    cancel_job(db, r.analyze_id)
    assert db.cascade_blocked(time.time()) != []
    assert {j.status for j in db.jobs_of_pipeline(r.pipeline_id)} == {"cancelled"}


# ─────────────── 6d: one queue at a time per project folder ───────────────


def lock_path(root: Path) -> Path:
    return root / "footage" / "edit" / ".project.lock"


def foreign_queue(tmp_path: Path, root: Path, active: bool = True) -> QueueDb:
    """Another queue that took the folder long ago and (if `active`) still has a job in it."""
    other = QueueDb(tmp_path / "other" / "queue.db")
    folder = (root / "footage").resolve()
    acquire(folder, other.path, now=time.time() - 100)
    if active:
        other.enqueue(NewJob(kind="render", project_dir=str(folder), options_file="o", draft="A",
                             pipeline_id=9), time.time() - 100)  # fmt: skip
    return other


def test_a_pipeline_takes_the_folder_for_its_queue(db: QueueDb, root: Path) -> None:
    pipe(db, root)
    owner = json.loads(lock_path(root).read_text(encoding="utf-8"))
    assert Path(owner["queue_db"]) == db.path.resolve()


def test_a_second_queue_cannot_start_a_pipeline_on_a_folder_the_first_holds(
    db: QueueDb, root: Path, tmp_path: Path
) -> None:
    first = pipe(db, root)
    saved = root / "footage" / "edit" / "options.json"
    before = saved.read_text(encoding="utf-8")
    other = QueueDb(tmp_path / "other" / "queue.db")
    with pytest.raises(ProjectLockedError) as caught:
        enqueue_pipeline({**OPTS, "do_dai_giay": 30}, root / "footage", other, rules=RULES,
                         settings=cfg(root), now=time.time() + 60)  # fmt: skip
    assert (caught.value.job_id, caught.value.pipeline_id) == (first.analyze_id, first.pipeline_id)
    msg = str(caught.value)
    assert "footage" in msg and "hàng đợi khác" in msg and f"#{first.analyze_id}" in msg
    assert saved.read_text(encoding="utf-8") == before  # the running pipeline's options survive
    assert other.list_jobs() == []


def test_once_the_first_queue_is_done_a_second_queue_takes_the_folder(
    db: QueueDb, root: Path, tmp_path: Path
) -> None:
    first = pipe(db, root)
    for jid in [first.analyze_id, first.draft_id, *first.render_ids]:
        cancel_job(db, jid)
    other = QueueDb(tmp_path / "other" / "queue.db")
    done = enqueue_pipeline({**OPTS, "do_dai_giay": 30}, root / "footage", other, rules=RULES,
                            settings=cfg(root), now=time.time() + 60)  # fmt: skip
    assert done.pipeline_id == 1
    assert Path(json.loads(lock_path(root).read_text("utf-8"))["queue_db"]) == other.path.resolve()


def test_every_way_into_a_queue_respects_a_lock_held_by_another_queue(
    db: QueueDb, root: Path, tmp_path: Path, opt: Path
) -> None:
    ready_project(root)
    foreign_queue(tmp_path, root)
    folder = root / "footage"
    entries: dict[str, Callable[[], object]] = {
        "pipeline": lambda: pipe(db, root),
        "analyze": lambda: enqueue_analyze(folder, db, rules=RULES, settings=cfg(root)),
        "draft": lambda: enqueue_draft(opt, folder, db, rules=RULES, settings=cfg(root)),
        "render": lambda: add(db, opt, folder, root),
        "project_render": lambda: by_project(db, folder, root),
    }  # fmt: skip
    for name, call in entries.items():
        with pytest.raises(ProjectLockedError) as caught:
            call()
        assert caught.value.pipeline_id == 9, name
    assert db.list_jobs() == []  # nothing got in, through any door


def test_the_queue_that_holds_the_folder_can_keep_adding_jobs(db: QueueDb, root: Path,
                                                               opt: Path) -> None:  # fmt: skip
    ready_project(root)
    folder = root / "footage"
    enqueue_analyze(folder, db, rules=RULES, settings=cfg(root))
    add(db, opt, folder, root)
    by_project(db, folder, root)
    enqueue_draft(opt, folder, db, rules=RULES, settings=cfg(root))
    assert len(db.list_jobs()) == 4


def test_a_lock_taken_for_a_job_that_could_not_be_queued_is_given_back(
    db: QueueDb, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*a: Any, **k: Any) -> int:
        raise QueueDbError("disco lleno")

    monkeypatch.setattr(QueueDb, "enqueue", broken)
    with pytest.raises(QueueDbError):
        enqueue_analyze(root / "footage", db, rules=RULES, settings=cfg(root))
    assert not lock_path(root).exists()


def test_a_pipeline_that_fails_half_way_leaves_no_lock_behind(
    db: QueueDb, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = QueueDb._insert
    calls: list[int] = []

    def flaky(con: Any, job: Any, now: float) -> int:
        calls.append(1)
        if len(calls) == 2:
            raise QueueDbError("disco lleno")
        return real(con, job, now)

    monkeypatch.setattr(QueueDb, "_insert", staticmethod(flaky))
    with pytest.raises(QueueDbError):
        pipe(db, root)
    assert db.list_jobs() == [] and not lock_path(root).exists()


def test_a_refused_pipeline_does_not_disturb_the_lock_of_the_queue_that_already_has_it(
    db: QueueDb, root: Path
) -> None:
    pipe(db, root)
    before = lock_path(root).read_text(encoding="utf-8")
    with pytest.raises(PipelineBusyError):
        pipe(db, root)  # same queue, folder busy: the older guard answers first
    assert Path(json.loads(before)["queue_db"]) == db.path.resolve() and lock_path(root).is_file()


def test_the_lock_can_be_switched_off_in_the_rules(db: QueueDb, root: Path, tmp_path: Path) -> None:
    off = {**RULES, "queue": {**RULES["queue"], "project_lock": False}}
    enqueue_pipeline(OPTS, root / "footage", db, rules=off, settings=cfg(root))
    assert not lock_path(root).exists()
    other = QueueDb(tmp_path / "other" / "queue.db")
    enqueue_analyze(root / "footage", other, rules=off, settings=cfg(root))  # nothing stops it


def test_a_retry_takes_the_folder_like_a_new_job(db: QueueDb, root: Path) -> None:
    """Retry used to skip the folder lock and the allow-list (audit L2)."""
    folder = ready_project(root)
    [r] = by_project(db, folder, root, draft="A")
    cancel_job(db, r.job_id)
    lock_path(root).unlink()
    assert retry_job(db, r.job_id, rules=RULES, settings=cfg(root)) is True
    assert Path(json.loads(lock_path(root).read_text("utf-8"))["queue_db"]) == db.path.resolve()


def test_a_retry_on_a_folder_another_queue_took_is_refused_and_changes_nothing(
    db: QueueDb, root: Path, tmp_path: Path
) -> None:
    folder = ready_project(root)
    [r] = by_project(db, folder, root, draft="A")
    cancel_job(db, r.job_id)
    lock_path(root).unlink()
    foreign_queue(tmp_path, root)
    with pytest.raises(ProjectLockedError) as caught:
        retry_job(db, r.job_id, rules=RULES, settings=cfg(root))
    assert caught.value.pipeline_id == 9
    job = db.get(r.job_id)
    assert job is not None and job.status == "cancelled"


def test_a_retry_keeps_the_allow_list_rules(db: QueueDb, root: Path, tmp_path: Path) -> None:
    folder = ready_project(root)
    [r] = by_project(db, folder, root, draft="A")
    cancel_job(db, r.job_id)
    with pytest.raises(ConfigError, match="GANI_DV_ALLOWED_ROOTS"):
        retry_job(db, r.job_id, rules=RULES, settings=cfg(None))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ConfigError, match="không nằm trong"):
        retry_job(db, r.job_id, rules=RULES, settings=cfg(elsewhere))
    job = db.get(r.job_id)
    assert job is not None and job.status == "cancelled"


def test_a_retry_that_is_refused_gives_a_new_lock_back(db: QueueDb, root: Path) -> None:
    folder = ready_project(root)
    [r] = by_project(db, folder, root, draft="A")
    lock_path(root).unlink()
    assert retry_job(db, r.job_id, rules=RULES, settings=cfg(root)) is False  # still queued
    assert lock_path(root).is_file()  # the queued job is ours: the lock stays
    db.claim(time.time(), 1)
    db.finish(r.job_id, time.time(), "completed", "passed", 0, None, None)
    lock_path(root).unlink()
    assert retry_job(db, r.job_id, rules=RULES, settings=cfg(root)) is False  # passed
    assert not lock_path(root).exists()
