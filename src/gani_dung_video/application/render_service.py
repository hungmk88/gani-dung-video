"""Use case: edit/drafts.json → one video per draft (phase 5a, spec §7.5).

Per draft, in edit/render/<letter>/: the MP4 (540p preview or 1080×1920 final), sub.ass,
phude.srt, cover.jpg and render_log.json (options, music, shots, placeholders) so a video
that performs well can be rebuilt exactly.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gani_dung_video.application.delivery_service import DeliveryTarget, apply_e1, deliver
from gani_dung_video.application.draft_service import DraftsFile
from gani_dung_video.application.qc_service import (
    QcReport,
    check_file,
    listen_speech_asr,
    render_checked,
)
from gani_dung_video.domain.asr_check import T8Measures
from gani_dung_video.domain.compliance import check_edl_claims
from gani_dung_video.domain.edl import Edl, SpeechDraft, build_edl, build_speech_edl
from gani_dung_video.domain.footage import Inventory
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.platforms import PlatformError, apply_platform, render_dir_name
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.qc import Finding, check_captions, fit_keys
from gani_dung_video.domain.subtitles import ass_document, srt_document
from gani_dung_video.infrastructure.config_files import ConfigError, load_glossary, load_rules
from gani_dung_video.infrastructure.ffmpeg_render import (
    extract_cover,
    measure_loudnorm,
    render,
    render_voice_only,
)
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, INVENTORY_FILE
from gani_dung_video.infrastructure.media_probe import FontMetrics
from gani_dung_video.infrastructure.whisper_asr import Transcriber, load_transcriber

logger = logging.getLogger(__name__)
RENDER_DIR = "render"
# (edl, finished video, work dir) → findings and what Whisper measured
T8Hook = Callable[[Edl, Path, Path], tuple[list[Finding], T8Measures | None]]


class RenderResult(BaseModel):
    draft: str
    video: Path
    duration_sec: float
    qc_passed: bool
    qc_notes: list[str]  # fixes applied + findings left, Vietnamese


class SpeechDraftsFile(BaseModel):
    """Content of edit/speech_drafts.json (speech-driven styles, spec Insight 4 "LỜI").

    5c-1: hand-assembled from phrases.json to test the render pipeline before Claude chooses
    phrases; 5c-2 will have Claude write this file the way draft_service writes drafts.json.
    """

    model: str
    cost_vnd: float = 0.0
    drafts: list[SpeechDraft]


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


def _log(e: Edl, plan: Plan, model: str, cost_vnd: float, letter: str, label: str, video: Path,
         rules: Rules) -> dict[str, Any]:  # fmt: skip
    brand = rules["render"]["brand"]
    return {
        "time": datetime.now(UTC).isoformat(timespec="seconds"), "draft": letter, "label": label,
        "platform": e.platform, "no_music": e.no_music,
        "video": video.name, "rules_version": plan.rules_version, "options": plan.options,
        "music": plan.music.model_dump(exclude={"path"}) if plan.music and not e.no_music
        else None,
        "ai_model": model, "ai_cost_vnd": cost_vnd,
        "brand_placeholders": [k for k in ("logo", "zalo") if brand[f"{k}_placeholder"]],
        "shots": [s.model_dump() for s in e.shots],
        "captions": [c.model_dump() for c in e.captions],
    }  # fmt: skip


def _video_name(letter: str, preview: bool) -> str:
    return f"nhap_{letter}_540p.mp4" if preview else f"video_{letter}_1080x1920.mp4"


def _read_plan_and_inventory(folder: Path, rules: Rules) -> tuple[Plan, Inventory]:
    edit = folder / EDIT_DIR
    plan: Plan = _read(edit / "plan.json", Plan, "Chưa có edit/plan.json — chạy lệnh `plan`.")
    inv: Inventory = _read(edit / INVENTORY_FILE, Inventory,
                           "Chưa có edit/inventory.json — chạy lệnh `inventory`.")  # fmt: skip
    return plan, inv


def _t8_hook(folder: Path, rules: Rules, transcribe: Transcriber | None) -> T8Hook:
    """T8 (5c-3): Whisper listens to the finished speech-mode video. The model loads on first
    use only, and is reused for the music-free listen that names a masking problem."""
    cfg, model = rules["speech"], str(rules["qc"]["t8"]["model"])  # own key: Dell "small"
    terms = load_glossary().term_objects()
    loaded: list[Transcriber] = []

    def listen(path: Path) -> Any:
        if not loaded:
            loaded.append(transcribe or load_transcriber(model, cfg))
        return loaded[0](path)

    def hook(e: Edl, video: Path, work: Path) -> tuple[list[Finding], T8Measures | None]:
        return listen_speech_asr(
            video,
            e,
            rules,
            listen,
            terms,
            voice_only=lambda: render_voice_only(e, folder, work, rules),
        )

    return hook


_NO_SPEECH_DRAFTS = ("Chưa có edit/speech_drafts.json — dựng ghép tay (5c-1) hoặc chờ Claude "
                    "ở bước 5c-2.")  # fmt: skip


def recheck(folder: Path, draft: str, preview: bool = True, rules: Rules | None = None,
            asr: bool = True, transcribe: Transcriber | None = None,
            platform: str = "", no_music: bool = False) -> list[str]:  # fmt: skip
    """Re-run T1–T6 on an existing video (no re-render), plus T8 (Whisper) on a final
    speech-mode video unless `asr` is False. `platform` and `no_music` say how the video was
    rendered (as for build_render). Returns findings, Vietnamese.

    Raises:
        ConfigError: inputs missing or the video was not rendered yet.
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    plan, inv = _read_plan_and_inventory(folder, rules)
    clips = {c.file: c for c in inv.clips}
    edit = folder / EDIT_DIR
    if plan.driver == "speech":
        sd: SpeechDraftsFile = _read(edit / "speech_drafts.json", SpeechDraftsFile,
                                     _NO_SPEECH_DRAFTS)  # fmt: skip
        sdraft = next((x for x in sd.drafts if x.draft == draft), None)
        if sdraft is None:
            raise ConfigError(f"Không có bản nháp '{draft}' trong speech_drafts.json.")
        e = build_speech_edl(sdraft.cuts, sdraft.broll, plan, clips, rules, preview=preview)
    else:
        no_drafts = "Chưa có edit/drafts.json — chạy lệnh `draft` trước."
        drafts: DraftsFile = _read(edit / "drafts.json", DraftsFile, no_drafts)
        d = next((x for x in drafts.drafts if x.draft == draft), None)
        if d is None:
            raise ConfigError(f"Không có bản nháp '{draft}' trong drafts.json.")
        e = build_edl(d, plan, clips, rules, preview=preview)
    e = _as_posted(e, plan, rules, platform, no_music)
    out = folder / EDIT_DIR / RENDER_DIR / render_dir_name(draft, platform)
    video = out / _video_name(draft, preview)
    if not video.is_file():
        raise ConfigError(f"Chưa có {video.name} — chạy lệnh `render` trước.")
    report = out / "qc_report.json"
    px = {k: int(v) for k, v in rules["render"]["font_px"].items() if k in fit_keys(e)}
    if report.is_file():  # sizes the render actually used (may be lowered by T7)
        px = {**px, **QcReport.model_validate_json(report.read_text(encoding="utf-8")).font_px}
    found = check_captions(e, rules, px, FontMetrics().width) + check_file(video, e, rules)
    if asr and not preview and e.mode == "speech":
        found += _t8_hook(folder, rules, transcribe)(e, video, video.parent)[0]
    return [f"{f.level.upper()} {f.code}: {f.msg}" for f in found]


