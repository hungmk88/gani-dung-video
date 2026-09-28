"""Use case: transcribe every inventoried clip → edit/transcripts/*.json + edit/speech.json (3a).

Cache: a clip is re-transcribed only if its size/mtime or the model changed, because a
Whisper pass costs minutes per clip on the Dell and still seconds on the Mac Mini.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from gani_dung_video.domain.footage import Inventory
from gani_dung_video.domain.glossary import Term
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.speech import (
    RawTranscript,
    Transcript,
    build_transcript,
    is_no_speech,
)
from gani_dung_video.infrastructure.config_files import ConfigError, load_glossary, load_rules
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, INVENTORY_FILE
from gani_dung_video.infrastructure.whisper_asr import AsrError, Transcriber, load_transcriber

logger = logging.getLogger(__name__)
TRANSCRIPTS_DIR = "transcripts"
SPEECH_FILE = "speech.json"


class ClipSpeech(BaseModel):
    file: str
    no_speech: bool
    word_count: int
    speech_sec: float
    cached: bool


class SpeechSummary(BaseModel):
    """Content of edit/speech.json — `co_loi_noi` feeds preflight rule X7."""

    model: str
    co_loi_noi: bool
    clips: list[ClipSpeech]
    failed: dict[str, str]


def _read_inventory(folder: Path) -> Inventory:
    p = folder / EDIT_DIR / INVENTORY_FILE
    if not p.is_file():
        raise ConfigError(f"Chưa có {EDIT_DIR}/{INVENTORY_FILE} — chạy lệnh `inventory` trước.")
    try:
        return Inventory.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(
            f"{INVENTORY_FILE} hỏng — chạy lại lệnh `inventory`. ({e.error_count()} lỗi)"
        ) from e


def _cached(out: Path, key: str) -> Transcript | None:
    if not out.is_file():
        return None
    try:
        t = Transcript.model_validate_json(out.read_text(encoding="utf-8"))
    except ValidationError:
        return None  # unreadable cache → just redo it
    return t if t.cache_key == key else None


def _write_json(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def transcribe_folder(
    folder: Path, model: str | None = None, rules: Rules | None = None,
    loader: Callable[[str, Mapping[str, Any]], Transcriber] | None = None,
) -> tuple[SpeechSummary, Path]:  # fmt: skip
    """Transcribe all clips listed in <folder>/edit/inventory.json.

    Args:
        folder: Footage folder already inventoried.
        model: Whisper model name; defaults to rules.json speech.model_default.
        rules: Parsed rules.json (loaded when omitted).
        loader: Builds the transcriber (default: faster-whisper); injectable so tests need
            no model.

    Returns:
        (summary, path of edit/speech.json). A clip that fails is listed in ``failed``.

    Raises:
        ConfigError: inventory missing/corrupt. AsrError: the model cannot be loaded.
    """
    rules = rules if rules is not None else load_rules()
    cfg = rules["speech"]
    model = model or str(cfg["model_default"])
    folder = folder.expanduser().resolve()
    inv = _read_inventory(folder)
    terms = load_glossary().term_objects()
    out_dir = folder / EDIT_DIR / TRANSCRIPTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    load = loader or load_transcriber
    lazy = _LazyTranscriber(lambda: load(model, cfg))
    clips: list[ClipSpeech] = []
    failed: dict[str, str] = {}
    for clip in inv.clips:
        try:
            clips.append(_one_clip(folder, clip.file, model, out_dir, lazy, terms, cfg))
        except AsrError as e:
            failed[clip.file] = str(e)
    summary = SpeechSummary(model=model, co_loi_noi=any(not c.no_speech for c in clips),
                            clips=clips, failed=failed)  # fmt: skip
    path = folder / EDIT_DIR / SPEECH_FILE
    _write_json(path, summary.model_dump_json(indent=1))
    return summary, path


class _LazyTranscriber:
    """Loads the model on first use only: a fully cached re-run never loads Whisper."""

    def __init__(self, load: Callable[[], Transcriber]) -> None:
        self._load = load
        self._fn: Transcriber | None = None

    def __call__(self, path: Path) -> RawTranscript:
        if self._fn is None:
            self._fn = self._load()
        return self._fn(path)


def _one_clip(
    folder: Path, name: str, model: str, out_dir: Path, transcribe: Transcriber,
    terms: Sequence[Term], cfg: Mapping[str, Any],
) -> ClipSpeech:  # fmt: skip
    src = folder / name
    st = src.stat()
    key = f"{st.st_size}-{st.st_mtime_ns}-{model}"
    out = out_dir / f"{name}.json"
    t = _cached(out, key)
    cached = t is not None
    if t is None:
        logger.info("Đang nhận dạng lời: %s", name)
        t = build_transcript(name, model, key, transcribe(src), terms, cfg)
        _write_json(out, t.model_dump_json(indent=1))
    elif (flag := is_no_speech(t, cfg)) != t.no_speech:
        # Thresholds changed since this transcript was cached: re-judge from stored data.
        t = t.model_copy(update={"no_speech": flag})
        _write_json(out, t.model_dump_json(indent=1))
    return ClipSpeech(file=t.file, no_speech=t.no_speech, word_count=t.word_count,
                      speech_sec=t.speech_sec, cached=cached)  # fmt: skip
