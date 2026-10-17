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
python -m gani_dung_video draft <yeu-cau.md|options.json> <thu_muc_footage> [--dry-run]
    exit 0 = đã ghi edit/drafts.json (hoặc in ước tính), 1 = hết vòng sửa / chạm trần chi phí,
    2 = thiếu khóa API, lỗi file, lỗi mạng
python -m gani_dung_video render <thu_muc_footage> [--preview] [--ban A] [--no-asr] \
    [--nen-tang tiktok|facebook] [--khong-nhac]
    exit 0 = đã dựng + đạt soát kỹ thuật, 1 = còn lỗi soát, 2 = thiếu drafts.json / lỗi ffmpeg
    (bản cuối chế độ lời còn được Whisper nghe lại — T8; --no-asr để bỏ;
    --khong-nhac: không nhúng nhạc, chèn nhạc trên app khi đăng)
python -m gani_dung_video qc <thu_muc_footage> --ban A [--final] [--no-asr] [--nen-tang ...] \
    [--khong-nhac]
    exit 0 = đạt, 1 = còn lỗi, 2 = chưa có video / lỗi file
python -m gani_dung_video semantics <thu_muc_footage>
    exit 0 = đã thêm nhãn/điểm thẩm mỹ vào scenes.json, 2 = chưa chạy scenes / chưa cài [ai]
python -m gani_dung_video phrases <yeu-cau.md|options.json> <thu_muc_footage>
    exit 0 = đã ghi edit/phrases.json, 2 = phong cách không dẫn nhịp theo lời / chưa transcribe
python -m gani_dung_video queue add <yeu-cau.md|options.json> <thu_muc_footage> \
    [--ban A|all] [--final] [--no-asr] [--khong-nhac]
python -m gani_dung_video review list [--trang-thai pending|approved|changes_requested|tat-ca]
python -m gani_dung_video review approve ID [--nguoi-duyet TÊN]
python -m gani_dung_video review reject ID --ly-do "..." [--nguoi-duyet TÊN]
    duyệt video cuối đã giao (T-F); exit 0 = xong, 2 = bị từ chối (in lý do, danh sách check hỏng)
python -m gani_dung_video queue list | show ID | cancel ID | retry ID | stop [--now]
    hàng đợi dựng (GĐ6a); exit 0 = xong, 2 = thiếu GANI_DV_ALLOWED_ROOTS, thư mục ngoài danh
    sách cho phép, hoặc không có job đó
python -m gani_dung_video music-check [<thu_muc_nhac>] [--chi-tiet] [--ca-180]
    kiểm thư mục nhạc, CHỈ ĐỌC: BPM từng bài và bài nào dựng được phong cách × độ dài nào;
    mặc định lấy GANI_DV_MUSIC_DIR; exit 0 = xong, 2 = thư mục không có / library.json hỏng
python -m gani_dung_video serve [--port 8765]
    API web cục bộ cho giao diện (chỉ 127.0.0.1); chạy độc lập với daemon; Ctrl+C để dừng;
    exit 2 = cổng đang bị chiếm
python -m gani_dung_video daemon
    chạy nền lấy job từ hàng đợi; Ctrl+C = dừng sau khi job đang chạy xong, Ctrl+C lần 2 =
    dừng ngay (trả job về hàng đợi); exit 2 = đã có daemon khác đang chạy
