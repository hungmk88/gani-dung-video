"""The machine's music library as the web UI sees it: files + BPM + length, measured in the
background (ticket 14/10/2026).

Listing must be instant. A track listed in library.json has its BPM at once; an unlisted track
is measured (ffmpeg decode + numpy, seconds on the Dell) by ONE background thread, one track at
a time, and the answer is cached next to queue.db keyed by (size, mtime_ns). library.json and
the music folder are never written: the first is a person's checked data, the second may be
read-only.

Not recursive: only the files directly inside the folder, at most MAX_TRACKS.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gani_dung_video.infrastructure.music_analysis import (
    LibraryTrack,
    MusicError,
    Rights,
    read_library,
    track_duration,
    track_info,
)

logger = logging.getLogger(__name__)

MAX_TRACKS = 500
DEFAULT_RIGHTS: Rights = "thu_vien_noi_bo"
CACHE_VERSION = 1
Source = Literal["library", "measured", "unknown", "pending"]


@dataclass(frozen=True)
class IndexedTrack:
    file: str  # file name only: the folder is not walked, and no absolute path leaves the server
    title: str
    bpm: float | None
    bpm_source: Source
    first_beat_sec: float | None
    duration_sec: float | None
    rights: Rights


def audio_files(folder: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Audio files directly in `folder`: right extension, not hidden, really inside it."""
    try:
        root = folder.resolve()
        found = []
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
            if p.name.startswith(".") or p.suffix.lower() not in extensions or not p.is_file():
                continue
            if p.resolve().parent != root:  # a link that leaves the folder
                continue
            found.append(p)
            if len(found) >= MAX_TRACKS:
                break
        return found
    except OSError:
        return []


def _stamp(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


class MusicIndex:
    """One music folder: what is known now, and a worker that fills in the rest."""

    def __init__(self, folder: Path, cache_path: Path, cfg: Mapping[str, Any]) -> None:
        self.folder, self.cache_path, self._cfg = folder, cache_path, cfg
        self._extensions = tuple(str(e).lower() for e in cfg["extensions"])
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cache: dict[str, dict[str, Any]] = self._load()

    # ─────────────── cache file ───────────────

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION and isinstance(data.get("tracks"), dict):
                return {str(k): dict(v) for k, v in data["tracks"].items()}
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        return {}

    def _save(self) -> None:
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps({"version": CACHE_VERSION, "tracks": self._cache}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.cache_path)
        except OSError as e:  # a cache that cannot be written only costs a re-measure
            logger.warning("Không ghi được bộ nhớ đệm nhạc %s: %s", self.cache_path.name, e)

    def _fresh(self, p: Path) -> dict[str, Any] | None:
        """The cached entry of `p` when the file is still the one it was measured on."""
        entry = self._cache.get(p.name)
        stamp = _stamp(p)
        if entry is None or stamp is None:
            return None
        if (entry.get("size"), entry.get("mtime_ns")) != stamp:
            return None
        return entry

    # ─────────────── what the UI sees ───────────────

    def snapshot(self) -> tuple[list[IndexedTrack], int]:
        """Every track with what is known now, and how many still wait for the worker.

        Raises:
            MusicError: library.json is unreadable or not in its format.
        """
        library = read_library(self.folder)
        files = audio_files(self.folder, self._extensions)
        with self._lock:
            tracks = [self._view(p, library) for p in files]
        pending = sum(1 for t in tracks if t.bpm_source == "pending" or (t.duration_sec is None
                      and t.bpm_source != "unknown"))  # fmt: skip
        self._ensure_worker()
        return tracks, pending

    def _view(self, p: Path, library: Mapping[str, LibraryTrack]) -> IndexedTrack:
        cached = self._fresh(p)
        known = library.get(p.name)
        duration = cached.get("duration_sec") if cached else None
        rights = known.rights if known and known.rights else DEFAULT_RIGHTS
        title = known.title if known and known.title else p.stem
        if known is not None:
            return IndexedTrack(p.name, title, known.bpm, "library", known.first_beat_sec,
                                duration, rights)  # fmt: skip
        if cached is None or cached.get("from_library"):  # never measured: it was only listed
            return IndexedTrack(p.name, title, None, "pending", None, None, rights)
        if cached.get("bpm") is None:
            return IndexedTrack(p.name, title, None, "unknown", None, duration, rights)
        return IndexedTrack(p.name, title, float(cached["bpm"]), "measured",
                            cached.get("first_beat_sec"), duration, rights)  # fmt: skip

    # ─────────────── the worker ───────────────

    def _todo(self, library: Mapping[str, LibraryTrack]) -> list[Path]:
        out = []
        for p in audio_files(self.folder, self._extensions):
            cached = self._fresh(p)
            listed = p.name in library
            if cached is None:
                out.append(p)
            elif listed and cached.get("duration_sec") is None and not cached.get("error"):
                out.append(p)  # a listed track still needs its length
            elif not listed and cached.get("from_library"):
                out.append(p)  # it left library.json: its BPM was never measured
        return out

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            try:
                library = read_library(self.folder)
            except MusicError:
                return
            if not self._todo(library):
                return
            self._worker = threading.Thread(target=self._run, name="music-index", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        """Measure what is missing, one track at a time, saving after each."""
        while True:
            try:
                library = read_library(self.folder)
            except MusicError:
                return
            with self._lock:
                todo = self._todo(library)
            if not todo:
                return
            self._measure(todo[0], library)

    def _measure(self, p: Path, library: Mapping[str, LibraryTrack]) -> None:
        stamp = _stamp(p)
        if stamp is None:
            return
        entry: dict[str, Any] = {"size": stamp[0], "mtime_ns": stamp[1]}
        timeout = float(self._cfg["ffmpeg_timeout_sec"])
        try:
            info = track_info(p, library, self._cfg)
            if info is not None and p.name in library:  # declared by a person: keep only length
                entry.update(bpm=None, first_beat_sec=None, duration_sec=info.duration_sec,
                             from_library=True)  # fmt: skip
            elif info is not None:
                entry.update(bpm=info.bpm, first_beat_sec=info.first_beat_sec,
                             duration_sec=info.duration_sec)  # fmt: skip
            else:  # no clear beat: still say how long it is
                entry.update(bpm=None, first_beat_sec=None, duration_sec=track_duration(p, timeout))
        except MusicError as e:
            logger.warning("Không đo được %s: %s", p.name, e)
            entry.update(bpm=None, first_beat_sec=None, duration_sec=None, error=str(e)[:200])
        with self._lock:
            self._cache[p.name] = entry
            self._save()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until the worker has nothing left to do (for tests and scripts)."""
        t = self._worker
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True


_REGISTRY: dict[tuple[str, str], MusicIndex] = {}
_REGISTRY_LOCK = threading.Lock()


def get_index(folder: Path, cache_path: Path, cfg: Mapping[str, Any]) -> MusicIndex:
    """The one MusicIndex of this folder in this process (so one worker, one cache)."""
    key = (str(folder.resolve()), str(cache_path.resolve()))
    with _REGISTRY_LOCK:
        index = _REGISTRY.get(key)
        if index is None:
            index = MusicIndex(folder, cache_path, cfg)
            _REGISTRY[key] = index
        return index
