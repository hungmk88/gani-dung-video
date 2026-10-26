"""Footage folder I/O: list clips, fingerprint duplicate candidates, write edit/inventory.json.

Source files are only ever READ; everything generated goes to <folder>/edit/ (spec §7.1).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO

from gani_dung_video.domain.footage import Inventory

EDIT_DIR = "edit"
INVENTORY_FILE = "inventory.json"
_READ_BLOCK = 1024 * 1024


def list_videos(folder: Path, extensions: Sequence[str]) -> list[Path]:
    """Video files directly inside folder (not recursive, hidden files skipped), sorted by name."""
    exts = {e.lower() for e in extensions}
    return sorted(
        (p for p in folder.iterdir()
         if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts),
        key=lambda p: p.name.lower(),
    )  # fmt: skip


def first_picture(folder: Path, subfolder: str, extensions: Sequence[str]) -> str | None:
    """The first picture (by name) in <folder>/<subfolder>, as a path relative to folder; None
    when there is none. Hidden files and anything resolving outside the folder are skipped."""
    sub = folder / subfolder
    if not sub.is_dir():
        return None
    root, exts = folder.resolve(), {e.lower() for e in extensions}
    pics = sorted(
        (p for p in sub.iterdir()
         if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in exts
         and p.resolve().is_relative_to(root)),
        key=lambda p: p.name.lower(),
    )  # fmt: skip
    return pics[0].relative_to(folder).as_posix() if pics else None


def fingerprint(path: Path, chunk_bytes: int) -> str:
    """SHA-256 over (size, first chunk, last chunk) — reads at most 2 × chunk_bytes.

    Why not the full file: hashing whole 4K clips on a slow HDD stalls I/O (decision
    28/09/2026). Two files with equal size AND equal head/tail are treated as identical.
    """
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        _feed(h.update, f, chunk_bytes)
        if size > chunk_bytes:
            f.seek(max(chunk_bytes, size - chunk_bytes))
            _feed(h.update, f, chunk_bytes)
    return h.hexdigest()


def _feed(update: Callable[[bytes], None], f: BinaryIO, limit: int) -> None:
    left = limit
    while left > 0:
        block = f.read(min(_READ_BLOCK, left))
        if not block:
            break
        update(block)
        left -= len(block)


def write_inventory(folder: Path, inventory: Inventory) -> Path:
    """Write <folder>/edit/inventory.json atomically (temp file + replace)."""
    out_dir = folder / EDIT_DIR
    out_dir.mkdir(exist_ok=True)
    target = out_dir / INVENTORY_FILE
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(inventory.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, target)
    return target
