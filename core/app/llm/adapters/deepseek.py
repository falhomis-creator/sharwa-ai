"""core/app/llm/adapters/deepseek.py - the real LLM provider (P4.2, owner
decision: DeepSeek is the marketing engine's ONLY LLM; OpenAI is excluded).

DeepSeek's API is fully OpenAI-compatible (verified against
api-docs.deepseek.com): the official ``openai`` SDK works unchanged once
base_url points at https://api.deepseek.com. The default model is
deepseek-chat; DEEPSEEK_MODEL overrides it without a code deploy (the current
DeepSeek lineup also offers deepseek-flash / deepseek-v4-pro).

H38 still holds: the model only CLASSIFIES (complete_json) or summarizes
conversation context (complete_text) - nothing it emits is ever sent to a
customer. The adapter maps SDK exceptions onto the port's transient-error
vocabulary so the caller's ONE retry + circuit breaker (H39) stay in charge:
the SDK's own retry loop is disabled (max_retries=0), so every "retry" is OUR
bounded retry, never a hidden second billing.
"""
from __future__ import annotations

import json
import time
from typing import Any

import openai

from app.llm.port import (
    LlmJsonResult,
    LlmProvider,
    LlmProviderError,
    LlmTextResult,
    LlmTimeoutError,
    LlmUsage,
)

PROVIDER_NAME = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekProvider(LlmProvider):
    """OpenAI-compatible chat adapter for the DeepSeek API."""

    def __init__(
        self, *, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
        client: Any = None,
    ) -> None:
        if not api_key:
            # Defensive only - app/workers/config.py already refuses an empty
            # key at boot (H5). This keeps a mis-wired construction from
            # silently calling with no credentials.
            raise RuntimeError("DeepSeek provider requires a non-empty DEEPSEEK_API_KEY")
        self._model = model
        # max_retries=0: retries are OURS (H39 - one retry, then the breaker).
        self._client = client if client is not None else openai.OpenAI(
            api_key=api_key, base_url=base_url, max_retries=0,
        )

    def complete_json(
        self, *, system: str, messages: list[dict[str, str]], schema_name: str,
        max_output_tokens: int, timeout_s: float,
    ) -> LlmJsonResult:
        started = time.monotonic()
        response = self._chat(
            system=system, messages=messages, max_output_tokens=max_output_tokens,
            timeout_s=timeout_s, json_mode=True,
        )
        content = response.choices[0].message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {}  # M4: any malformation => 'other' upstream (never re-called)
        if not isinstance(data, dict):
            data = {}
        return LlmJsonResult(data=data, usage=self._usage(response, started))

    def complete_text(
        self, *, system: str, messages: list[dict[str, str]],
        max_output_tokens: int, timeout_s: float,
    ) -> LlmTextResult:
        started = time.monotonic()
        response = self._chat(
            system=system, messages=messages, max_output_tokens=max_output_tokens,
            timeout_s=timeout_s, json_mode=False,
        )
        text = response.choices[0].message.content or ""
        return LlmTextResult(text=text, usage=self._usage(response, started))

    def _chat(
        self, *, system: str, messages: list[dict[str, str]],
        max_output_tokens: int, timeout_s: float, json_mode: bool,
    ) -> Any:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_output_tokens,
            # The router wants a stable verdict, not creativity - an adapter
            # detail, the port stays provider-neutral.
            "temperature": 0.0,
            "timeout": timeout_s,
        }
        if json_mode:
            # The router system prompt literally contains the word "JSON" (the
            # API's documented requirement for JSON output mode).
            payload["response_format"] = {"type": "json_object"}
        try:
            return self._client.chat.completions.create(**payload)
        except openai.APITimeoutError as exc:
            raise LlmTimeoutError(f"deepseek: no response within {timeout_s}s") from exc
        except openai.APIConnectionError as exc:  # connection reset / DNS / refused
            raise LlmProviderError("deepseek: connection error") from exc
        except openai.RateLimitError as exc:
            raise LlmProviderError("deepseek: rate limited (429)") from exc
        except openai.InternalServerError as exc:
            raise LlmProviderError("deepseek: server error (5xx)") from exc
        except openai.APIStatusError as exc:
            raise LlmProviderError(f"deepseek: http {exc.status_code}") from exc
        except openai.APIError as exc:
            raise LlmProviderError(f"deepseek: api error ({exc.__class__.__name__})") from exc

    def _usage(self, response: Any, started: float) -> LlmUsage:
        usage = getattr(response, "usage", None)
        return LlmUsage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            model=str(getattr(response, "model", None) or self._model),
            provider=PROVIDER_NAME,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def build(settings: Any) -> DeepSeekProvider:
    """Registry entry point: registry.build_provider imports this module by
    provider name and calls this with the worker settings."""
    return DeepSeekProvider(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
    )
