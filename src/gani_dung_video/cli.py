"""Command line.

python -m gani_dung_video preflight <yeu-cau.md|options.json>
    exit 0 = dựng được, 1 = bị CHẶN, 2 = không đọc được file / sai cách gọi
python -m gani_dung_video inventory <thu_muc_footage>
    exit 0 = đã ghi edit/inventory.json, 2 = thiếu thư mục / thiếu ffprobe
python -m gani_dung_video transcribe <thu_muc_footage> [--model small]
    exit 0 = xong, 1 = có clip lỗi, 2 = chưa inventory / không nạp được model
python -m gani_dung_video scenes <thu_muc_footage>
    exit 0 = đã ghi edit/scenes.json, 2 = chưa inventory
python -m gani_dung_video plan <yeu-cau.md|options.json> <thu_muc_footage> [--nhac-thu-muc DIR]
    exit 0 = đã ghi edit/plan.json, 1 = bị chặn (preflight) hoặc chưa hỗ trợ, 2 = lỗi file/nhạc
python -m gani_dung_video pick <yeu-cau.md|options.json> <thu_muc_footage>
    exit 0 = đã ghi edit/candidates.json, 2 = chưa chạy plan / plan cũ / lỗi file
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
from gani_dung_video.application.pick_service import build_pick
from gani_dung_video.application.plan_service import PreflightBlocked, build_plan
from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.application.scenes_service import build_scenes
from gani_dung_video.application.semantics_service import build_semantics
from gani_dung_video.application.transcribe_service import transcribe_folder
from gani_dung_video.domain.plan import PlanError
from gani_dung_video.infrastructure.clip_model import SemanticError
from gani_dung_video.infrastructure.config_files import ConfigError
from gani_dung_video.infrastructure.ffprobe import FfprobeMissingError
from gani_dung_video.infrastructure.music_analysis import MusicError
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
    pl = sub.add_parser("plan", help="Biên dịch tùy chọn → số phách từng khối → edit/plan.json")
    pl.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json")
    pl.add_argument("folder", type=Path, help="Thư mục footage đã chạy scenes và semantics")
    pl.add_argument("--nhac-thu-muc", type=Path, help="Thư mục chứa nhạc (nếu ngoài 2 nơi trên)")
    pk = sub.add_parser("pick", help="Ứng viên cảnh cho từng khối → edit/candidates.json")
    pk.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json (đã chạy plan)")
    pk.add_argument("folder", type=Path, help="Thư mục footage đã chạy plan")
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


def _run_pick(file: Path, folder: Path) -> int:
    r, out = build_pick(file, folder)
    lines = [
        f"Phong cách: {r.style} — món trong ghi chú: {', '.join(r.dishes) or 'không có'}",
        *[
            f"  {b.role}: {len(b.candidates)} ứng viên ({b.beats} phách)"
            + (f", thiếu nhãn {', '.join(b.missing_tags)}" if b.missing_tags else "")
            for b in r.blocks
        ],
        f"  Cảnh còn thiếu: {', '.join(r.missing_shots) or 'không'}",
        f"  Cặp đoạn giống nhau cần tránh dùng cả hai: {len(r.similar_pairs)}",
        f"Kết quả: {out}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _run_plan(file: Path, folder: Path, music_dir: Path | None) -> int:
    try:
        plan, out = build_plan(file, folder, music_dir)
    except PreflightBlocked as e:
        lines = [f"BỊ CHẶN [{i['code']}]: {i['msg']}" for i in e.result["errors"]]
        sys.stdout.write("\n".join(lines) + "\n")
        return 1
    except PlanError as e:
        sys.stdout.write(f"CHƯA HỖ TRỢ [{e.code}]: {e}\n")
        return 1
    m = plan.music
    nhip = (
        f"nhạc {m.track} {m.bpm:g} BPM ({m.source}), đếm {m.effective_bpm:g} BPM"
        if m
        else "lưới ảo"
    )
    lines = [
        f"Dẫn nhịp: {plan.driver} — {nhip}, 1 phách = {plan.beat_sec:.3f}s",
        f"Tổng: {plan.planned_beats} phách (mục tiêu {plan.target_beats}), "
        f"mỗi cảnh {plan.beats_per_scene_allowed} phách",
        "Khối — " + ", ".join(f"{b.role}: {b.beats}" for b in plan.blocks),
        *[f"  Cảnh báo {w}" for w in plan.warnings],
        *[f"  Tự điều chỉnh {a}" for a in plan.adjustments],
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
        if args.cmd == "pick":
            return _run_pick(args.file, args.folder)
        if args.cmd == "plan":
            return _run_plan(args.file, args.folder, args.nhac_thu_muc)
        return _run_preflight(args.file)
    except (ConfigError, FfprobeMissingError, AsrError, SemanticError, MusicError) as e:
        logger.error("LỖI: %s", e)
        return 2
