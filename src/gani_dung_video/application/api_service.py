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
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from gani_dung_video.application import review_service
from gani_dung_video.application.draft_service import DraftsFile
from gani_dung_video.application.qc_service import QcReport
from gani_dung_video.application.queue_service import (
    ApprovalRefused,
    NotReadyError,
    PipelineBusyError,
    ProjectLockedError,
    ai_key_configured,
    allowed_roots,
    approve_job,
    cancel_job,
    enqueue_pipeline,
    enqueue_project_render,
    music_dir,
    queue_dir,
    retry_job,
)
from gani_dung_video.application.render_limits import limits_of
from gani_dung_video.application.review_service import ReviewRefused, ReviewRow
from gani_dung_video.domain.api_models import (
    ApproveRequest,
    Artifacts,
    BudgetView,
    CostView,
    EventView,
    FeaturesView,
    FootageBlockView,
    FootageView,
    HealthView,
    JobCreate,
    JobDetail,
    JobView,
    MusicListView,
    MusicTrackView,
    PipelineCreate,
    PipelineView,
    PreflightRequest,
    ProjectView,
    QaView,
    ReviewItemView,
    ReviewListView,
    ReviewRequest,
    ReviewView,
    RunInfo,
    StructureItemView,
    StructureView,
    VerifyCheckView,
    VerifyRoundView,
    VerifyView,
    derive_steps,
    eta_sec,
    frontend_status,
    iso,
    make_project_id,
    media_file_name,
    parse_project_id,
    pipeline_status,
    qa_checks,
    t8_status,
)
from gani_dung_video.domain.feasibility import footage_fit
from gani_dung_video.domain.jobs import ANALYZE_STAGES, STATUSES, Job
from gani_dung_video.domain.pick import PickFile
from gani_dung_video.domain.plan import Plan, planned_sec
from gani_dung_video.domain.preflight import Rules, preflight_check
from gani_dung_video.domain.verify_report import RoundRecord, check_detail, check_rows
from gani_dung_video.infrastructure.config_files import CONFIG_DIR, ConfigError
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.music_analysis import MusicError
from gani_dung_video.infrastructure.music_index import get_index
from gani_dung_video.infrastructure.queue_db import QueueDb
from gani_dung_video.infrastructure.settings import Settings
from gani_dung_video.infrastructure.verify_report_file import read_report, report_path

JOB_KINDS = ("render", "draft", "analyze")
_REPORT_SLACK_SEC = 2.0  # file systems round modification times
_MEDIA_TYPES = {"video": "video/mp4", "cover": "image/jpeg", "srt": "text/plain; charset=utf-8"}
_AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}
MUSIC_CACHE_FILE = "music-cache.json"
_MAX_PROJECTS_PER_ROOT = 200


class ApiError(Exception):
    """A request the API refuses; `status` is the HTTP status, `message` is Vietnamese."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.extra: dict[str, Any] = extra  # more fields for the error body (e.g. pipeline_id)


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
    spent, reserved = ctx.db.spend_summary(ctx.clock())
    limit = ctx.rules["queue"].get("daily_budget_vnd")
    return HealthView(ok=True, rules_version=str(ctx.rules["rules_version"]),
                      daemon_online=st.online, daemon_pid=st.pid if st.online else None,
                      daemon_stopping=st.stop_request if st.online else None,
                      jobs=ctx.db.counts(), allowed_roots_configured=bool(ctx.roots),
                      ai_key_configured=ai_key_configured(ctx.settings),
                      budget=BudgetView(limit_vnd=float(limit) if limit is not None else None,
                                        spent_24h_vnd=spent, reserved_vnd=reserved),
                      features=FeaturesView(long_video=bool(
                          ctx.rules["durations"]["180"].get("enabled", False))))  # fmt: skip


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
    return _true_case(root, folder)


def _true_case(root: Path, folder: Path) -> Path:
    """`folder` spelled as its directory entries are. A case-insensitive file system (macOS)
    opens 'gani' for 'Gani' and resolve() keeps the client's spelling, but the queue compares
    folders as text: one folder must have one name. A name the folder lists as it is stays as
    it is, so on a case-sensitive file system (Linux) nothing ever changes."""
    out = root
    for part in folder.relative_to(root).parts:
        try:
            names = [p.name for p in out.iterdir()]
        except OSError:
            return folder
        if part not in names:
            same = [n for n in names if n.casefold() == part.casefold()]
            if len(same) != 1:  # none, or two that differ only in case: do not guess
                return folder
            part = same[0]
        out = out / part
    return out


def _project_id_of(ctx: ApiContext, folder: Path) -> str | None:
    for index, root in enumerate(ctx.roots):
        if folder == root:
            return make_project_id(index, ".")
        if folder.parent == root:
            with contextlib.suppress(ValueError):
                return make_project_id(index, folder.name)
    return None


# ─────────────── jobs ───────────────


def _private_dirs(ctx: ApiContext) -> list[tuple[str, str]]:
    """Folders outside the projects that console lines still quote: the user's home (Whisper
    and CLIP caches), the music library and the program's own folder."""
    out: list[tuple[str, str]] = []
    with contextlib.suppress(RuntimeError, OSError):  # no home directory can be determined
        home = Path.home()
        out += [(str(home), "~"), (str(home.resolve()), "~")]
    music = music_dir(ctx.settings)
    if music is not None:
        folder = Path(music).expanduser()
        out += [(str(folder), "<nhạc>"), (str(folder.resolve()), "<nhạc>")]
    return [*out, (str(CONFIG_DIR.parent), "<chương trình>")]


