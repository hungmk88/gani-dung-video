"""Keeps edit/verify_report.json up to date while a draft loop runs (phase 6i).

The report is written after every round (final_status "running"), so a job that is killed
midway still leaves its history, and once more when the loop ends. Money is already spent when
this runs, so a write problem is logged and swallowed: it must never cost the drafts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from gani_dung_video.domain.verify_report import FinalStatus, Mode, RoundRecord, VerifyReportFile
from gani_dung_video.infrastructure.verify_report_file import write_report

logger = logging.getLogger(__name__)


class VerifyRecorder:
    def __init__(self, folder: Path, mode: Mode, extra_codes: Sequence[str] = ()) -> None:
        self._folder = folder
        self._mode: Mode = mode
        self._extra = list(extra_codes)
        self._rounds: list[RoundRecord] = []

    def add(self, record: RoundRecord) -> None:
        self._rounds.append(record)
        self._write("running")

    def finish(self, status: FinalStatus) -> None:
        self._write(status)

    def _write(self, status: FinalStatus) -> None:
        report = VerifyReportFile(
            mode=self._mode, final_status=status, rounds=self._rounds, extra_codes=self._extra
        )
        try:
            write_report(self._folder, report)
        except OSError as e:
            logger.warning("Không ghi được edit/verify_report.json (%s); bỏ qua.", type(e).__name__)
