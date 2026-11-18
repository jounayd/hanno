"""Async Anthropic API client wrapper with rate-limit retry/backoff and an
optional agentic tool loop for miner-registered skills (hanno/skills/):
when tools are supplied, generation becomes multi-turn — the model may call
a tool, Hanno executes it locally via `tool_executor`, and the result is fed
back in until the model finishes or the round budget runs out.

AnthropicClient is one of possibly several ModelClient implementations (see
hanno/excavator/openai_client.py, hanno/excavator/client_factory.py) -- the
Protocol below formalizes a shape every test fake in this codebase already
implemented without anyone declaring it (FakeClaudeClient, ScriptedClient,
etc. never subclassed the old ClaudeClient, they just duck-typed generate()
and close()).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from anthropic import APIStatusError, AsyncAnthropic, RateLimitError
from anthropic.types import Message

from hanno.excavator.spend_tracker import RequestBudget
from hanno.logging_setup import get_logger
from hanno.skills.registry import SkillDefinition, to_anthropic_tools

logger = get_logger("excavator.client")

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0

# Observed verbatim on Claude Haiku 4.5: 5-6 tool rounds per candidate, most
# of them re-running an equivalent-or-near-identical run_code check instead
# of a genuinely new investigation -- burning the whole round budget (and,
# since each round re-bills the full growing conversation, tens of
# thousands of tokens) without adding information. This is exactly the
# "trajectory reduction" failure the migration plan's Phase 2 targets: skip
# re-executing a call already made this generation, and tell the model so,
# rather than silently letting it repeat itself until the budget runs out.
REPEATED_TOOL_CALL_MESSAGE = (
    "[You already ran this exact check earlier in this investigation and got "
    "the result below -- it was not re-executed. Repeating the same check "
    "will not produce new information: try a genuinely different "
    "verification, or finalize your answer now if you've learned enough.]\n"
)


def _canonical_tool_call_key(name: str, tool_input: object) -> tuple[str, str]:
    try:
        canonical_input = json.dumps(tool_input, sort_keys=True)
    except TypeError:
        canonical_input = str(tool_input)
    return (name, canonical_input)


def _build_initial_content(prompt: str, cache_prefix: str | None) -> str | list[dict]:
    """When the caller identifies a literal prefix of `prompt` that is
    byte-identical across many calls (e.g. hanno/excavator/prompts.py's
    per-variant instructions, which never change across candidates or
    seeds), split it into its own content block with an ephemeral cache
    breakpoint so repeated calls read it from cache instead of rebilling
    it as fresh input. Below Anthropic's per-model minimum cacheable
    length, the API silently serves the block uncached rather than
    erroring, so this is always safe to set even for short prompts."""
    if not cache_prefix or not prompt.startswith(cache_prefix):
        return prompt
    remainder = prompt[len(cache_prefix) :]
    content = [{"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}}]
    if remainder:
        content.append({"type": "text", "text": remainder})
    return content


# Fed back as the tool_result for every tool call requested AFTER the round
# budget is exhausted. Observed failure mode without this: the model is
# mid-investigation ("Now let me test the tail bounds:" + tool_use), the loop
# returns that prose as the candidate text, and JSON parsing fails — every
# tool-using candidate in a real run parse-failed this way.
TOOL_BUDGET_EXHAUSTED_MESSAGE = (
    "Tool budget exhausted — this call was NOT executed and no further tool "
    "calls are available. Stop investigating now. Based on what you have "
    "already verified, respond immediately with the single required JSON "
    "object — no prose before or after it. If parts of your claim remain "
    "unverified, say so honestly in identified_weaknesses and lower "
    "self_rated_rigor accordingly rather than omitting the JSON."
)

# Phase 2 cost control (EXCAVATOR_MIGRATION_PLAN.md): a dynamic, earlier
# version of the same forced-finalization mechanism above. The duplicate-call
# cache already stops a repeated call from being RE-EXECUTED; this stops the
# ROUND ITSELF from continuing once it's clear the model is just repeating —
# no genuinely new tool call for this many consecutive rounds — rather than
# waiting out the rest of a fixed round budget that was never going to add
# information. Directly targets the observed Haiku pattern (5-6 rounds/
# candidate, most re-running an equivalent already-seen check).
STALE_ROUNDS_THRESHOLD = 2
STALE_LOOP_MESSAGE = (
    "Tool budget forced closed early — the last few rounds repeated checks "
    "you already ran without learning anything new. Stop investigating now. "
    "Based on what you have already verified, respond immediately with the "
    "single required JSON object — no prose before or after it. If parts of "
    "your claim remain unverified, say so honestly in identified_weaknesses "
    "and lower self_rated_rigor accordingly rather than omitting the JSON."
)


@dataclass
class GenerationResult:
    text: str
    input_tokens: int
    output_tokens: int
    tool_rounds: int = 0


class ModelClient(Protocol):
    """The shape every backend adapter implements -- formalizes what every
    test fake in this codebase already duck-typed. Callers (ExcavatorPipeline,
    hanno/lean/*, hanno/verification/*) depend on this, never on a concrete
    provider class."""

    async def generate(
        self,
        prompt: str,
        temperature: float,
        *,
        max_tokens: int | None = None,
        thinking_budget_tokens: int | None = None,
        tools: list[SkillDefinition] | None = None,
        tool_executor: Callable[[str, dict], str] | None = None,
        max_tool_rounds: int = 3,
        cache_prefix: str | None = None,
    ) -> GenerationResult: ...

    async def close(self) -> None: ...


class AnthropicClient:
    def __init__(
        self,
        model: str,
        max_tokens: int,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        budget: RequestBudget | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._budget = budget
        self.usage_accounted = budget is not None
        self._client = AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=timeout_seconds,
            max_retries=0,  # this wrapper reserves budget separately for every retry
        )

    async def _create_with_retry(self, kwargs: dict) -> Message:
        attempt = 0
        while True:
            try:
                call_id = self._budget.reserve(kwargs) if self._budget is not None else None
                try:
                    response = await self._client.messages.create(**kwargs)
                except APIStatusError as error:
                    if call_id is not None and self._budget is not None and error.status_code < 500:
                        self._budget.settle(call_id, 0, 0)
                    raise
                if call_id is not None and self._budget is not None:
                    # Include cache tokens conservatively at the normal input rate.
                    usage = response.usage
                    input_tokens = (
                        usage.input_tokens
                        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
                        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
                    )
                    self._budget.settle(call_id, input_tokens, usage.output_tokens)
                return response
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
        # Translated to this provider's wire format here (not by the caller):
        # ExcavatorPipeline passes raw SkillDefinitions so it never needs to
        # know which backend's tool-calling shape is active.
        anthropic_tools = to_anthropic_tools(tools) if tools else None
        messages: list[dict] = [
            {"role": "user", "content": _build_initial_content(prompt, cache_prefix)}
        ]
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
                "messages": messages,
            }
            if thinking_budget_tokens is not None:
                # Extended thinking requires the API's own fixed sampling
                # temperature — passing an explicit temperature alongside
                # it is rejected, so it's omitted for these calls.
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget_tokens}
            else:
                kwargs["temperature"] = temperature
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools
                if finalizing:
                    # Tool budget spent: the API must not let the model call
                    # again — it can only answer. (tools stays present because
                    # the conversation already contains tool_use blocks.)
                    kwargs["tool_choice"] = {"type": "none"}

            response = await self._create_with_retry(kwargs)
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            wants_tools = (
                tools
                and tool_executor is not None
                and response.stop_reason == "tool_use"
                and not finalizing
            )
            if not wants_tools:
                text = "".join(block.text for block in response.content if block.type == "text")
                return GenerationResult(
                    text=text,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    tool_rounds=rounds,
                )

            messages.append({"role": "assistant", "content": response.content})

            if rounds >= max_tool_rounds or stale_rounds >= STALE_ROUNDS_THRESHOLD:
                # Budget exhausted (fixed cap) OR the model is demonstrably
                # stuck repeating (dynamic, earlier stop). Either way: do NOT
                # execute the pending call(s); refuse each one via its
                # tool_result (the API requires every tool_use to be
                # answered) and make one last tool_choice="none" call so the
                # model emits its final JSON instead of the loop returning
                # mid-workflow prose.
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
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": refusal_message,
                            }
                            for block in response.content
                            if block.type == "tool_use"
                        ],
                    }
                )
                finalizing = True
                continue

            # Feed the full assistant content back (including any thinking
            # blocks — required by the API when thinking + tools combine),
            # execute each requested tool off the event loop, and continue.
            tool_results = []
            any_new_this_round = False
            for block in response.content:
                if block.type != "tool_use":
                    continue
                key = _canonical_tool_call_key(block.name, block.input)
                if key in seen_tool_calls:
                    output = REPEATED_TOOL_CALL_MESSAGE + seen_tool_calls[key]
                else:
                    any_new_this_round = True
                    try:
                        assert tool_executor is not None
                        output = await asyncio.to_thread(tool_executor, block.name, block.input)
                    except Exception as e:  # noqa: BLE001 — a broken tool must not kill the candidate
                        logger.error(f"tool {block.name} raised: {e}")
                        output = f"tool execution error: {e}"
                    seen_tool_calls[key] = output
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            rounds += 1
            stale_rounds = 0 if any_new_this_round else stale_rounds + 1

    async def close(self) -> None:
        await self._client.close()
