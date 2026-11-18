"""OpenAI-compatible ModelClient adapter -- covers OpenAI itself, and every
self-hosted or hosted server that speaks the same /v1/chat/completions wire
format (Ollama, LM Studio, vLLM, Groq, Together, OpenRouter, ...). One
adapter, many backends, since `base_url` is the only thing that changes.

Supports the Excavator's tool/skills agentic loop via OpenAI's standard
function-calling wire format -- mirrors AnthropicClient.generate()'s loop
shape (hanno/excavator/client.py) using this provider's own request/response
shapes instead of Anthropic's tool-use content blocks. Tools are attempted
by default (OpenAI itself and many Ollama-served models genuinely support
this); if the specific server/model rejects the `tools` parameter outright
(a BadRequestError naming tool support), that's caught once and the request
is transparently retried without tools rather than failing the candidate. A
model that silently ignores tools and just answers directly (common on
local servers that don't understand function-calling but don't error
either) already degrades gracefully with no special handling needed.

`thinking_budget_tokens` is a no-op here: reasoning models in the DeepSeek-R1
family don't take an API parameter for it, they just emit `<think>` tags as
regular output text -- see LOCAL_MODEL_SETUP.md.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from openai import APIStatusError, AsyncOpenAI, BadRequestError, RateLimitError
from openai.types.chat import ChatCompletion

from hanno.excavator.client import (
    REPEATED_TOOL_CALL_MESSAGE,
    STALE_LOOP_MESSAGE,
    STALE_ROUNDS_THRESHOLD,
    TOOL_BUDGET_EXHAUSTED_MESSAGE,
    GenerationResult,
    _canonical_tool_call_key,
)
from hanno.logging_setup import get_logger
from hanno.skills.registry import SkillDefinition, to_openai_tools

logger = get_logger("excavator.openai_client")

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0

# The SDK requires a truthy api_key at construction even when the target
# server (e.g. Ollama) doesn't check auth at all.
_PLACEHOLDER_API_KEY = "not-needed"

# Substrings looked for in a BadRequestError's message to distinguish "this
# model/server doesn't support tool-calling" from any other 400 (bad model
# name, malformed request, etc.) -- only the former should trigger a silent
# retry without tools; anything else should surface as a real error.
_TOOL_UNSUPPORTED_HINTS = ("tool", "function_call", "function calling")


def _looks_like_tool_unsupported(error: BadRequestError) -> bool:
    message = str(error).lower()
    return any(hint in message for hint in _TOOL_UNSUPPORTED_HINTS)


class OpenAICompatibleClient:
    def __init__(
        self,
        model: str,
        max_tokens: int,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = AsyncOpenAI(
            api_key=api_key or _PLACEHOLDER_API_KEY,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._tools_unsupported = False  # sticky once discovered, avoids re-probing every call

    async def _create_with_retry(self, kwargs: dict) -> ChatCompletion:
        attempt = 0
        while True:
            try:
                return await self._client.chat.completions.create(**kwargs)
            except RateLimitError:
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(f"rate limited, retrying in {backoff:.1f}s (attempt {attempt})")
                await asyncio.sleep(backoff)
            except APIStatusError as e:
                attempt += 1
                if attempt > MAX_RETRIES or e.status_code < 500:
                    raise
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(f"API error {e.status_code}, retrying in {backoff:.1f}s")
                await asyncio.sleep(backoff)

    async def generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int | None = None,
        thinking_budget_tokens: int | None = None,
        tools: list[SkillDefinition] | None = None,
        tool_executor: Callable[[str, dict], str] | None = None,
        max_tool_rounds: int = 3,
        cache_prefix: str | None = None,
    ) -> GenerationResult:
        # A no-op here, like thinking_budget_tokens above: OpenAI's own API
        # already caches long stable prefixes automatically (no manual
        # breakpoint marker in this wire format, unlike Anthropic's explicit
        # cache_control), and most local servers either do their own prefix
        # caching or don't benefit from one at all -- nothing to wire up.
        del cache_prefix
        openai_tools = to_openai_tools(tools) if (tools and not self._tools_unsupported) else None
        messages: list[dict] = [{"role": "user", "content": prompt}]
        total_input = 0
        total_output = 0
        rounds = 0
        stale_rounds = 0
        finalizing = False
        seen_tool_calls: dict[tuple[str, str], str] = {}

        while True:
            kwargs: dict = {
                "model": self._model,
                "max_tokens": max_tokens or self._max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools
                if finalizing:
                    kwargs["tool_choice"] = "none"

            try:
                response = await self._create_with_retry(kwargs)
            except BadRequestError as e:
                if openai_tools and _looks_like_tool_unsupported(e):
                    logger.warning(
                        "tools rejected by this backend (%s), retrying without them: %s",
                        self._model,
                        e,
                    )
                    self._tools_unsupported = True
                    openai_tools = None
                    continue
                raise

            total_input += response.usage.prompt_tokens if response.usage else 0
            total_output += response.usage.completion_tokens if response.usage else 0
            choice = response.choices[0]

            wants_tools = (
                openai_tools
                and tool_executor is not None
                and choice.finish_reason == "tool_calls"
                and not finalizing
            )
            if not wants_tools:
                return GenerationResult(
                    text=choice.message.content or "",
                    input_tokens=total_input,
                    output_tokens=total_output,
                    tool_rounds=rounds,
                )

            messages.append(choice.message.model_dump(exclude_none=True))
            tool_calls = choice.message.tool_calls or []

            if rounds >= max_tool_rounds or stale_rounds >= STALE_ROUNDS_THRESHOLD:
                if rounds >= max_tool_rounds:
                    logger.info(
                        "tool round budget (%d) exhausted; forcing finalization", max_tool_rounds
                    )
                    refusal_message = TOOL_BUDGET_EXHAUSTED_MESSAGE
                else:
                    logger.info(
                        "%d consecutive stale tool round(s); forcing finalization early",
                        stale_rounds,
                    )
                    refusal_message = STALE_LOOP_MESSAGE
                for call in tool_calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": refusal_message,
                        }
                    )
                finalizing = True
                continue

            any_new_this_round = False
            for call in tool_calls:
                if call.type != "function":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": "Unsupported tool call type; use the declared function tools.",
                        }
                    )
                    continue
                try:
                    args = json.loads(call.function.arguments)
                    input_text = args.get("input", "") if isinstance(args, dict) else str(args)
                except (json.JSONDecodeError, AttributeError):
                    # The SDK itself warns arguments "may hallucinate parameters
                    # not defined by your function schema" or be invalid JSON --
                    # fall back to the raw string rather than dropping the call.
                    input_text = call.function.arguments or ""
                key = _canonical_tool_call_key(call.function.name, {"input": input_text})
                if key in seen_tool_calls:
                    output = REPEATED_TOOL_CALL_MESSAGE + seen_tool_calls[key]
                else:
                    any_new_this_round = True
                    try:
                        assert tool_executor is not None
                        output = await asyncio.to_thread(
                            tool_executor, call.function.name, {"input": input_text}
                        )
                    except Exception as e:  # noqa: BLE001 -- a broken tool must not kill the candidate
                        logger.error(f"tool {call.function.name} raised: {e}")
                        output = f"tool execution error: {e}"
                    seen_tool_calls[key] = output
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
            rounds += 1
            stale_rounds = 0 if any_new_this_round else stale_rounds + 1

    async def close(self) -> None:
        await self._client.close()
