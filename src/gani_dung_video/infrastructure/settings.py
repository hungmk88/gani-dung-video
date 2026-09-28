"""Secrets and per-machine settings from the environment or the project's .env (phase 4c).

The API key is a SecretStr: printing or logging the settings shows '**********', never the
key. .env is git-ignored; .env.example lists the variables with empty values.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from gani_dung_video.infrastructure.config_files import CONFIG_DIR

ENV_FILE = CONFIG_DIR.parent / ".env"


class SettingsError(RuntimeError):
    """A required setting is missing (message says where to put it, never its value)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None
    gani_dv_claude_model: str | None = None  # overrides rules.json ai.model on this machine


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Environment variables win over the .env file; a missing .env is fine."""
    if env_file is not None and env_file.is_file():
        return Settings(_env_file=env_file)
    return Settings()


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
