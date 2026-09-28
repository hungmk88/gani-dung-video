"""Command line.

python -m gani_dung_video preflight <yeu-cau.md|options.json>
    exit 0 = dựng được, 1 = bị CHẶN, 2 = không đọc được file / sai cách gọi
python -m gani_dung_video inventory <thu_muc_footage>
    exit 0 = đã ghi edit/inventory.json, 2 = thiếu thư mục / thiếu ffprobe
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.infrastructure.config_files import ConfigError
from gani_dung_video.infrastructure.ffprobe import FfprobeMissingError

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
    inv = sub.add_parser("inventory", help="Kiểm kê thư mục footage → edit/inventory.json")
    inv.add_argument("folder", type=Path, help="Thư mục chứa file quay thô (không bị sửa)")
    return ap


def _run_preflight(file: Path) -> int:
    result = check_options_file(file)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0 if result["valid"] else 1


def _run_inventory(folder: Path) -> int:
    inv, out = build_inventory(folder)
    hdr = sum(c.is_hdr for c in inv.clips)
    mute = sum(not c.has_audio_stream for c in inv.clips)
    lines = [
        f"Đã kiểm kê: {len(inv.clips)} clip, tổng {inv.total_duration_sec:g}s",
        f"  HDR (cần chuyển SDR khi render): {hdr}",
        f"  Không có luồng âm thanh: {mute}",
        f"  Bản trùng đã bỏ: {len(inv.duplicates)}",
        f"  Không đọc được: {len(inv.skipped)}",
        *[f"    - {s.file}: {s.reason}" for s in inv.skipped],
        f"Kết quả: {out}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    _utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as e:  # argparse already printed usage
        return 2 if e.code else 0
    try:
        if args.cmd == "inventory":
            return _run_inventory(args.folder)
        return _run_preflight(args.file)
    except (ConfigError, FfprobeMissingError) as e:
        logger.error("LỖI: %s", e)
        return 2
