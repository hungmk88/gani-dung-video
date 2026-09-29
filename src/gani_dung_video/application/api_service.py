"""Use cases behind the local web API (phase 6b): projects, jobs, events, media, preflight.

Everything the HTTP layer needs, with no HTTP in it. Safety rules kept here, in one place:
- clients name a project by an opaque id ("<root index>/<folder name>"), never a path;
- a project must resolve inside GANI_DV_ALLOWED_ROOTS (links cannot point outside);
- media paths are rebuilt from the job's folder and draft letter, never taken from the
  database's output_path or from the client, and re-checked against the allow-list each time;
- error messages never contain absolute paths.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gani_dung_video.application.qc_service import QcReport
from gani_dung_video.application.queue_service import (
    NotReadyError,
    allowed_roots,
    cancel_job,
    enqueue_project_render,
    retry_job,
)
from gani_dung_video.domain.api_models import (
    Artifacts,
    EventView,
    HealthView,
    JobCreate,
    JobDetail,
    JobView,
    PreflightRequest,
    ProjectView,
    QaView,
    RunInfo,
    derive_steps,
    eta_sec,
    frontend_status,
    iso,
    make_project_id,
    media_file_name,
    parse_project_id,
    qa_checks,
)
from gani_dung_video.domain.jobs import STATUSES, Job
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules, preflight_check
from gani_dung_video.infrastructure.config_files import ConfigError
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.queue_db import QueueDb
from gani_dung_video.infrastructure.settings import Settings

_REPORT_SLACK_SEC = 2.0  # file systems round modification times
_MEDIA_TYPES = {"video": "video/mp4", "cover": "image/jpeg", "srt": "text/plain; charset=utf-8"}
_MAX_PROJECTS_PER_ROOT = 200


class ApiError(Exception):
    """A request the API refuses; `status` is the HTTP status, `message` is Vietnamese."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class ApiContext:
    db: QueueDb
    rules: Rules
    settings: Settings
    clock: Callable[[], float] = field(default=time.time)

    @property
    def roots(self) -> list[Path]:
        return allowed_roots(self.settings)


@dataclass(frozen=True)
class MediaFile:
    path: Path
    content_type: str
    size: int


def _no_roots() -> ApiError:
    return ApiError(503, "unavailable", "Chưa đặt GANI_DV_ALLOWED_ROOTS trong .env của máy dựng.")


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"Không tìm thấy {what}.")


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


# ─────────────── health, projects ───────────────


def health(ctx: ApiContext) -> HealthView:
    st = ctx.db.daemon_status(ctx.clock(), float(ctx.rules["queue"]["daemon_stale_sec"]))
    return HealthView(ok=True, rules_version=str(ctx.rules["rules_version"]),
                      daemon_online=st.online, daemon_pid=st.pid if st.online else None,
                      daemon_stopping=st.stop_request if st.online else None,
                      jobs=ctx.db.counts(), allowed_roots_configured=bool(ctx.roots))  # fmt: skip


def _flags(folder: Path) -> tuple[bool, bool, bool]:
    edit = folder / EDIT_DIR
    drafts = (edit / "drafts.json").is_file() or (edit / "speech_drafts.json").is_file()
    return (edit / "inventory.json").is_file(), (edit / "plan.json").is_file(), drafts


def _project_view(index: int, name: str, folder: Path) -> ProjectView:
    inv, plan, drafts = _flags(folder)
    return ProjectView(id=make_project_id(index, name), name=folder.name if name == "." else name,
                       has_inventory=inv, has_plan=plan, has_drafts=drafts,
                       ready_to_render=plan and drafts)  # fmt: skip


def list_projects(ctx: ApiContext) -> list[ProjectView]:
    """Every project of every allowed root: the root itself (if it has an edit/ folder) and
    its direct sub-folders. Ids are opaque; no path leaves the server."""
    out: list[ProjectView] = []
    for index, root in enumerate(ctx.roots):
        if not root.is_dir():
            continue
        if (root / EDIT_DIR).is_dir():
            out.append(_project_view(index, ".", root))
        try:
            children = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
        except OSError:
            continue
        for child in children[:_MAX_PROJECTS_PER_ROOT]:
            if child.name.startswith((".", "$")) or child.name == EDIT_DIR:
                continue
            with contextlib.suppress(ValueError):  # a name the id format refuses is skipped
                out.append(_project_view(index, child.name, child))
    return out