def _scrub(ctx: ApiContext, job_dir: str, text: str | None) -> str | None:
    """Console lines and errors come from other processes and may quote absolute paths: the
    job's folder becomes <its name>, an allowed root <gốc>, the music library <nhạc>, the
    program's folder <chương trình> and the home folder ~. The longest path goes first, so a
    root inside the home folder is still <gốc>."""
    if not text:
        return text
    swaps = [(job_dir, f"<{Path(job_dir).name}>"), *((str(r), "<gốc>") for r in ctx.roots),
             *_private_dirs(ctx)]  # fmt: skip
    table: dict[str, str] = {}
    for base, repl in swaps:
        if len(Path(base).parts) < 2:  # "/" or "C:\\" would eat every path separator
            continue
        for variant in (base, base.replace("\\", "/")):
            table.setdefault(variant, repl)
    for variant in sorted(table, key=len, reverse=True):
        text = text.replace(variant, table[variant])
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


def _fresh(path: Path, job: Job) -> bool:
    """The file was written during THIS run: plan.json and candidates.json belong to the project,
    so one left by an older run says nothing about the job that is waiting now."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return job.started_at is not None and mtime >= job.started_at - _REPORT_SLACK_SEC


def _read_footage(folder: Path, job: Job, rules: Rules) -> FootageView | None:
    """Room to spare between the picked scenes and the plan, for a draft job at the approval
    gate. None for every other job, for speech mode, and whenever the files cannot be trusted."""
    if job.kind != "draft" or job.status != "awaiting_approval":
        return None
    edit = folder / EDIT_DIR
    try:
        if not (_fresh(edit / "plan.json", job) and _fresh(edit / "candidates.json", job)):
            return None
        plan = Plan.model_validate_json((edit / "plan.json").read_text("utf-8"))
        pick = PickFile.model_validate_json((edit / "candidates.json").read_text("utf-8"))
    except (OSError, ValueError):  # pydantic's ValidationError is a ValueError
        return None
    fit = footage_fit(plan, pick, rules)
    if fit is None:
        return None
    return FootageView(
        scenes_available=fit.scenes_available, scenes_needed_min=fit.scenes_needed_min,
        scenes_needed_max=fit.scenes_needed_max, warn_below=fit.warn_below, tight=fit.tight,
        blocks=[FootageBlockView(role=b.role, candidates=b.candidates,
                                 beats_needed=b.beats_needed, max_fill=b.max_fill,
                                 scenes_min=b.scenes_min, scenes_max=b.scenes_max, tight=b.tight)
                for b in fit.blocks],
    )  # fmt: skip


def _read_verify(ctx: ApiContext, folder: Path, job: Job) -> VerifyView | None:
    """What the checks said during THIS draft job (phase 6i). The report belongs to the project
    folder, so one written outside the job's run window is another run's and is not shown."""
    if job.kind != "draft" or job.status not in ("completed", "failed"):
        return None
    path = report_path(folder)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if job.started_at is None or mtime < job.started_at - _REPORT_SLACK_SEC:
        return None
    if job.finished_at is not None and mtime > job.finished_at + _REPORT_SLACK_SEC:
        return None
    report = read_report(path)
    if report is None:
        return None
    rules = ctx.rules
    labels: Mapping[str, str] = rules.get("verify_labels", {})

    def rows(record: RoundRecord) -> list[VerifyCheckView]:
        return [VerifyCheckView(code=r.code, label=labels.get(r.code, ""), status=r.status,
                                detail=_scrub(ctx, job.project_dir, check_detail(r)) or "")
                for r in check_rows(report.mode, record, report.extra_codes)]  # fmt: skip

    rounds = [
        VerifyRoundView(round=r.round, drafts=r.drafts, parsed=r.parsed, passed=r.passed,
                        cost_vnd=r.cost_vnd, checks=rows(r))
        for r in report.rounds
    ]  # fmt: skip
    final: Literal["passed", "failed", "cost_cap", "api_error", "interrupted"] = (
        "interrupted" if report.final_status == "running" else report.final_status
    )
    return VerifyView(mode=report.mode, final_status=final, rounds_used=len(rounds),
                      max_rounds=int(rules["ai"]["max_attempts"]),
                      checks=rounds[-1].checks if rounds else [], rounds=rounds)  # fmt: skip


def _read_structure(folder: Path, job: Job) -> StructureView | None:
    """Hook / chapters / CTA on the timeline, with the chapter titles of this job's draft when
    drafts.json has them. None for short videos, analyze jobs and unreadable files."""
    if job.kind == "analyze":
        return None
    edit = folder / EDIT_DIR
    try:
        plan = Plan.model_validate_json((edit / "plan.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not plan.chapters:
        return None
    titles: dict[str, str] = {}
    with contextlib.suppress(OSError, ValueError):
        drafts = DraftsFile.model_validate_json((edit / "drafts.json").read_text("utf-8"))
        mine = next((d for d in drafts.drafts if d.draft == job.draft), None)
        titles = {t.chapter: t.title for t in mine.chapter_titles} if mine else {}
    beat, total = plan.beat_sec, planned_sec(plan)
    first, last = plan.chapters[0], plan.chapters[-1]
    end_of_chapters = (last.start_beat + last.beats) * beat
    items = [StructureItemView(kind="opening", id=plan.blocks[0].role, start_sec=0.0,
                               end_sec=first.start_beat * beat, title=None)]  # fmt: skip
    items += [StructureItemView(kind="chapter", id=c.id, start_sec=c.start_beat * beat,
                                end_sec=(c.start_beat + c.beats) * beat,
                                title=titles.get(c.id) or None) for c in plan.chapters]  # fmt: skip
    items.append(
        StructureItemView(
            kind="closing",
            id=plan.blocks[-1].role,
            start_sec=end_of_chapters,
            end_sec=total,
            title=None,
        )
    )
    return StructureView(duration_sec=total, items=items)


def _plan_is_speech(folder: Path) -> bool | None:
    """Whether the project's plan is speech-driven; None when plan.json cannot be read."""
    with contextlib.suppress(OSError, ValidationError):
        plan = Plan.model_validate_json((folder / EDIT_DIR / "plan.json").read_text("utf-8"))
        return plan.driver == "speech"
    return None


