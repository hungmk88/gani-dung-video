"""Use case: analyse every inventoried clip → edit/scenes.json (phase 3b).

Per-clip frame metrics are cached in edit/vision/ (decoding is the slow part); scene rules
run on every call, so changing thresholds in rules.json never needs a re-decode.
"""

from __future__ import annotations

import logging
import os
import statistics
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gani_dung_video.domain.footage import Inventory
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.scenes import (
    FrameStats,
    Scene,
    ScenesFile,
    Segment,
    blur_flags,
    classify_motion,
    motion_windows,
    pick_clip_segments,
    split_scenes,
    style_segment_range,
    usable_by_style,
    usable_spans,
)
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, INVENTORY_FILE
from gani_dung_video.infrastructure.video_frames import VisionError, analyse_clip

logger = logging.getLogger(__name__)
VISION_DIR = "vision"
SCENES_FILE = "scenes.json"
Analyser = Callable[[Path, float, int], list[FrameStats]]


class _StatsCache(BaseModel):
    cache_key: str
    stats: list[FrameStats]


def _read_inventory(folder: Path) -> Inventory:
    p = folder / EDIT_DIR / INVENTORY_FILE
    if not p.is_file():
        raise ConfigError(f"Chưa có {EDIT_DIR}/{INVENTORY_FILE} — chạy lệnh `inventory` trước.")
    try:
        return Inventory.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{INVENTORY_FILE} hỏng — chạy lại lệnh `inventory`.") from e


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _clip_stats(
    folder: Path, name: str, cfg: Mapping[str, Any], analyse: Analyser
) -> list[FrameStats]:
    src, out = folder / name, folder / EDIT_DIR / VISION_DIR / f"{name}.json"
    st = src.stat()
    key = f"{st.st_size}-{st.st_mtime_ns}-{cfg['sample_fps']}-{cfg['analysis_width']}"
    if out.is_file():
        try:
            cached = _StatsCache.model_validate_json(out.read_text(encoding="utf-8"))
            if cached.cache_key == key:
                return cached.stats
        except ValidationError:
            pass  # unreadable cache: fall through and re-analyse
    logger.info("Đang phân tích hình: %s", name)
    stats = analyse(src, float(cfg["sample_fps"]), int(cfg["analysis_width"]))
    _write(out, _StatsCache(cache_key=key, stats=stats).model_dump_json())
    return stats


def _clip_scenes(
    name: str, duration: float, stats: list[FrameStats], cfg: Mapping[str, Any]
) -> list[Scene]:
    ranges = split_scenes(stats, duration, float(cfg["cut_threshold"]), float(cfg["min_scene_sec"]))
    scenes: list[Scene] = []
    for k, (i0, i1) in enumerate(ranges):
        part = stats[i0:i1]
        start = part[0].t if k else 0.0
        end = round(stats[i1].t if i1 < len(stats) else duration, 3)
        motion, jitter, consistency = classify_motion(part, cfg)
        wins = motion_windows(part, start, end, float(cfg["window_sec"]), cfg)
        scenes.append(Scene(
            clip=name, index=k, start=start, end=end,
            sharpness=round(statistics.median(s.sharpness for s in part), 2),
            motion=motion, jitter=jitter, consistency=consistency,
            shaky=not usable_spans(wins), windows=wins,
        ))  # fmt: skip
    return scenes


def _add_segments(
    scenes: list[Scene], stats: list[FrameStats], cfg: Mapping[str, Any],
    styles: Mapping[str, Any],
) -> list[Scene]:  # fmt: skip
    """Pick each style's segments over the whole clip (cap/gap are per clip), then file
    every segment under the scene that contains it."""
    spans = [sp for sc in scenes for sp in usable_spans(sc.windows)]
    per_scene: list[dict[str, list[Segment]]] = [{} for _ in scenes]
    for name, style in styles.items():
        lo, hi = style_segment_range(style)
        segs = pick_clip_segments(stats, spans, lo, hi, float(cfg["segment_gap_sec"]),
                                  int(cfg["max_segments_per_clip"]))  # fmt: skip
        for d, sc in zip(per_scene, scenes, strict=True):
            d[name] = [g for g in segs if sc.start <= g.start < sc.end]
    return [sc.model_copy(update={"segments": d}) for sc, d in zip(scenes, per_scene, strict=True)]


def build_scenes(
    folder: Path, rules: Rules | None = None, analyse: Analyser | None = None
) -> tuple[ScenesFile, Path]:
    """Analyse all clips in <folder>/edit/inventory.json and write edit/scenes.json.

    Raises:
        ConfigError: inventory missing/corrupt.
    """
    rules = rules if rules is not None else load_rules()
    cfg, styles = rules["vision"], rules["styles"]
    folder = folder.expanduser().resolve()
    inv = _read_inventory(folder)
    (folder / EDIT_DIR / VISION_DIR).mkdir(parents=True, exist_ok=True)
    all_scenes: list[Scene] = []
    for clip in inv.clips:
        try:
            stats = _clip_stats(folder, clip.file, cfg, analyse or analyse_clip)
        except VisionError as e:
            logger.warning("Bỏ qua %s: %s", clip.file, e)
            continue
        scenes = _clip_scenes(clip.file, clip.duration_sec, stats, cfg)
        all_scenes.extend(_add_segments(scenes, stats, cfg, styles))
    blurry = blur_flags([s.sharpness for s in all_scenes], float(cfg["blur_floor"]))
    all_scenes = [
        s.model_copy(update={"blurry": b}) for s, b in zip(all_scenes, blurry, strict=True)
    ]
    result = ScenesFile(
        folder=str(folder),
        rules_version=str(rules["rules_version"]),
        scenes=all_scenes,
        usable_by_style=usable_by_style(all_scenes, styles),
    )
    path = folder / EDIT_DIR / SCENES_FILE
    _write(path, result.model_dump_json(indent=1))
    return result, path