def _as_posted(e: Edl, plan: Plan, rules: Rules, platform: str, no_music: bool) -> Edl:
    """The EDL as posted on `platform` (CTA, end card), "" = unchanged; with `no_music` the
    plan's music is left out (cuts and lengths stay as planned, music is added in the app).

    Raises:
        ConfigError: unknown platform or unusable profile in rules.json.
    """
    try:
        e = apply_platform(e, rules, str(plan.options["purpose"]), platform)
    except PlatformError as err:
        raise ConfigError(str(err)) from err
    return e.model_copy(update={"music": None, "no_music": True}) if no_music else e


def _claims_gate(e: Edl) -> Edl:
    """C1 right before ffmpeg: every text of the video (on-screen lines, chapter titles, CTA,
    spoken words, highlight keywords) is checked against banned_claims. The approved highlight
    keywords are attached to the EDL here, so what is checked is exactly what gets drawn.

    Raises:
        ConfigError: a text holds a banned claim; names the source, the text and the phrase.
    """
    g = load_glossary()
    e = e.model_copy(update={"emphasis": list(g.caption_highlight_keywords)})
    hits = check_edl_claims(e, g.banned_claims)
    if hits:
        found = "; ".join(f"{h.source}: '{h.text}' chứa cụm cấm '{h.phrase}'" for h in hits)
        raise ConfigError(f"C1: không dựng vì có chữ vi phạm. {found}. Sửa chữ này ở bản nháp "
                          "hoặc bỏ cụm khỏi glossary.json nếu chủ đã duyệt.")  # fmt: skip
    return e