def _qa_view(report: QcReport, job: Job, folder: Path) -> QaView:
    return QaView(passed=report.passed, rounds=report.rounds, fixes=report.fixes,
                  loudness_two_pass=report.loudness_two_pass, lufs=report.lufs,
                  true_peak_db=report.true_peak_db,
                  checks=qa_checks(report.findings, report.rounds, report.lufs,
                                   report.true_peak_db, report.t8),
                  t8=t8_status(final=job.final, speech=_plan_is_speech(folder),
                               no_asr=job.no_asr, measured=report.t8 is not None))  # fmt: skip


def _media_path(ctx: ApiContext, job: Job, kind: str) -> Path | None:
    """Where a media file of a finished job lives, or None. Rebuilt, never trusted.

    Every folder on the way (edit/, render/, the draft's) may be a link: the file itself, once
    every link is followed, must still be inside an allowed root."""
    if job.status != "completed":
        return None
    try:
        name = media_file_name(kind, job.draft, job.final)
    except ValueError:
        return None
    roots = ctx.roots
    folder = Path(job.project_dir).resolve()
    if not any(_inside(folder, r) for r in roots):
        return None
    out = (folder / EDIT_DIR / "render" / job.draft).resolve()
    path = (out / name).resolve()
    if path.parent != out or not any(_inside(path, r) for r in roots) or not path.is_file():
        return None
    return path


