"""Command line: `python -m gani_dung_video preflight <yeu-cau.md|options.json>`.

Exit codes: 0 = dựng được, 1 = bị CHẶN, 2 = không đọc được file / sai cách gọi.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.infrastructure.config_files import ConfigError

logger = logging.getLogger("gani_dung_video")


def _utf8_stdio() -> None:
    # Windows consoles default to cp1252 and crash on Vietnamese output; force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gani-dv", description="App Dựng Video GANI v2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight", help="Kiểm tra tổ hợp tùy chọn trước khi gọi AI")
    pf.add_argument("file", type=Path, help="yeu-cau.md (khối Phụ lục D) hoặc options.json")
    return ap


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    _utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as e:  # argparse already printed usage
        return 2 if e.code else 0
    try:
        result = check_options_file(args.file)
    except ConfigError as e:
        logger.error("LỖI: %s", e)
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0 if result["valid"] else 1
