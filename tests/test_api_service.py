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
from gani_dung_video.infrastructure.config_files import load_rules
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
    analyze, draft = view.jobs[0].id, view.jobs[1].id
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
    assert (h.budget.limit_vnd, h.budget.spent_24h_vnd, h.budget.reserved_vnd) == (None, 0.0, 0.0)
    keyed = ApiContext(ctx.db, {**RULES, "queue": {**RULES["queue"], "daily_budget_vnd": 100_000}},
                       Settings(gani_dv_allowed_roots=str(root),
                                anthropic_api_key="test-key-not-real"))  # fmt: skip
    got = health(keyed)
    assert got.ai_key_configured is True and got.budget.limit_vnd == 100_000.0
    assert "test-key-not-real" not in got.model_dump_json()


def test_the_budget_in_health_counts_what_approved_jobs_may_still_spend(
    ctx: ApiContext, root: Path
) -> None:
    draft_id = reach_approval(ctx, start(ctx, root))
    approve(ctx, draft_id, ApproveRequest(max_vnd=6000, estimate_vnd=4166))
    assert health(ctx).budget.reserved_vnd == 6000.0