def _expected_sec(ctx: ApiContext, job: Job) -> float:
    """Expected run time of a job, only for its % / ETA."""
    cfg = ctx.rules["queue"]
    stages = cfg["stage_expected_sec"]
    if job.kind == "analyze":
        return float(sum(stages[s] for s in ANALYZE_STAGES))
    if job.kind == "draft":
        names = ("estimate", "draft") if job.phase == "paid" else ("plan", "pick", "phrases",
                                                                    "estimate")  # fmt: skip
        return float(sum(stages[s] for s in names))
    return limits_of(cfg, Path(job.project_dir), job.final)[0]


def _cost_view(job: Job) -> CostView | None:
    if job.kind != "draft" or job.cost_worst_call_vnd is None:
        return None
    return CostView(input_tokens=job.cost_input_tokens or 0,
                    worst_call_vnd=job.cost_worst_call_vnd, cap_vnd=job.cost_cap_vnd or 0.0,
                    approved_vnd=job.approved_vnd, spent_vnd=job.spent_vnd)  # fmt: skip


_KIND_TITLE = {"analyze": "phân tích footage", "draft": "kịch bản"}


def _view(ctx: ApiContext, job: Job) -> JobView:
    folder = Path(job.project_dir)
    size = _job_size(job)
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
                  error_msg=error, kind=job.kind)  # fmt: skip
    urls = {k: (f"/media/{job.id}/{k}" if _media_path(ctx, job, k) else None)
            for k in ("video", "cover", "srt")}  # fmt: skip
    report = _read_qc(folder / EDIT_DIR / "render" / job.draft, job) if urls["video"] else None
    label = "cuối" if job.final else "nháp"
    what = _KIND_TITLE.get(job.kind) or f"bản {job.draft} ({label})"
    return JobView(
        id=job.id, pipeline_id=job.pipeline_id, depends_on=job.depends_on,
        needs_approval=job.kind == "draft" and job.status == "awaiting_approval",
        approval_expires_at=(iso(job.approval_expires_at)
                             if job.approval_expires_at is not None else None),
        cost=_cost_view(job),
        project_id=_project_id_of(ctx, folder), project_name=folder.name,
        title=f"{folder.name} — {what}", kind=job.kind, draft=job.draft,
        size=size, status=job.status, result=job.result,
        frontend_status=frontend_status(job.status, job.result), attempts=job.attempts,
        max_attempts=job.max_attempts, progress_pct=job.progress_pct, stage=job.stage,
        eta_sec=eta_sec(job.status, job.started_at, _expected_sec(ctx, job), ctx.clock()),
        created_at=iso(job.created_at),
        started_at=iso(job.started_at) if job.started_at is not None else None,
        finished_at=iso(job.finished_at) if job.finished_at is not None else None,
        error_msg=error, no_asr=job.no_asr,
        options=_read_options(folder),
        video_url=urls["video"], cover_url=urls["cover"], srt_url=urls["srt"],
        steps=derive_steps(arts, run), qa=_qa_view(report, job, folder) if report else None,
        footage=_read_footage(folder, job, ctx.rules), verify=_read_verify(ctx, folder, job),
        structure=_read_structure(folder, job),
        review=_review_view_of_job(ctx, job),
    )  # fmt: skip


def _visible(ctx: ApiContext, job: Job) -> bool:
    """The job's folder is (still) inside the allow-list: the API neither shows nor touches
    any other job, whatever queue.db holds."""
    folder = Path(job.project_dir).resolve()
    return any(_inside(folder, r) for r in ctx.roots)


# ─────────────── review of delivered final videos (T-F) ───────────────

