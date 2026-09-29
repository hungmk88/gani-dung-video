"""Tests: application/queue_service.py + the queue/daemon CLI commands (6a)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.queue_service import (
    NotReadyError,
    allowed_roots,
    cancel_job,
    enqueue_project_render,
    enqueue_render,
    queue_dir,
    retry_job,
)
from gani_dung_video.cli import main
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
