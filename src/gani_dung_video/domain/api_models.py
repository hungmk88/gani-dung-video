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

from pydantic import BaseModel, ConfigDict, field_validator

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


class QaView(BaseModel):
    passed: bool
    rounds: int
    fixes: list[str]
    loudness_two_pass: bool
    lufs: float | None
    true_peak_db: float | None
    checks: list[QaCheckView]


class JobView(BaseModel):
    id: int
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


class JobDetail(JobView):
    events: list[EventView]


class HealthView(BaseModel):
    ok: bool
    rules_version: str
    daemon_online: bool
    daemon_pid: int | None
    daemon_stopping: str | None
    jobs: dict[str, int]
    allowed_roots_configured: bool


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
    if status == "completed":
        return "done" if result == "passed" else "qa_review"
    return "error"  # failed, cancelled


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


def derive_steps(art: Artifacts, run: RunInfo) -> list[StepView]:
    """The frontend's eight pipeline steps from real files and the job's state."""
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


def _detail(code: str, mine: Sequence[Finding], lufs: float | None, tp: float | None) -> str:
    if mine:
        prefix = "" if any(f.level in ("error", "fix") for f in mine) else "Cảnh báo: "
        return prefix + " ".join(f.msg for f in mine)
    if code == "T3" and lufs is not None and tp is not None:
        return f"Đo được {lufs:.1f} LUFS, đỉnh thật {tp:.1f} dBTP."
    return "Đạt."


def qa_checks(findings: Sequence[Finding], rounds: int, lufs: float | None,
              true_peak_db: float | None) -> list[QaCheckView]:  # fmt: skip
    """One row per technical check T1–T7 (all run whenever a video was rendered), plus T8 when
    the Whisper check left a finding. A warning keeps the check passed."""
    if rounds < 1:
        return []
    codes = [f"T{i}" for i in range(1, 8)]
    if any(f.code == "T8" for f in findings):
        codes.append("T8")
    out = []
    for code in codes:
        mine = [f for f in findings if f.code == code]
        failed = any(f.level in ("error", "fix") for f in mine)
        out.append(QaCheckView(code=code, label=_QA_LABELS[code], passed=not failed,
                               detail=_detail(code, mine, lufs, true_peak_db)))  # fmt: skip
    return out
