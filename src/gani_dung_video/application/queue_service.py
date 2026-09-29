"""Use case: put render jobs in the queue, list them, cancel, retry (phase 6a).

Nothing gets in unless the project folder is inside GANI_DV_ALLOWED_ROOTS (the allow-list the
foundation phase promised for the web phase) and the options pass preflight — the same check
that runs before any AI spend. One job per draft; asking twice for the same render returns the
job that is already waiting instead of rendering it twice.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.config_files import (
    CONFIG_DIR,
    ConfigError,
    load_rules,
    read_options_file,
)
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb
from gani_dung_video.infrastructure.settings import Settings, load_settings

QUEUE_FILE = "queue.db"
LETTERS = "ABC"


class EnqueueResult(BaseModel):
    job_id: int
    draft: str
    duplicate: bool  # True: an identical render was already waiting or running


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


def enqueue_render(
    options_file: Path, folder: Path, db: QueueDb, *, ban: str | None = None,
    final: bool = False, no_asr: bool = False, rules: Rules | None = None,
    settings: Settings | None = None, now: float | None = None,
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
    stamp = now if now is not None else time.time()
    out: list[EnqueueResult] = []
    for letter in chosen:
        existing = db.find_active("render", str(project), letter, final)
        if existing is not None:
            out.append(EnqueueResult(job_id=existing, draft=letter, duplicate=True))
            continue
        jid = db.enqueue(
            NewJob(kind="render", project_dir=str(project), options_file=str(options),
                   draft=letter, final=final, no_asr=no_asr,
                   max_attempts=int(rules["queue"]["max_attempts"])), stamp)  # fmt: skip
        out.append(EnqueueResult(job_id=jid, draft=letter, duplicate=False))
    return out


def cancel_job(db: QueueDb, job_id: int, now: float | None = None) -> str:
    """Cancel a waiting job now, or ask a running one to stop. Returns its status afterwards."""
    return db.request_cancel(job_id, now if now is not None else time.time())


def retry_job(db: QueueDb, job_id: int) -> bool:
    """Queue a failed, cancelled or QC-failed job again."""
    return db.retry_manual(job_id)
