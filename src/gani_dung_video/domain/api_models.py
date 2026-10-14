"""Data contract of the local web API (phase 6b) and its pure derivations. No I/O.

The API returns the queue's own jobs (one job = one draft at one size) plus a
`frontend_status` in the UI's vocabulary; the UI groups preview and final jobs itself. The
pipeline `steps` are derived from files that really exist, never invented. See
docs/api-jobs.md for the mapping to the frontend's `RenderJob`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gani_dung_video.domain.asr_check import T8Measures
from gani_dung_video.domain.qc import Finding

_LETTERS = "ABC"
_BAD_NAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_PROJECT_ID = re.compile(r"^(\d{1,2})/(.+)$", re.S)
MAX_NAME_LEN = 120

# ─────────────── project ids: "<allowed root index>/<one folder name>" ───────────────


def _check_name(name: str) -> None:
    if name == ".":  # the allowed root itself is the project
        return
    if (not name or len(name) > MAX_NAME_LEN or _BAD_NAME_CHARS.search(name)
            or name != name.strip() or not name.strip(". ")):  # fmt: skip
        raise ValueError(f"Sai mã dự án: tên thư mục '{name[:40]}' không hợp lệ.")


def make_project_id(root_index: int, name: str) -> str:
    """Opaque id of a project: never an absolute path, never more than one folder name."""
    if root_index < 0:
        raise ValueError("Sai mã dự án: chỉ số thư mục gốc âm.")
    _check_name(name)
    return f"{root_index}/{name}"


def parse_project_id(project_id: str) -> tuple[int, str]:
    """Split an id from a client; raises ValueError (Vietnamese, contains 'mã dự án')."""
    m = _PROJECT_ID.match(project_id)
    if m is None:
        raise ValueError("Sai mã dự án: phải có dạng '<số>/<tên thư mục>'.")
    _check_name(m.group(2))
    return int(m.group(1)), m.group(2)


# ─────────────── requests ───────────────


class JobCreate(BaseModel):
    """POST /api/jobs body."""

    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: str
    draft: str = "all"  # A | B | C | all
    final: bool = False
    no_asr: bool = False

    @field_validator("project_id")
    @classmethod
    def _project(cls, v: str) -> str:
        parse_project_id(v)
        return v

    @field_validator("draft")
    @classmethod
    def _draft(cls, v: str) -> str:
        if v.lower() == "all":
            return "all"
        if len(v) == 1 and v.upper() in _LETTERS:
            return v.upper()
        raise ValueError("Bản nháp phải là A, B, C hoặc all.")


class PipelineCreate(BaseModel):
    """POST /api/pipelines body: the wizard's options and what to build from them."""

    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: str
    options: dict[str, Any]  # Any: arbitrary options JSON, judged by domain.preflight
    draft: str = "all"  # A | B | C | all (which drafts to render)
    final: bool = False
    no_asr: bool = False
    force_analyze: bool = False  # analyse the footage again even if it looks unchanged

    @field_validator("project_id")
    @classmethod
    def _project(cls, v: str) -> str:
        parse_project_id(v)
        return v

    @field_validator("draft")
    @classmethod
    def _draft(cls, v: str) -> str:
        if v.lower() == "all":
            return "all"
        if len(v) == 1 and v.upper() in _LETTERS:
            return v.upper()
        raise ValueError("Bản nháp phải là A, B, C hoặc all.")


class ApproveRequest(BaseModel):
    """POST /api/jobs/{id}/approve body: the most to spend, and the estimate the person saw."""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_vnd: float = Field(gt=0, allow_inf_nan=False)
    estimate_vnd: float = Field(ge=0, allow_inf_nan=False)


class PreflightRequest(BaseModel):
    """POST /api/preflight body: the options as the wizard collects them."""

    model_config = ConfigDict(extra="forbid")

    options: dict[str, Any]  # Any: arbitrary options JSON, judged by domain.preflight


# ─────────────── responses ───────────────

StepStatus = Literal["done", "running", "queued", "error"]


class ProjectView(BaseModel):
    id: str
    name: str
    has_inventory: bool
    has_plan: bool
    has_drafts: bool
    ready_to_render: bool  # plan and drafts exist: a job can be queued


class EventView(BaseModel):
    id: int
    ts: str
    level: str
    text: str


class StepView(BaseModel):
    id: str
    label: str
    status: StepStatus
    detail: str | None = None


