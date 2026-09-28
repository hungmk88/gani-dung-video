"""Tests: infrastructure/settings.py — API key from env/.env, never shown (4c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gani_dung_video.infrastructure.settings import (
    ENV_FILE,
    SettingsError,
    load_settings,
    require_api_key,
)

FAKE = "test-key-not-real-0000"  # a made-up value: tests never touch a real key


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GANI_DV_CLAUDE_MODEL", raising=False)


def test_key_and_model_are_read_from_the_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE}\nGANI_DV_CLAUDE_MODEL=claude-sonnet-5\n",
                   encoding="utf-8")  # fmt: skip
    s = load_settings(env)
    assert require_api_key(s) == FAKE and s.gani_dv_claude_model == "claude-sonnet-5"


def test_the_key_never_shows_in_repr_or_str(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE}\n", encoding="utf-8")
    s = load_settings(env)
    assert FAKE not in repr(s) and FAKE not in str(s) and FAKE not in s.model_dump_json()


def test_environment_variable_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert require_api_key(load_settings(env)) == "from-env"


@pytest.mark.parametrize("content", ["", "ANTHROPIC_API_KEY=\n", "ANTHROPIC_API_KEY=   \n"])
def test_missing_key_is_an_actionable_error(tmp_path: Path, content: str) -> None:
    env = tmp_path / ".env"
    env.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError, match=r"\.env"):
        require_api_key(load_settings(env))


def test_no_env_file_is_fine(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "missing.env").anthropic_api_key is None


def test_env_file_lives_in_the_project_root_and_is_git_ignored() -> None:
    assert ENV_FILE.name == ".env" and (ENV_FILE.parent / "pyproject.toml").is_file()
    assert ".env" in (ENV_FILE.parent / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_env_example_carries_no_key() -> None:
    lines = (ENV_FILE.parent / ".env.example").read_text(encoding="utf-8").splitlines()
    key_lines = [ln for ln in lines if ln.startswith("ANTHROPIC_API_KEY")]
    assert key_lines == ["ANTHROPIC_API_KEY="]  # incident 29/09/2026: a real key was pasted here