_REVIEW_STATES = ("pending", "approved", "changes_requested")
_REVIEW_STATUS = {"not_found": 404, "reason_required": 422, "reviewer_required": 422}


def _review_view(row: ReviewRow) -> ReviewView:
    b = row.block
    return ReviewView(status=b.status, requested_at=b.requested_at, decided_at=b.decided_at,
                      decided_by=b.decided_by, reason=b.reason, reminded_at=b.reminded_at,
                      overdue=row.overdue, failed_checks=row.problems)  # fmt: skip


def _review_view_of_job(ctx: ApiContext, job: Job) -> ReviewView | None:
    if job.kind != "render" or not job.final or job.status != "completed":
        return None
    row = review_service.review_of_job(job, ctx.rules, ctx.clock())
    return _review_view(row) if row is not None else None


def _review_item(ctx: ApiContext, row: ReviewRow) -> ReviewItemView:
    folder = Path(row.job.project_dir)
    return ReviewItemView(job_id=row.job.id, project_id=_project_id_of(ctx, folder),
                          project_name=folder.name,
                          title=f"{folder.name} — bản {row.job.draft} (cuối)",
                          video=row.video.name, review=_review_view(row))  # fmt: skip


def authorize_reviewer(ctx: ApiContext, reviewer: str) -> str:
    """The one place that decides who may press approve/reject.

    T-F core runs on a machine whose server listens on 127.0.0.1 only: whoever can reach it is
    trusted and `reviewer` is just the name to record. The Telegram bot will tighten THIS
    function (an allow-list of ids, a shared key) instead of every route.
    """
    return reviewer


def _inside_roots(ctx: ApiContext, job: Job) -> bool:
    folder = Path(job.project_dir).resolve()
    return any(_inside(folder, root.resolve()) for root in ctx.roots)


def list_reviews(ctx: ApiContext, status: str | None = None,
                 since: str | None = None) -> ReviewListView:  # fmt: skip
    """Final videos delivered and waiting for (or already given) a decision."""
    if status is not None and status not in _REVIEW_STATES:
        raise ApiError(400, "bad_request", f"Trạng thái phải là: {', '.join(_REVIEW_STATES)}.")
    day = None
    if since is not None:
        try:
            day = date.fromisoformat(since)
        except ValueError:
            raise ApiError(400, "bad_request", "from phải có dạng YYYY-MM-DD.") from None
    if not ctx.roots:
        raise _no_roots()
    rows = review_service.list_reviews(ctx.db, ctx.rules, status=status, since=day,
                                       now=ctx.clock())  # fmt: skip
    return ReviewListView(items=[_review_item(ctx, r) for r in rows if _inside_roots(ctx, r.job)])


def review_decide(ctx: ApiContext, job_id: int, action: Literal["approve", "reject"],
                  req: ReviewRequest) -> ReviewItemView:  # fmt: skip
    """Approve (the file moves to final/) or ask for changes (reason required)."""
    job = _need(ctx, job_id)
    if not _inside_roots(ctx, job):
        raise _not_found(f"job #{job_id}")
    who = authorize_reviewer(ctx, req.reviewer)
    try:
        if action == "approve":
            row = review_service.approve(ctx.db, ctx.rules, job_id, who, req.reason, ctx.clock())
        else:
            row = review_service.reject(ctx.db, ctx.rules, job_id, who, req.reason or "",
                                        ctx.clock())  # fmt: skip
    except ReviewRefused as e:
        raise ApiError(_REVIEW_STATUS.get(e.code, 409), e.code, str(e), **e.extra) from e
    return _review_item(ctx, row)


def _need(ctx: ApiContext, job_id: int) -> Job:
    """A job (of any kind) by id, or a 404 (also for a job outside the allow-list)."""
    job = ctx.db.get(job_id)
    if job is None or not _visible(ctx, job):
        raise _not_found(f"job #{job_id}")
    return job


def _locked(e: ProjectLockedError) -> ApiError:
    """Another queue owns the folder: 409 naming that queue's job (never its path)."""
    return ApiError(409, "project_locked", str(e), pipeline_id=e.pipeline_id, job_id=e.job_id)