class QaCheckView(BaseModel):
    code: str
    label: str
    passed: bool
    detail: str


T8Status = Literal["ran", "skipped_draft", "skipped_music", "skipped_no_asr", "unavailable"]


class QaView(BaseModel):
    passed: bool
    rounds: int
    fixes: list[str]
    loudness_two_pass: bool
    lufs: float | None
    true_peak_db: float | None
    checks: list[QaCheckView]
    t8: T8Status = "unavailable"  # why there is (or is not) a T8 row in `checks`


class CostView(BaseModel):
    """What a draft job may cost (Claude is the only thing in the pipeline that spends money)."""

    input_tokens: int
    worst_call_vnd: float  # one call, worst case
    cap_vnd: float  # ceiling for this video length
    approved_vnd: float | None  # what a person allowed, once approved
    spent_vnd: float | None  # what ai_log.jsonl says was really spent


class FootageBlockView(BaseModel):
    """One plan block against the scenes picked for it."""

    role: str
    candidates: int  # different scenes offered to this block
    beats_needed: int
    max_fill: int  # most beats those scenes can carry
    scenes_min: int  # fewest / most scenes that make exactly `beats_needed`
    scenes_max: int
    tight: bool


class FootageView(BaseModel):
    """How much room the footage leaves a draft waiting for approval (phase 6h). Never blocks:
    `tight` only tells the UI to warn that Claude may repeat a scene and need several rounds."""

    scenes_available: int  # usable different scenes over all blocks
    scenes_needed_min: int
    scenes_needed_max: int
    warn_below: int  # tight when scenes_available is below this (rules.pick.footage_warn_ratio)
    tight: bool
    blocks: list[FootageBlockView]


class VerifyCheckView(BaseModel):
    """One check (V1–V9 / S1–S6 / SNAP) as the UI shows it."""

    code: str
    label: str  # Vietnamese name from rules.verify_labels ("" for a code nobody named)
    status: Literal["pass", "fail", "fixed", "warn", "skipped"]
    detail: str  # what the check said; empty when it passed cleanly


class VerifyRoundView(BaseModel):
    round: int
    drafts: list[str]  # drafts Claude was asked for in this round
    parsed: bool  # False: the answer could not be read, so the checks never ran
    passed: bool  # nothing left for Claude to fix
    cost_vnd: float
    checks: list[VerifyCheckView]


class VerifyView(BaseModel):
    """What the draft's V1–V9 (music) / S1–S6 (speech) checks said, round by round (phase 6i).
    Only for a finished draft job whose run wrote edit/verify_report.json."""

    mode: Literal["music", "speech"]
    final_status: Literal["passed", "failed", "cost_cap", "api_error", "interrupted"]
    rounds_used: int
    max_rounds: int  # rules.ai.max_attempts
    checks: list[VerifyCheckView]  # the last round (empty when no call was made)
    rounds: list[VerifyRoundView]


class StructureItemView(BaseModel):
    """One stretch of a 3-minute video on its timeline (seconds from the first beat)."""

    kind: Literal["opening", "chapter", "closing"]
    id: str  # the chapter id, or the hook / CTA block role
    start_sec: float
    end_sec: float
    title: str | None  # chapter title written by Claude (chapters of a finished draft only)


class StructureView(BaseModel):
    """The hook / chapters / CTA layout of a 3-minute plan (phase 7g), for the timeline."""

    duration_sec: float
    items: list[StructureItemView]


class ReviewView(BaseModel):
    """Where a delivered final video stands with its reviewer (T-F)."""

    status: Literal["pending", "approved", "changes_requested"]
    requested_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str | None = None  # why changes were asked for (or a note on an approval)
    reminded_at: str | None = None  # the one reminder that went out
    overdue: bool = False  # waiting longer than review.ttl_sec and still undecided
    failed_checks: list[str] = []  # "CODE: detail" of every check that did not pass


class ReviewItemView(BaseModel):
    job_id: int
    project_id: str | None
    project_name: str
    title: str
    video: str  # the delivered file name (never a path)
    review: ReviewView


class ReviewListView(BaseModel):
    items: list[ReviewItemView]


