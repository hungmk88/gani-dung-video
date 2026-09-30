"""edit/verify_report.json: atomic write and tolerant read of the per-round check record (6i)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from pydantic import ValidationError

from gani_dung_video.domain.verify_report import VerifyReportFile
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR

REPORT_FILE = "verify_report.json"


def report_path(folder: Path) -> Path:
    return folder / EDIT_DIR / REPORT_FILE


def write_report(folder: Path, report: VerifyReportFile) -> None:
    """Replace the report atomically (tmp + rename), so a reader never sees half a file.

    Raises:
        OSError: the folder or the target cannot be written.
    """
    path = report_path(folder)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(report.model_dump_json(indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def read_report(path: Path) -> VerifyReportFile | None:
    """The report at `path`, or None when it is missing, unreadable or not a valid report."""
    try:
        return VerifyReportFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        return None