def _busy(e: PipelineBusyError) -> ApiError:
    """The folder has an active pipeline (or job): 409 naming it."""
    return ApiError(409, "pipeline_active", str(e), pipeline_id=e.pipeline_id, job_id=e.job_id)


def create_jobs(ctx: ApiContext, req: JobCreate) -> list[JobView]:
    """Queue the render(s) a request asks for and return their views.

    Raises:
        ApiError: 404 project, 409 not ready, a pipeline active in the folder, or the folder
            held by another queue.
    """
    folder = resolve_project(ctx, req.project_id)
    try:
        queued = enqueue_project_render(folder, ctx.db, draft=req.draft, final=req.final,
                                        no_asr=req.no_asr, rules=ctx.rules,
                                        settings=ctx.settings, now=ctx.clock())  # fmt: skip
    except NotReadyError as e:
        raise ApiError(409, "not_ready", str(e)) from e
    except PipelineBusyError as e:
        raise _busy(e) from e
    except ProjectLockedError as e:
        raise _locked(e) from e
    except ConfigError as e:
        raise ApiError(400, "bad_request", str(e)) from e
    return [_view(ctx, _need(ctx, r.job_id)) for r in queued]


def list_jobs(ctx: ApiContext, status: str | None = None, project_id: str | None = None,
              limit: int = 50, kind: str | None = None) -> list[JobView]:  # fmt: skip
    if status is not None and status not in STATUSES:
        raise ApiError(400, "bad_request", f"Trạng thái phải là một trong: {', '.join(STATUSES)}.")
    if kind is not None and kind not in JOB_KINDS:
        raise ApiError(400, "bad_request", f"Loại job phải là một trong: {', '.join(JOB_KINDS)}.")
    folder = str(resolve_project(ctx, project_id)) if project_id else None
    want = max(1, min(limit, 200))
    # Jobs outside the roots are dropped before the page is cut, so read on until it is full.
    out: list[JobView] = []
    offset = 0
    while len(out) < want:
        jobs = ctx.db.list_jobs(status, want, project_dir=folder, kind=kind, offset=offset)
        out += [_view(ctx, j) for j in jobs if _visible(ctx, j)]
        if len(jobs) < want:
            break
        offset += len(jobs)
    return out[:want]


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
    ctx.db.cascade_blocked(ctx.clock())  # jobs waiting for this one can never run: say so now
    return _view(ctx, _need(ctx, job_id))


def retry(ctx: ApiContext, job_id: int) -> JobView:
    job = _need(ctx, job_id)
    try:
        retried = retry_job(ctx.db, job_id, rules=ctx.rules, settings=ctx.settings,
                            now=ctx.clock())  # fmt: skip
    except ProjectLockedError as e:
        raise _locked(e) from e
    except ConfigError as e:  # the folder went away or left the allow-list meanwhile
        raise ApiError(409, "conflict", _scrub(ctx, job.project_dir, str(e)) or "") from e
    if not retried:
        raise ApiError(409, "conflict", f"Job #{job.id} đang '{job.status}': chỉ thử lại được job "
                                        "lỗi, đã hủy hoặc QC không đạt.")  # fmt: skip
    return _view(ctx, _need(ctx, job_id))


def _pipeline_view(ctx: ApiContext, pipeline_id: int, jobs: list[Job]) -> PipelineView:
    views = [_view(ctx, j) for j in jobs]
    folder = Path(jobs[0].project_dir)
    return PipelineView(
        id=pipeline_id, project_id=_project_id_of(ctx, folder), project_name=folder.name,
        status=pipeline_status([(j.status, j.result) for j in jobs]),
        needs_approval=any(v.needs_approval for v in views), jobs=views)  # fmt: skip


def create_pipeline(ctx: ApiContext, req: PipelineCreate) -> PipelineView:
    """Save the wizard's options and queue analyze → draft → render for the project.

    Raises:
        ApiError: 400 options blocked (nothing was written), 404 project, 409 the project already
            has an active job (the body names its pipeline).
    """
    folder = resolve_project(ctx, req.project_id)
    try:
        result = enqueue_pipeline(
            req.options, folder, ctx.db, draft=req.draft, final=req.final, no_asr=req.no_asr,
            force_analyze=req.force_analyze, rules=ctx.rules, settings=ctx.settings,
            now=ctx.clock())  # fmt: skip
    except PipelineBusyError as e:
        raise _busy(e) from e
    except ProjectLockedError as e:
        raise _locked(e) from e
    except ConfigError as e:
        raise ApiError(400, "bad_request", str(e)) from e
    return _pipeline_view(ctx, result.pipeline_id, ctx.db.jobs_of_pipeline(result.pipeline_id))


