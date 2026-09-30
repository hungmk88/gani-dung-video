"""Tests: application/api_service.py — the use cases behind the HTTP API (6b)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.api_service import (
    ApiContext,
    ApiError,
    approve,
    cancel,
    create_jobs,
    create_pipeline,
    get_job,
    get_pipeline,
    health,
    job_events,
    list_jobs,
    list_projects,
    preflight,
    resolve_media,
    resolve_project,
    retry,
)
from gani_dung_video.domain.api_models import (
    ApproveRequest,
    JobCreate,
    PipelineCreate,
    PreflightRequest,
)
from gani_dung_video.domain.jobs import Estimate
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.domain.verify_report import Finding, RoundRecord, VerifyReportFile
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.project_lock import acquire
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb
from gani_dung_video.infrastructure.settings import Settings

RULES = load_rules()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "allowed"
    r.mkdir()
    return r


@pytest.fixture
def ctx(tmp_path: Path, root: Path) -> ApiContext:
    return ApiContext(QueueDb(tmp_path / "q" / "queue.db"), RULES,
                      Settings(gani_dv_allowed_roots=str(root)))  # fmt: skip


def make_project(root: Path, name: str = "footage", count: int = 2, drafts: bool = True) -> Path:
    folder = root / name
    (folder / "edit").mkdir(parents=True, exist_ok=True)
    opts = VideoOptions(phong_cach="ke_chuyen_nghe", muc_dich="gioi_thieu_xuong", do_dai_giay=15,
                        so_ban_nhap=count, nhac="khong_nhac")  # fmt: skip
    plan = compile_plan(opts, RULES, music=None, available_tags=set())
    (folder / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    if drafts:
        (folder / "edit" / "drafts.json").write_text("{}", encoding="utf-8")
    return folder


def finish_job(ctx: ApiContext, jid: int, result: str = "passed") -> Path:
    """Run a queued job to completion and write the files a real render leaves."""
    job = ctx.db.claim(time.time(), 1)
    assert job is not None and job.id == jid
    out = Path(job.project_dir) / "edit" / "render" / job.draft
    out.mkdir(parents=True, exist_ok=True)
    name = f"video_{job.draft}_1080x1920.mp4" if job.final else f"nhap_{job.draft}_540p.mp4"
    (out / name).write_bytes(b"0123456789" * 100)
    (out / "cover.jpg").write_bytes(b"jpg")
    (out / "phude.srt").write_text("1\n", encoding="utf-8")
    report = {"video": name, "passed": result == "passed", "rounds": 1, "font_px": {},
              "loudness_two_pass": False, "fixes": [], "findings": [], "lufs": -13.9,
              "true_peak_db": -2.0}  # fmt: skip
    (out / "qc_report.json").write_text(json.dumps(report), encoding="utf-8")
    ctx.db.finish(jid, time.time(), "completed", result, 0 if result == "passed" else 1, None,
                  str(out / name))  # fmt: skip
    return out


def queue_one(ctx: ApiContext, root: Path, **kw: Any) -> int:
    make_project(root)
    body = {"project_id": "0/footage", "draft": "A", **kw}
    return create_jobs(ctx, JobCreate(**body))[0].id


# ─────────────── health ───────────────


def test_health_reports_the_daemon_and_the_counts(ctx: ApiContext, root: Path) -> None:
    h = health(ctx)
    assert h.ok and not h.daemon_online and h.allowed_roots_configured
    assert h.jobs["queued"] == 0 and h.rules_version == RULES["rules_version"]
    queue_one(ctx, root)
    ctx.db.register_daemon(4321, time.time(), 15, lambda p: True)
    h = health(ctx)
    assert h.daemon_online and h.daemon_pid == 4321 and h.jobs["queued"] == 1


def test_health_says_when_the_allow_list_is_missing(ctx: ApiContext) -> None:
    bare = ApiContext(ctx.db, RULES, Settings())
    assert not health(bare).allowed_roots_configured


# ─────────────── projects ───────────────


def test_projects_are_listed_with_opaque_ids_and_readiness(ctx: ApiContext, root: Path) -> None:
    make_project(root, "Xuong GANI")
    make_project(root, "chua_xong", drafts=False)
    (root / "trong").mkdir()
    (root / ".an").mkdir()
    got = {p.name: p for p in list_projects(ctx)}
    assert set(got) == {"Xuong GANI", "chua_xong", "trong"}  # hidden folders are not shown
    assert got["Xuong GANI"].id == "0/Xuong GANI" and got["Xuong GANI"].ready_to_render
    assert got["chua_xong"].has_plan and not got["chua_xong"].ready_to_render
    assert not got["trong"].has_plan
    assert str(root) not in json.dumps([p.model_dump() for p in got.values()])  # no paths leak


def test_a_root_that_is_itself_a_project_is_listed_as_dot(tmp_path: Path) -> None:
    solo = tmp_path / "solo"
    make_project(solo.parent, "solo")
    c = ApiContext(QueueDb(tmp_path / "q.db"), RULES, Settings(gani_dv_allowed_roots=str(solo)))
    assert [p.id for p in list_projects(c)] == ["0/."]


def test_projects_of_several_roots_get_their_own_index(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    make_project(a, "x")
    make_project(b, "y")
    roots = f"{a}{os.pathsep}{b}"
    c = ApiContext(QueueDb(tmp_path / "q.db"), RULES, Settings(gani_dv_allowed_roots=roots))
    assert sorted(p.id for p in list_projects(c)) == ["0/x", "1/y"]


def test_no_allow_list_means_no_projects_and_a_clear_refusal(ctx: ApiContext) -> None:
    bare = ApiContext(ctx.db, RULES, Settings())
    assert list_projects(bare) == []
    with pytest.raises(ApiError) as e:
        resolve_project(bare, "0/x")
    assert e.value.status == 503 and "GANI_DV_ALLOWED_ROOTS" in e.value.message


def test_resolve_project_refuses_bad_ids_and_unknown_projects(ctx: ApiContext, root: Path) -> None:
    make_project(root)
    assert resolve_project(ctx, "0/footage") == (root / "footage").resolve()
    for bad, status in [("0/..", 400), ("nonsense", 400), ("0/a/b", 400), ("0/none", 404),
                        ("5/footage", 404)]:  # fmt: skip
        with pytest.raises(ApiError) as e:
            resolve_project(ctx, bad)
        assert e.value.status == status and str(root) not in e.value.message


def test_a_link_pointing_outside_the_root_is_not_a_project(ctx: ApiContext, root: Path,
                                                           tmp_path: Path) -> None:  # fmt: skip
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "sneaky"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without the symlink privilege: a directory junction leads outside just the same
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                              capture_output=True, check=False)  # fmt: skip
        if made.returncode != 0:
            pytest.skip("cannot create links here")
    with pytest.raises(ApiError) as e:
        resolve_project(ctx, "0/sneaky")
    assert e.value.status == 404


# ─────────────── creating jobs ───────────────


def test_create_jobs_queues_the_drafts_and_returns_their_views(ctx: ApiContext,
                                                               root: Path) -> None:  # fmt: skip
    make_project(root, count=2)
    views = create_jobs(ctx, JobCreate(project_id="0/footage", final=True))
    assert [(v.draft, v.size, v.status, v.frontend_status) for v in views] == [
        ("A", "final", "queued", "queued"), ("B", "final", "queued", "queued")]  # fmt: skip
    assert views[0].project_id == "0/footage" and views[0].title == "footage — bản A (cuối)"
    assert views[0].options["style"] == "ke_chuyen_nghe"  # echoed from plan.json


def test_asking_twice_returns_the_same_job(ctx: ApiContext, root: Path) -> None:
    make_project(root)
    first = create_jobs(ctx, JobCreate(project_id="0/footage", draft="A"))
    again = create_jobs(ctx, JobCreate(project_id="0/footage", draft="A"))
    assert first[0].id == again[0].id and len(list_jobs(ctx)) == 1


def test_a_project_without_drafts_is_a_409_with_the_next_step(ctx: ApiContext,
                                                             root: Path) -> None:  # fmt: skip
    make_project(root, drafts=False)
    with pytest.raises(ApiError) as e:
        create_jobs(ctx, JobCreate(project_id="0/footage"))
    assert e.value.status == 409 and "draft" in e.value.message and list_jobs(ctx) == []


def test_a_draft_the_plan_does_not_have_is_a_400(ctx: ApiContext, root: Path) -> None:
    make_project(root, count=1)
    with pytest.raises(ApiError) as e:
        create_jobs(ctx, JobCreate(project_id="0/footage", draft="B"))
    assert e.value.status == 400


def test_an_unknown_project_is_a_404(ctx: ApiContext) -> None:
    with pytest.raises(ApiError) as e:
        create_jobs(ctx, JobCreate(project_id="0/ghost"))
    assert e.value.status == 404


# ─────────────── reading jobs ───────────────


def test_a_running_job_shows_progress_steps_and_an_eta(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    ctx.db.claim(time.time() - 30, 1)
    ctx.db.progress(jid, time.time(), "dung", 40.0)
    v = get_job(ctx, jid)
    assert (v.status, v.frontend_status, v.stage, v.progress_pct) == ("processing", "running",
                                                                     "dung", 40.0)  # fmt: skip
    assert v.eta_sec is not None and 0 <= v.eta_sec <= RULES["queue"]["expected_sec"][
        "render_preview"]  # fmt: skip
    steps = {s.id: s.status for s in v.steps}
    assert steps["ai_draft"] == "done" and steps["render_preview"] == "running"
    assert v.video_url is None and v.qa is None


def test_a_finished_job_has_media_links_and_the_real_qa_numbers(ctx: ApiContext,
                                                                root: Path) -> None:  # fmt: skip
    jid = queue_one(ctx, root)
    finish_job(ctx, jid)
    v = get_job(ctx, jid)
    assert v.frontend_status == "done" and v.progress_pct == 100.0
    assert (v.video_url, v.cover_url, v.srt_url) == (f"/media/{jid}/video", f"/media/{jid}/cover",
                                                      f"/media/{jid}/srt")  # fmt: skip
    assert v.qa is not None and v.qa.passed and v.qa.lufs == -13.9 and v.qa.true_peak_db == -2.0
    assert [c.code for c in v.qa.checks] == ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]
    assert {s.id: s.status for s in v.steps}["qc_verify"] == "done"


def test_a_qc_failed_job_is_qa_review(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    finish_job(ctx, jid, result="qc_failed")
    v = get_job(ctx, jid)
    assert v.frontend_status == "qa_review" and v.video_url is not None
    assert v.qa is not None and not v.qa.passed


def test_a_report_from_another_run_is_not_shown(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    out = finish_job(ctx, jid)
    old = time.time() - 7200
    os.utime(out / "qc_report.json", (old, old))  # written long before this job started
    assert get_job(ctx, jid).qa is None


def test_a_failed_job_has_no_media_and_an_error_step(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    ctx.db.claim(time.time(), 1)
    ctx.db.finish(jid, time.time(), "failed", None, 2, "ffmpeg lỗi", None)
    v = get_job(ctx, jid)
    assert v.frontend_status == "error" and v.error_msg == "ffmpeg lỗi" and v.video_url is None
    assert {s.id: s.status for s in v.steps}["render_preview"] == "error"


def test_the_list_filters_by_status_and_project(ctx: ApiContext, root: Path) -> None:
    a = queue_one(ctx, root)
    make_project(root, "other")
    b = create_jobs(ctx, JobCreate(project_id="0/other", draft="A"))[0].id
    ctx.db.claim(time.time(), 1)  # A of "footage" starts
    assert [v.id for v in list_jobs(ctx)] == [b, a]
    assert [v.id for v in list_jobs(ctx, status="queued")] == [b]
    assert [v.id for v in list_jobs(ctx, project_id="0/footage")] == [a]
    with pytest.raises(ApiError) as e:
        list_jobs(ctx, status="bogus")
    assert e.value.status == 400


def test_an_unknown_job_is_a_404(ctx: ApiContext) -> None:
    for call in (lambda: get_job(ctx, 99), lambda: job_events(ctx, 99, 0), lambda: cancel(ctx, 99),
                 lambda: retry(ctx, 99)):  # fmt: skip
        with pytest.raises(ApiError) as e:
            call()
        assert e.value.status == 404


def test_events_come_after_a_cursor_and_the_detail_holds_the_latest(
    ctx: ApiContext, root: Path
) -> None:
    jid = queue_one(ctx, root)
    for i in range(5):
        ctx.db.add_event(jid, 1_759_000_000.0 + i, "warn" if i == 4 else "info", f"dòng {i}")
    all_events = job_events(ctx, jid, 0)
    assert [e.text for e in all_events] == [f"dòng {i}" for i in range(5)]
    assert all_events[0].ts.endswith("Z") and all_events[4].level == "warn"
    assert [e.text for e in job_events(ctx, jid, all_events[2].id)] == ["dòng 3", "dòng 4"]
    assert [e.text for e in get_job(ctx, jid).events] == [f"dòng {i}" for i in range(5)]


# ─────────────── cancel / retry ───────────────


def test_cancel_a_waiting_job_and_retry_it(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    assert cancel(ctx, jid).status == "cancelled"
    assert retry(ctx, jid).status == "queued"


def test_cancelling_a_running_job_only_raises_the_flag(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    ctx.db.claim(time.time(), 1)
    assert cancel(ctx, jid).status == "processing"
    assert ctx.db.cancel_requested(jid)


def test_retry_of_a_running_or_passed_job_is_a_409(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    ctx.db.claim(time.time(), 1)
    with pytest.raises(ApiError) as e:
        retry(ctx, jid)
    assert e.value.status == 409


# ─────────────── preflight ───────────────


def test_preflight_uses_the_python_engine(ctx: ApiContext) -> None:
    good = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 15,
            "so_ban_nhap": 1, "nhac": "khong_nhac"}  # fmt: skip
    assert preflight(ctx, PreflightRequest(options=good))["valid"] is True
    bad = preflight(ctx, PreflightRequest(options={**good, "do_dai_giay": 7}))
    assert bad["valid"] is False and bad["errors"]


# ─────────────── media ───────────────


def test_media_resolves_to_the_files_of_the_job_only(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root, final=True)
    out = finish_job(ctx, jid)
    m = resolve_media(ctx, jid, "video")
    assert m.path == (out / "video_A_1080x1920.mp4").resolve()
    assert (m.content_type, m.size) == ("video/mp4", 1000)
    assert resolve_media(ctx, jid, "cover").content_type == "image/jpeg"
    assert resolve_media(ctx, jid, "srt").content_type.startswith("text/plain")


@pytest.mark.parametrize("kind", ["exe", "", "../video", "video/../../x", "VIDEO"])
def test_unknown_media_kinds_are_a_404(ctx: ApiContext, root: Path, kind: str) -> None:
    jid = queue_one(ctx, root)
    finish_job(ctx, jid)
    with pytest.raises(ApiError) as e:
        resolve_media(ctx, jid, kind)
    assert e.value.status == 404


def test_media_of_a_job_that_is_not_finished_is_a_404(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    with pytest.raises(ApiError) as e:
        resolve_media(ctx, jid, "video")
    assert e.value.status == 404


def test_a_deleted_video_is_a_404_and_no_link_is_offered(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    out = finish_job(ctx, jid)
    (out / "nhap_A_540p.mp4").unlink()
    assert get_job(ctx, jid).video_url is None
    with pytest.raises(ApiError) as e:
        resolve_media(ctx, jid, "video")
    assert e.value.status == 404


def test_media_stops_at_once_when_the_folder_leaves_the_allow_list(
    ctx: ApiContext, root: Path, tmp_path: Path
) -> None:
    jid = queue_one(ctx, root)
    finish_job(ctx, jid)
    other = tmp_path / "other-root"
    other.mkdir()
    narrowed = ApiContext(ctx.db, RULES, Settings(gani_dv_allowed_roots=str(other)))
    with pytest.raises(ApiError) as e:
        resolve_media(narrowed, jid, "video")
    assert e.value.status == 404 and get_job(narrowed, jid).video_url is None


def test_a_forged_row_cannot_point_media_elsewhere(ctx: ApiContext, root: Path,
                                                   tmp_path: Path) -> None:  # fmt: skip
    """The path is rebuilt from the job's folder + draft, never taken from output_path."""
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    make_project(root)
    forged = NewJob(kind="render", project_dir=str(root / "footage"), options_file="x",
                    draft="A", max_attempts=1)  # fmt: skip
    jid = ctx.db.enqueue(forged, time.time())
    ctx.db.claim(time.time(), 1)
    ctx.db.finish(jid, time.time(), "completed", "passed", 0, None, str(secret))
    with pytest.raises(ApiError) as e:
        resolve_media(ctx, jid, "video")  # the real render folder has no video
    assert e.value.status == 404