def resolve_project(ctx: ApiContext, project_id: str) -> Path:
    """The folder of a project id, inside the allow-list, or an ApiError."""
    try:
        index, name = parse_project_id(project_id)
    except ValueError as e:
        raise ApiError(400, "bad_request", str(e)) from e
    roots = ctx.roots
    if not roots:
        raise _no_roots()
    if index >= len(roots):
        raise _not_found("dự án")
    root = roots[index]
    folder = (root if name == "." else root / name).resolve()
    if not _inside(folder, root) or not folder.is_dir():  # also stops links leading outside
        raise _not_found("dự án")
    return folder


def _project_id_of(ctx: ApiContext, folder: Path) -> str | None:
    for index, root in enumerate(ctx.roots):
        if folder == root:
            return make_project_id(index, ".")
        if folder.parent == root:
            with contextlib.suppress(ValueError):
                return make_project_id(index, folder.name)
    return None


# ─────────────── jobs ───────────────


def _scrub(ctx: ApiContext, job_dir: str, text: str | None) -> str | None:
    """Console lines and errors come from other processes and may quote absolute paths: the
    job's folder becomes <its name>, an allowed root becomes <gốc>."""
    if not text:
        return text
    swaps = [(job_dir, f"<{Path(job_dir).name}>"), *((str(r), "<gốc>") for r in ctx.roots)]
    for base, repl in swaps:
        for variant in {base, base.replace("\\", "/")}:
            text = text.replace(variant, repl)
    return text


def _job_size(job: Job) -> str:
    return "final" if job.final else "preview"


def _read_options(folder: Path) -> dict[str, Any]:
    with contextlib.suppress(OSError, ValidationError):
        plan = Plan.model_validate_json((folder / EDIT_DIR / "plan.json").read_text("utf-8"))
        return dict(plan.options)
    return {}


def _read_qc(out: Path, job: Job) -> QcReport | None:
    """The QC report of THIS job: draft folders share one qc_report.json, so a report written
    outside the job's run window belongs to another run and is not shown."""
    path = out / "qc_report.json"
    try:
        mtime = path.stat().st_mtime
        if job.started_at is None or mtime < job.started_at - _REPORT_SLACK_SEC:
            return None
        if job.finished_at is not None and mtime > job.finished_at + _REPORT_SLACK_SEC:
            return None
        return QcReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _qa_view(report: QcReport) -> QaView:
    return QaView(passed=report.passed, rounds=report.rounds, fixes=report.fixes,
                  loudness_two_pass=report.loudness_two_pass, lufs=report.lufs,
                  true_peak_db=report.true_peak_db,
                  checks=qa_checks(report.findings, report.rounds, report.lufs,
                                   report.true_peak_db))  # fmt: skip


def _media_path(ctx: ApiContext, job: Job, kind: str) -> Path | None:
    """Where a media file of a finished job lives, or None. Rebuilt, never trusted."""
    if job.status != "completed":
        return None
    try:
        name = media_file_name(kind, job.draft, job.final)
    except ValueError:
        return None
    folder = Path(job.project_dir).resolve()
    if not any(_inside(folder, r) for r in ctx.roots):
        return None
    out = (folder / EDIT_DIR / "render" / job.draft).resolve()
    path = (out / name).resolve()
    if path.parent != out or not path.is_file():
        return None
    return path


def _view(ctx: ApiContext, job: Job) -> JobView:
    folder = Path(job.project_dir)
    size = _job_size(job)
    kind = "render_final" if job.final else "render_preview"
    inv, plan, drafts = _flags(folder)
    edit = folder / EDIT_DIR
    arts = Artifacts(
        inventory=inv, scenes=(edit / "scenes.json").is_file(),
        speech=(edit / "speech.json").is_file(), plan=plan,
        pick=(edit / "candidates.json").is_file() or (edit / "phrases.json").is_file(),
        drafts=drafts, preview_video=(edit / "render" / job.draft / f"nhap_{job.draft}_540p.mp4"
                                      ).is_file(),
    )  # fmt: skip
    error = _scrub(ctx, job.project_dir, job.error_msg)
    run = RunInfo(size=size, status=job.status, result=job.result, stage=job.stage,
                  error_msg=error)  # fmt: skip
    urls = {k: (f"/media/{job.id}/{k}" if _media_path(ctx, job, k) else None)
            for k in ("video", "cover", "srt")}  # fmt: skip
    report = _read_qc(folder / EDIT_DIR / "render" / job.draft, job) if urls["video"] else None
    label = "cuối" if job.final else "nháp"
    return JobView(
        id=job.id, project_id=_project_id_of(ctx, folder), project_name=folder.name,
        title=f"{folder.name} — bản {job.draft} ({label})", kind=job.kind, draft=job.draft,
        size=size, status=job.status, result=job.result,
        frontend_status=frontend_status(job.status, job.result), attempts=job.attempts,
        max_attempts=job.max_attempts, progress_pct=job.progress_pct, stage=job.stage,
        eta_sec=eta_sec(job.status, job.started_at,
                        float(ctx.rules["queue"]["expected_sec"][kind]), ctx.clock()),
        created_at=iso(job.created_at),
        started_at=iso(job.started_at) if job.started_at is not None else None,
        finished_at=iso(job.finished_at) if job.finished_at is not None else None,
        error_msg=error, no_asr=job.no_asr,
        options=_read_options(folder),
        video_url=urls["video"], cover_url=urls["cover"], srt_url=urls["srt"],
        steps=derive_steps(arts, run), qa=_qa_view(report) if report else None,
    )  # fmt: skip


