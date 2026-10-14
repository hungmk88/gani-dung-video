"""Use case: transcripts → usable spoken phrases for the AI to choose from (phase 5c-1).

Reads every clip's Transcript (edit/transcripts/<clip>.json, words already glossary-fixed by
phase 3a), splits each into phrases at real pauses and sentence ends, drops fillers, and
keeps only phrases short enough and free of banned claims — spoken words cannot be
auto-corrected like on-screen text (spec §4.3 rule 5). Writes edit/phrases.json, the input
pick.py/Claude will choose from in 5c-2.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gani_dung_video.application.transcribe_service import TRANSCRIPTS_DIR
from gani_dung_video.domain.footage import Inventory, clip_stems
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.speech import Transcript
from gani_dung_video.domain.speech_edit import Phrase, PhrasesFile, classify_phrases, split_phrases
from gani_dung_video.infrastructure.config_files import (
    ConfigError,
    GlossaryFile,
    load_glossary,
    load_rules,
    read_options_file,
)
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, INVENTORY_FILE

PHRASES_FILE = "phrases.json"


def _read_inventory(folder: Path) -> Inventory:
    p = folder / EDIT_DIR / INVENTORY_FILE
    if not p.is_file():
        raise ConfigError(f"Chưa có {EDIT_DIR}/{INVENTORY_FILE} — chạy lệnh `inventory` trước.")
    try:
        return Inventory.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError as e:
        raise ConfigError(f"{INVENTORY_FILE} hỏng — chạy lại lệnh `inventory`.") from e


def _read_transcript(folder: Path, clip: str) -> Transcript | None:
    p = folder / EDIT_DIR / TRANSCRIPTS_DIR / f"{clip}.json"
    if not p.is_file():
        return None  # clip not transcribed (e.g. transcribe skipped/failed on it)
    try:
        return Transcript.model_validate_json(p.read_text(encoding="utf-8"))
    except ValidationError:
        return None  # unreadable cache: skip rather than fail the whole run


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build_phrases(
    options_file: Path,
    folder: Path,
    rules: Rules | None = None,
    glossary: GlossaryFile | None = None,
) -> tuple[PhrasesFile, Path]:
    """Split every speech clip into usable phrases for <folder>/edit/phrases.json.

    Raises:
        ConfigError: the option's style is not speech-driven, or inventory/transcripts
            are missing/corrupt.
    """
    rules = rules if rules is not None else load_rules()
    glossary = glossary if glossary is not None else load_glossary()
    folder = folder.expanduser().resolve()
    raw = read_options_file(options_file.expanduser().resolve())
    opts: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    style_name = str(opts.get("phong_cach"))
    style = rules["styles"].get(style_name)
    msgs = rules["input_msgs"]
    if style is None or style.get("driver") != "speech":
        raise ConfigError(msgs["PHRASES_NOT_SPEECH_STYLE"].format(style=style_name))
    inv = _read_inventory(folder)
    if not (folder / EDIT_DIR / TRANSCRIPTS_DIR).is_dir():
        raise ConfigError(msgs["PHRASES_NO_TRANSCRIPTS"])
    se_cfg = rules["speech_edit"]
    phrases: list[Phrase] = []
    excluded = []
    stems = clip_stems(c.file for c in inv.clips)
    for clip in inv.clips:
        t = _read_transcript(folder, clip.file)
        if t is None or t.no_speech or not t.words:
            continue
        candidates = split_phrases(t.words, clip.file, se_cfg, style, stems[clip.file])
        ok, dropped = classify_phrases(candidates, glossary.banned_claims, se_cfg)
        phrases += ok
        excluded += dropped
    result = PhrasesFile(rules_version=str(rules["rules_version"]), style=style_name,
                         phrases=phrases, excluded=excluded)  # fmt: skip
    path = folder / EDIT_DIR / PHRASES_FILE
    _write(path, result.model_dump_json(indent=1))
    return result, path
