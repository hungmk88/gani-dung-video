"""Tests: infrastructure/claude_client.py with a fake SDK client (no network, no key)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from gani_dung_video.infrastructure.claude_client import AiError, make_caller
from gani_dung_video.infrastructure.config_files import load_rules

CFG = load_rules()["ai"]
SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


class FakeMessages:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def install(monkeypatch: pytest.MonkeyPatch, outcome: object) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kw: Any) -> None:
            seen["client"] = kw
            self.messages = FakeMessages(outcome)
            seen["messages"] = self.messages

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return seen


def reply(*blocks: object) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), model="claude-sonnet-5-5", stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=345))  # fmt: skip


def test_request_carries_model_effort_schema_timeout_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thinking = SimpleNamespace(type="thinking", thinking="...")
    text = SimpleNamespace(type="text", text='{"drafts": []}')
    seen = install(monkeypatch, reply(thinking, text))
    call = make_caller("test-key-not-real", "claude-sonnet-5-5", CFG)
    out = call("system", "user", SCHEMA)
    assert (out.text, out.input_tokens, out.output_tokens) == ('{"drafts": []}', 1200, 345)
    assert out.stop_reason == "end_turn" and out.model == "claude-sonnet-5-5"
    assert seen["client"]["timeout"] == float(CFG["timeout_sec"])
    assert seen["client"]["max_retries"] == int(CFG["network_retries"])
    kw = seen["messages"].kwargs
    assert kw["model"] == "claude-sonnet-5-5" and kw["max_tokens"] == CFG["max_tokens"]
    assert kw["system"] == "system" and kw["messages"] == [{"role": "user", "content": "user"}]
    assert kw["output_config"] == {"effort": "medium",
                                   "format": {"type": "json_schema", "schema": SCHEMA}}  # fmt: skip


class _Auth(Exception):
    pass


class _Rate(Exception):
    pass


class _Conn(Exception):
    pass


class _Status(Exception):
    status_code = 400
    message = "bad request"


@pytest.mark.parametrize(
    ("error", "expected"),
    [(_Auth(), "Khóa API bị từ chối"), (_Rate(), "giới hạn tần suất"),
     (_Conn(), "Không kết nối được"), (_Status(), "lỗi 400")],
)  # fmt: skip
def test_api_errors_become_actionable_messages_without_the_key(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    monkeypatch.setattr(anthropic, "AuthenticationError", _Auth)
    monkeypatch.setattr(anthropic, "RateLimitError", _Rate)
    monkeypatch.setattr(anthropic, "APIConnectionError", _Conn)
    monkeypatch.setattr(anthropic, "APITimeoutError", _Conn)
    monkeypatch.setattr(anthropic, "APIStatusError", _Status)
    install(monkeypatch, error)
    call = make_caller("test-key-not-real", "m", CFG)
    with pytest.raises(AiError, match=expected) as e:
        call("s", "u", SCHEMA)
    assert "test-key-not-real" not in str(e.value)