"""

from __future__ import annotations

import argparse
import getpass
import io
import json
import logging
import math
import signal
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from gani_dung_video.application.api_service import ApiContext
from gani_dung_video.application.daemon import Daemon, DaemonError
from gani_dung_video.application.draft_service import (
    DraftError,
    DraftsFile,
    DryRun,
    InfeasibleError,
    build_drafts,
)
from gani_dung_video.application.inventory_service import build_inventory
from gani_dung_video.application.music_check import (
    DEFAULT_DURATIONS,
    check_music_folder,
    format_report,
)
from gani_dung_video.application.pick_service import build_pick
from gani_dung_video.application.plan_service import BpmBlockedError, PreflightBlocked, build_plan
from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.application.queue_service import (
    ai_key_configured,
    approve_job,
    cancel_job,
    enqueue_analyze,
    enqueue_draft,
    enqueue_render,
    music_dir,
    open_queue,
    retry_job,
)
from gani_dung_video.application.render_service import build_render, recheck
from gani_dung_video.application.review_service import (
    ReviewRow,
    approve_review,
    list_reviews,
    reject_review,
)
from gani_dung_video.application.scenes_service import build_scenes
from gani_dung_video.application.semantics_service import build_semantics
from gani_dung_video.application.speech_draft_service import (
    SpeechDraftError,
    SpeechDraftsResult,
    build_speech_drafts,
)
from gani_dung_video.application.speech_service import build_phrases
from gani_dung_video.application.transcribe_service import transcribe_folder
from gani_dung_video.domain.bpm_safety import BPM_BLOCKED_EXIT_CODE, BPM_BLOCKED_MARKER
from gani_dung_video.domain.feasibility import INFEASIBLE_EXIT_CODE
from gani_dung_video.domain.jobs import Job, format_vnd
from gani_dung_video.domain.plan import Plan, PlanError
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.claude_client import AiError
from gani_dung_video.infrastructure.clip_model import SemanticError
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.ffmpeg_render import RenderError, ffmpeg_problem
from gani_dung_video.infrastructure.ffprobe import FfprobeMissingError
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.http_server import ApiServerError, make_server
from gani_dung_video.infrastructure.media_probe import ProbeFailed
from gani_dung_video.infrastructure.music_analysis import MusicError
from gani_dung_video.infrastructure.queue_db import QueueDb, QueueDbError
from gani_dung_video.infrastructure.settings import SettingsError, load_settings
from gani_dung_video.infrastructure.static_files import dist_root, has_index
from gani_dung_video.infrastructure.whisper_asr import AsrError

logger = logging.getLogger("gani_dung_video")


def _utf8_stdio() -> None:
    # Windows consoles default to cp1252 and crash on Vietnamese output; force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


_NO_MUSIC_HELP = (
    "Không nhúng nhạc của kế hoạch: chỉ còn lời / tiếng hiện trường, nhạc chèn trên "
    "app TikTok/Facebook khi đăng (quảng cáo cần nhạc có bản quyền của nền tảng)"
)


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
    pl.add_argument(
        "--bpm-override",
        action="store_true",
        help="Vẫn dùng bài nhạc có BPM không dựng được độ dài đã chọn (bỏ cảnh báo)",
    )
    pk = sub.add_parser("pick", help="Ứng viên cảnh cho từng khối → edit/candidates.json")
    pk.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json (đã chạy plan)")
    pk.add_argument("folder", type=Path, help="Thư mục footage đã chạy plan")
    ph = sub.add_parser("phrases", help="Câu nói dùng được cho AI chọn → edit/phrases.json")
    ph.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json (phong cách dẫn lời)")
    ph.add_argument("folder", type=Path, help="Thư mục footage đã chạy transcribe")
    dr = sub.add_parser("draft", help="Gọi Claude chọn cảnh + viết chữ → edit/drafts.json")
    dr.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json (đã chạy pick)")
    dr.add_argument("folder", type=Path, help="Thư mục footage đã chạy pick")
    dr.add_argument("--dry-run", action="store_true", help="Chỉ ước tính token/chi phí")
    dr.add_argument("--json", action="store_true", help="Với --dry-run: in ước tính 1 dòng JSON")
    dr.add_argument(
        "--max-vnd", type=_positive_vnd, help="Trần chi phí (đồng); chỉ hạ được trần cấu hình"
    )
    rd = sub.add_parser("render", help="Dựng video từ edit/drafts.json → edit/render/")
    rd.add_argument("folder", type=Path, help="Thư mục footage đã chạy draft")
    rd.add_argument("--preview", action="store_true", help="Bản nháp 540p (nhanh)")
    rd.add_argument("--ban", help="Chỉ dựng một bản nháp (A, B hoặc C)")
    rd.add_argument(
        "--no-asr", action="store_true", help="Bỏ hậu kiểm Whisper (T8) ở bản cuối chế độ lời"
    )
    rd.add_argument("--san-pham", help="Sản phẩm trong tên file giao (mặc định: chung)")
    rd.add_argument("--chien-dich", help="Tên chiến dịch = thư mục giao (mặc định: tên thư mục)")
    rd.add_argument(
        "--nen-tang",
        default="",
        help="Nền tảng đăng (rules.json → platforms, ví dụ tiktok, facebook): đổi câu kêu gọi, "
        "khung kết và tên file; bỏ trống = không theo nền tảng",
    )
    rd.add_argument("--khong-nhac", action="store_true", help=_NO_MUSIC_HELP)
    qc = sub.add_parser("qc", help="Soát kỹ thuật lại một video đã dựng (không dựng lại)")
    qc.add_argument("folder", type=Path, help="Thư mục footage đã chạy render")
    qc.add_argument("--ban", required=True, help="Bản nháp cần soát (A, B hoặc C)")
    qc.add_argument("--final", action="store_true", help="Soát bản 1080×1920 thay vì nháp")
    qc.add_argument("--nen-tang", default="", help="Nền tảng của bản cần soát (như lệnh render)")
    qc.add_argument("--khong-nhac", action="store_true", help="Bản được dựng với --khong-nhac")
    qc.add_argument(
        "--no-asr", action="store_true", help="Bỏ hậu kiểm Whisper (T8) ở bản cuối chế độ lời"
    )
    q = sub.add_parser("queue", help="Hàng đợi dựng video (chạy nền bằng lệnh daemon)")
    qsub = q.add_subparsers(dest="qcmd", required=True)
    qa = qsub.add_parser("add", help="Đưa các bản nháp cần dựng vào hàng đợi")
    qa.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json")
    qa.add_argument("folder", type=Path, help="Thư mục footage (trong GANI_DV_ALLOWED_ROOTS)")
    qa.add_argument("--ban", default="all", help="Một bản (A, B, C) hoặc all (mặc định)")
    qa.add_argument("--final", action="store_true", help="Dựng bản cuối 1080×1920 (mặc định: nháp)")
    qa.add_argument("--no-asr", action="store_true", help="Bỏ hậu kiểm Whisper (T8)")
    qa.add_argument("--khong-nhac", action="store_true", help=_NO_MUSIC_HELP)
    qa.add_argument(
        "--after", type=int, help="Chỉ chạy sau khi job này xong (ví dụ một job phân tích)"
    )
    qd = qsub.add_parser("draft", help="Kịch bản: plan, pick, ước tính chi phí rồi CHỜ DUYỆT")
    qd.add_argument("file", type=Path, help="yeu-cau.md hoặc options.json")
    qd.add_argument("folder", type=Path, help="Thư mục footage (trong GANI_DV_ALLOWED_ROOTS)")
    qd.add_argument("--after", type=int, help="Chỉ chạy sau khi job này xong (vd job phân tích)")
    qp = qsub.add_parser("approve", help="Duyệt chi phí gọi Claude cho một job kịch bản đang chờ")
    qp.add_argument("id", type=int)
    qp.add_argument(
        "--max-vnd",
        type=float,
        required=True,
        help="Số đồng TỐI ĐA được phép tiêu cho job này (bắt buộc)",
    )
    qp.add_argument(
        "--estimate",
        type=float,
        required=True,
        help="Ước tính một lần gọi mà job đang giữ (lấy từ `queue show`; bắt buộc)",
    )
    qn = qsub.add_parser("analyze", help="Phân tích footage (inventory, Whisper, scenes, CLIP)")
    qn.add_argument("folder", type=Path, help="Thư mục footage (trong GANI_DV_ALLOWED_ROOTS)")
    qn.add_argument("--force", action="store_true", help="Phân tích lại dù footage không đổi")
    ql = qsub.add_parser("list", help="Xem hàng đợi")
    ql.add_argument("--status", help="Chỉ hiện trạng thái này (queued, processing, …)")
    ql.add_argument("--limit", type=int, default=30)
    for name, text in (
        ("show", "Chi tiết một job"),
        ("cancel", "Hủy một job"),
        ("retry", "Đưa job lỗi/đã hủy/QC không đạt vào hàng đợi lại"),
    ):
        qs = qsub.add_parser(name, help=text)
        qs.add_argument("id", type=int)
    qst = qsub.add_parser("stop", help="Yêu cầu daemon dừng")
    qst.add_argument("--now", action="store_true", help="Dừng ngay, trả job đang chạy về hàng đợi")
    rv = sub.add_parser("review", help="Duyệt video cuối đã giao (T-F): xem, duyệt, yêu cầu sửa")
    rsub = rv.add_subparsers(dest="rcmd", required=True)
    rl = rsub.add_parser("list", help="Video cuối đã giao và trạng thái duyệt")
    rl.add_argument(
        "--trang-thai", default="pending",
        choices=("pending", "approved", "changes_requested", "tat-ca"),
        help="Chỉ hiện trạng thái này (mặc định pending = chờ duyệt)",
    )  # fmt: skip
    rl.add_argument("--tu-ngay", help="Chỉ video bắt đầu chờ từ ngày YYYY-MM-DD (UTC) trở đi")
    rl.add_argument("--limit", type=int, default=50)
    for name, text in (("approve", "Duyệt: chuyển tệp sang final/"),
                       ("reject", "Yêu cầu sửa (bắt buộc --ly-do)")):  # fmt: skip
        ra = rsub.add_parser(name, help=text)
        ra.add_argument("id", type=int, help="Số job dựng bản cuối (xem `review list`)")
        ra.add_argument("--nguoi-duyet", help="Tên người duyệt (mặc định: người dùng hệ điều hành)")
        if name == "reject":
            ra.add_argument("--ly-do", default="", help="Một dòng lý do (bắt buộc)")
        else:
            ra.add_argument("--ghi-chu", default=None, help="Ghi chú kèm khi duyệt (tùy chọn)")
    sub.add_parser("daemon", help="Chạy nền: lấy job từ hàng đợi và dựng lần lượt")
    mc = sub.add_parser("music-check", help="Kiểm thư mục nhạc: BPM và bài nào dựng được (chỉ đọc)")
    mc.add_argument(
        "folder", nargs="?", type=Path, help="Thư mục nhạc (mặc định: GANI_DV_MUSIC_DIR của máy)"
    )
    mc.add_argument(
        "--chi-tiet", action="store_true", help="Thêm bảng từng bài × phong cách × độ dài"
    )
    mc.add_argument("--ca-180", action="store_true", help="Tính cả video 3 phút (chậm hơn)")
    sv = sub.add_parser("serve", help="API web cục bộ 127.0.0.1 cho giao diện (độc lập với daemon)")
    sv.add_argument("--port", type=int, help="Cổng (mặc định theo rules.json api.port: 8765)")
    return ap


def _positive_vnd(text: str) -> float:
    """argparse type: a finite amount above zero (NaN and inf would switch the cap off)."""
    try:
        value = float(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"không phải số: {text!r}") from e
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("phải là số đồng lớn hơn 0")
    return value


def _job_line(j: Job) -> str:
    result = f"/{j.result}" if j.result else ""
    what = (
        "phân tích"
        if j.kind == "analyze"
        else "kịch bản"
        if j.kind == "draft"
        else f"bản {j.draft} ({'cuối' if j.final else 'nháp'})"
    )
    after = f" — sau job #{j.depends_on}" if j.depends_on else ""
    return (f"#{j.id} {what} {j.status}{result} {j.progress_pct:.0f}% — "
            f"{Path(j.project_dir).name}{after}")  # fmt: skip


def _cost_lines(job: Job) -> list[str]:
    """The cost block of a draft job: what it may cost, what was approved, what it spent."""
    if job.kind != "draft":
        return []
    out: list[str] = []
    worst = job.cost_worst_call_vnd
    if worst is not None:
        out.append(
            f"  Ước tính tối đa một lần gọi: {format_vnd(worst)} "
            f"({job.cost_input_tokens or 0} token vào), trần {format_vnd(job.cost_cap_vnd or 0)}"
        )
    if job.status == "awaiting_approval":
        out.append(
            f"  Đang chờ duyệt: queue approve {job.id} --max-vnd <số đồng tối đa> "
            f"--estimate {round(worst or 0)}"
        )
    if job.approved_vnd is not None:
        out.append(f"  Đã duyệt tối đa: {format_vnd(job.approved_vnd)}")
    if job.spent_vnd is not None:
        out.append(f"  Đã tiêu: {format_vnd(job.spent_vnd)}")
    return out


def _daemon_line(db: QueueDb, rules: Rules) -> str:
    st = db.daemon_status(time.time(), float(rules["queue"]["daemon_stale_sec"]))
    if st.online:
        stopping = f", đang dừng ({st.stop_request})" if st.stop_request else ""
        return f"Daemon: đang chạy (pid {st.pid}){stopping}"
    return (
        "Daemon: KHÔNG chạy — job sẽ nằm chờ. Mở cửa sổ khác và chạy: "
        "python -m gani_dung_video daemon"
    )


def _review_line(r: ReviewRow) -> str:
    b = r.block
    label = {"pending": "chờ duyệt", "approved": "đã duyệt",
             "changes_requested": "cần sửa"}[b.status]  # fmt: skip
    late = " — QUÁ HẠN" if r.overdue else ""
    return (
        f"#{r.job.id} {label}{late}  {r.video.name}  "
        f"({Path(r.job.project_dir).name}, bản {r.job.draft}, từ {b.requested_at})"
    )


def _run_review(args: argparse.Namespace) -> int:
    rules = load_rules()
    db = open_queue()
    who = getattr(args, "nguoi_duyet", None) or _os_user()
    if args.rcmd == "list":
        status = None if args.trang_thai == "tat-ca" else args.trang_thai
        since = None
        if args.tu_ngay:
            try:
                since = date.fromisoformat(args.tu_ngay)
            except ValueError:
                raise ConfigError("--tu-ngay phải có dạng YYYY-MM-DD.") from None
        rows = list_reviews(db, rules, status=status, since=since, limit=args.limit)
        lines = [_review_line(r) for r in rows] or ["Không có video nào."]
        for r in rows:
            lines += [f"    chưa đạt: {p}" for p in r.problems]
            if r.block.reason:
                lines.append(f"    lý do: {r.block.reason}")
    elif args.rcmd == "approve":
        r = approve_review(db, rules, args.id, who, args.ghi_chu)
        lines = [f"Đã duyệt #{r.job.id}: {r.video.name} → final/ (người duyệt: {who})."]
    else:
        r = reject_review(db, rules, args.id, who, args.ly_do)
        lines = [f"Đã yêu cầu sửa #{r.job.id}: {r.video.name} ở lại drafts/.",
                 f"Lý do: {r.block.reason}"]  # fmt: skip
    sys.stdout.write(("\n").join(lines) + "\n")
    return 0


def _os_user() -> str:
    try:
        return getpass.getuser() or "cli"
    except Exception:  # no login name available (service account, odd container)
        return "cli"


def _run_queue(args: argparse.Namespace) -> int:
    rules = load_rules()
    db = open_queue()
    cmd = args.qcmd
    if cmd == "add":
        out = enqueue_render(args.file, args.folder, db, ban=args.ban, final=args.final,
                             no_asr=args.no_asr, rules=rules, after=args.after,
                             no_music=args.khong_nhac)  # fmt: skip
        lines = [
            f"Bản {r.draft}: đã có trong hàng đợi (job #{r.job_id})"
            if r.duplicate
            else f"Đã đưa vào hàng đợi: job #{r.job_id} — bản {r.draft}"
            for r in out
        ]
        lines.append(_daemon_line(db, rules))
    elif cmd == "analyze":
        r = enqueue_analyze(args.folder, db, force=args.force, rules=rules)
        lines = [f"Job phân tích #{r.job_id} đã có trong hàng đợi." if r.duplicate
                 else f"Đã đưa vào hàng đợi: job #{r.job_id} — phân tích footage",
                 _daemon_line(db, rules)]  # fmt: skip
    elif cmd == "draft":
        r = enqueue_draft(args.file, args.folder, db, rules=rules, after=args.after)
        lines = [f"Job kịch bản #{r.job_id} đã có trong hàng đợi." if r.duplicate
                 else f"Đã đưa vào hàng đợi: job #{r.job_id} — kịch bản (dừng ở bước chờ duyệt "
                      "chi phí, chưa gọi API)",
                 _daemon_line(db, rules)]  # fmt: skip
    elif cmd == "approve":
        approved = approve_job(db, args.id, args.max_vnd, args.estimate, rules=rules)
        lines = [
            f"Đã duyệt job #{approved.id}: tối đa {format_vnd(args.max_vnd)}. "
            "Daemon sẽ kiểm lại ước tính rồi mới gọi Claude.",
            _daemon_line(db, rules),
        ]
    elif cmd == "list":
        jobs = db.list_jobs(args.status, args.limit)
        lines = [_daemon_line(db, rules), *(_job_line(j) for j in jobs)]
        if not jobs:
            lines.append("Hàng đợi trống.")
    elif cmd == "show":
        job = db.get(args.id)
        if job is None:
            raise ConfigError(f"Không có job #{args.id}.")
        lines = [_job_line(job), f"  Thư mục: {job.project_dir}",
                 f"  Lần thử: {job.attempts}/{job.max_attempts}"]  # fmt: skip
        if job.stage:
            lines.append(f"  Giai đoạn: {job.stage}")
        if job.output_path:
            lines.append(f"  Video: {job.output_path}")
        if job.error_msg:
            lines.append(f"  Lỗi: {job.error_msg}")
        lines += _cost_lines(job)
        lines += [f"  | {e.text}" for e in db.events(job.id)[-20:]]
    elif cmd == "cancel":
        lines = [f"Job #{args.id}: {cancel_job(db, args.id)}"]
    elif cmd == "retry":
        ok = retry_job(db, args.id, rules=rules)
        lines = [
            f"Job #{args.id}: đã đưa vào hàng đợi lại."
            if ok
            else f"Job #{args.id}: không thể thử lại (không có, đang chạy, hoặc đã dựng đạt)."
        ]
    else:  # stop
        sent = db.set_stop_request("now" if args.now else "graceful")
        lines = [
            "Đã gửi yêu cầu dừng cho daemon." if sent else "Hiện không có daemon nào đang chạy."
        ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _run_daemon() -> int:
    rules = load_rules()
    settings = load_settings()
    daemon = Daemon(
        open_queue(settings),
        rules,
        whisper_model=(settings.gani_dv_whisper_model or "").strip() or None,
        music_dir=music_dir(settings),
        ai_key_present=lambda: ai_key_configured(settings),
    )

    def on_signal(signum: int, frame: object) -> None:
        daemon.request_stop()  # the first signal is gentle, the second stops now
        sys.stdout.write("Đang dừng… (nhấn Ctrl+C lần nữa để dừng ngay)\n")

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if (sig := getattr(signal, name, None)) is not None:
            signal.signal(sig, on_signal)
    if (problem := ffmpeg_problem()) is not None:
        sys.stdout.write(f"Cảnh báo: {problem} Job kịch bản sẽ dừng ở 0đ.\n")
    sys.stdout.write("Daemon hàng đợi đang chạy. Ctrl+C: dừng sau khi job đang dựng xong.\n")
    daemon.run()
    sys.stdout.write("Daemon đã dừng.\n")
    return 0


def _run_music_check(folder: Path | None, detail: bool, with_180: bool) -> int:
    """Read-only report of a music folder; nothing is written anywhere."""
    rules = load_rules()
    if folder is None:
        raw = music_dir(load_settings())
        if raw is None:
            logger.error("LỖI: chưa chỉ thư mục nhạc: truyền đường dẫn hoặc đặt GANI_DV_MUSIC_DIR.")
            return 2
        folder = Path(raw).expanduser()
    durations = (*DEFAULT_DURATIONS, 180) if with_180 else DEFAULT_DURATIONS

    def progress(line: str) -> None:
        sys.stderr.write(f"Đang kiểm {line}\n")

    chk = check_music_folder(folder, rules, durations=durations, progress=progress)
    sys.stdout.write(format_report(chk, detail) + "\n")
    return 0


def _run_serve(port: int | None) -> int:
    rules = load_rules()
    settings = load_settings()
    ctx = ApiContext(open_queue(settings), rules, settings)
    server = make_server(ctx, port=port)
    try:
        real_port = int(server.server_address[1])
        sys.stdout.write(f"API đang chạy ở http://127.0.0.1:{real_port} (Ctrl+C để dừng). "
                         "Việc dựng vẫn do `daemon` đảm nhiệm.\n")  # fmt: skip
        ui = dist_root(rules["api"])
        if ui is not None and has_index(ui):
            sys.stdout.write(f"Giao diện: mở http://127.0.0.1:{real_port}/ (bản build trong "
                             f"{ui.parent.name}/{ui.name}).\n")  # fmt: skip
        if (problem := ffmpeg_problem()) is not None:
            sys.stdout.write(f"Cảnh báo: {problem}\n")
        if not ctx.roots:
            sys.stdout.write("Cảnh báo: chưa đặt GANI_DV_ALLOWED_ROOTS trong .env — chưa có dự án "
                             "nào được phép, mọi job sẽ bị từ chối.\n")  # fmt: skip
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            sys.stdout.write("Đã dừng API.\n")
    finally:
        server.server_close()
    return 0


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


def _num(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def _vnd(x: float) -> str:
    return _num(x) + "đ"


def _run_render(
    folder: Path,
    preview: bool,
    draft: str | None,
    asr: bool = True,
    product: str | None = None,
    campaign: str | None = None,
    platform: str = "",
    no_music: bool = False,
) -> int:
    results = build_render(
        folder, draft=draft, preview=preview, asr=asr, product=product, campaign=campaign,
        platform=platform, no_music=no_music,
    )  # fmt: skip
    lines: list[str] = []
    for r in results:
        verdict = "QC: ĐẠT" if r.qc_passed else "QC: KHÔNG ĐẠT — không giao bản này"
        lines.append(f"Bản {r.draft}: {r.video} ({r.duration_sec:g}s) — {verdict}")
        lines += [f"  {n}" for n in r.qc_notes]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if all(r.qc_passed for r in results) else 1


def _run_qc(folder: Path, draft: str, final: bool, asr: bool = True, platform: str = "",
            no_music: bool = False) -> int:  # fmt: skip
    found = recheck(folder, draft, preview=not final, asr=asr, platform=platform,
                    no_music=no_music)  # fmt: skip
    bad = [f for f in found if not f.startswith("WARN")]
    lines = ["QC: ĐẠT" if not bad else "QC: KHÔNG ĐẠT", *[f"  {f}" for f in found]]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if not bad else 1


def _driver_of(folder: Path) -> str | None:
    """Peek at plan.json's driver so `draft` can dispatch without reading it twice."""
    p = folder / EDIT_DIR / "plan.json"
    if not p.is_file():
        return None
    try:
        return Plan.model_validate_json(p.read_text(encoding="utf-8")).driver
    except ValidationError:
        return None


