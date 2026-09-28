"""Load config/*.json and user option files from disk (the only I/O of the rule layer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from gani_dung_video.domain.glossary import Term
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.yeu_cau import parse_yeu_cau

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
MAX_OPTIONS_BYTES = 1_000_000  # an option file is a few hundred bytes; refuse anything huge
OPTION_SUFFIXES = {".md", ".json"}
REQUIRED_RULE_KEYS = (
    "rules_version",
    "styles",
    "purposes",
    "durations",
    "drafts",
    "music_modes",
    "music",
    "footage",
    "scenes_needed",
    "compat_style_purpose",
    "compat_style_duration",
    "conflicts",
    "compat_msgs",
    "input_msgs",
)


class ConfigError(Exception):
    """A config or option file is missing or malformed; message is actionable (Vietnamese)."""


class _TermModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correct: str
    heard_as: list[str] = []


_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# 1–3 English sentences per dish (CLIP only reads English); spec §7.1 "fix S3".
_Prompts = Annotated[list[_Text], Field(min_length=1, max_length=3)]


class GlossaryFile(BaseModel):
    """Shape of config/glossary.json."""

    model_config = ConfigDict(extra="ignore")
    terms: list[_TermModel]
    banned_claims: list[str]
    visual: dict[_Text, _Prompts] = {}

    def term_objects(self) -> list[Term]:
        return [Term(t.correct, tuple(t.heard_as)) for t in self.terms]


def _read_json(path: Path) -> Any:  # Any: arbitrary JSON, validated by the caller
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"Không tìm thấy file: {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name} không phải JSON hợp lệ (dòng {e.lineno}): {e.msg}") from e


def load_rules(path: Path | None = None) -> Rules:
    """Load and sanity-check config/rules.json."""
    p = path or CONFIG_DIR / "rules.json"
    data = _read_json(p)
    if not isinstance(data, dict):
        raise ConfigError(f"{p.name}: phải là một object JSON.")
    missing = [k for k in REQUIRED_RULE_KEYS if k not in data]
    if missing:
        raise ConfigError(f"{p.name} thiếu mục: {', '.join(missing)}")
    return data


def load_glossary(path: Path | None = None) -> GlossaryFile:
    """Load and validate config/glossary.json."""
    p = path or CONFIG_DIR / "glossary.json"
    try:
        return GlossaryFile.model_validate(_read_json(p))
    except ValidationError as e:
        raise ConfigError(
            f"{p.name} sai cấu trúc: {e.errors()[0]['msg']} tại {e.errors()[0]['loc']}"
        ) from e


def read_options_file(path: Path) -> object:
    """Read a yeu-cau.md (Appendix D block) or an options .json file.

    Raises:
        ConfigError: wrong extension, missing, too large, or unreadable.
        YeuCauFormatError: the .md block is malformed.
    """
    p = path.expanduser().resolve()
    if p.suffix.lower() not in OPTION_SUFFIXES:
        raise ConfigError(f"Chỉ nhận file .md (yeu-cau.md) hoặc .json — nhận được '{p.name}'.")
    if not p.is_file():
        raise ConfigError(f"Không tìm thấy file: {p}")
    if p.stat().st_size > MAX_OPTIONS_BYTES:
        raise ConfigError(f"{p.name} quá lớn cho một file tùy chọn (> 1 MB).")
    if p.suffix.lower() == ".json":
        return _read_json(p)
    return parse_yeu_cau(p.read_text(encoding="utf-8"))