def _render_one(
    e: Edl,
    letter: str,
    label: str,
    model: str,
    cost_vnd: float,
    folder: Path,
    edit: Path,
    music: Path | None,
    rules: Rules,
    preview: bool,
    metrics: FontMetrics,
    plan: Plan,
    t8: T8Hook | None = None,
    target: DeliveryTarget | None = None,
) -> RenderResult:
    e = _claims_gate(e)  # before any file is written or ffmpeg started
    out = edit / RENDER_DIR / render_dir_name(letter, e.platform)
    out.mkdir(parents=True, exist_ok=True)
    ass, video = out / "sub.ass", out / _video_name(letter, preview)
    (out / "phude.srt").write_text(srt_document(e), encoding="utf-8")

    def do_render(font_px: Mapping[str, int], loud: Mapping[str, float] | None,
                  true_peak_db: float | None = None, *, e: Edl = e, ass: Path = ass,
                  video: Path = video) -> None:  # fmt: skip
        ass.write_text(ass_document(e, rules, font_px), encoding="utf-8")
        logger.info("Đang dựng bản %s (%s)…", video.parent.name, video.name)
        if true_peak_db is not None:  # 7d: the encoder added to the peak, limit the mix lower
            e = e.model_copy(update={"true_peak_db": true_peak_db})
        render(e, folder, music, ass, video, rules, loud)

    def pass1(e: Edl = e, out: Path = out) -> Mapping[str, float]:
        return measure_loudnorm(e, folder, music, out, rules)

    started = time.monotonic()
    rep = render_checked(e, video, rules, do_render, pass1, metrics.width)
    if t8 is not None and rep.rounds:  # the file exists: Whisper listens to it (5c-3)
        heard, measured = t8(e, video, out)
        blocked = any(f.level in ("error", "fix") for f in heard)
        rep = rep.model_copy(update={"findings": [*rep.findings, *heard], "t8": measured,
                                     "passed": rep.passed and not blocked})  # fmt: skip
    render_sec = time.monotonic() - started
    glossary = load_glossary()
    if rep.rounds:  # E1 (T-E): numbers on the finished file; a failed check blocks delivery
        rep, e1 = apply_e1(rep, e, plan, video, rules, metrics, glossary)
    (out / "qc_report.json").write_text(rep.model_dump_json(indent=1), encoding="utf-8")
    if rep.rounds:
        cover_at = e.captions[0].start + 0.3 if e.captions else 0.5
        extract_cover(video, min(cover_at, e.content_end), out / "cover.jpg")
        log = _log(e, plan, model, cost_vnd, letter, label, video, rules)
        log["qc"] = {"passed": rep.passed, "rounds": rep.rounds, "fixes": rep.fixes,
                     "findings": [f.model_dump() for f in rep.findings]}  # fmt: skip
        (out / "render_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1),
                                             encoding="utf-8")  # fmt: skip
        deliver(
            e=e,
            plan=plan,
            report=e1,
            passed=rep.passed,
            folder=folder,
            out=out,
            rules=rules,
            glossary=glossary,
            letter=letter,
            label=label,
            model=model,
            preview=preview,
            video=video,
            render_sec=render_sec,
            target=target or DeliveryTarget(),
        )
    return RenderResult(draft=letter, video=video, duration_sec=e.duration, qc_passed=rep.passed,
                        qc_notes=[*rep.fixes, *(f"{f.code}: {f.msg}"
                                                for f in rep.findings)])  # fmt: skip


