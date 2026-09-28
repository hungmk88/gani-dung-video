"""Use case: check an option file before the pipeline spends any AI tokens."""

from __future__ import annotations

from pathlib import Path

from gani_dung_video.domain.preflight import Issue, Result, Rules, preflight_check
from gani_dung_video.domain.yeu_cau import YeuCauFormatError
from gani_dung_video.infrastructure.config_files import load_rules, read_options_file


def check_options_file(path: Path, rules: Rules | None = None) -> Result:
    """Read yeu-cau.md / options.json and run preflight.

    A malformed yeu-cau.md is reported as a blocking INVALID_FORMAT issue (same JSON shape as
    other blocks) so the future UI can show it like any other error.

    Raises:
        ConfigError: the file itself cannot be read (missing, wrong type, too large).
    """
    rules = rules if rules is not None else load_rules()
    try:
        options = read_options_file(path)
    except YeuCauFormatError as e:
        issue: Issue = {"code": "INVALID_FORMAT", "level": "block", "msg": str(e)}
        return {
            "valid": False, "error_code": "INVALID_FORMAT", "error_msg": str(e),
            "errors": [issue], "warnings": [], "adjustments": [], "scenes_needed": None,
        }  # fmt: skip
    return preflight_check(options, rules)
