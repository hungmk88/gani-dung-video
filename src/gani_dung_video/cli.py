"""Command line.

python -m gani_dung_video preflight <yeu-cau.md|options.json>
    exit 0 = dựng được, 1 = bị CHẶN, 2 = không đọc được file / sai cách gọi
python -m gani_dung_video inventory <thu_muc_footage>
    exit 0 = đã ghi edit/inventory.json, 2 = thiếu thư mục / thiếu ffprobe
python -m gani_dung_video transcribe <thu_muc_footage> [--model small]
    exit 0 = xong, 1 = có clip lỗi, 2 = chưa inventory / không nạp được model
python -m gani_dung_video scenes <thu_muc_footage>
    exit 0 = đã ghi edit/scenes.json, 2 = chưa inventory
python -m gani_dung_video semantics <thu_muc_footage>
    exit 0 = đã thêm nhãn/điểm thẩm mỹ vào scenes.json, 2 = chưa chạy scenes / chưa cài [ai]
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
from gani_dung_video.application.scenes_service import build_scenes
from gani_dung_video.application.semantics_service import build_semantics
from gani_dung_video.application.transcribe_service import transcribe_folder
from gani_dung_video.infrastructure.clip_model import SemanticError
from gani_dung_video.infrastructure.config_files import ConfigError
from gani_dung_video.infrastructure.ffprobe import FfprobeMissingError
from gani_dung_video.infrastructure.whisper_asr import AsrError

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
    tr = sub.add_parser("transcribe", help="Nhận dạng lời từng clip → edit/transcripts/")
    tr.add_argument("folder", type=Path, help="Thư mục footage đã chạy inventory")
    tr.add_argument("--model", help="Model Whisper (mặc định theo rules.json; Dell: small)")
    sc = sub.add_parser("scenes", help="Tách cảnh, đo nét/rung, đoạn đẹp → edit/scenes.json")
    sc.add_argument("folder", type=Path, help="Thư mục footage đã chạy inventory")
    se = sub.add_parser("semantics", help="Nhãn cảnh CLIP, điểm thẩm mỹ, đoạn trùng → scenes.json")
    se.add_argument("folder", type=Path, help="Thư mục footage đã chạy scenes")
    return ap


def _run_scenes(folder: Path) -> int:
    r, out = build_scenes(folder)
    n = len(r.scenes)
    counts = ", ".join(f"{k}: {v}" for k, v in r.usable_by_style.items())
    lines = [
        f"Đã tách: {n} cảnh từ {len({s.clip for s in r.scenes})} clip",
        f"  Mờ: {sum(s.blurry for s in r.scenes)} · Rung cả cảnh: {sum(s.shaky for s in r.scenes)}"
        f" · Lia máy: {sum(s.motion == 'lia' for s in r.scenes)}",
        f"  Cửa sổ 2s rung: {sum(w.shaky for s in r.scenes for w in s.windows)}"
        f"/{sum(len(s.windows) for s in r.scenes)}",
        f"  Đoạn đẹp dùng được theo phong cách — {counts}",
        f"Kết quả: {out}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _run_semantics(folder: Path) -> int:
    r, out = build_semantics(folder)
    label_count: dict[str, int] = {}
    for sc in r.scenes:
        for lb in sc.labels:
            label_count[lb] = label_count.get(lb, 0) + 1
    labels = ", ".join(f"{k}: {v}" for k, v in sorted(label_count.items())) or "chưa có"
    lines = [
        f"Model: {r.semantic_model} — {len(r.scenes)} cảnh",
        f"  Nhãn cảnh — {labels}",
        f"  Cặp thời điểm giống nhau (cùng clip): {len(r.similar)}",
        *[f"    - {p.clip} {p.peak_a:g}s ≈ {p.peak_b:g}s ({p.similarity:.2f})"
          f" — {', '.join(p.styles)}" for p in r.similar[:10]],
        "  Đoạn dùng được sau khi loại màn chữ/logo — "
        + ", ".join(f"{k}: {v}" for k, v in r.usable_by_style.items()),
        f"Kết quả: {out}",
    ]  # fmt: skip
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _run_transcribe(folder: Path, model: str | None) -> int:
    s, out = transcribe_folder(folder, model)
    lines = [
        f"Model: {s.model} — {len(s.clips)} clip, có lời nói trong footage: "
        f"{'CÓ' if s.co_loi_noi else 'KHÔNG'}",
        *[f"  {c.file}: {'không lời' if c.no_speech else f'{c.word_count} từ'}, "
          f"{c.speech_sec:g}s tiếng nói{' (cache)' if c.cached else ''}" for c in s.clips],
        *[f"  LỖI {f}: {msg}" for f, msg in s.failed.items()],
        f"Kết quả: {out}",
    ]  # fmt: skip
    sys.stdout.write("\n".join(lines) + "\n")
    return 1 if s.failed else 0


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
        if args.cmd == "transcribe":
            return _run_transcribe(args.folder, args.model)
        if args.cmd == "scenes":
            return _run_scenes(args.folder)
        if args.cmd == "semantics":
            return _run_semantics(args.folder)
        return _run_preflight(args.file)
    except (ConfigError, FfprobeMissingError, AsrError, SemanticError) as e:
        logger.error("LỖI: %s", e)
        return 2
