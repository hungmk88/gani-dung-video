"""Unit tests: domain/api_models.py — request/response models and pure derivations (6b)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gani_dung_video.application.render_service import _video_name
from gani_dung_video.domain.api_models import (
    ApproveRequest,
    Artifacts,
    CostView,
    HealthView,
    JobCreate,
    JobView,
    PipelineCreate,
    PreflightRequest,
    RunInfo,
    derive_steps,
    eta_sec,
    frontend_status,
    iso,
    make_project_id,
    media_file_name,
    parse_project_id,
    pipeline_status,
    qa_checks,
)
from gani_dung_video.domain.qc import Finding

# ─────────────── project ids ───────────────


def test_a_project_id_round_trips() -> None:
    pid = make_project_id(0, "Xuong GANI")
    assert pid == "0/Xuong GANI" and parse_project_id(pid) == (0, "Xuong GANI")
    assert parse_project_id("3/.") == (3, ".")  # the root itself


@pytest.mark.parametrize(
    "bad",
    ["", "0", "0/", "/x", "x/y", "-1/a", "0/..", "0/...", "0/a/b", "0\\a", "0/a\\b", "0/C:",
     "0/a*b", "0/a\x00b", "0/" + "x" * 200, "123/a", "0/ ", "0/a\nb"],
)  # fmt: skip
def test_unsafe_or_malformed_project_ids_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="mã dự án"):
        parse_project_id(bad)


def test_make_project_id_refuses_what_parse_would() -> None:
    with pytest.raises(ValueError):
        make_project_id(0, "a/b")


# ─────────────── requests ───────────────


def test_job_create_defaults_and_normalisation() -> None:
    j = JobCreate(project_id="0/x")
    assert (j.draft, j.final, j.no_asr) == ("all", False, False)
    assert JobCreate(project_id="0/x", draft="b").draft == "B"
    assert JobCreate(project_id="0/x", draft="ALL").draft == "all"


@pytest.mark.parametrize("body", [
    {"project_id": "0/x", "draft": "D"},
    {"project_id": "0/../x"},
    {"project_id": "0/x", "final": "yes please"},
    {"project_id": "0/x", "extra": 1},
    {"draft": "A"},
    {"project_id": "0/x", "no_asr": None},
])  # fmt: skip
def test_bad_job_requests_are_refused(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobCreate.model_validate(body)


def test_preflight_request_takes_an_options_object_only() -> None:
    assert PreflightRequest(options={"do_dai_giay": 15}).options["do_dai_giay"] == 15
    with pytest.raises(ValidationError):
        PreflightRequest.model_validate({"options": "not an object"})
    with pytest.raises(ValidationError):
        PreflightRequest.model_validate({"options": {}, "x": 1})


# ─────────────── status mapping ───────────────


@pytest.mark.parametrize(
    "status,result,expected",
    [("queued", None, "queued"), ("processing", None, "running"), ("completed", "passed", "done"),
     ("completed", "qc_failed", "qa_review"), ("failed", None, "error"),
     ("cancelled", None, "error"), ("awaiting_approval", None, "awaiting_approval")],
)  # fmt: skip
def test_frontend_status_mapping(status: str, result: str | None, expected: str) -> None:
    assert frontend_status(status, result) == expected


def test_a_completed_job_without_a_result_is_not_called_done() -> None:
    assert frontend_status("completed", None) == "qa_review"


def test_iso_time_is_utc_with_a_z_suffix() -> None:
    assert iso(0.0) == "1970-01-01T00:00:00Z"
    assert iso(1_759_000_000.5).endswith("Z")


def test_eta_counts_down_only_while_processing() -> None:
    assert eta_sec("processing", 100.0, 300.0, 160.0) == 240
    assert eta_sec("processing", 100.0, 300.0, 1000.0) == 0  # never negative
    assert eta_sec("queued", None, 300.0, 160.0) is None
    assert eta_sec("completed", 100.0, 300.0, 160.0) is None
    assert eta_sec("processing", None, 300.0, 160.0) is None


# ─────────────── media names ───────────────


def test_media_names_match_the_render_service() -> None:
    for letter in "ABC":
        for final in (False, True):
            assert media_file_name("video", letter, final) == _video_name(letter, not final)
    assert media_file_name("cover", "A", False) == "cover.jpg"
    assert media_file_name("srt", "B", True) == "phude.srt"


@pytest.mark.parametrize("kind,letter", [("exe", "A"), ("video", "D"), ("video", "../A"),
                                         ("video", "AA"), ("", "A")])  # fmt: skip
def test_unknown_media_kinds_or_drafts_are_refused(kind: str, letter: str) -> None:
    with pytest.raises(ValueError):
        media_file_name(kind, letter, False)


# ─────────────── pipeline steps (derived from real files) ───────────────

ALL = Artifacts(inventory=True, scenes=True, speech=True, plan=True, pick=True, drafts=True,
                preview_video=False)  # fmt: skip


def steps(art: Artifacts = ALL, **kw: object) -> dict[str, str]:
    base: dict[str, object] = {"size": "preview", "status": "queued", "result": None,
                               "stage": None, "error_msg": None}  # fmt: skip
    got = derive_steps(art, RunInfo(**{**base, **kw}))  # type: ignore[arg-type]
    return {s.id: s.status for s in got}


def test_there_are_always_the_eight_frontend_steps_in_order() -> None:
    got = derive_steps(ALL, RunInfo(size="preview", status="queued", result=None, stage=None,
                                    error_msg=None))  # fmt: skip
    assert [s.id for s in got] == ["inventory", "nhan_dang", "plan_compile", "pick", "ai_draft",
                                   "render_preview", "qc_verify", "final_export"]  # fmt: skip
    assert all(s.label for s in got)


def test_earlier_steps_follow_the_files_that_exist() -> None:
    art = Artifacts(inventory=True, scenes=True, speech=False, plan=False, pick=False,
                    drafts=False, preview_video=False)  # fmt: skip
    got = steps(art)
    assert got["inventory"] == "done" and got["nhan_dang"] == "queued"  # speech.json missing
    assert got["plan_compile"] == "queued" and got["ai_draft"] == "queued"


def test_recognition_needs_both_scenes_and_speech() -> None:
    both = steps(Artifacts(inventory=True, scenes=True, speech=True, plan=False, pick=False,
                           drafts=False, preview_video=False))  # fmt: skip
    only_scenes = steps(Artifacts(inventory=True, scenes=True, speech=False, plan=False,
                                  pick=False, drafts=False, preview_video=False))  # fmt: skip
    assert both["nhan_dang"] == "done" and only_scenes["nhan_dang"] == "queued"


def test_a_waiting_preview_job_has_everything_after_the_drafts_waiting() -> None:
    got = steps(status="queued")
    assert [got[k] for k in ("render_preview", "qc_verify", "final_export")] == ["queued"] * 3


def test_a_running_preview_job_is_on_the_render_step() -> None:
    got = steps(status="processing", stage="dung")
    assert got["render_preview"] == "running" and got["qc_verify"] == "queued"
    assert got["ai_draft"] == "done"


def test_a_passed_preview_job_finishes_render_and_qc_but_not_the_final_export() -> None:
    got = steps(status="completed", result="passed")
    assert (got["render_preview"], got["qc_verify"], got["final_export"]) == ("done", "done",
                                                                            "queued")  # fmt: skip


def test_a_qc_failed_job_errors_on_the_qc_step_only() -> None:
    got = steps(status="completed", result="qc_failed")
    assert got["render_preview"] == "done" and got["qc_verify"] == "error"


def test_a_failed_or_cancelled_job_errors_on_the_render_step() -> None:
    for status in ("failed", "cancelled"):
        got = steps(status=status, error_msg="lỗi")
        assert got["render_preview"] == "error" and got["qc_verify"] == "queued"


def test_a_final_job_drives_the_export_step_and_a_preview_file_marks_the_preview() -> None:
    art = ALL.model_copy(update={"preview_video": True})
    got = steps(art, size="final", status="processing")
    assert got["render_preview"] == "done" and got["final_export"] == "running"
    no_preview = steps(ALL, size="final", status="completed", result="passed")
    assert no_preview["render_preview"] == "queued" and no_preview["final_export"] == "done"
    assert no_preview["qc_verify"] == "done"


def test_step_details_carry_the_stage_and_the_error() -> None:
    got = derive_steps(ALL, RunInfo(size="preview", status="failed", result=None, stage="dung",
                                    error_msg="ffmpeg lỗi"))  # fmt: skip
    render = next(s for s in got if s.id == "render_preview")
    assert render.detail is not None and "ffmpeg lỗi" in render.detail


# ─────────────── QA checks ───────────────


def test_a_clean_report_passes_every_technical_check_with_the_real_numbers() -> None:
    got = qa_checks([], rounds=1, lufs=-13.9, true_peak_db=-2.0)
    assert [c.code for c in got] == ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]
    assert all(c.passed for c in got)
    t3 = next(c for c in got if c.code == "T3")
    assert "-13.9" in t3.detail and "-2.0" in t3.detail


def test_errors_fail_their_check_and_warnings_do_not() -> None:
    found = [Finding(code="T4", level="error", msg="Khung hình đen ở 1s"),
             Finding(code="T5", level="warn", msg="Khung hình đứng 2s")]  # fmt: skip
    got = {c.code: c for c in qa_checks(found, rounds=1, lufs=-14.0, true_peak_db=-2.0)}
    assert not got["T4"].passed and "đen" in got["T4"].detail
    assert got["T5"].passed and "Cảnh báo" in got["T5"].detail


def test_a_report_that_never_rendered_has_no_checks() -> None:
    assert qa_checks([Finding(code="T7", level="error", msg="chữ dài")], rounds=0, lufs=None,
                     true_peak_db=None) == []  # fmt: skip


def test_whisper_findings_appear_as_their_own_check() -> None:
    found = [Finding(code="T8", level="error", msg="sai 30% số từ")]
    got = qa_checks(found, rounds=1, lufs=None, true_peak_db=None)
    t8 = next(c for c in got if c.code == "T8")
    assert not t8.passed and "30%" in t8.detail
    assert "T8" not in [c.code for c in qa_checks([], rounds=1, lufs=None, true_peak_db=None)]


def test_missing_numbers_do_not_break_the_loudness_check() -> None:
    t3 = next(c for c in qa_checks([], rounds=1, lufs=None, true_peak_db=None) if c.code == "T3")
    assert t3.passed and t3.detail


def test_a_finished_step_carries_no_stale_stage_text() -> None:
    got = derive_steps(ALL, RunInfo(size="preview", status="completed", result="passed",
                                    stage="khoi_dong", error_msg=None))  # fmt: skip
    assert next(s for s in got if s.id == "render_preview").detail is None
    running = derive_steps(ALL, RunInfo(size="preview", status="processing", result=None,
                                        stage="dung", error_msg=None))  # fmt: skip
    assert next(s for s in running if s.id == "render_preview").detail == "dung"


# ─────────────── 6c-3: pipelines, approval, cost ───────────────

OPTS = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 15,
        "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip


def test_pipeline_create_defaults_and_normalisation() -> None:
    p = PipelineCreate(project_id="0/x", options=OPTS)
    assert (p.draft, p.final, p.no_asr, p.force_analyze) == ("all", False, False, False)
    assert PipelineCreate(project_id="0/x", options=OPTS, draft="c").draft == "C"


@pytest.mark.parametrize("body", [
    {"project_id": "0/x"},  # no options
    {"options": OPTS},  # no project
    {"project_id": "0/../x", "options": OPTS},
    {"project_id": "0/x", "options": "phong_cach: x"},
    {"project_id": "0/x", "options": [OPTS]},
    {"project_id": "0/x", "options": OPTS, "draft": "D"},
    {"project_id": "0/x", "options": OPTS, "force_analyze": "yes"},
    {"project_id": "0/x", "options": OPTS, "options_file": "x"},  # unknown field
])  # fmt: skip
def test_bad_pipeline_requests_are_refused(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PipelineCreate.model_validate(body)


def test_an_approval_needs_two_positive_finite_numbers() -> None:
    a = ApproveRequest(max_vnd=6000, estimate_vnd=4207.3)
    assert (a.max_vnd, a.estimate_vnd) == (6000.0, 4207.3)


@pytest.mark.parametrize("body", [
    {"max_vnd": 6000},  # the estimate must be echoed
    {"estimate_vnd": 4207},
    {"max_vnd": 0, "estimate_vnd": 4207},
    {"max_vnd": -5, "estimate_vnd": 4207},
    {"max_vnd": float("nan"), "estimate_vnd": 4207},
    {"max_vnd": float("inf"), "estimate_vnd": 4207},
    {"max_vnd": 6000, "estimate_vnd": float("nan")},
    {"max_vnd": 6000, "estimate_vnd": -1},
    {"max_vnd": "6000", "estimate_vnd": 4207},
    {"max_vnd": True, "estimate_vnd": 4207},
    {"max_vnd": 6000, "estimate_vnd": 4207, "note": "x"},
])  # fmt: skip
def test_bad_approvals_are_refused_before_any_logic(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ApproveRequest.model_validate(body)


@pytest.mark.parametrize(
    "jobs,expected",
    [([], "queued"),
     ([("queued", None)] * 3, "queued"),
     ([("completed", "passed"), ("queued", None)], "running"),  # between two jobs
     ([("processing", None), ("queued", None)], "running"),
     ([("awaiting_approval", None), ("queued", None)], "awaiting_approval"),
     ([("completed", "passed")] * 2, "done"),
     ([("completed", "passed"), ("completed", "qc_failed")], "qa_review"),
     ([("failed", None), ("cancelled", None)], "error"),
     ([("completed", "passed"), ("cancelled", None), ("queued", None)], "error"),
     ([("completed", "qc_failed"), ("processing", None)], "running")],
)  # fmt: skip
def test_a_pipeline_status_is_read_from_its_jobs(jobs: list[tuple[str, str | None]],
                                                 expected: str) -> None:  # fmt: skip
    assert pipeline_status(jobs) == expected


def kind_steps(art: Artifacts, kind: str, **kw: object) -> dict[str, str]:
    base: dict[str, object] = {"size": "preview", "status": "queued", "result": None,
                               "stage": None, "error_msg": None, "kind": kind}  # fmt: skip
    got = derive_steps(art, RunInfo(**{**base, **kw}))  # type: ignore[arg-type]
    return {s.id: s.status for s in got}


NOTHING = Artifacts(inventory=False, scenes=False, speech=False, plan=False, pick=False,
                    drafts=False, preview_video=False)  # fmt: skip


def test_a_running_analysis_sits_on_the_recognition_step_and_never_touches_render_steps() -> None:
    art = NOTHING.model_copy(update={"inventory": True})
    got = kind_steps(art, "analyze", status="processing", stage="transcribe")
    assert got["inventory"] == "done" and got["nhan_dang"] == "running"
    later = ("plan_compile", "pick", "ai_draft", "render_preview", "qc_verify", "final_export")
    assert {got[k] for k in later} == {"queued"}


def test_an_analysis_that_fails_marks_the_step_it_was_in() -> None:
    art = NOTHING.model_copy(update={"inventory": True})
    got = derive_steps(art, RunInfo(size="preview", status="failed", result=None, stage="scenes",
                                    error_msg="Hết bộ nhớ", kind="analyze"))  # fmt: skip
    by = {s.id: s for s in got}
    assert by["nhan_dang"].status == "error" and by["nhan_dang"].detail == "Hết bộ nhớ"
    assert by["inventory"].status == "done"


def test_a_running_draft_job_walks_plan_pick_then_the_ai_step() -> None:
    art = NOTHING.model_copy(update={"inventory": True, "scenes": True, "speech": True})
    assert kind_steps(art, "draft", status="processing", stage="plan")["plan_compile"] == "running"
    assert kind_steps(art, "draft", status="processing", stage="pick")["pick"] == "running"
    for stage in ("phrases", "estimate", "draft"):
        assert kind_steps(art, "draft", status="processing", stage=stage)["ai_draft"] == "running"


def test_a_draft_waiting_for_approval_says_so_and_is_not_running() -> None:
    art = NOTHING.model_copy(update={"inventory": True, "plan": True, "pick": True})
    got = derive_steps(art, RunInfo(size="preview", status="awaiting_approval", result=None,
                                    stage="cho_duyet", error_msg=None, kind="draft"))  # fmt: skip
    by = {s.id: s for s in got}
    assert by["ai_draft"].status == "queued" and "duyệt" in (by["ai_draft"].detail or "")
    assert by["plan_compile"].status == "done" and by["pick"].status == "done"


def test_a_finished_draft_job_has_the_ai_step_done_and_no_render_yet() -> None:
    art = NOTHING.model_copy(update={"plan": True, "pick": True, "drafts": True})
    got = kind_steps(art, "draft", status="completed", result="passed")
    assert got["ai_draft"] == "done" and got["render_preview"] == "queued"


def test_a_cancelled_draft_errors_on_its_current_step_with_the_reason() -> None:
    got = derive_steps(NOTHING, RunInfo(size="preview", status="cancelled", result=None,
                                        stage="cho_duyet", error_msg="Hết hạn duyệt chi phí",
                                        kind="draft"))  # fmt: skip
    by = {s.id: s for s in got}
    assert by["ai_draft"].status == "error" and by["ai_draft"].detail == "Hết hạn duyệt chi phí"


def test_render_steps_are_unchanged_by_the_new_kind_field() -> None:
    got = derive_steps(ALL, RunInfo(size="preview", status="processing", result=None, stage="dung",
                                    error_msg=None))  # fmt: skip
    assert {s.id: s.status for s in got}["render_preview"] == "running"


def test_the_cost_block_and_the_new_job_fields_are_part_of_the_view() -> None:
    cost = CostView(input_tokens=1678, worst_call_vnd=4165.58, cap_vnd=20000.0, approved_vnd=None,
                    spent_vnd=None)  # fmt: skip
    assert cost.model_dump()["approved_vnd"] is None
    wanted = {"pipeline_id", "depends_on", "needs_approval", "approval_expires_at", "cost"}
    assert wanted <= set(JobView.model_fields)
    assert {"ai_key_configured", "budget"} <= set(HealthView.model_fields)