def get_pipeline(ctx: ApiContext, pipeline_id: int) -> PipelineView:
    jobs = ctx.db.jobs_of_pipeline(pipeline_id)
    if not jobs or not all(_visible(ctx, j) for j in jobs):
        raise _not_found(f"pipeline #{pipeline_id}")
    return _pipeline_view(ctx, pipeline_id, jobs)


def approve(ctx: ApiContext, job_id: int, req: ApproveRequest) -> JobView:
    """Approve the cost of a waiting draft job (the same check as `queue approve`)."""
    job = _need(ctx, job_id)
    if job.kind != "draft":
        raise ApiError(409, "conflict", f"Job #{job.id} là job {job.kind}: không có chi phí để "
                                        "duyệt.")  # fmt: skip
    try:
        approve_job(ctx.db, job_id, req.max_vnd, req.estimate_vnd, rules=ctx.rules,
                    now=ctx.clock())  # fmt: skip
    except ApprovalRefused as e:
        raise ApiError(409, "approval_refused", str(e)) from e
    return _view(ctx, _need(ctx, job_id))


def preflight(ctx: ApiContext, req: PreflightRequest) -> Mapping[str, Any]:
    """The same preflight the CLI runs before any AI spend (single source of truth)."""
    return preflight_check(req.options, ctx.rules)


def _music_folder(ctx: ApiContext) -> Path | None:
    raw = music_dir(ctx.settings)
    if raw is None:
        return None
    folder = Path(raw).expanduser()
    return folder if folder.is_dir() else None


def list_music(ctx: ApiContext) -> MusicListView:
    """The machine's music folder (GANI_DV_MUSIC_DIR) with BPM and length, from library.json or
    measured in the background. Never an absolute path; not configured is an empty list."""
    folder = _music_folder(ctx)
    if folder is None:
        return MusicListView(configured=False, pending=0, tracks=[])
    cache = queue_dir(ctx.settings) / MUSIC_CACHE_FILE
    index = get_index(folder, cache, ctx.rules["music_analysis"])
    try:
        tracks, pending = index.snapshot()
    except MusicError as e:
        raise ApiError(422, "music_library_invalid", str(e)) from e
    return MusicListView(configured=True, pending=pending, tracks=[
        MusicTrackView(file=t.file, path=t.file, title=t.title, bpm=t.bpm, bpm_source=t.bpm_source,
                       first_beat_sec=t.first_beat_sec, duration_sec=t.duration_sec,
                       rights=t.rights)
        for t in tracks])  # fmt: skip


def resolve_music(ctx: ApiContext, name: str) -> MediaFile:
    """The audio file behind /media/music/<name>: a file name directly inside the music folder
    with an audio extension, or a 404 (the same one for every refusal)."""
    folder = _music_folder(ctx)
    extensions = {str(e).lower() for e in ctx.rules["music_analysis"]["extensions"]}
    suffix = Path(name).suffix.lower()
    if (folder is None or not name or name != name.strip() or name.startswith(".")
            or any(c in name for c in '/\\:\x00') or suffix not in extensions):  # fmt: skip
        raise _not_found("bài nhạc")
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise _not_found("bài nhạc")
    return MediaFile(path=path, content_type=_AUDIO_TYPES.get(suffix, "application/octet-stream"),
                     size=path.stat().st_size)  # fmt: skip


def resolve_media(ctx: ApiContext, job_id: int, kind: str) -> MediaFile:
    """The file behind /media/<job>/<kind>, or a 404."""
    job = _need(ctx, job_id)
    path = _media_path(ctx, job, kind)
    if path is None or kind not in _MEDIA_TYPES:
        raise _not_found("tệp")
    return MediaFile(path=path, content_type=_MEDIA_TYPES[kind], size=path.stat().st_size)
