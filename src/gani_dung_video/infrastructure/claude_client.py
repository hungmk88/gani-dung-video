"""Claude Messages API adapter (phase 4c): one call in, JSON text + token usage out.

Structured outputs (output_config.format = json_schema) force the reply into the draft
schema; output_config.effort bounds how much the model thinks. Network calls always have a
timeout; the SDK retries connection errors and 429/5xx a few times with backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AiReply:
    text: str
    input_tokens: int
    output_tokens: int  # includes thinking tokens: they are billed as output
    stop_reason: str
    model: str


Caller = Callable[[str, str, Mapping[str, Any]], AiReply]  # (system, user, schema) → reply


class AiError(RuntimeError):
    """The API call failed (message in Vietnamese, says what to check; never the key)."""


def make_caller(api_key: str, model: str, cfg: Mapping[str, Any]) -> Caller:
    """Build the real caller. Raises AiError if the anthropic package is missing."""
    try:
        import anthropic
    except ImportError as e:
        raise AiError("Chưa cài thư viện anthropic: chạy `pip install -e .`.") from e
    client = anthropic.Anthropic(api_key=api_key, timeout=float(cfg["timeout_sec"]),
                                 max_retries=int(cfg["network_retries"]))  # fmt: skip

    def call(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        try:
            msg = client.messages.create(
                model=model, max_tokens=int(cfg["max_tokens"]), system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": cfg["effort"],
                               "format": {"type": "json_schema", "schema": dict(schema)}},
            )  # fmt: skip
        except anthropic.AuthenticationError as e:
            raise AiError("Khóa API bị từ chối — kiểm tra ANTHROPIC_API_KEY trong .env.") from e
        except anthropic.RateLimitError as e:
            raise AiError("Claude API báo quá giới hạn tần suất; đợi vài phút rồi chạy lại.") from e
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            raise AiError(f"Không kết nối được Claude API (quá {cfg['timeout_sec']}s hoặc mất "
                          "mạng).") from e  # fmt: skip
        except anthropic.APIStatusError as e:
            raise AiError(f"Claude API trả lỗi {e.status_code}: {e.message}") from e
        text = "".join(getattr(b, "text", "") for b in msg.content if b.type == "text")
        logger.info("Claude %s: %d token vào, %d token ra (%s)", msg.model,
                    msg.usage.input_tokens, msg.usage.output_tokens, msg.stop_reason)  # fmt: skip
        return AiReply(text=text, input_tokens=msg.usage.input_tokens,
                       output_tokens=msg.usage.output_tokens,
                       stop_reason=str(msg.stop_reason or ""), model=msg.model)  # fmt: skip

    return call
