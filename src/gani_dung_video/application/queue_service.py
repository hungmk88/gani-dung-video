"""Use case: put render jobs in the queue, list them, cancel, retry (phase 6a).

Nothing gets in unless the project folder is inside GANI_DV_ALLOWED_ROOTS (the allow-list the
foundation phase promised for the web phase) and the options pass preflight — the same check
that runs before any AI spend. One job per draft; asking twice for the same render returns the
job that is already waiting instead of rendering it twice.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.domain.jobs import ApprovalError, BudgetState, Job, validate_approval
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.platforms import PlatformError, profile
from gani_dung_video.domain.preflight import Rules, preflight_check
from gani_dung_video.infrastructure.config_files import (
    CONFIG_DIR,
    ConfigError,
    load_rules,
    read_options_file,
)
from gani_dung_video.infrastructure.project_lock import ProjectLockedError as FolderLockedError
from gani_dung_video.infrastructure.project_lock import acquire, release
from gani_dung_video.infrastructure.queue_db import (
    FolderBusyError,
    NewJob,
    QueueDb,
    QueueDbError,
)
from gani_dung_video.infrastructure.settings import Settings, load_settings

QUEUE_FILE = "queue.db"
LETTERS = "ABC"


class NotReadyError(ConfigError):
    """The project cannot be rendered yet (no plan or no drafts); the API answers 409."""


class ApprovalRefused(ConfigError):
    """A cost approval that was refused (nothing changed; the message says why)."""


class PipelineBusyError(ConfigError):
    """The project already has an active job, so a new pipeline would collide with it, or a
    pipeline is active there, so a plain render would collide with it (409)."""

    def __init__(self, message: str, job_id: int, pipeline_id: int | None) -> None:
        super().__init__(message)
        self.job_id, self.pipeline_id = job_id, pipeline_id


class ProjectLockedError(ConfigError):
    """Another queue (another queue.db) owns the project folder (409 project_locked)."""

    def __init__(self, message: str, job_id: int | None, pipeline_id: int | None) -> None:
        super().__init__(message)
        self.job_id, self.pipeline_id = job_id, pipeline_id


class PipelineResult(BaseModel):
    pipeline_id: int
    analyze_id: int
    draft_id: int
    render_ids: list[int]  # one per draft letter, in the order of `drafts`
    drafts: list[str]


class EnqueueResult(BaseModel):
    job_id: int
    draft: str
    duplicate: bool  # True: an identical render was already waiting or running
    platform: str = ""  # tiktok | facebook; "" = no platform profile


def allowed_roots(settings: Settings) -> list[Path]:
    """The configured allow-list, resolved; blanks ignored. Empty = nothing is allowed."""
    raw = settings.gani_dv_allowed_roots or ""
    return [Path(p.strip()).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]


def queue_dir(settings: Settings) -> Path:
    """Where queue.db and the job logs live (default: <project>/jobs, git-ignored)."""
    if settings.gani_dv_queue_dir and settings.gani_dv_queue_dir.strip():
        return Path(settings.gani_dv_queue_dir.strip()).expanduser()
    return CONFIG_DIR.parent / "jobs"


def open_queue(settings: Settings | None = None) -> QueueDb:
    settings = settings if settings is not None else load_settings()
    return QueueDb(queue_dir(settings) / QUEUE_FILE)


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def _take_folder(db: QueueDb, project: Path, rules: Rules, stamp: float) -> bool:
    """Lock the folder for this queue; True when the lock is new (a failure may give it back).

    Raises:
        ProjectLockedError: another queue owns the folder.
    """
    cfg = rules["queue"]
    if not cfg.get("project_lock", True):
        return False
    try:
        return acquire(project, db.path, now=stamp,
                       grace_sec=float(cfg.get("project_lock_grace_sec", 10)))  # fmt: skip
    except FolderLockedError as e:
        if e.job_id is None:
            detail = ""
        elif e.pipeline_id is None:
            detail = f" (job #{e.job_id})"
        else:
            detail = f" (job #{e.job_id}, pipeline #{e.pipeline_id})"
        raise ProjectLockedError(
            rules["input_msgs"]["PROJECT_LOCKED"].format(folder=project.name, detail=detail),
            e.job_id, e.pipeline_id) from e  # fmt: skip


def _give_back(db: QueueDb, project: Path) -> None:
    """Release a lock taken for a job that never got in (the queue has nothing there)."""
    active = ("queued", "processing", "awaiting_approval")
    if not any(j.status in active for j in db.list_jobs(project_dir=str(project), limit=200)):
        release(project, db.path)


@contextlib.contextmanager
def _folder(db: QueueDb, project: Path, rules: Rules, stamp: float) -> Iterator[None]:
    """Hold the folder for the block; if it fails and nothing was queued, give the lock back."""
    took = _take_folder(db, project, rules, stamp)
    try:
        yield
    except BaseException:
        if took:
            _give_back(db, project)
        raise


def _pipeline_busy(e: FolderBusyError, project: Path, msgs: Mapping[str, Any]) -> PipelineBusyError:
    suffix = f" (pipeline #{e.pipeline_id})" if e.pipeline_id is not None else ""
    return PipelineBusyError(
        msgs["PIPELINE_BUSY"].format(folder=project.name, job=e.job_id, pipeline=suffix),
        e.job_id, e.pipeline_id)  # fmt: skip


def _need_dependency(db: QueueDb, after: int | None, msgs: Mapping[str, Any]) -> None:
    if after is not None and db.get(after) is None:
        raise ConfigError(msgs["QUEUE_NO_DEPENDENCY"].format(job=after))


def enqueue_render(
    options_file: Path, folder: Path, db: QueueDb, *, ban: str | None = None,
    final: bool = False, no_asr: bool = False, rules: Rules | None = None,
    settings: Settings | None = None, now: float | None = None, after: int | None = None,
) -> list[EnqueueResult]:  # fmt: skip
    """Queue the render of one draft (`ban`), or of every draft the request asks for.

    Raises:
        ConfigError: no allow-list configured, folder outside it or missing, options file
            unreadable or blocked by preflight, or a draft letter the request does not have.
    """
    rules = rules if rules is not None else load_rules()
    settings = settings if settings is not None else load_settings()
    msgs = rules["input_msgs"]
    roots = allowed_roots(settings)
    if not roots:
        raise ConfigError(msgs["QUEUE_NO_ROOTS"])
    project = folder.expanduser().resolve()
    if not _inside(project, roots):
        raise ConfigError(msgs["QUEUE_OUTSIDE_ROOTS"].format(folder=project))
    if not project.is_dir():
        raise ConfigError(msgs["QUEUE_NO_FOLDER"].format(folder=project))
    options = options_file.expanduser().resolve()
    check = check_options_file(options, rules)
    if not check["valid"]:
        first = check["errors"][0] if check["errors"] else {"code": "?", "msg": check["error_msg"]}
        raise ConfigError(msgs["QUEUE_PREFLIGHT_BLOCKED"].format(code=first["code"],
                                                                   msg=first["msg"]))  # fmt: skip
    raw = read_options_file(options)
    opts: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    count = int(opts.get("so_ban_nhap", 1))
    letters = list(LETTERS[:count])
    if ban is None or ban.lower() == "all":
        chosen = letters
    elif ban.upper() in letters:
        chosen = [ban.upper()]
    else:
        raise ConfigError(msgs["QUEUE_BAD_DRAFT"].format(draft=ban.upper(), count=count,
                                                         letters=", ".join(letters)))  # fmt: skip
    _need_dependency(db, after, msgs)
    stamp = now if now is not None else time.time()
    out: list[EnqueueResult] = []
    with _folder(db, project, rules, stamp):
        for letter in chosen:
            existing = db.find_active("render", str(project), letter, final)
            if existing is not None:
                out.append(EnqueueResult(job_id=existing, draft=letter, duplicate=True))
                continue
            jid = db.enqueue(
                NewJob(kind="render", project_dir=str(project), options_file=str(options),
                       draft=letter, final=final, no_asr=no_asr, depends_on=after,
                       max_attempts=int(rules["queue"]["max_attempts"])), stamp)  # fmt: skip
            out.append(EnqueueResult(job_id=jid, draft=letter, duplicate=False))
    return out


def cancel_job(db: QueueDb, job_id: int, now: float | None = None) -> str:
    """Cancel a waiting job now, or ask a running one to stop. Returns its status afterwards."""
    return db.request_cancel(job_id, now if now is not None else time.time())


def retry_job(db: QueueDb, job_id: int, *, rules: Rules | None = None,
              settings: Settings | None = None, now: float | None = None) -> bool:  # fmt: skip
    """Queue a failed, cancelled or QC-failed job again. False: no such job, or not one of those.

    A retried job is a job entering the queue: its folder must still be inside the allow-list
    and is taken for this queue, as when it was first queued (another queue may have taken it
    since).

    Raises:
        ConfigError: no allow-list, or the job's folder is outside it or missing.
        ProjectLockedError: another queue owns the folder (nothing changed).
    """
    job = db.get(job_id)
    if job is None:
        return False
    rules = rules if rules is not None else load_rules()
    settings = settings if settings is not None else load_settings()
    msgs = rules["input_msgs"]
    roots = allowed_roots(settings)
    if not roots:
        raise ConfigError(msgs["QUEUE_NO_ROOTS"])
    project = Path(job.project_dir).resolve()
    if not _inside(project, roots):
        raise ConfigError(msgs["QUEUE_OUTSIDE_ROOTS"].format(folder=project))
    if not project.is_dir():
        raise ConfigError(msgs["QUEUE_NO_FOLDER"].format(folder=project))
    stamp = now if now is not None else time.time()
    took = _take_folder(db, project, rules, stamp)
    try:
        ok = db.retry_manual(job_id)
    except BaseException:
        if took:
            _give_back(db, project)
        raise
    if took and not ok:  # nothing went back in: a lock taken just now is not ours to keep
        _give_back(db, project)
    return ok


def _platforms(platforms: Sequence[str] | None, rules: Rules) -> list[str]:
    """The platforms to render for, checked against rules.json; None or empty = [""] (one
    render without a platform profile, the behaviour before 14/10/2026).

    Raises:
        ConfigError: an unknown platform id, or one given twice.
    """
    chosen = list(platforms or [""])
    for p in chosen:
        try:
            profile(rules, p)
        except PlatformError as e:
            raise ConfigError(str(e)) from e
    if len(set(chosen)) != len(chosen) or ("" in chosen and len(chosen) > 1):
        raise ConfigError(f"Danh sách nền tảng không hợp lệ: {chosen}.")
    return chosen


def enqueue_project_render(
    project: Path, db: QueueDb, *, draft: str = "all", final: bool = False,
    no_asr: bool = False, rules: Rules | None = None, settings: Settings | None = None,
    now: float | None = None, after: int | None = None, platforms: Sequence[str] | None = None,
) -> list[EnqueueResult]:  # fmt: skip
    """Queue renders of a project that is already planned and drafted (the web API's path).

    The options are the ones plan.json was compiled from, so nothing is asked twice. One render
    per draft letter and per platform (`platforms`, None = one render without a profile).

    Raises:
        ConfigError: no allow-list, project outside it or missing, or a draft letter the plan
            does not have.
        NotReadyError: no plan.json, or none of the drafts file the plan's driver renders.
    """
    rules = rules if rules is not None else load_rules()
    settings = settings if settings is not None else load_settings()
    msgs = rules["input_msgs"]
    roots = allowed_roots(settings)
    if not roots:
        raise ConfigError(msgs["QUEUE_NO_ROOTS"])
    folder = project.expanduser().resolve()
    if not _inside(folder, roots):
        raise ConfigError(msgs["QUEUE_OUTSIDE_ROOTS"].format(folder=folder))
    if not folder.is_dir():
        raise ConfigError(msgs["QUEUE_NO_FOLDER"].format(folder=folder))
    plan_path = folder / "edit" / "plan.json"
    if not plan_path.is_file():
        raise NotReadyError(msgs["QUEUE_NO_PLAN"])
    try:
        plan = Plan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise NotReadyError(msgs["QUEUE_NO_PLAN"]) from e
    drafts_file = "speech_drafts.json" if plan.driver == "speech" else "drafts.json"
    if not (folder / "edit" / drafts_file).is_file():
        raise NotReadyError(msgs["QUEUE_NO_DRAFTS"].format(file=drafts_file))
    count = int(plan.options.get("drafts", 1))
    letters = list(LETTERS[:count])
    if draft.lower() == "all":
        chosen = letters
    elif draft.upper() in letters:
        chosen = [draft.upper()]
    else:
        raise ConfigError(msgs["QUEUE_BAD_DRAFT"].format(draft=draft.upper(), count=count,
                                                         letters=", ".join(letters)))  # fmt: skip
    targets = _platforms(platforms, rules)
    _need_dependency(db, after, msgs)
    stamp = now if now is not None else time.time()
    out: list[EnqueueResult] = []
    try:
        with _folder(db, folder, rules, stamp):
            db.check_no_pipeline(str(folder))  # not even a duplicate of one of its renders
            for letter in chosen:
                for platform in targets:
                    existing = db.find_active("render", str(folder), letter, final, platform)
                    if existing is not None:
                        out.append(EnqueueResult(job_id=existing, draft=letter, duplicate=True,
                                                 platform=platform))  # fmt: skip
                        continue
                    jid = db.enqueue(
                        NewJob(kind="render", project_dir=str(folder),
                               options_file=str(plan_path), draft=letter, final=final,
                               no_asr=no_asr, depends_on=after, platform=platform,
                               max_attempts=int(rules["queue"]["max_attempts"])), stamp,
                        guard_pipeline=True)  # fmt: skip
                    out.append(EnqueueResult(job_id=jid, draft=letter, duplicate=False,
                                             platform=platform))  # fmt: skip
    except FolderBusyError as e:
        raise _pipeline_busy(e, folder, msgs) from e
    return out


def enqueue_analyze(
    project: Path, db: QueueDb, *, force: bool = False, rules: Rules | None = None,
    settings: Settings | None = None, now: float | None = None,
) -> EnqueueResult:  # fmt: skip
    """Queue the analysis (inventory, Whisper, scenes, CLIP) of one project folder.

    With `force` the daemon analyses even if the footage looks unchanged. Asking again while
    an analysis of the same folder waits or runs returns that job.

    Raises:
        ConfigError: no allow-list, or the folder is outside it or missing.
    """
    rules = rules if rules is not None else load_rules()
    settings = settings if settings is not None else load_settings()
    msgs = rules["input_msgs"]
    roots = allowed_roots(settings)
    if not roots:
        raise ConfigError(msgs["QUEUE_NO_ROOTS"])
    folder = project.expanduser().resolve()
    if not _inside(folder, roots):
        raise ConfigError(msgs["QUEUE_OUTSIDE_ROOTS"].format(folder=folder))
    if not folder.is_dir():
        raise ConfigError(msgs["QUEUE_NO_FOLDER"].format(folder=folder))
    stamp = now if now is not None else time.time()
    with _folder(db, folder, rules, stamp):
        existing = db.find_active("analyze", str(folder), "", False)
        if existing is not None:
            return EnqueueResult(job_id=existing, draft="", duplicate=True)
        jid = db.enqueue(
            NewJob(kind="analyze", project_dir=str(folder), options_file="", draft="",
                   force=force, max_attempts=int(rules["queue"]["max_attempts"])),
            stamp)  # fmt: skip
    return EnqueueResult(job_id=jid, draft="", duplicate=False)


def ai_key_configured(settings: Settings) -> bool:
    """Whether an API key is set: only yes or no, never the key itself."""
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    return bool(key.strip())


def music_dir(settings: Settings) -> str | None:
    """The machine's music library folder (for the plan stage), if configured."""
    raw = (settings.gani_dv_music_dir or "").strip()
    return raw or None


def enqueue_draft(
    options_file: Path, folder: Path, db: QueueDb, *, after: int | None = None,
    rules: Rules | None = None, settings: Settings | None = None, now: float | None = None,
) -> EnqueueResult:  # fmt: skip
    """Queue a draft job: free stages and an estimate now, the paid call only once approved.

    Raises:
        ConfigError: no allow-list, folder outside it or missing, options unreadable or blocked
            by preflight, or `after` names a job that does not exist.
    """
    rules = rules if rules is not None else load_rules()
    settings = settings if settings is not None else load_settings()
    msgs = rules["input_msgs"]
    roots = allowed_roots(settings)
    if not roots:
        raise ConfigError(msgs["QUEUE_NO_ROOTS"])
    project = folder.expanduser().resolve()
    if not _inside(project, roots):
        raise ConfigError(msgs["QUEUE_OUTSIDE_ROOTS"].format(folder=project))
    if not project.is_dir():
        raise ConfigError(msgs["QUEUE_NO_FOLDER"].format(folder=project))
    options = options_file.expanduser().resolve()
    check = check_options_file(options, rules)
    if not check["valid"]:
        first = check["errors"][0] if check["errors"] else {"code": "?", "msg": check["error_msg"]}
        raise ConfigError(msgs["QUEUE_PREFLIGHT_BLOCKED"].format(code=first["code"],
                                                                   msg=first["msg"]))  # fmt: skip
    _need_dependency(db, after, msgs)
    stamp = now if now is not None else time.time()
    with _folder(db, project, rules, stamp):
        existing = db.find_active("draft", str(project), "", False)
        if existing is not None:
            return EnqueueResult(job_id=existing, draft="", duplicate=True)
        jid = db.enqueue(
            NewJob(kind="draft", project_dir=str(project), options_file=str(options), draft="",
                   depends_on=after, max_attempts=1),  # one chance: code never retries a paid job
            stamp)  # fmt: skip
    return EnqueueResult(job_id=jid, draft="", duplicate=False)


def _write_options(folder: Path, options: Mapping[str, Any]) -> None:
    """edit/options.json next to the project's other files, written atomically."""
    edit = folder / "edit"
    edit.mkdir(parents=True, exist_ok=True)
    target = edit / "options.json"
    tmp = edit / "options.json.tmp"
    try:
        tmp.write_text(json.dumps(options, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def enqueue_pipeline(
    options: Mapping[str, Any], folder: Path, db: QueueDb, *, draft: str = "all",
    final: bool = False, no_asr: bool = False, force_analyze: bool = False,
    rules: Rules | None = None, settings: Settings | None = None, now: float | None = None,
    platforms: Sequence[str] | None = None,
) -> PipelineResult:  # fmt: skip
    """Queue a whole build: analyse the footage, write the drafts, render them, in that order.

    The options are judged by preflight first (nothing is written or queued if they block),
    saved as <folder>/edit/options.json, and the chain analyze → draft → one render per draft
    letter and per platform (`platforms`, None = one render without a profile) is created in
    one transaction. The draft job stops at a cost approval; the renders
    wait for it. A project with any waiting or running job refuses a new pipeline, so options
    of a running one are never overwritten.

    Raises:
        ConfigError: no allow-list, folder outside it or missing, options blocked by preflight,
            or a draft letter the options do not have.
        PipelineBusyError: the project has an active job.
    """
    rules = rules if rules is not None else load_rules()
    settings = settings if settings is not None else load_settings()
    msgs = rules["input_msgs"]
    roots = allowed_roots(settings)
    if not roots:
        raise ConfigError(msgs["QUEUE_NO_ROOTS"])
    project = folder.expanduser().resolve()
    if not _inside(project, roots):
        raise ConfigError(msgs["QUEUE_OUTSIDE_ROOTS"].format(folder=project))
    if not project.is_dir():
        raise ConfigError(msgs["QUEUE_NO_FOLDER"].format(folder=project))
    check = preflight_check(options, rules)
    if not check["valid"]:
        first = check["errors"][0] if check["errors"] else {"code": "?", "msg": check["error_msg"]}
        raise ConfigError(msgs["QUEUE_PREFLIGHT_BLOCKED"].format(code=first["code"],
                                                                   msg=first["msg"]))  # fmt: skip
    count = int(options.get("so_ban_nhap", 1))
    letters = list(LETTERS[:count])
    if draft.lower() == "all":
        chosen = letters
    elif draft.upper() in letters:
        chosen = [draft.upper()]
    else:
        raise ConfigError(msgs["QUEUE_BAD_DRAFT"].format(draft=draft.upper(), count=count,
                                                         letters=", ".join(letters)))  # fmt: skip
    targets = _platforms(platforms, rules)
    where, tries = str(project), int(rules["queue"]["max_attempts"])
    saved = str(project / "edit" / "options.json")
    steps: list[tuple[NewJob, int | None]] = [
        (NewJob(kind="analyze", project_dir=where, options_file="", draft="", force=force_analyze,
                max_attempts=tries), None),
        (NewJob(kind="draft", project_dir=where, options_file=saved, draft="",
                max_attempts=1), 0),  # one chance: code never retries a paid job
        *((NewJob(kind="render", project_dir=where, options_file=saved, draft=letter,
                  final=final, no_asr=no_asr, max_attempts=tries, platform=platform), 1)
          for letter in chosen for platform in targets),
    ]  # fmt: skip
    stamp = now if now is not None else time.time()
    took = False

    def prepare() -> None:  # inside the transaction: the lock, then the options file
        nonlocal took
        took = _take_folder(db, project, rules, stamp)
        _write_options(project, options)

    try:
        pipeline, ids = db.enqueue_chain(steps, stamp, guard_folder=where, before_insert=prepare)
    except FolderBusyError as e:
        raise _pipeline_busy(e, project, msgs) from e
    except BaseException:
        if took:  # nothing was queued: give the folder back
            release(project, db.path)
        raise
    return PipelineResult(pipeline_id=pipeline, analyze_id=ids[0], draft_id=ids[1],
                          render_ids=ids[2:], drafts=chosen)  # fmt: skip


def approve_job(db: QueueDb, job_id: int, max_vnd: float, estimate_vnd: float, *,
                rules: Rules | None = None, now: float | None = None) -> Job:  # fmt: skip
    """Approve spending up to `max_vnd` on a waiting draft job and queue its paid phase.

    `estimate_vnd` must repeat the estimate the job holds: nobody approves a number they have
    not seen. The daily budget (rules queue.daily_budget_vnd, off when null) counts what was
    spent in the last 24 hours, what approved jobs may still spend, and this maximum.

    Raises:
        ConfigError: no such job.
        ApprovalRefused: the numbers, the window or the budget do not allow it.
    """
    rules = rules if rules is not None else load_rules()
    stamp = now if now is not None else time.time()
    job = db.get(job_id)
    if job is None:
        raise ConfigError(f"Không có job #{job_id}.")
    raw_limit = rules["queue"].get("daily_budget_vnd")
    limit = float(raw_limit) if raw_limit is not None else None

    def check(current: Job, spent: float, reserved: float) -> None:
        # runs inside the transaction that writes the approval (see QueueDb.approve)
        budget = BudgetState(limit=limit, spent_24h=spent, reserved=reserved)
        validate_approval(current, max_vnd, estimate_vnd, stamp, budget)

    try:
        db.approve(job_id, stamp, max_vnd, check=check)
    except (ApprovalError, QueueDbError) as e:
        raise ApprovalRefused(str(e)) from e
    approved = db.get(job_id)
    assert approved is not None
    return approved