# ─────────────── no absolute paths leave the server (console lines, errors) ───────────────


def test_console_lines_and_errors_never_carry_absolute_paths(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    folder = str((root / "footage").resolve())
    slash = folder.replace("\\", "/")
    win_tail = "\\edit\\render\\A\\v.mp4"
    ctx.db.add_event(jid, time.time(), "info", f"Bản A: {folder}{win_tail} — QC: ĐẠT")
    ctx.db.add_event(jid, time.time(), "info", f"ffmpeg đọc {slash}/a.mp4 trong {root}")
    ctx.db.claim(time.time(), 1)
    err = f"Thư mục footage không còn: {folder}."
    ctx.db.finish(jid, time.time(), "failed", None, 2, err, None)
    views = [e.model_dump() for e in job_events(ctx, jid, 0)]
    views += [get_job(ctx, jid).model_dump(mode="json")]
    views += [v.model_dump(mode="json") for v in list_jobs(ctx)]
    # json.dumps doubles backslashes, so compare against the decoded text of every string
    text = json.dumps(views, ensure_ascii=False).replace("\\\\", "\\")
    for leak in (folder, slash, str(root), str(root).replace("\\", "/"), "allowed"):
        assert leak not in text, leak
    assert "QC: ĐẠT" in text and "footage" in text  # the useful part stays


# ─────────────── phase 6c-1: analysis jobs are not part of the render API yet ───────────────


def analysis_job(ctx: ApiContext, root: Path) -> int:
    make_project(root)
    folder = str((root / "footage").resolve())
    job = NewJob(kind="analyze", project_dir=folder, options_file="", draft="", max_attempts=2)
    return ctx.db.enqueue(job, time.time())


def test_analysis_jobs_are_ordinary_jobs_of_the_api_since_6c3(ctx: ApiContext, root: Path) -> None:
    jid = analysis_job(ctx, root)
    queue_one(ctx, root)
    assert sorted(v.kind for v in list_jobs(ctx)) == ["analyze", "render"]
    assert get_job(ctx, jid).kind == "analyze" and job_events(ctx, jid, 0) == []
    assert cancel(ctx, jid).status == "cancelled"
    assert retry(ctx, jid).status == "queued"
    assert health(ctx).jobs["queued"] == 2  # the counts are of every kind


def test_media_of_an_analysis_job_is_a_404(ctx: ApiContext, root: Path) -> None:
    jid = analysis_job(ctx, root)
    with pytest.raises(ApiError) as e:
        resolve_media(ctx, jid, "video")
    assert e.value.status == 404


# ─────────────── 6c-3: pipelines, approval, cost, every kind of job ───────────────

WIZARD: dict[str, Any] = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong",
                          "do_dai_giay": 15, "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip


def bare_project(root: Path, name: str = "footage") -> str:
    (root / name).mkdir(exist_ok=True)
    return f"0/{name}"


def start(ctx: ApiContext, root: Path, name: str = "footage", **kw: Any) -> Any:
    req = PipelineCreate(project_id=bare_project(root, name), options=WIZARD, **kw)
    return create_pipeline(ctx, req)


def estimate() -> Estimate:
    return Estimate(model="m", input_tokens=1678, input_only_vnd=85.58, worst_call_vnd=4165.58,
                    cap_vnd=20000.0, max_attempts=3)  # fmt: skip


def reach_approval(ctx: ApiContext, view: Any) -> int:
    """Run the analysis to completion and park the draft job at the approval gate."""
    analyze: int = view.jobs[0].id
    draft: int = view.jobs[1].id
    job = ctx.db.claim(time.time(), 1)
    assert job is not None and job.id == analyze
    ctx.db.finish(analyze, time.time(), "completed", "passed", 0, None, None)
    job = ctx.db.claim(time.time(), 1)
    assert job is not None and job.id == draft
    ctx.db.hold_for_approval(draft, time.time(), estimate(), 86_400.0)
    return draft


def test_a_pipeline_request_creates_the_whole_chain_and_returns_it(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    assert view.project_id == "0/footage" and view.project_name == "footage"
    assert view.status == "queued" and view.needs_approval is False
    assert [j.kind for j in view.jobs] == ["analyze", "draft", "render", "render"]
    assert [j.draft for j in view.jobs if j.kind == "render"] == ["A", "B"]
    assert {j.pipeline_id for j in view.jobs} == {view.id}
    assert [j.depends_on for j in view.jobs] == [None, view.jobs[0].id, view.jobs[1].id,
                                                 view.jobs[1].id]  # fmt: skip
    assert (root / "footage" / "edit" / "options.json").is_file()


def test_pipeline_options_are_judged_by_preflight_before_anything_is_written(
    ctx: ApiContext, root: Path
) -> None:
    req = PipelineCreate(project_id=bare_project(root), options={**WIZARD, "phong_cach": "sai"})
    with pytest.raises(ApiError) as caught:
        create_pipeline(ctx, req)
    assert caught.value.status == 400 and caught.value.code == "bad_request"
    assert ctx.db.list_jobs() == [] and not (root / "footage" / "edit").exists()


def test_a_pipeline_for_an_unknown_project_is_a_404_and_without_roots_a_503(
    ctx: ApiContext, root: Path, tmp_path: Path
) -> None:
    with pytest.raises(ApiError) as caught:
        create_pipeline(ctx, PipelineCreate(project_id="0/ghost", options=WIZARD))
    assert caught.value.status == 404
    bare = ApiContext(ctx.db, RULES, Settings())
    with pytest.raises(ApiError) as caught:
        create_pipeline(bare, PipelineCreate(project_id="0/footage", options=WIZARD))
    assert caught.value.status == 503


def test_a_second_pipeline_on_a_busy_project_is_a_conflict_that_names_the_first(
    ctx: ApiContext, root: Path
) -> None:
    first = start(ctx, root)
    with pytest.raises(ApiError) as caught:
        start(ctx, root)
    err = caught.value
    assert err.status == 409 and err.code == "pipeline_active"
    assert err.extra == {"pipeline_id": first.id, "job_id": first.jobs[0].id}
    assert len(ctx.db.list_jobs()) == 4


def test_a_pipeline_can_be_read_back_and_unknown_ones_are_404(ctx: ApiContext,
                                                              root: Path) -> None:  # fmt: skip
    view = start(ctx, root)
    again = get_pipeline(ctx, view.id)
    assert again.id == view.id and [j.id for j in again.jobs] == [j.id for j in view.jobs]
    with pytest.raises(ApiError) as caught:
        get_pipeline(ctx, 999)
    assert caught.value.status == 404


def test_a_pipeline_outside_the_allowed_roots_is_invisible(ctx: ApiContext, root: Path,
                                                            tmp_path: Path) -> None:  # fmt: skip
    view = start(ctx, root)
    other = ApiContext(ctx.db, RULES, Settings(gani_dv_allowed_roots=str(tmp_path / "elsewhere")))
    with pytest.raises(ApiError) as caught:
        get_pipeline(other, view.id)
    assert caught.value.status == 404


def test_a_draft_waiting_for_approval_carries_its_cost_and_asks_for_the_modal(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    draft_id = reach_approval(ctx, view)
    job = get_job(ctx, draft_id)
    assert (job.kind, job.status, job.frontend_status) == ("draft", "awaiting_approval",
                                                           "awaiting_approval")  # fmt: skip
    assert job.needs_approval is True and job.approval_expires_at is not None
    assert job.approval_expires_at.endswith("Z")
    assert job.cost is not None
    assert (job.cost.input_tokens, job.cost.worst_call_vnd, job.cost.cap_vnd) == (
        1678,
        4165.58,
        20000.0,
    )
    assert job.cost.approved_vnd is None and job.cost.spent_vnd is None
    now = get_pipeline(ctx, view.id)
    assert now.status == "awaiting_approval" and now.needs_approval is True


def test_jobs_that_cost_nothing_have_no_cost_block_and_need_no_approval(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    for j in view.jobs:
        assert j.cost is None and j.needs_approval is False and j.approval_expires_at is None


def test_approving_from_the_web_queues_the_paid_phase_and_shows_the_agreed_maximum(
    ctx: ApiContext, root: Path
) -> None:
    draft_id = reach_approval(ctx, start(ctx, root))
    job = approve(ctx, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert job.status == "queued" and job.needs_approval is False
    assert job.cost is not None and job.cost.approved_vnd == 6000.0


def test_an_approval_that_repeats_the_wrong_estimate_is_a_conflict_and_changes_nothing(
    ctx: ApiContext, root: Path
) -> None:
    draft_id = reach_approval(ctx, start(ctx, root))
    with pytest.raises(ApiError) as caught:
        approve(ctx, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=3000))
    assert caught.value.status == 409 and caught.value.code == "approval_refused"
    assert "4.166đ" in caught.value.message
    assert get_job(ctx, draft_id).status == "awaiting_approval"


def test_a_maximum_below_one_call_is_refused_at_once(ctx: ApiContext, root: Path) -> None:
    draft_id = reach_approval(ctx, start(ctx, root))
    with pytest.raises(ApiError) as caught:
        approve(ctx, draft_id, ApproveRequest(max_vnd=1000, estimate_vnd=4166))
    assert caught.value.status == 409 and caught.value.code == "approval_refused"


def test_the_daily_budget_can_refuse_an_approval(ctx: ApiContext, root: Path) -> None:
    tight = {**RULES, "queue": {**RULES["queue"], "daily_budget_vnd": 5000}}
    capped = ApiContext(ctx.db, tight, ctx.settings)
    draft_id = reach_approval(capped, start(capped, root))
    with pytest.raises(ApiError) as caught:
        approve(capped, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert caught.value.code == "approval_refused" and "hạn mức" in caught.value.message


def test_only_a_draft_job_can_be_approved_and_only_an_existing_one(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    with pytest.raises(ApiError) as caught:
        approve(ctx, view.jobs[0].id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert caught.value.status == 409 and caught.value.code == "conflict"
    with pytest.raises(ApiError) as caught:
        approve(ctx, 999, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert caught.value.status == 404


def test_a_draft_job_that_is_not_waiting_cannot_be_approved(ctx: ApiContext,
                                                             root: Path) -> None:  # fmt: skip
    view = start(ctx, root)  # the draft job still waits for the analysis, no estimate yet
    with pytest.raises(ApiError) as caught:
        approve(ctx, view.jobs[1].id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert caught.value.status == 409 and caught.value.code == "approval_refused"


def test_the_list_shows_every_kind_and_can_be_filtered_by_kind(ctx: ApiContext,
                                                               root: Path) -> None:  # fmt: skip
    start(ctx, root)
    assert sorted(j.kind for j in list_jobs(ctx)) == ["analyze", "draft", "render", "render"]
    assert [j.kind for j in list_jobs(ctx, kind="draft")] == ["draft"]
    assert [j.kind for j in list_jobs(ctx, kind="analyze")] == ["analyze"]
    assert len(list_jobs(ctx, kind="render")) == 2
    with pytest.raises(ApiError) as caught:
        list_jobs(ctx, kind="video")
    assert caught.value.status == 400


def test_analyze_and_draft_jobs_are_readable_with_titles_and_real_steps(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    analyze, draft = get_job(ctx, view.jobs[0].id), get_job(ctx, view.jobs[1].id)
    assert "phân tích" in analyze.title and "kịch bản" in draft.title
    assert (analyze.draft, draft.draft) == ("", "")
    assert len(analyze.steps) == 8 and analyze.steps[0].status == "queued"
    assert job_events(ctx, analyze.id, 0) == []


def test_a_running_analysis_shows_its_stage_on_the_matching_step(ctx: ApiContext,
                                                                  root: Path) -> None:  # fmt: skip
    view = start(ctx, root)
    job = ctx.db.claim(time.time(), 1)
    assert job is not None
    ctx.db.progress(job.id, time.time(), "transcribe", 20.0)
    steps = {s.id: s for s in get_job(ctx, job.id).steps}
    assert steps["nhan_dang"].status == "running" and steps["nhan_dang"].detail == "transcribe"
    assert view.jobs[0].id == job.id


def test_cancelling_a_waiting_draft_takes_its_renders_down_at_once(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    draft_id = reach_approval(ctx, view)
    cancel(ctx, draft_id)
    states = {j.kind: j.status for j in get_pipeline(ctx, view.id).jobs}
    assert states == {"analyze": "completed", "draft": "cancelled", "render": "cancelled"}
    assert get_pipeline(ctx, view.id).status == "error"


def test_retrying_a_failed_analysis_leaves_the_cancelled_jobs_for_the_user_to_retry(
    ctx: ApiContext, root: Path
) -> None:
    view = start(ctx, root)
    first = ctx.db.claim(time.time(), 1)
    assert first is not None
    ctx.db.finish(first.id, time.time(), "failed", None, 1, "lỗi", None)
    ctx.db.cascade_blocked(time.time())
    retry(ctx, first.id)
    now = {j.kind: j.status for j in get_pipeline(ctx, view.id).jobs}
    assert now == {"analyze": "queued", "draft": "cancelled", "render": "cancelled"}


def test_health_reports_the_key_and_the_budget_without_leaking_the_key(
    ctx: ApiContext, root: Path
) -> None:
    h = health(ctx)
    assert h.ai_key_configured is False
    assert (h.budget.limit_vnd, h.budget.spent_24h_vnd, h.budget.reserved_vnd) == (
        100_000.0,
        0.0,
        0.0,
    )  # the shipped daily limit
    keyed = ApiContext(ctx.db, {**RULES, "queue": {**RULES["queue"], "daily_budget_vnd": None}},
                       Settings(gani_dv_allowed_roots=str(root),
                                anthropic_api_key="test-key-not-real"))  # fmt: skip
    got = health(keyed)
    assert got.ai_key_configured is True and got.budget.limit_vnd is None  # limit switched off
    assert "test-key-not-real" not in got.model_dump_json()


def test_the_budget_in_health_counts_what_approved_jobs_may_still_spend(
    ctx: ApiContext, root: Path
) -> None:
    draft_id = reach_approval(ctx, start(ctx, root))
    approve(ctx, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert health(ctx).budget.reserved_vnd == 6000.0


# ─────────────── 6d: a folder held by another queue ───────────────


def held_elsewhere(ctx: ApiContext, root: Path, tmp_path: Path) -> None:
    folder = (root / "footage").resolve()
    other = QueueDb(tmp_path / "other" / "queue.db")
    acquire(folder, other.path, now=time.time() - 100)
    other.enqueue(NewJob(kind="analyze", project_dir=str(folder), options_file="", draft="",
                         pipeline_id=4), time.time() - 100)  # fmt: skip


def test_a_pipeline_on_a_folder_another_queue_holds_is_a_409_naming_that_queue_job(
    ctx: ApiContext, root: Path, tmp_path: Path
) -> None:
    bare_project(root)
    held_elsewhere(ctx, root, tmp_path)
    with pytest.raises(ApiError) as caught:
        create_pipeline(ctx, PipelineCreate(project_id="0/footage", options=WIZARD))
    err = caught.value
    assert (err.status, err.code) == (409, "project_locked")
    assert err.extra == {"pipeline_id": 4, "job_id": 1}
    assert "hàng đợi khác" in err.message and str(root) not in err.message
    assert ctx.db.list_jobs() == [] and not (root / "footage" / "edit" / "options.json").exists()


def test_a_plain_render_on_a_folder_another_queue_holds_is_also_a_409(
    ctx: ApiContext, root: Path, tmp_path: Path
) -> None:
    make_project(root)
    held_elsewhere(ctx, root, tmp_path)
    with pytest.raises(ApiError) as caught:
        create_jobs(ctx, JobCreate(project_id="0/footage", draft="A"))
    assert (caught.value.status, caught.value.code) == (409, "project_locked")


# ─────────────── 6f: T8 in the job's QA ───────────────


T8_NUMBERS = {"wer": 0.062, "median_drift_sec": 0.085, "voice_only_wer": None, "max_wer": 0.15,
              "max_median_drift_sec": 0.15}  # fmt: skip


def set_speech(root: Path) -> None:
    """Make the project's plan speech-driven (after queue_one, which writes a music plan)."""
    edit = root / "footage" / "edit"
    plan = json.loads((edit / "plan.json").read_text(encoding="utf-8"))
    plan["driver"] = "speech"
    (edit / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def add_t8(
    out: Path, numbers: dict[str, Any] | None, findings: list[dict[str, Any]] | None = None
) -> None:
    path = out / "qc_report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    if numbers is not None:
        report["t8"] = numbers
    report["findings"] = findings or []
    path.write_text(json.dumps(report), encoding="utf-8")


def final_speech_job(ctx: ApiContext, root: Path, **kw: Any) -> tuple[int, Path]:
    jid = queue_one(ctx, root, final=True, **kw)
    set_speech(root)
    return jid, finish_job(ctx, jid)


def test_a_final_speech_job_that_was_listened_to_shows_the_t8_row_and_says_ran(
    ctx: ApiContext, root: Path
) -> None:
    jid, out = final_speech_job(ctx, root)
    add_t8(out, T8_NUMBERS)
    qa = get_job(ctx, jid).qa
    assert qa is not None and qa.t8 == "ran"
    row = next(c for c in qa.checks if c.code == "T8")
    assert row.passed and "6,2%" in row.detail and "85ms" in row.detail


def test_a_failed_listen_is_a_failed_t8_row(ctx: ApiContext, root: Path) -> None:
    jid, out = final_speech_job(ctx, root)
    bad = {"code": "T8", "level": "error", "msg": "Whisper nghe lại thành phẩm sai 40% số từ."}
    add_t8(out, {**T8_NUMBERS, "wer": 0.4}, [bad])
    qa = get_job(ctx, jid).qa
    assert qa is not None and qa.t8 == "ran"
    assert next(c for c in qa.checks if c.code == "T8").passed is False


def test_a_preview_says_skipped_draft_and_has_no_t8_row(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)  # a 540p preview
    set_speech(root)
    finish_job(ctx, jid)
    qa = get_job(ctx, jid).qa
    assert qa is not None and qa.t8 == "skipped_draft" and "T8" not in [c.code for c in qa.checks]


def test_a_music_final_says_skipped_music(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root, final=True)  # the plan of make_project drives by music
    finish_job(ctx, jid)
    qa = get_job(ctx, jid).qa
    assert qa is not None and qa.t8 == "skipped_music" and "T8" not in [c.code for c in qa.checks]


def test_a_final_speech_job_rendered_without_asr_says_skipped_no_asr(
    ctx: ApiContext, root: Path
) -> None:
    jid, _ = final_speech_job(ctx, root, no_asr=True)
    qa = get_job(ctx, jid).qa
    assert qa is not None and qa.t8 == "skipped_no_asr" and "T8" not in [c.code for c in qa.checks]


def test_an_old_report_without_t8_numbers_is_unavailable_not_a_crash(
    ctx: ApiContext, root: Path
) -> None:
    jid, _ = final_speech_job(ctx, root)  # finish_job writes a report with no "t8" at all
    qa = get_job(ctx, jid).qa
    assert qa is not None and qa.t8 == "unavailable" and "T8" not in [c.code for c in qa.checks]


# ─────────────── 6h: footage that is only just enough, shown at the approval gate ───────────────

FIT_BLOCKS = [("hook", 4), ("ve_sinh", 10), ("quy_trinh", 16), ("bang_chung", 6), ("cta", 6)]


def write_fit_files(folder: Path, distinct: int, driver: str = "music") -> None:
    """The plan and the picked scenes of the real 30s run that failed on V4: five blocks that all
    draw on the same `distinct` scenes (one clip each)."""
    from gani_dung_video.domain.pick import BlockCandidates, Candidate, PickFile
    from gani_dung_video.domain.plan import Plan, PlanBlock

    pool = [Candidate(id=f"s{i}", clip=f"c{i}.mp4", start=10.0 * i, end=10.0 * i + 5.0,
                      peak=10.0 * i + 2.0, duration=5.0, fits_beats=[4, 6], labels=[],
                      aesthetic=5.0) for i in range(distinct)]  # fmt: skip
    total = sum(b for _, b in FIT_BLOCKS)
    plan = Plan(rules_version="2.0", options={"style": "ke_chuyen_nghe", "drafts": 1},
                driver=driver, music=None, beat_sec=0.7143, target_beats=total,
                planned_beats=total, beats_per_scene_allowed=[4, 6],
                blocks=[PlanBlock(role=r, beats=b) for r, b in FIT_BLOCKS], text_rules={},
                draft_strategy=["toi_uu"])  # fmt: skip
    pick = PickFile(rules_version="2.0", style="ke_chuyen_nghe", beat_sec=0.7143,
                    beats_per_scene_allowed=[4, 6], dishes=[],
                    blocks=[BlockCandidates(role=r, beats=b, required_tags=[], missing_tags=[],
                                            candidates=pool) for r, b in FIT_BLOCKS],
                    missing_shots=[], similar_pairs=[])  # fmt: skip
    edit = folder / "edit"
    edit.mkdir(parents=True, exist_ok=True)
    (edit / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    (edit / "candidates.json").write_text(pick.model_dump_json(), encoding="utf-8")


def waiting_draft(ctx: ApiContext, root: Path, distinct: int = 9, **kw: Any) -> tuple[int, Path]:
    view = start(ctx, root)
    folder = root / "footage"
    write_fit_files(folder, distinct, **kw)
    return reach_approval(ctx, view), folder


def test_a_draft_waiting_for_approval_says_how_tight_the_footage_is(
    ctx: ApiContext, root: Path
) -> None:
    draft_id, _ = waiting_draft(ctx, root, distinct=9)
    fit = get_job(ctx, draft_id).footage
    assert fit is not None
    assert (fit.scenes_available, fit.scenes_needed_min, fit.scenes_needed_max) == (9, 8, 9)
    assert fit.warn_below == 12 and fit.tight is True
    assert [b.role for b in fit.blocks] == [r for r, _ in FIT_BLOCKS]
    ve_sinh = fit.blocks[1]
    assert (ve_sinh.candidates, ve_sinh.beats_needed, ve_sinh.max_fill) == (9, 10, 54)
    assert (ve_sinh.scenes_min, ve_sinh.scenes_max, ve_sinh.tight) == (2, 2, False)


def test_plenty_of_scenes_is_reported_as_not_tight(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = waiting_draft(ctx, root, distinct=20)
    fit = get_job(ctx, draft_id).footage
    assert fit is not None and fit.scenes_available == 20 and fit.tight is False


def test_the_footage_block_reaches_the_json_the_ui_reads(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = waiting_draft(ctx, root)
    body = get_job(ctx, draft_id).model_dump(mode="json")
    assert set(body["footage"]) == {"scenes_available", "scenes_needed_min", "scenes_needed_max",
                                    "warn_below", "tight", "blocks"}  # fmt: skip
    assert set(body["footage"]["blocks"][0]) == {"role", "candidates", "beats_needed", "max_fill",
                                                 "scenes_min", "scenes_max", "tight"}  # fmt: skip
    assert json.dumps(body)  # plain JSON types only


def test_only_a_draft_at_the_gate_has_footage_numbers(ctx: ApiContext, root: Path) -> None:
    view = start(ctx, root)
    write_fit_files(root / "footage", 9)
    for j in view.jobs:  # nothing has run yet
        assert get_job(ctx, j.id).footage is None
    draft_id = reach_approval(ctx, view)
    assert get_job(ctx, draft_id).footage is not None
    approve(ctx, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert get_job(ctx, draft_id).footage is None  # approved: no longer a decision to help with
    assert all(get_job(ctx, j.id).footage is None for j in view.jobs if j.kind != "draft")


@pytest.mark.parametrize("damage", ["no_candidates", "no_plan", "bad_json", "wrong_shape"])
def test_unreadable_files_give_null_not_an_error(ctx: ApiContext, root: Path,
                                                 damage: str) -> None:  # fmt: skip
    draft_id, folder = waiting_draft(ctx, root)
    edit = folder / "edit"
    if damage == "no_candidates":
        (edit / "candidates.json").unlink()
    elif damage == "no_plan":
        (edit / "plan.json").unlink()
    elif damage == "bad_json":
        (edit / "candidates.json").write_text("{not json", encoding="utf-8")
    else:
        (edit / "candidates.json").write_text('{"blocks": 3}', encoding="utf-8")
    assert get_job(ctx, draft_id).footage is None


def test_files_from_before_this_run_are_not_shown(ctx: ApiContext, root: Path) -> None:
    """The plan and candidates are shared by the project: a leftover from an older run says
    nothing about the run that is waiting now (same rule as the QC report)."""
    draft_id, folder = waiting_draft(ctx, root)
    started = ctx.db.get(draft_id)
    assert started is not None and started.started_at is not None
    old = started.started_at - 3600
    for name in ("plan.json", "candidates.json"):
        os.utime(folder / "edit" / name, (old, old))
    assert get_job(ctx, draft_id).footage is None


def test_speech_mode_has_no_footage_numbers(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = waiting_draft(ctx, root, driver="speech")
    assert get_job(ctx, draft_id).footage is None


def test_the_list_carries_the_same_block_as_the_detail(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = waiting_draft(ctx, root)
    listed = next(j for j in list_jobs(ctx) if j.id == draft_id)
    assert listed.footage is not None and listed.footage.tight is True


# ─────────────── 6i: the V1–V9 / S1–S6 record, shown for a finished draft job ───────────────


def report_file(rounds: list[Any], mode: str = "music", final: str = "failed") -> str:
    return VerifyReportFile(mode=mode, final_status=final, rounds=rounds).model_dump_json()


def one_round(n: int, findings: list[tuple[str, str, str]] | None = None, parsed: bool = True,
              cost: float = 900.0) -> Any:  # fmt: skip
    fs = [Finding(code=c, level=lv, draft="A", msg=m) for c, lv, m in (findings or [])]
    return RoundRecord(round=n, drafts=["A", "B"], parsed=parsed,
                       passed=not any(f.level == "ai" for f in fs), cost_vnd=cost,
                       input_tokens=4000, output_tokens=2000, stop_reason="end_turn",
                       findings=fs)  # fmt: skip


def finish_paid_draft(ctx: ApiContext, root: Path, text: str | None, result: str = "passed",
                      status: str = "completed") -> tuple[int, Path]:  # fmt: skip
    """Drive a draft job through approval and its paid phase; `text` is what the run wrote to
    edit/verify_report.json DURING the run (None: it wrote nothing)."""
    view = start(ctx, root)
    draft_id = reach_approval(ctx, view)
    approve(ctx, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    job = ctx.db.claim(time.time(), 1)
    assert job is not None and job.id == draft_id
    edit = root / "footage" / "edit"
    edit.mkdir(parents=True, exist_ok=True)
    if text is not None:
        (edit / "verify_report.json").write_text(text, encoding="utf-8")
    ctx.db.finish(
        draft_id,
        time.time(),
        status,
        result if status == "completed" else None,
        0 if status == "completed" else 1,
        None if status == "completed" else "lỗi",
        None,
    )
    return draft_id, edit / "verify_report.json"


V4_MSG = "Cảnh 'VID_150353_s0_2' dùng 2 lần."


def test_a_failed_draft_keeps_its_three_rounds_and_says_which_check_failed(
    ctx: ApiContext, root: Path
) -> None:
    rounds = [one_round(n, [("V4", "ai", V4_MSG)]) for n in (1, 2, 3)]
    draft_id, _ = finish_paid_draft(ctx, root, report_file(rounds), status="failed")
    v = get_job(ctx, draft_id).verify
    assert v is not None
    assert (v.mode, v.final_status, v.rounds_used, v.max_rounds) == ("music", "failed", 3, 3)
    by_code = {c.code: c for c in v.checks}
    assert list(by_code) == ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "SNAP"]
    assert by_code["V4"].status == "fail" and V4_MSG in by_code["V4"].detail
    assert by_code["V2"].status == "pass" and by_code["V2"].detail == ""
    assert [r.round for r in v.rounds] == [1, 2, 3] and all(not r.passed for r in v.rounds)
    assert v.rounds[0].checks[3].status == "fail" and v.rounds[0].cost_vnd == 900.0


def test_a_passed_draft_shows_every_check_as_passed(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = finish_paid_draft(ctx, root, report_file([one_round(1)], final="passed"))
    v = get_job(ctx, draft_id).verify
    assert v is not None and v.final_status == "passed" and v.rounds_used == 1
    assert {c.status for c in v.checks} == {"pass"}


def test_fixed_and_warned_checks_are_told_apart(ctx: ApiContext, root: Path) -> None:
    rnd = one_round(1, [("V3", "auto", "5 phách → 4."), ("V6", "warn", "Khối thiếu cảnh nhãn.")])
    draft_id, _ = finish_paid_draft(ctx, root, report_file([rnd], final="passed"))
    v = get_job(ctx, draft_id).verify
    assert v is not None
    assert {c.code: c.status for c in v.checks}["V3"] == "fixed"
    assert {c.code: c.status for c in v.checks}["V6"] == "warn"


def test_the_labels_come_from_the_rules_not_from_the_ui(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = finish_paid_draft(ctx, root, report_file([one_round(1)], final="passed"))
    v = get_job(ctx, draft_id).verify
    assert v is not None
    assert all(c.label == RULES["verify_labels"][c.code] and c.label for c in v.checks)


def test_speech_mode_lists_s1_to_s6(ctx: ApiContext, root: Path) -> None:
    rnd = one_round(1, [("S6", "ai", "Câu mở đầu trùng bản A.")])
    draft_id, _ = finish_paid_draft(ctx, root, report_file([rnd], mode="speech"), status="failed")
    v = get_job(ctx, draft_id).verify
    assert v is not None and v.mode == "speech"
    assert [c.code for c in v.checks] == ["S1", "S2", "S3", "S4", "S5", "S6", "SNAP"]
    assert {c.code: c.status for c in v.checks}["S6"] == "fail"


def test_an_unreadable_answer_marks_the_other_checks_skipped(ctx: ApiContext, root: Path) -> None:
    rnd = one_round(1, [("V1", "ai", "JSON không đúng cấu trúc.")], parsed=False)
    draft_id, _ = finish_paid_draft(ctx, root, report_file([rnd]), status="failed")
    v = get_job(ctx, draft_id).verify
    assert v is not None
    st = {c.code: c.status for c in v.checks}
    assert st["V1"] == "fail" and {st[c] for c in st if c != "V1"} == {"skipped"}


@pytest.mark.parametrize("final", ["cost_cap", "api_error"])
def test_a_stop_before_the_end_is_reported_as_such(ctx: ApiContext, root: Path,
                                                   final: str) -> None:  # fmt: skip
    draft_id, _ = finish_paid_draft(ctx, root, report_file([one_round(1)], final=final),
                                    status="failed")  # fmt: skip
    v = get_job(ctx, draft_id).verify
    assert v is not None and v.final_status == final


def test_a_run_cut_off_midway_shows_as_interrupted(ctx: ApiContext, root: Path) -> None:
    """The daemon stopped during the paid phase: the file still says "running"."""
    draft_id, _ = finish_paid_draft(ctx, root, report_file([one_round(1)], final="running"),
                                    status="failed")  # fmt: skip
    v = get_job(ctx, draft_id).verify
    assert v is not None and v.final_status == "interrupted" and v.rounds_used == 1


def test_the_verify_block_reaches_the_json_and_the_list(ctx: ApiContext, root: Path) -> None:
    draft_id, _ = finish_paid_draft(ctx, root, report_file([one_round(1)], final="passed"))
    body = get_job(ctx, draft_id).model_dump(mode="json")["verify"]
    assert set(body) == {"mode", "final_status", "rounds_used", "max_rounds", "checks", "rounds"}
    assert set(body["checks"][0]) == {"code", "label", "status", "detail"}
    assert set(body["rounds"][0]) == {"round", "drafts", "passed", "parsed", "cost_vnd", "checks"}
    assert json.dumps(body)
    listed = next(j for j in list_jobs(ctx) if j.id == draft_id)
    assert listed.verify is not None and listed.verify.final_status == "passed"


def test_absolute_paths_in_a_message_are_scrubbed(ctx: ApiContext, root: Path) -> None:
    leaky = one_round(1, [("V4", "ai", f"mở {root / 'footage' / 'edit' / 'x.json'} thất bại")])
    draft_id, _ = finish_paid_draft(ctx, root, report_file([leaky]), status="failed")
    v = get_job(ctx, draft_id).verify
    assert v is not None and str(root) not in json.dumps(v.model_dump(mode="json"))


def test_only_a_finished_draft_has_a_verify_block(ctx: ApiContext, root: Path) -> None:
    view = start(ctx, root)
    folder = root / "footage" / "edit"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "verify_report.json").write_text(report_file([one_round(1)]), encoding="utf-8")
    assert all(get_job(ctx, j.id).verify is None for j in view.jobs)  # nothing has run
    draft_id = reach_approval(ctx, view)
    assert get_job(ctx, draft_id).verify is None  # waiting for approval
    assert all(get_job(ctx, j.id).verify is None for j in view.jobs if j.kind != "draft")


@pytest.mark.parametrize("damage", ["missing", "bad_json", "wrong_shape", "unknown_mode"])
def test_an_unusable_report_gives_null_not_an_error(ctx: ApiContext, root: Path,
                                                    damage: str) -> None:  # fmt: skip
    text = {
        "missing": None,
        "bad_json": "{not json",
        "wrong_shape": '{"rounds": 3}',
        "unknown_mode": '{"mode": "video", "final_status": "passed", "rounds": []}',
    }[damage]
    draft_id, _ = finish_paid_draft(ctx, root, text, status="failed")
    assert get_job(ctx, draft_id).verify is None


def test_a_report_from_an_older_run_is_not_shown(ctx: ApiContext, root: Path) -> None:
    """The report belongs to the project; one left by an earlier run says nothing about this
    job, which failed before it could write its own (same rule as qc_report.json)."""
    draft_id, path = finish_paid_draft(ctx, root, report_file([one_round(1)]), status="failed")
    job = ctx.db.get(draft_id)
    assert job is not None and job.started_at is not None
    os.utime(path, (job.started_at - 3600, job.started_at - 3600))
    assert get_job(ctx, draft_id).verify is None


def test_a_chapter_plan_report_shows_the_v10_row_before_snap(ctx: ApiContext, root: Path) -> None:
    rep = VerifyReportFile(mode="music", final_status="passed", rounds=[one_round(1)],
                           extra_codes=["V10"])  # fmt: skip
    draft_id, _ = finish_paid_draft(ctx, root, rep.model_dump_json())
    v = get_job(ctx, draft_id).verify
    assert v is not None
    codes = [c.code for c in v.checks]
    assert codes[-3:] == ["V9", "V10", "SNAP"]
    row = next(c for c in v.checks if c.code == "V10")
    assert row.label == RULES["verify_labels"]["V10"] and row.status == "pass"


def test_the_eta_of_a_three_minute_render_is_not_the_fixed_guess(ctx: ApiContext,
                                                                 root: Path) -> None:  # fmt: skip
    jid = queue_one(ctx, root)
    (root / "footage" / "edit").mkdir(parents=True, exist_ok=True)
    (root / "footage" / "edit" / "plan.json").write_text(
        json.dumps({"options": {"duration": 180}}), encoding="utf-8"
    )
    ctx.db.claim(time.time() - 10, 1)
    v = get_job(ctx, jid)
    assert v.eta_sec is not None and v.eta_sec > RULES["queue"]["expected_sec"]["render_preview"]


# ─────────────── 3-minute videos in the API (phase 7g) ───────────────


def rules_with_long_video() -> Any:
    import copy

    r = copy.deepcopy(dict(RULES))
    r["durations"]["180"]["enabled"] = True
    return r


def write_chapter_project(root: Path, titles: bool = True) -> Path:
    """A project whose plan has chapters (as 7a builds them) and, optionally, drafts with titles."""
    from gani_dung_video.application.draft_service import DraftsFile
    from gani_dung_video.domain.drafts import ChapterTitle, SnappedDraft
    from gani_dung_video.domain.plan import MusicInfo

    folder = make_project(root, "chapters")
    opts = VideoOptions(phong_cach="ke_chuyen_nghe", muc_dich="gioi_thieu_xuong", do_dai_giay=180,
                        so_ban_nhap=2, nhac="thu_vien", nhac_file="t.mp3")  # fmt: skip
    music = MusicInfo(track="t.mp3", bpm=84.0, first_beat_sec=0.5, duration_sec=200.0,
                      source="library")  # fmt: skip
    plan = compile_plan(opts, rules_with_long_video(), music=music, available_tags=set())
    (folder / "edit" / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    if titles:
        names = ["Khởi đầu sạch sẽ", "Từng mẻ giò thủ công", "Minh chứng chất lượng"]
        d = SnappedDraft(
            draft="A",
            label="A",
            segments=[],
            missing_shots=[],
            warnings=[],
            chapter_titles=[
                ChapterTitle(chapter=c.id, title=t)
                for c, t in zip(plan.chapters, names, strict=True)
            ],
        )
        out = DraftsFile(model="m", attempts=1, input_tokens=0, output_tokens=0, cost_vnd=0.0,
                         cap_vnd=30000.0, drafts=[d], notes=[])  # fmt: skip
        (folder / "edit" / "drafts.json").write_text(out.model_dump_json(), encoding="utf-8")
    return folder


def test_health_says_whether_three_minute_videos_are_open(ctx: ApiContext) -> None:
    assert health(ctx).features.long_video is False  # shipped: closed until the 7e acceptance
    opened = ApiContext(ctx.db, rules_with_long_video(), ctx.settings)
    assert health(opened).features.long_video is True


def test_a_job_of_a_chapter_plan_shows_the_structure_with_titles(ctx: ApiContext,
                                                                 root: Path) -> None:  # fmt: skip
    write_chapter_project(root)
    jid = create_jobs(ctx, JobCreate(project_id="0/chapters", draft="A"))[0].id
    s = get_job(ctx, jid).structure
    assert s is not None
    assert [(i.kind, i.id) for i in s.items] == [
        ("opening", "hook"), ("chapter", "ve_sinh"), ("chapter", "quy_trinh"),
        ("chapter", "bang_chung"), ("closing", "cta")]  # fmt: skip
    assert s.items[0].start_sec == 0.0 and s.items[-1].end_sec == pytest.approx(s.duration_sec)
    for a, b in zip(s.items, s.items[1:], strict=False):
        assert a.end_sec == pytest.approx(b.start_sec)  # no gap, no overlap
    assert [i.title for i in s.items if i.kind == "chapter"] == [
        "Khởi đầu sạch sẽ", "Từng mẻ giò thủ công", "Minh chứng chất lượng"]  # fmt: skip
    assert s.items[0].title is None and s.items[-1].title is None
    assert 175 <= s.duration_sec <= 185


def test_the_structure_of_a_draft_without_titles_has_none(ctx: ApiContext, root: Path) -> None:
    write_chapter_project(root, titles=False)
    jid = create_jobs(ctx, JobCreate(project_id="0/chapters", draft="A"))[0].id
    s = get_job(ctx, jid).structure
    assert s is not None and all(i.title is None for i in s.items)


def test_titles_come_from_the_jobs_own_draft_letter(ctx: ApiContext, root: Path) -> None:
    write_chapter_project(root)
    jid = create_jobs(ctx, JobCreate(project_id="0/chapters", draft="B"))[0].id
    s = get_job(ctx, jid).structure
    assert s is not None and all(i.title is None for i in s.items)  # drafts.json only has A


def test_short_videos_and_analyze_jobs_have_no_structure(ctx: ApiContext, root: Path) -> None:
    jid = queue_one(ctx, root)
    assert get_job(ctx, jid).structure is None
    view = start(ctx, root, "another")
    assert all(get_job(ctx, j.id).structure is None for j in view.jobs if j.kind == "analyze")


def test_a_broken_plan_gives_no_structure_not_an_error(ctx: ApiContext, root: Path) -> None:
    folder = write_chapter_project(root)
    jid = create_jobs(ctx, JobCreate(project_id="0/chapters", draft="A"))[0].id
    (folder / "edit" / "plan.json").write_text("{not json", encoding="utf-8")  # damaged later
    assert get_job(ctx, jid).structure is None


def test_the_structure_reaches_the_json(ctx: ApiContext, root: Path) -> None:
    write_chapter_project(root)
    jid = create_jobs(ctx, JobCreate(project_id="0/chapters", draft="A"))[0].id
    body = get_job(ctx, jid).model_dump(mode="json")["structure"]
    assert set(body) == {"duration_sec", "items"}
    assert set(body["items"][1]) == {"kind", "id", "start_sec", "end_sec", "title"}
    assert json.dumps(body)
