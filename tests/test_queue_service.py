"""Tests: application/queue_service.py + the queue/daemon CLI commands (6a)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.queue_service import (
    ApprovalRefused,
    NotReadyError,
    ai_key_configured,
    allowed_roots,
    approve_job,
    cancel_job,
    enqueue_analyze,
    enqueue_draft,
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
from gani_dung_video.infrastructure.queue_db import QueueDb
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
    assert retry_job(db, r.job_id) is True
    assert cancel_job(db, 999) == "missing" and retry_job(db, 999) is False


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


def paid_and_done(db: QueueDb, root: Path, name: str, approved: float, spent: float) -> None:
    jid = waiting_job(db, root, name)
    approve(db, jid, approved, 4207.3)
    db.claim(time.time(), 1)
    db.finish(jid, time.time(), "completed", "passed", 0, None, None, spent_vnd=spent)


BUDGET = {**RULES, "queue": {**RULES["queue"], "daily_budget_vnd": 100_000}}


def test_without_a_daily_budget_nothing_limits_the_total(db: QueueDb, root: Path) -> None:
    assert RULES["queue"]["daily_budget_vnd"] is None  # off by default
    for n in range(6):
        paid_and_done(db, root, f"p{n}", 20_000.0, 20_000.0)
    approve(db, waiting_job(db, root, "last"), 20_000.0)


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