def build_render(
    folder: Path, draft: str | None = None, preview: bool = True, rules: Rules | None = None,
    asr: bool = True, transcribe: Transcriber | None = None, product: str | None = None,
    campaign: str | None = None, platform: str = "", no_music: bool = False,
) -> list[RenderResult]:  # fmt: skip
    """Render every draft (or only `draft`) of the style's drafts file.

    Speech-driven styles (spec Insight 4 "LỜI") read edit/speech_drafts.json — the A-roll
    follows the voice, B-roll overlays and ducking come from build_speech_edl; everything
    else reads edit/drafts.json (beat-driven, build_edl). A final (not preview) speech-mode
    video is also listened to by Whisper (T8, phase 5c-3) unless `asr` is False.

    Every video gets E1 checks and a manifest.json; one that passes is also copied to
    edit/delivery/<campaign>/drafts under its E3 name (T-E). `product` and `campaign` only
    feed that name and folder (defaults: rules' default product, the footage folder's name).
    `platform` (rules.json → platforms, e.g. "tiktok") sets the CTA, the end card, the render
    folder edit/render/<draft>-<platform> and the platform part of the delivered name; "" =
    no profile (the behaviour from before 16/10/2026). `no_music` leaves the plan's music out
    (the video keeps the voice / camera sound; music is added in the TikTok / Facebook app), and
    the delivered name gets rules.delivery.naming.no_music_tag.

    Raises:
        ConfigError: drafts/plan/inventory missing or corrupt, unknown draft, music gone.
        RenderError: ffmpeg failed.
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    edit = folder / EDIT_DIR
    plan, inv = _read_plan_and_inventory(folder, rules)
    music = None if no_music else _music_file(plan)  # a missing track does not matter then
    clips = {c.file: c for c in inv.clips}
    metrics = FontMetrics()
    target = DeliveryTarget(product=product, campaign=campaign)
    if plan.driver == "speech":
        t8 = _t8_hook(folder, rules, transcribe) if asr and not preview else None
        sd: SpeechDraftsFile = _read(edit / "speech_drafts.json", SpeechDraftsFile,
                                     _NO_SPEECH_DRAFTS)  # fmt: skip
        speech_chosen = [x for x in sd.drafts if draft is None or x.draft == draft]
        if not speech_chosen:
            have = ", ".join(x.draft for x in sd.drafts)
            raise ConfigError(f"Không có bản nháp '{draft}'; speech_drafts.json có: {have}.")
        return [
            _render_one(_as_posted(build_speech_edl(x.cuts, x.broll, plan, clips, rules,
                                                    preview=preview), plan, rules, platform,
                                   no_music),
                       x.draft, x.label, sd.model, sd.cost_vnd, folder, edit, music, rules,
                       preview, metrics, plan, t8, target)
            for x in speech_chosen
        ]  # fmt: skip
    no_drafts = "Chưa có edit/drafts.json — chạy lệnh `draft` trước."
    drafts: DraftsFile = _read(edit / "drafts.json", DraftsFile, no_drafts)
    chosen = [x for x in drafts.drafts if draft is None or x.draft == draft]
    if not chosen:
        have = ", ".join(x.draft for x in drafts.drafts)
        raise ConfigError(f"Không có bản nháp '{draft}'; drafts.json có: {have}.")
    return [
        _render_one(_as_posted(build_edl(x, plan, clips, rules, preview=preview), plan, rules,
                               platform, no_music), x.draft, x.label,
                   drafts.model, drafts.cost_vnd, folder, edit, music, rules, preview, metrics,
                   plan, None, target)
        for x in chosen
    ]  # fmt: skip
