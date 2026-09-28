"""Use case: inventory a footage folder into <folder>/edit/inventory.json (phase 2)."""

from __future__ import annotations

import logging
from pathlib import Path

from gani_dung_video.domain.footage import (
    Clip,
    Duplicate,
    Inventory,
    ProbeDataError,
    Skipped,
    clip_from_probe,
    duplicate_candidates,
)
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.config_files import ConfigError, load_rules
from gani_dung_video.infrastructure.ffprobe import ProbeError, probe, require_ffprobe
from gani_dung_video.infrastructure.footage_fs import fingerprint, list_videos, write_inventory

logger = logging.getLogger(__name__)
_MB = 1024 * 1024


def _find_duplicates(
    folder: Path, names: list[str], patterns: list[str], chunk: int
) -> list[Duplicate]:
    """Confirm name-based candidates by size + head/tail hash; never hash other files."""
    dups: list[Duplicate] = []
    for dup, original in duplicate_candidates(names, patterns):
        a, b = folder / dup, folder / original
        if a.stat().st_size == b.stat().st_size and fingerprint(a, chunk) == fingerprint(b, chunk):
            dups.append(Duplicate(file=dup, duplicate_of=original))
        else:
            logger.info("'%s' giống tên '%s' nhưng khác nội dung — giữ cả hai.", dup, original)
    return dups


def _probe_all(folder: Path, names: list[str], rules: Rules) -> tuple[list[Clip], list[Skipped]]:
    cfg = rules["footage"]
    exe = require_ffprobe()
    clips: list[Clip] = []
    skipped: list[Skipped] = []
    for name in names:
        path = folder / name
        try:
            data = probe(path, float(cfg["ffprobe_timeout_sec"]), exe)
            clips.append(clip_from_probe(name, path.stat().st_size, data, cfg["hdr_transfers"]))
        except (ProbeError, ProbeDataError) as e:
            skipped.append(Skipped(file=name, reason=str(e)))
    return clips, skipped


def build_inventory(folder: Path, rules: Rules | None = None) -> tuple[Inventory, Path]:
    """Inventory every clip directly inside folder and write edit/inventory.json.

    Args:
        folder: The footage folder (source files are read, never modified).
        rules: Parsed rules.json; loaded from config/ when omitted.

    Returns:
        (inventory, path of the written inventory.json).

    Raises:
        ConfigError: folder missing / not a directory.
        FfprobeMissingError: ffprobe is not installed.
    """
    rules = rules if rules is not None else load_rules()
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise ConfigError(f"Không tìm thấy thư mục footage: {folder}")
    cfg = rules["footage"]
    names = [p.name for p in list_videos(folder, cfg["video_extensions"])]
    dups = _find_duplicates(
        folder, names, list(cfg["duplicate_patterns"]), int(cfg["duplicate_hash_mb"]) * _MB
    )
    dup_names = {d.file for d in dups}
    clips, skipped = _probe_all(folder, [n for n in names if n not in dup_names], rules)
    inv = Inventory(
        folder=str(folder),
        rules_version=str(rules["rules_version"]),
        clips=clips,
        duplicates=dups,
        skipped=skipped,
        total_duration_sec=round(sum(c.duration_sec for c in clips), 3),
    )
    return inv, write_inventory(folder, inv)