class ReviewRequest(BaseModel):
    """POST /api/jobs/{id}/review/approve|reject body. `reviewer` is whoever pressed (a name,
    or later a Telegram id); `reason` is required to ask for changes."""

    model_config = ConfigDict(extra="forbid", strict=True)

    reviewer: str = Field(max_length=64)
    reason: str | None = Field(default=None, max_length=500)


class JobView(BaseModel):
    id: int
    pipeline_id: int | None = None
    depends_on: int | None = None
    needs_approval: bool = False  # a draft job waiting for a cost approval
    approval_expires_at: str | None = None
    cost: CostView | None = None
    project_id: str | None  # None when the job's folder is not a direct project of a root
    project_name: str
    title: str
    kind: str
    draft: str
    size: Literal["preview", "final"]
    status: str
    result: str | None
    frontend_status: str
    attempts: int
    max_attempts: int
    progress_pct: float
    stage: str | None
    eta_sec: int | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_msg: str | None
    no_asr: bool
    options: dict[str, Any]  # Any: plan.json's options echoed as written (English keys)
    video_url: str | None
    cover_url: str | None
    srt_url: str | None
    steps: list[StepView]
    qa: QaView | None
    footage: FootageView | None = None  # only a draft job at the approval gate, music modes
    verify: VerifyView | None = None  # only a finished draft job (phase 6i)
    structure: StructureView | None = None  # only a draft / render job of a chapter plan (7g)
    review: ReviewView | None = None  # only a finished FINAL render that was delivered (T-F)


class JobDetail(JobView):
    events: list[EventView]


class PipelineView(BaseModel):
    """One wizard run: its jobs in creation order and a status for the whole chain."""

    id: int
    project_id: str | None
    project_name: str
    status: (
        str  # the UI's vocabulary (queued | running | awaiting_approval | qa_review | done | error)
    )
    needs_approval: bool
    jobs: list[JobView]


class MusicTrackView(BaseModel):
    """One track of the machine's music folder. `file` is a name inside that folder, never an
    absolute path; `path` is the same name relative to it (the folder is not walked)."""

    file: str
    path: str
    title: str
    bpm: float | None
    # library = declared in library.json (checked by a person); measured = by the machine;
    # unknown = no clear beat; pending = the background measurement has not reached it yet
    bpm_source: Literal["library", "measured", "unknown", "pending"]
    first_beat_sec: float | None
    duration_sec: float | None
    rights: Literal["thu_vien_noi_bo", "mua_ban_quyen", "royalty_free"]


class MusicListView(BaseModel):
    configured: bool  # GANI_DV_MUSIC_DIR is set and is a folder
    pending: int  # tracks the background measurement still has to reach
    tracks: list[MusicTrackView]


class BudgetView(BaseModel):
    limit_vnd: float | None  # None: no daily limit configured
    spent_24h_vnd: float
    reserved_vnd: float  # approved but not yet spent by jobs that are waiting or running


class FeaturesView(BaseModel):
    """What this server lets the UI offer. `long_video`: 3-minute videos are open (phase 7e
    switches `durations.180.enabled` on after the real acceptance run)."""

    long_video: bool


class HealthView(BaseModel):
    ok: bool
    rules_version: str
    daemon_online: bool
    daemon_pid: int | None
    daemon_stopping: str | None
    jobs: dict[str, int]
    allowed_roots_configured: bool
    ai_key_configured: bool  # yes or no, never the key
    budget: BudgetView
    features: FeaturesView


class ErrorBody(BaseModel):
    code: str
    message: str


# ─────────────── pure derivations ───────────────


def frontend_status(status: str, result: str | None) -> str:
    """Queue status → the UI's JobStatus (queued | running | qa_review | done | error)."""
    if status == "queued":
        return "queued"
    if status == "processing":
        return "running"
    if status == "awaiting_approval":
        return "awaiting_approval"  # a new value for the UI (phase 6c-2 fills it)
    if status == "completed":
        return "done" if result == "passed" else "qa_review"
    return "error"  # failed, cancelled


def pipeline_status(jobs: Sequence[tuple[str, str | None]]) -> str:
    """One status for a chain of (queue status, result) pairs, in the UI's vocabulary."""
    seen = {frontend_status(s, r) for s, r in jobs}
    if not seen:
        return "queued"
    if "error" in seen:
        return "error"
    if "awaiting_approval" in seen:
        return "awaiting_approval"
    if seen & {"queued", "running"}:
        return "queued" if seen == {"queued"} else "running"
    return "qa_review" if "qa_review" in seen else "done"


