"""Use case: edit/drafts.json → one video per draft (phase 5a, spec §7.5).

Per draft, in edit/render/<letter>/: the MP4 (540p preview or 1080×1920 final), sub.ass,
phude.srt, cover.jpg and render_log.json (options, music, shots, placeholders) so a video
that performs well can be rebuilt exactly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gani_dung_video.application.draft_service import DraftsFile
from gani_dung_video.application.qc_service import QcReport, check_file, render_checked
from gani_dung_video.domain.edl import Edl, build_edl
from gani_dung_video.domain.footage import Inventory
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.qc import check_captions
from gani_dung_video.domain.subtitles import ass_document, srt_document
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.ffmpeg_render import extract_cover, measure_loudnorm, render
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, INVENTORY_FILE
from gani_dung_video.infrastructure.media_probe import FontMetrics

logger = logging.getLogger(__name__)
RENDER_DIR = "render"


class RenderResult(BaseModel):
    draft: str
    video: Path
    duration_sec: float
    qc_passed: bool
    qc_notes: list[str]  # fixes applied + findings left, Vietnamese


def _read(path: Path, model: type[BaseModel], missing: str) -> Any:
    if not path.is_file():
        raise ConfigError(missing)
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{path.name} hỏng — chạy lại lệnh tạo ra nó.") from e


def _music_file(plan: Plan) -> Path | None:
    if plan.music is None:
        return None
    if not plan.music.path or not Path(plan.music.path).is_file():
        raise ConfigError(f"Không thấy file nhạc '{plan.music.track}' đã chọn ở bước plan — "
                          "chạy lại lệnh `plan`.")  # fmt: skip
    return Path(plan.music.path)


def _log(e: Edl, plan: Plan, drafts: DraftsFile, letter: str, label: str, video: Path,
         rules: Rules) -> dict[str, Any]:  # fmt: skip
    brand = rules["render"]["brand"]
    return {
        "time": datetime.now(UTC).isoformat(timespec="seconds"), "draft": letter, "label": label,
        "video": video.name, "rules_version": plan.rules_version, "options": plan.options,
        "music": plan.music.model_dump(exclude={"path"}) if plan.music else None,
        "ai_model": drafts.model, "ai_cost_vnd": drafts.cost_vnd,
        "brand_placeholders": [k for k in ("logo", "zalo") if brand[f"{k}_placeholder"]],
        "shots": [s.model_dump() for s in e.shots],
        "captions": [c.model_dump() for c in e.captions],
    }  # fmt: skip


def _video_name(letter: str, preview: bool) -> str:
    return f"nhap_{letter}_540p.mp4" if preview else f"video_{letter}_1080x1920.mp4"


def _inputs(folder: Path, rules: Rules) -> tuple[DraftsFile, Plan, Inventory]:
    edit = folder / EDIT_DIR
    drafts: DraftsFile = _read(edit / "drafts.json", DraftsFile,
                               "Chưa có edit/drafts.json — chạy lệnh `draft` trước.")  # fmt: skip
    plan: Plan = _read(edit / "plan.json", Plan, "Chưa có edit/plan.json — chạy lệnh `plan`.")
    inv: Inventory = _read(edit / INVENTORY_FILE, Inventory,
                           "Chưa có edit/inventory.json — chạy lệnh `inventory`.")  # fmt: skip
    return drafts, plan, inv


def recheck(folder: Path, draft: str, preview: bool = True, rules: Rules | None = None
            ) -> list[str]:  # fmt: skip
    """Re-run T1–T6 on an existing video (no re-render). Returns findings, Vietnamese.

    Raises:
        ConfigError: inputs missing or the video was not rendered yet.
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    drafts, plan, inv = _inputs(folder, rules)
    d = next((x for x in drafts.drafts if x.draft == draft), None)
    if d is None:
        raise ConfigError(f"Không có bản nháp '{draft}' trong drafts.json.")
    video = folder / EDIT_DIR / RENDER_DIR / draft / _video_name(draft, preview)
    if not video.is_file():
        raise ConfigError(f"Chưa có {video.name} — chạy lệnh `render` trước.")
    e = build_edl(d, plan, {c.file: c for c in inv.clips}, rules, preview=preview)
    report = folder / EDIT_DIR / RENDER_DIR / draft / "qc_report.json"
    px = {k: int(v) for k, v in rules["render"]["font_px"].items() if k in ("caption", "cta")}
    if report.is_file():  # sizes the render actually used (may be lowered by T7)
        px = {**px, **QcReport.model_validate_json(report.read_text(encoding="utf-8")).font_px}
    found = check_captions(e, rules, px, FontMetrics().width) + check_file(video, e, rules)
    return [f"{f.level.upper()} {f.code}: {f.msg}" for f in found]


def build_render(
    folder: Path, draft: str | None = None, preview: bool = True, rules: Rules | None = None
) -> list[RenderResult]:
    """Render every draft (or only `draft`) of <folder>/edit/drafts.json.

    Raises:
        ConfigError: drafts/plan/inventory missing or corrupt, unknown draft, music gone.
        RenderError: ffmpeg failed.
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    edit = folder / EDIT_DIR
    drafts, plan, inv = _inputs(folder, rules)
    chosen = [d for d in drafts.drafts if draft is None or d.draft == draft]
    if not chosen:
        have = ", ".join(d.draft for d in drafts.drafts)
        raise ConfigError(f"Không có bản nháp '{draft}'; drafts.json có: {have}.")
    music = _music_file(plan)
    clips = {c.file: c for c in inv.clips}
    metrics = FontMetrics()
    results = []
    for d in chosen:
        e = build_edl(d, plan, clips, rules, preview=preview)
        out = edit / RENDER_DIR / d.draft
        out.mkdir(parents=True, exist_ok=True)
        ass, video = out / "sub.ass", out / _video_name(d.draft, preview)
        (out / "phude.srt").write_text(srt_document(e), encoding="utf-8")

        def do_render(font_px: Mapping[str, int], loud: Mapping[str, float] | None,
                      e: Edl = e, ass: Path = ass, video: Path = video) -> None:  # fmt: skip
            ass.write_text(ass_document(e, rules, font_px), encoding="utf-8")
            logger.info("Đang dựng bản %s (%s)…", video.parent.name, video.name)
            render(e, folder, music, ass, video, rules, loud)

        def pass1(e: Edl = e, out: Path = out) -> Mapping[str, float]:
            return measure_loudnorm(e, folder, music, out, rules)

        rep = render_checked(e, video, rules, do_render, pass1, metrics.width)
        (out / "qc_report.json").write_text(rep.model_dump_json(indent=1), encoding="utf-8")
        if rep.rounds:
            cover_at = e.captions[0].start + 0.3 if e.captions else 0.5
            extract_cover(video, min(cover_at, e.content_end), out / "cover.jpg")
            log = _log(e, plan, drafts, d.draft, d.label, video, rules)
            log["qc"] = {"passed": rep.passed, "rounds": rep.rounds, "fixes": rep.fixes,
                         "findings": [f.model_dump() for f in rep.findings]}  # fmt: skip
            (out / "render_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")  # fmt: skip
        results.append(RenderResult(draft=d.draft, video=video, duration_sec=e.duration,
                                    qc_passed=rep.passed,
                                    qc_notes=[*rep.fixes, *(f"{f.code}: {f.msg}"
                                                            for f in rep.findings)]))  # fmt: skip
    return results
