"""Secrets and per-machine settings from the environment or the project's .env (phase 4c).

The API key is a SecretStr: printing or logging the settings shows '**********', never the
key. .env is git-ignored; .env.example lists the variables with empty values.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from gani_dung_video.infrastructure.config_files import CONFIG_DIR

ENV_FILE = CONFIG_DIR.parent / ".env"
logger = logging.getLogger(__name__)


class SettingsError(RuntimeError):
    """A required setting is missing (message says where to put it, never its value)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None
    gani_dv_claude_model: str | None = None  # overrides rules.json ai.model on this machine
    # Phase 6 (job queue): where footage may be queued from (paths separated by the OS path
    # separator: ";" on Windows, ":" on Mac) and where queue.db lives (default: <project>/jobs).
    gani_dv_allowed_roots: str | None = None
    gani_dv_queue_dir: str | None = None
    # Whisper model the queue's `transcribe` stage uses on this machine (rules.json's default,
    # large-v3-turbo, is not on the Dell and is ~1.6 GB to download): e.g. "small".
    gani_dv_whisper_model: str | None = None
    # The machine's music library: the queue's `plan` stage looks there for library tracks.
    gani_dv_music_dir: str | None = None
    # Browser origins the API trusts besides its own (comma-separated, e.g. the Vite dev server
    # http://localhost:5173 while developing the UI). Empty on a normal machine: see dev_origins.
    gani_dv_dev_origins: str | None = None


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Environment variables win over the .env file; a missing .env is fine."""
    if env_file is not None and env_file.is_file():
        return Settings(_env_file=env_file)
    return Settings()


def dev_origins(settings: Settings) -> list[str]:
    """The extra browser origins GANI_DV_DEV_ORIGINS trusts, each as `scheme://host[:port]`.

    A page served from a listed origin can call the API as the user (approvals included), so
    nothing is trusted by default and the list is parsed strictly: only http(s) origins, no
    wildcard, no path. Entries that do not parse are skipped with a warning.
    """
    found: list[str] = []
    for raw in (settings.gani_dv_dev_origins or "").split(","):
        item = raw.strip().rstrip("/")
        if not item:
            continue
        parts = urlsplit(item)
        ok = (parts.scheme in {"http", "https"} and bool(parts.netloc) and "*" not in item
              and not parts.path and not parts.query and not parts.fragment)  # fmt: skip
        if ok:
            found.append(item)
        else:
            logger.warning("GANI_DV_DEV_ORIGINS: bỏ qua '%s' (cần dạng http://localhost:5173, "
                           "không có đường dẫn và không dùng dấu *).", item)  # fmt: skip
    return found


def require_api_key(settings: Settings) -> str:
    """The key's value, for the API client only.

    Raises:
        SettingsError: no key configured.
    """
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not key.strip():
        raise SettingsError(
            "Chưa có ANTHROPIC_API_KEY. Chép .env.example thành .env ở thư mục dự án rồi dán "
            "khóa vào đó (không dán khóa vào chat hay vào code)."
        )
    return key.strip()