def iso(ts: float) -> str:
    """Epoch seconds → ISO 8601 UTC ('...Z')."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def eta_sec(status: str, started_at: float | None, expected_sec: float, now: float) -> int | None:
    """Seconds left by the expected duration; only while the job runs, never negative."""
    if status != "processing" or started_at is None:
        return None
    return max(0, round(expected_sec - (now - started_at)))


def media_file_name(kind: str, draft: str, final: bool) -> str:
    """File a media request may serve. Same names as render_service; anything else raises."""
    if len(draft) != 1 or draft not in _LETTERS:
        raise ValueError("Bản nháp phải là A, B hoặc C.")
    if kind == "video":
        return f"video_{draft}_1080x1920.mp4" if final else f"nhap_{draft}_540p.mp4"
    if kind == "cover":
        return "cover.jpg"
    if kind == "srt":
        return "phude.srt"
    raise ValueError("Loại tệp phải là video, cover hoặc srt.")


class Artifacts(BaseModel):
    """Which pipeline files a project has (presence only)."""

    inventory: bool
    scenes: bool
    speech: bool
    plan: bool
    pick: bool
    drafts: bool
    preview_video: bool  # this draft's 540p preview exists


class RunInfo(BaseModel):
    """The job facts the render steps depend on."""

    size: Literal["preview", "final"]
    status: str
    result: str | None
    stage: str | None
    error_msg: str | None
    kind: str = "render"  # render | analyze | draft


STEP_LABELS: dict[str, str] = {
    "inventory": "Inventory (kiểm kê footage)",
    "nhan_dang": "Nhận dạng (Whisper + CLIP + OpenCV)",
    "plan_compile": "Biên dịch plan (khối / phách)",
    "pick": "Chọn ứng viên cảnh",
    "ai_draft": "AI viết kịch bản (Claude)",
    "render_preview": "Dựng bản xem trước (540p)",
    "qc_verify": "Soát kỹ thuật (T1–T7)",
    "final_export": "Xuất bản cuối (1080×1920)",
}


def _done(flag: bool) -> StepStatus:
    return "done" if flag else "queued"


_RUN_STATUS: dict[str, StepStatus] = {"queued": "queued", "processing": "running",
                                      "completed": "done"}  # fmt: skip


def _run_status(status: str) -> StepStatus:
    return _RUN_STATUS.get(status, "error")


# The step a job stage belongs to (analyze and draft jobs).
_STAGE_STEP = {"inventory": "inventory", "transcribe": "nhan_dang", "scenes": "nhan_dang",
               "semantics": "nhan_dang", "plan": "plan_compile", "pick": "pick",
               "phrases": "ai_draft", "estimate": "ai_draft", "draft": "ai_draft",
               "cho_duyet": "ai_draft"}  # fmt: skip
_OWN_STEPS = {"analyze": ("inventory", "nhan_dang"), "draft": ("plan_compile", "pick", "ai_draft")}


def _steps_before_render(art: Artifacts, run: RunInfo) -> list[StepView]:
    """Steps of an analyze or draft job: files say what is done, the stage says what runs."""
    status: dict[str, StepStatus] = {
        "inventory": _done(art.inventory), "nhan_dang": _done(art.scenes and art.speech),
        "plan_compile": _done(art.plan), "pick": _done(art.pick), "ai_draft": _done(art.drafts),
        "render_preview": "queued", "qc_verify": "queued", "final_export": "queued",
    }  # fmt: skip
    details: dict[str, str | None] = {}
    active = _STAGE_STEP.get(run.stage or "")
    own = _OWN_STEPS.get(run.kind, ())
    if run.status == "processing" and active:
        status[active], details[active] = "running", run.stage
    elif run.status == "awaiting_approval":
        status["ai_draft"], details["ai_draft"] = "queued", "Chờ duyệt chi phí"
    elif run.status in ("failed", "cancelled"):
        target = active or next((s for s in own if status[s] != "done"), own[-1] if own else None)
        if target:
            status[target], details[target] = "error", run.error_msg
    return [StepView(id=k, label=STEP_LABELS[k], status=v, detail=details.get(k))
            for k, v in status.items()]  # fmt: skip


def derive_steps(art: Artifacts, run: RunInfo) -> list[StepView]:
    """The frontend's eight pipeline steps from real files and the job's state."""
    if run.kind != "render":
        return _steps_before_render(art, run)
    render = _run_status(run.status)
    detail = run.error_msg if render == "error" else (run.stage if render == "running" else None)
    qc: StepStatus = "queued"
    if run.status == "completed":
        qc = "done" if run.result == "passed" else "error"
    preview: StepStatus
    final: StepStatus
    if run.size == "preview":
        preview, final = render, "queued"
        export_detail, preview_detail = None, detail
    else:
        preview, final = _done(art.preview_video), render
        export_detail, preview_detail = detail, None
    status: dict[str, StepStatus] = {
        "inventory": _done(art.inventory),
        "nhan_dang": _done(art.scenes and art.speech),
        "plan_compile": _done(art.plan),
        "pick": _done(art.pick),
        "ai_draft": _done(art.drafts),
        "render_preview": preview,
        "qc_verify": qc,
        "final_export": final,
    }
    details = {"render_preview": preview_detail, "final_export": export_detail}
    return [StepView(id=k, label=STEP_LABELS[k], status=v, detail=details.get(k))
            for k, v in status.items()]  # fmt: skip


_QA_LABELS = {
    "T1": "Khung hình, fps, codec, độ dài",
    "T2": "Số khung hình",
    "T3": "Độ to và đỉnh âm thanh",
    "T4": "Khung hình đen",
    "T5": "Khung hình đứng",
    "T6": "Chữ chồng nhau",
    "T7": "Chữ quá dài",
    "T8": "Whisper nghe lại thành phẩm",
}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}".replace(".", ",") + "%"


def _t8_detail(m: T8Measures) -> str:
    """A passed T8: what Whisper heard against the limits, in Vietnamese number style."""
    head = f"Đạt: Whisper nghe lại sai {_pct(m.wer)} số từ (tối đa {m.max_wer * 100:g}%)"
    if m.median_drift_sec is None:
        text = f"{head}; không đo được độ lệch phụ đề."
    else:
        text = (f"{head}, phụ đề lệch tiếng trung vị {m.median_drift_sec * 1000:.0f}ms "
                f"(tối đa {m.max_median_drift_sec * 1000:.0f}ms).")  # fmt: skip
    if m.voice_only_wer is not None:
        text += f" Bản bỏ nhạc sai {_pct(m.voice_only_wer)}."
    return text


def t8_status(*, final: bool, speech: bool | None, no_asr: bool, measured: bool) -> T8Status:
    """Why a job's QA has (or lacks) a T8 row. `speech` is None when the plan cannot be read.
    The reasons T8 cannot apply come first: a draft never listens, whatever else was asked."""
    if not final:
        return "skipped_draft"
    if speech is False:
        return "skipped_music"
    if no_asr:
        return "skipped_no_asr"
    return "ran" if measured else "unavailable"


def _detail(code: str, mine: Sequence[Finding], lufs: float | None, tp: float | None,
            t8: T8Measures | None = None) -> str:  # fmt: skip
    if code == "T8" and t8 is not None and not mine:
        return _t8_detail(t8)
    if mine:
        prefix = "" if any(f.level in ("error", "fix") for f in mine) else "Cảnh báo: "
        return prefix + " ".join(f.msg for f in mine)
    if code == "T3" and lufs is not None and tp is not None:
        return f"Đo được {lufs:.1f} LUFS, đỉnh thật {tp:.1f} dBTP."
    return "Đạt."


def qa_checks(findings: Sequence[Finding], rounds: int, lufs: float | None,
              true_peak_db: float | None, t8: T8Measures | None = None
              ) -> list[QaCheckView]:  # fmt: skip
    """One row per technical check T1–T7 (all run whenever a video was rendered), plus T8
    whenever Whisper listened (its numbers are stored) or left a finding, even a clean pass.
    A warning keeps the check passed."""
    if rounds < 1:
        return []
    codes = [f"T{i}" for i in range(1, 8)]
    if t8 is not None or any(f.code == "T8" for f in findings):
        codes.append("T8")
    out = []
    for code in codes:
        mine = [f for f in findings if f.code == code]
        failed = any(f.level in ("error", "fix") for f in mine)
        out.append(QaCheckView(code=code, label=_QA_LABELS[code], passed=not failed,
                               detail=_detail(code, mine, lufs, true_peak_db, t8)))  # fmt: skip
    return out
