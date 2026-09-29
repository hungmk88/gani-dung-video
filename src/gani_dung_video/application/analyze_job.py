"""Multi-stage `analyze` jobs (phase 6c-1): inventory → transcribe → scenes → semantics.

One job, four child commands run in turn (each is the CLI command of that name), so a hung or
crashed stage cannot take the daemon down. Two things make a long analysis cheap to repeat:

- `stages_done` on the job: a retry (automatic or by hand) resumes at the stage that failed, so
  Whisper is not run again because CLIP was missing;
- edit/analysis_stamp.json: the footage (names, sizes, mtimes) an analysis was done on. If the
  footage is unchanged, all results are still there and nobody passed `force`, the job
  completes at once without running anything.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from gani_dung_video.domain.jobs import (
    ANALYZE_STAGES,
    AnalysisStamp,
    Job,
    Verdict,
    analyze_progress,
    footage_signature,
    judge_stage,
    line_level,
    may_retry,
    parse_clip_progress,
    remaining_stages,
    stamp_is_fresh,
)
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.config_files import CONFIG_DIR
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, list_videos
from gani_dung_video.infrastructure.process_runner import RunResult
from gani_dung_video.infrastructure.queue_db import QueueDb

logger = logging.getLogger(__name__)
REPO_ROOT = CONFIG_DIR.parent
STAMP_FILE = "analysis_stamp.json"
ARTIFACTS = ("inventory.json", "scenes.json", "speech.json")  # what a finished analysis leaves
_TAIL_LINES = 3


@dataclass(frozen=True)
class JobEnv:
    """What a job runner needs from the daemon (all injectable, so tests need no process)."""

    db: QueueDb
    rules: Rules
    runner: Callable[..., RunResult]
    clock: Callable[[], float]
    pid: int
    argv_base: Sequence[str] | None
    log_dir: Path
    child_env: Mapping[str, str]
    should_stop: Callable[[Job], str | None]
    whisper_model: str | None = None


def stage_argv(stage: str, folder: str, base: Sequence[str] | None = None,
               whisper_model: str | None = None) -> list[str]:  # fmt: skip
    """The CLI command for one stage (transcribe gets the machine's Whisper model, if set)."""
    head = list(base) if base is not None else [sys.executable, "-m", "gani_dung_video"]
    extra = ["--model", whisper_model] if stage == "transcribe" and whisper_model else []
    return [*head, stage, folder, *extra]


def footage_files(folder: Path, rules: Rules) -> list[tuple[str, int, int]]:
    """(name, size, mtime_ns) of each video directly inside the folder."""
    out = []
    for p in list_videos(folder, rules["footage"]["video_extensions"]):
        st = p.stat()
        out.append((p.name, st.st_size, st.st_mtime_ns))
    return out


def read_stamp(folder: Path) -> AnalysisStamp | None:
    try:
        text = (folder / EDIT_DIR / STAMP_FILE).read_text(encoding="utf-8")
        return AnalysisStamp.model_validate_json(text)
    except (OSError, ValidationError):
        return None  # missing or unreadable: analyse again


def _write_stamp(folder: Path, stamp: AnalysisStamp) -> None:
    path = folder / EDIT_DIR / STAMP_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(stamp.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _fail(env: JobEnv, job: Job, msg: str) -> None:
    now = env.clock()
    env.db.finish(job.id, now, "failed", None, None, msg, None)
    env.db.add_event(job.id, now, "error", msg)


def _precheck(folder: Path, rules: Rules) -> str | None:
    if not folder.is_dir():
        return f"Thư mục footage không còn: {folder.name}."
    if not footage_files(folder, rules):
        exts = ", ".join(rules["footage"]["video_extensions"])
        return f"Thư mục không có clip video nào ({exts}); không có gì để phân tích."
    return None


def _settle_stop(env: JobEnv, job: Job, v: Verdict, res: RunResult, tail: str) -> None:
    """A stage ended badly (or was stopped): the job is cancelled, handed back, retried from
    that stage, or failed. Finished stages stay recorded in every case."""
    cfg = env.rules["queue"]
    now = env.clock()
    if v.message:
        env.db.add_event(job.id, now, "warn", v.message)
    if v.status == "cancelled":
        env.db.finish(job.id, now, "cancelled", None, None, None, None)
    elif v.status == "queued":  # a stop request: the attempt is not used
        env.db.requeue(job.id, now, v.message, consume_attempt=False)
    elif v.retry and may_retry(job.kind, job.attempts, job.max_attempts):
        env.db.requeue(job.id, now + float(cfg["retry_delay_sec"]), v.message,
                       consume_attempt=True)  # fmt: skip
    else:
        tried = f" (đã thử {job.attempts}/{job.max_attempts} lần)" if v.retry else ""
        last = f" Dòng cuối của tiến trình: {tail}" if tail else ""
        env.db.finish(job.id, now, "failed", None, res.exit_code, v.message + tried + last, None)


def _reuse(env: JobEnv, job: Job) -> None:
    for stage in ANALYZE_STAGES:
        env.db.mark_stage_done(job.id, stage)
    env.db.add_event(job.id, env.clock(), "info",
                     "Footage không đổi từ lần phân tích trước: dùng lại kết quả.")  # fmt: skip
    env.db.finish(job.id, env.clock(), "completed", "passed", 0, None, None)


def run_analyze(env: JobEnv, job: Job) -> None:
    """Run (or skip, or resume) the analysis of one project folder."""
    cfg = env.rules["queue"]
    folder = Path(job.project_dir)
    if (problem := _precheck(folder, env.rules)) is not None:
        _fail(env, job, problem)  # nothing here fixes itself: no retry
        return
    signature = footage_signature(footage_files(folder, env.rules))
    present = all((folder / EDIT_DIR / n).is_file() for n in ARTIFACTS)
    if not job.stages_done and stamp_is_fresh(read_stamp(folder), signature, job.force, present):
        _reuse(env, job)
        return
    todo = remaining_stages(ANALYZE_STAGES, job.stages_done)
    done = list(ANALYZE_STAGES[: len(ANALYZE_STAGES) - len(todo)])
    expected = cfg["stage_expected_sec"]
    tail: list[str] = []
    job_start = time.monotonic()
    for stage in todo:
        stage_start, last_tick = time.monotonic(), [0.0]
        fraction: list[float | None] = [None]

        def tick(force: bool = False, stage: str = stage, stage_start: float = stage_start,
                 last_tick: list[float] = last_tick, fraction: list[float | None] = fraction,
                 done: list[str] = done) -> None:  # fmt: skip
            mono = time.monotonic()
            if not force and mono - last_tick[0] < 1.0:
                return
            last_tick[0] = mono
            pct = analyze_progress(done, stage, mono - stage_start, expected, fraction[0])
            env.db.progress(job.id, env.clock(), stage, pct)
            env.db.daemon_heartbeat(env.pid, env.clock())

        def on_line(line: str, stage: str = stage, tick: Callable[..., None] = tick,
                    fraction: list[float | None] = fraction) -> None:  # fmt: skip
            if line.strip():
                tail.append(line.strip())
                del tail[:-_TAIL_LINES]
            env.db.add_event(job.id, env.clock(), line_level(line), line,
                             keep=int(cfg["max_events_per_job"]))  # fmt: skip
            clip = parse_clip_progress(line) if stage == "semantics" else None
            if clip is not None:
                fraction[0] = clip
            tick(force=clip is not None)

        def should_stop(tick: Callable[..., None] = tick) -> str | None:
            tick()
            return env.should_stop(job)

        env.db.add_event(job.id, env.clock(), "info", f"== Giai đoạn {stage} ==")
        tick(force=True)
        budget = float(cfg["analyze_timeout_sec"]) - (time.monotonic() - job_start)
        res = (env.runner(
            stage_argv(stage, str(folder), env.argv_base, env.whisper_model), REPO_ROOT,
            on_line, should_stop,
            [folder / EDIT_DIR], float(cfg["analyze_stall_sec"]), budget,
            on_start=lambda pid: env.db.set_child(job.id, pid),
            poll_sec=min(float(cfg["poll_sec"]), 0.5), log_path=env.log_dir / f"{job.id}.log",
            env=env.child_env,
        ) if budget > 0 else RunResult("timeout", None))  # fmt: skip
        limit = cfg["analyze_stall_sec"] if res.reason == "stalled" else cfg["analyze_timeout_sec"]
        verdict = judge_stage(res.reason, res.exit_code, stage, detail=f"{int(limit)}s")
        if verdict.status != "completed":
            _settle_stop(env, job, verdict, res, " | ".join(tail))
            return
        env.db.mark_stage_done(job.id, stage)
        done.append(stage)
    if footage_signature(footage_files(folder, env.rules)) == signature:
        version = str(env.rules["rules_version"])
        stamp = AnalysisStamp(
            signature=signature,
            stages=list(ANALYZE_STAGES),
            created_at=env.clock(),
            rules_version=version,
        )
        with contextlib.suppress(OSError):
            _write_stamp(folder, stamp)
    else:
        env.db.add_event(job.id, env.clock(), "warn",
                         "Footage thay đổi trong lúc phân tích: không ghi dấu, lần sau sẽ "
                         "phân tích lại.")  # fmt: skip
    env.db.add_event(job.id, env.clock(), "info", "Phân tích xong.")
    env.db.finish(job.id, env.clock(), "completed", "passed", 0, None, None)
