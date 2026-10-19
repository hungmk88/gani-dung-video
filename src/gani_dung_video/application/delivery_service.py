"""Use case: after a render — E1 checks, manifest.json, delivery tree (Dev Package 03, T-E).

E1 (domain/delivery_checks.py) judges the finished file by numbers; a failed check stops
delivery like any QC error. E2/E4 write edit/render/<draft>/manifest.json: how the video was
made and what it cost. A video that passed is also copied under its E3 name into
edit/delivery/<campaign>/drafts with the manifest in metadata. A FINAL video waits there as
`review: pending`; `application/review_service.py` moves it to `final/` when a person approves
(T-F). `raw/` is for the original footage: nothing is moved there here.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from gani_dung_video.application.qc_service import QcReport
from gani_dung_video.domain.delivery_checks import (
    E1Item,
    E1Report,
    build_e1,
    check_duration,
    check_duration_gap,
    check_glyphs,
    check_onscreen_claims,
    check_platform_words,
    check_safe_zone,
    duration_deviations,
)
from gani_dung_video.domain.delivery_naming import NameParts, next_version_name, product_of, slug
from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.manifest import CostFacts, Manifest, ai_cost, sha256_of
from gani_dung_video.domain.plan import Plan, natural_sec, planned_sec
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.qc import Finding
from gani_dung_video.domain.review import ReviewBlock, pending_block, stamp
from gani_dung_video.infrastructure.atomic_file import write_text_atomic
from gani_dung_video.infrastructure.config_files import GlossaryFile
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.media_probe import FontMetrics, probe_duration

logger = logging.getLogger(__name__)
AI_LOG = "ai_log.jsonl"
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class DeliveryTarget:
    """Who the video is for (render --san-pham / --chien-dich; the queue fills them from the
    option file's san_pham / chien_dich). Both are optional: a missing product becomes
    rules.delivery.naming.default_product and a missing campaign the name of the footage
    folder. `day` is for tests (default: today)."""

    product: str | None = None
    campaign: str | None = None
    day: date | None = None


def _brand_texts(e: Edl, rules: Rules) -> list[tuple[str, str]]:
    """(text, font family) of the text logo and the end card, which are drawn on every video."""
    r = rules["render"]
    brand = r["brand"]
    texts = [(str(brand["footer"]), e.font)]
    if e.end_card_zalo:
        texts.insert(0, (str(brand["zalo"]), e.font))
    if brand.get("logo_image"):  # a picture: no glyph to check
        return texts
    return [(str(brand["logo_text"]), str(r["fonts"]["default"])), *texts]


def apply_e1(
    rep: QcReport, e: Edl, plan: Plan, video: Path, rules: Rules, metrics: FontMetrics,
    glossary: GlossaryFile,
) -> tuple[QcReport, E1Report]:  # fmt: skip
    """Run E1 on the rendered `video` and fold its blocking findings into the QC report
    (`passed` turns False when any check failed)."""
    file_sec = probe_duration(video, float(rules["qc"]["probe_timeout_sec"]))
    planned = planned_sec(plan)
    chosen = int(plan.options["duration"])
    dur, f_dur = check_duration(file_sec, e, chosen, planned, rules)
    # The gap guard looks at the beats' own length: a cut to the chosen length must not hide a
    # plan that the tempo stretched far past what the user asked for.
    gap, f_gap = check_duration_gap(chosen, natural_sec(plan), rules)
    safe, f_safe = check_safe_zone(e, rules, rep.font_px, metrics.width)
    glyph, f_glyph = check_glyphs(e, _brand_texts(e, rules), metrics.has_glyph)
    claims, f_claims = check_onscreen_claims(e, glossary.banned_claims)
    words, f_words = check_platform_words(e, glossary.platform_risk_words.get(e.platform, []))
    checks: dict[str, E1Item] = {"E1-DUR": dur, "E1-DUR-GAP": gap, "E1-SAFE": safe,
                                 "E1-GLYPH": glyph, "C1": claims}  # fmt: skip
    if e.platform:  # a render without a profile keeps the E1 list it had before 14/10/2026
        checks["E1-NEN-TANG"] = words
    report = build_e1(e, rules, rep.findings, rep.lufs, rep.true_peak_db, checks)
    vs_chosen, vs_plan = duration_deviations(file_sec, e, chosen, planned)
    report = report.model_copy(update={"deviation_vs_chosen": vs_chosen,
                                       "deviation_vs_plan": vs_plan})  # fmt: skip
    stop: list[Finding] = [*f_dur, *f_gap, *f_safe, *f_glyph, *f_claims, *f_words]  # words: warn
    updated = rep.model_copy(update={"findings": [*rep.findings, *stop],
                                     "passed": rep.passed and report.passed})  # fmt: skip
    return updated, report


def _canonical_sha(data: object) -> str:
    return sha256_of(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _ai_entries(folder: Path) -> list[dict[str, Any]]:  # Any: free-form JSON log lines
    path = folder / EDIT_DIR / AI_LOG
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a half-written last line must not stop a delivery
        if isinstance(row, dict):
            out.append(row)
    return out


def _sources(e: Edl) -> list[str]:
    files = {s.source for s in [*e.shots, *e.overlays]}
    if e.music is not None:
        files.add(e.music.track)
    return sorted(files)


def initial_review(
    delivered_as: str | None, preview: bool, requested_at: str
) -> ReviewBlock | None:
    """A delivered final video starts `pending`. A preview (540p draft) is never reviewed, and a
    blocked video was not delivered, so neither gets a block."""
    if delivered_as is None or preview:
        return None
    return pending_block(requested_at)


def build_manifest(
    *, e: Edl, plan: Plan, report: E1Report, folder: Path, rules: Rules, glossary: GlossaryFile,
    letter: str, label: str, model: str, preview: bool, video: Path, delivered_as: str | None,
    render_sec: float, warnings: list[str],
) -> Manifest:  # fmt: skip
    """E2 + E4: everything needed to trace the video and rebuild it, with the real bill."""
    plan_bytes = (folder / EDIT_DIR / "plan.json").read_bytes()
    mode = "speech" if plan.driver == "speech" else "music"
    cost = CostFacts(**ai_cost(_ai_entries(folder), mode, letter), render_sec=round(render_sec, 1))
    listened = e.mode == "speech" and not preview  # T8 ran: Whisper was a model of this video
    made = datetime.now(UTC)
    return Manifest(
        created_at=made.isoformat(timespec="seconds"), video=video.name, platform=e.platform,
        no_music=e.no_music,
        delivered_as=delivered_as, draft=letter, label=label, resolution=f"{e.width}x{e.height}",
        deviation_vs_chosen=report.deviation_vs_chosen, deviation_vs_plan=report.deviation_vs_plan,
        options=plan.options, plan_sha256=sha256_of(plan_bytes),
        rules_version=str(rules["rules_version"]), rules_sha256=_canonical_sha(rules),
        glossary_sha256=_canonical_sha(glossary.model_dump(mode="json")),
        models={"ai": model, "asr": str(rules["qc"]["t8"]["model"]) if listened else None},
        source_files=_sources(e), e1=report, cost=cost, warnings=warnings,
        review=initial_review(delivered_as, preview, stamp(made)),
    )  # fmt: skip


def _today(target: DeliveryTarget) -> date:
    return target.day or datetime.now().astimezone().date()


def export_name(
    e: Edl, plan: Plan, folder: Path, rules: Rules, target: DeliveryTarget
) -> tuple[Path, str, list[str]]:  # fmt: skip
    """Make the campaign tree and pick the next free E3 name in its drafts folder.

    Returns:
        (campaign folder, file name, warnings such as a product outside the approved list).
    """
    campaign = slug(target.campaign or "") or slug(folder.name) or "chien-dich"
    root = folder / EDIT_DIR / str(rules["delivery"]["root_dir"]) / campaign
    for d in rules["delivery"]["dirs"]:
        (root / str(d)).mkdir(parents=True, exist_ok=True)
    product, warning = product_of(target.product, rules)
    opts = plan.options
    parts = NameParts(product=product, style=str(opts["style"]), purpose=str(opts["purpose"]),
                      duration_sec=int(opts["duration"]), day=_today(target),
                      platform=e.platform, no_music=e.no_music)  # fmt: skip
    # `final/` counts too: an approved video leaves `drafts/`, and its version number must not
    # be handed out again (the next video would overwrite it when it is approved).
    taken = {p.name for sub_dir in ("drafts", "final") for p in (root / sub_dir).glob("*.mp4")}
    name, _ = next_version_name(parts, taken, rules)
    return root, name, [warning] if warning else []


def write_manifest(manifest: Manifest, out_dir: Path, delivered: tuple[Path, str] | None) -> None:
    """manifest.json beside the working video; a delivered video also gets it in metadata."""
    text = manifest.model_dump_json(indent=1)
    write_text_atomic(out_dir / MANIFEST, text)
    if delivered is not None:
        root, name = delivered
        write_text_atomic(root / "metadata" / f"{Path(name).stem}.manifest.json", text)


def copy_to_drafts(video: Path, root: Path, name: str) -> None:
    """A real copy, not a link: the next render overwrites the working file in place."""
    shutil.copy2(video, root / "drafts" / name)


def deliver(
    *, e: Edl, plan: Plan, report: E1Report, passed: bool, folder: Path, out: Path, rules: Rules,
    glossary: GlossaryFile, letter: str, label: str, model: str, preview: bool, video: Path,
    render_sec: float, target: DeliveryTarget,
) -> Manifest:  # fmt: skip
    """Write the manifest and, when the video passed every check, copy it into the campaign
    tree under its E3 name. A blocked video keeps its manifest (the checks that failed are in
    it) but is not delivered."""
    delivered: tuple[Path, str] | None = None
    warnings: list[str] = []
    if passed:
        root, name, warnings = export_name(e, plan, folder, rules, target)
        delivered = (root, name)
    manifest = build_manifest(e=e, plan=plan, report=report, folder=folder, rules=rules,
                              glossary=glossary, letter=letter, label=label, model=model,
                              preview=preview, video=video,
                              delivered_as=delivered[1] if delivered else None,
                              render_sec=render_sec, warnings=warnings)  # fmt: skip
    write_manifest(manifest, out, delivered)
    if delivered is not None:
        copy_to_drafts(video, *delivered)
        logger.info("Đã giao %s → %s", video.name, delivered[1])
    return manifest