def _run_draft(file: Path, folder: Path, dry_run: bool, as_json: bool = False,
               max_vnd: float | None = None) -> int:  # fmt: skip
    if as_json and not dry_run:
        raise ConfigError("--json chỉ dùng cùng --dry-run.")
    r: DraftsFile | SpeechDraftsResult | DryRun
    try:
        if _driver_of(folder) == "speech":
            r, out = build_speech_drafts(file, folder, dry_run=dry_run, max_vnd=max_vnd)
        else:
            r, out = build_drafts(file, folder, dry_run=dry_run, max_vnd=max_vnd)
    except InfeasibleError as e:  # the footage cannot fill the plan: nothing was spent
        sys.stdout.write(f"KHÔNG ĐẠT: {e}\n")
        return INFEASIBLE_EXIT_CODE
    except (DraftError, SpeechDraftError) as e:
        sys.stdout.write(f"KHÔNG ĐẠT: {e}\n")
        return 1
    if isinstance(r, DryRun) and as_json:
        sys.stdout.write(json.dumps(asdict(r)) + "\n")
        return 0
    if isinstance(r, DryRun):
        lines = [
            f"Ước tính (không gọi API) — model {r.model}",
            f"  Token vào ≈ {_num(r.input_tokens)} → {_vnd(r.input_only_vnd)}",
            f"  Một lần gọi tối đa ≈ {_vnd(r.worst_call_vnd)} (gồm cả giới hạn token ra)",
            f"  Tối đa {r.max_attempts} vòng; trần {_vnd(r.cap_vnd)}",
        ]
    elif isinstance(r, SpeechDraftsResult):
        lines = [
            f"Model {r.model} — {len(r.drafts)} bản nháp sau {r.attempts} vòng",
            f"  Token {_num(r.input_tokens)} vào / {_num(r.output_tokens)} ra — {_vnd(r.cost_vnd)} "
            f"(trần {_vnd(r.cap_vnd)})",
            *[
                f"  Bản {d.draft} ({d.label}): {len(d.cuts)} câu, {d.cuts[-1].t_end:g}s, "
                f"{len(d.broll)} B-roll"
                for d in r.drafts
            ],
            *[f"  Ghi chú {n}" for n in r.notes],
            f"Kết quả: {out}",
        ]
    else:
        lines = [
            f"Model {r.model} — {len(r.drafts)} bản nháp sau {r.attempts} vòng",
            f"  Token {_num(r.input_tokens)} vào / {_num(r.output_tokens)} ra — {_vnd(r.cost_vnd)} "
            f"(trần {_vnd(r.cap_vnd)})",
            *[
                f"  Bản {d.draft} ({d.label}): {len(d.segments)} cảnh, {d.segments[-1].t_end:g}s"
                for d in r.drafts
            ],
            *[f"  Ghi chú {n}" for n in r.notes],
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


def _run_phrases(file: Path, folder: Path) -> int:
    r, out = build_phrases(file, folder)
    by_reason: dict[str, int] = {}
    for x in r.excluded:
        by_reason[x.reason] = by_reason.get(x.reason, 0) + 1
    reasons = ", ".join(f"{k}: {v}" for k, v in sorted(by_reason.items())) or "không có"
    lines = [
        f"Phong cách: {r.style} — {len(r.phrases)} câu dùng được, "
        f"{len(r.excluded)} câu bị loại ({reasons})",
        f"Kết quả: {out}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _run_plan(file: Path, folder: Path, music_dir: Path | None, bpm_override: bool = False) -> int:
    try:
        plan, out = build_plan(file, folder, music_dir, bpm_override=bpm_override)
    except BpmBlockedError as e:
        sys.stderr.write(f"{BPM_BLOCKED_MARKER}{e}\n")
        return BPM_BLOCKED_EXIT_CODE
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
        *[f"  Cảnh báo {w}" for w in plan.warnings if not w.startswith("W_BPM")],
        *[f"  Tự điều chỉnh {a}" for a in plan.adjustments],
        f"Kết quả: {out}",
    ]
    for w in plan.warnings:  # BPM warnings go to stderr: the first thing a person reads
        if w.startswith("W_BPM"):
            sys.stderr.write(f"CẢNH BÁO: {w.split(': ', 1)[-1]}\n")
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
        if args.cmd == "queue":
            return _run_queue(args)
        if args.cmd == "review":
            return _run_review(args)
        if args.cmd == "daemon":
            return _run_daemon()
        if args.cmd == "serve":
            return _run_serve(args.port)
        if args.cmd == "music-check":
            return _run_music_check(args.folder, args.chi_tiet, args.ca_180)
        if args.cmd == "inventory":
            return _run_inventory(args.folder)
        if args.cmd == "transcribe":
            return _run_transcribe(args.folder, args.model)
        if args.cmd == "scenes":
            return _run_scenes(args.folder)
        if args.cmd == "semantics":
            return _run_semantics(args.folder)
        if args.cmd == "qc":
            return _run_qc(args.folder, args.ban, args.final, not args.no_asr, args.nen_tang,
                           args.khong_nhac)  # fmt: skip
        if args.cmd == "render":
            return _run_render(args.folder, args.preview, args.ban, not args.no_asr, args.san_pham,
                               args.chien_dich, args.nen_tang, args.khong_nhac)  # fmt: skip
        if args.cmd == "draft":
            return _run_draft(args.file, args.folder, args.dry_run, args.json, args.max_vnd)
        if args.cmd == "pick":
            return _run_pick(args.file, args.folder)
        if args.cmd == "phrases":
            return _run_phrases(args.file, args.folder)
        if args.cmd == "plan":
            return _run_plan(args.file, args.folder, args.nhac_thu_muc, args.bpm_override)
        return _run_preflight(args.file)
    except (
        ConfigError,
        FfprobeMissingError,
        AsrError,
        SemanticError,
        MusicError,
        SettingsError,
        AiError,
        RenderError,
        ProbeFailed,
        DaemonError,
        QueueDbError,
        ApiServerError,
    ) as e:
        logger.error("LỖI: %s", e)
        return 2