def _need(ctx: ApiContext, job_id: int) -> Job:
    """A render job by id. Analysis and draft jobs join the API in phase 6c-3; until then they
    are invisible here, as if they did not exist."""
    job = ctx.db.get(job_id)
    if job is None or job.kind != "render":
        raise _not_found(f"job #{job_id}")
    return job


def create_jobs(ctx: ApiContext, req: JobCreate) -> list[JobView]:
    """Queue the render(s) a request asks for and return their views."""
    folder = resolve_project(ctx, req.project_id)
    try:
        queued = enqueue_project_render(folder, ctx.db, draft=req.draft, final=req.final,
                                        no_asr=req.no_asr, rules=ctx.rules,
                                        settings=ctx.settings, now=ctx.clock())  # fmt: skip
    except NotReadyError as e:
        raise ApiError(409, "not_ready", str(e)) from e
    except ConfigError as e:
        raise ApiError(400, "bad_request", str(e)) from e
    return [_view(ctx, _need(ctx, r.job_id)) for r in queued]


def list_jobs(ctx: ApiContext, status: str | None = None, project_id: str | None = None,
              limit: int = 50) -> list[JobView]:  # fmt: skip
    if status is not None and status not in STATUSES:
        raise ApiError(400, "bad_request", f"Trạng thái phải là một trong: {', '.join(STATUSES)}.")
    folder = str(resolve_project(ctx, project_id)) if project_id else None
    jobs = ctx.db.list_jobs(status, max(1, min(limit, 200)), project_dir=folder, kind="render")
    return [_view(ctx, j) for j in jobs]


def job_events(ctx: ApiContext, job_id: int, after: int) -> list[EventView]:
    job = _need(ctx, job_id)
    return [EventView(id=e.id, ts=iso(e.ts), level=e.level,
                      text=_scrub(ctx, job.project_dir, e.text) or "")
            for e in ctx.db.events(job_id, after)]  # fmt: skip


def get_job(ctx: ApiContext, job_id: int) -> JobDetail:
    job = _need(ctx, job_id)
    keep = int(ctx.rules["api"]["events_page"])
    events = job_events(ctx, job_id, 0)[-keep:]
    return JobDetail(**_view(ctx, job).model_dump(), events=events)


def cancel(ctx: ApiContext, job_id: int) -> JobView:
    _need(ctx, job_id)
    cancel_job(ctx.db, job_id, ctx.clock())
    return _view(ctx, _need(ctx, job_id))


def retry(ctx: ApiContext, job_id: int) -> JobView:
    job = _need(ctx, job_id)
    if not retry_job(ctx.db, job_id):
        raise ApiError(409, "conflict", f"Job #{job.id} đang '{job.status}': chỉ thử lại được job "
                                        "lỗi, đã hủy hoặc QC không đạt.")  # fmt: skip
    return _view(ctx, _need(ctx, job_id))


def preflight(ctx: ApiContext, req: PreflightRequest) -> Mapping[str, Any]:
    """The same preflight the CLI runs before any AI spend (single source of truth)."""
    return preflight_check(req.options, ctx.rules)


def resolve_media(ctx: ApiContext, job_id: int, kind: str) -> MediaFile:
    """The file behind /media/<job>/<kind>, or a 404."""
    job = _need(ctx, job_id)
    path = _media_path(ctx, job, kind)
    if path is None or kind not in _MEDIA_TYPES:
        raise _not_found("tệp")
    return MediaFile(path=path, content_type=_MEDIA_TYPES[kind], size=path.stat().st_size)
