"""Use case: options file + scenes.json + music → edit/plan.json (phase 4a).

Order matters: preflight first (block before touching audio), then the track is resolved
and measured, then preflight runs again with the music facts (X12/X13), then the plan is
compiled. Preflight warnings and adjustments are copied into the plan so one file tells
the whole story.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gani_dung_video.application.transcribe_service import read_speech_fact
from gani_dung_video.domain.bpm_measure import blocked_text, level_of_plan
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import MusicInfo, Plan, PlanError, compile_plan
from gani_dung_video.domain.preflight import Issue, Result, Rules, preflight_check
from gani_dung_video.domain.scenes import ScenesFile
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules, read_options_file
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR
from gani_dung_video.infrastructure.music_analysis import (
    read_library,
    resolve_music_path,
    track_info,
)

SCENES_FILE = "scenes.json"
PLAN_FILE = "plan.json"


class PreflightBlocked(Exception):
    """Preflight found a BLOCK issue; `result` is the full preflight result."""

    def __init__(self, result: Result) -> None:
        super().__init__(result["error_msg"])
        self.result = result


def _read_scenes(folder: Path, rules: Rules) -> ScenesFile:
    p = folder / EDIT_DIR / SCENES_FILE
    if not p.is_file():
        raise ConfigError(rules["input_msgs"]["PLAN_NO_SCENES"])
    try:
        return ScenesFile.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{SCENES_FILE} hỏng — chạy lại lệnh `scenes` và `semantics`.") from e


def _available_tags(scenes: ScenesFile, style: str) -> set[str]:
    """CLIP labels present on usable (non-blurry, non-"khac") segments of the style."""
    return {
        lb
        for sc in scenes.scenes if not sc.blurry
        for seg in sc.segments.get(style, []) if not seg.excluded
        for lb in seg.labels
    }  # fmt: skip


def _text(issues: list[Issue]) -> list[str]:
    return [f"{i['code']}: {i['msg']}" for i in issues]


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _music(
    o: VideoOptions, options_file: Path, folder: Path, music_dir: Path | None, rules: Rules
) -> MusicInfo | None:
    if o.nhac == "khong_nhac":
        return None
    if not o.nhac_file:
        raise PlanError("PLAN_NO_TRACK", rules["input_msgs"]["PLAN_NO_TRACK"].format(mode=o.nhac))
    cfg = rules["music_analysis"]
    allowed = [options_file.parent, folder] + ([music_dir] if music_dir else [])
    path = resolve_music_path(o.nhac_file, allowed, cfg["extensions"])
    info = track_info(path, read_library(path.parent), cfg)
    return info.model_copy(update={"path": str(path)}) if info else None


class BpmBlockedError(RuntimeError):
    """The track's tempo cannot build this video and the person did not choose to go on."""


def build_plan(
    options_file: Path,
    folder: Path,
    music_dir: Path | None = None,
    rules: Rules | None = None,
    bpm_override: bool = False,
) -> tuple[Plan, Path]:
    """Compile <options_file> into <folder>/edit/plan.json.

    Args:
        music_dir: extra folder the track may live in (besides the options file's folder and
            the footage folder).
        bpm_override: go on although the track's BPM cannot build the video (the options file
            can say the same with `bpm_override: true`). E1-DUR-GAP still judges the render.

    Raises:
        PreflightBlocked: a BLOCK rule fired (nothing is written).
        PlanError: the combo is not supported yet, or no track was named.
        ConfigError: options/scenes.json unreadable or missing.
        MusicError: the track is outside the allowed folders or not decodable audio.
        BpmBlockedError: the plan is more than ±duration_plan_gap_pct off the chosen length
            and nobody chose to go on (nothing is written; the message says what to do).
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    options_file = options_file.expanduser().resolve()
    scenes = _read_scenes(folder, rules)
    raw = read_options_file(options_file)
    facts: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
    style = facts.get("phong_cach")
    if style in scenes.usable_by_style:
        facts["so_canh_dung_duoc"] = scenes.usable_by_style[style]
    facts["co_loi_noi"] = read_speech_fact(folder)
    if bpm_override:
        facts["bpm_override"] = True
    first = preflight_check(facts if facts else raw, rules)
    if not first["valid"]:
        raise PreflightBlocked(first)
    o = VideoOptions.model_validate(facts)
    if o.so_sanh:  # split screen: both clips must be in this footage folder
        known = {s.clip for s in scenes.scenes}
        missing = [c for c in (o.so_sanh.tren, o.so_sanh.duoi) if c not in known]
        if missing:
            msg = rules["input_msgs"]["PLAN_COMPARE_CLIP"]
            raise ConfigError(msg.format(clips=", ".join(missing)))
    music = _music(o, options_file, folder, music_dir, rules)
    if music is not None:
        facts["nhac_bpm"], facts["nhac_giay"] = music.bpm, music.duration_sec
    final = preflight_check(facts, rules)
    if not final["valid"]:
        raise PreflightBlocked(final)
    plan = compile_plan(o, rules, music=music, available_tags=_available_tags(scenes, o.phong_cach))
    if plan.music is not None and music is not None and music.path:
        plan = plan.model_copy(update={"music": plan.music.model_copy(update={"path": music.path})})
    warnings, adjustments = _text(final["warnings"]) + plan.warnings, _text(final["adjustments"])
    if music is not None and level_of_plan(plan, rules) == "BLOCKING":
        if not o.bpm_override:
            raise BpmBlockedError(blocked_text(rules, plan, music.bpm))
        warnings = [w for w in warnings if not w.startswith("W_DUR_GAP")]  # already acknowledged
        adjustments.append("Dùng bài lệch BPM theo bpm_override: E1-DUR-GAP vẫn chặn khi giao "
                           "nếu kế hoạch lệch quá giới hạn.")  # fmt: skip
    if o.bpm_override:
        plan = plan.model_copy(update={"options": {**plan.options, "bpm_override": True}})
    plan = plan.model_copy(update={"warnings": warnings, "adjustments": adjustments})
    path = folder / EDIT_DIR / PLAN_FILE
    _write(path, plan.model_dump_json(indent=1))
    return plan, path
