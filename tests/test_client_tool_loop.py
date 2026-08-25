"""AnthropicClient agentic tool loop: when the model answers with tool_use,
the client must execute the tool via the executor, feed the result back,
keep accumulating token usage, and respect the round budget."""
from types import SimpleNamespace

import pytest

from hanno.excavator.client import AnthropicClient
from hanno.skills.registry import SkillDefinition

TOOLS = [SkillDefinition(name="checker", description="d", command=["true"], input_description="i")]


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(tool_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _response(content, stop_reason, tokens=(10, 20)):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class ScriptedAPI:
    """Stands in for AsyncAnthropic: returns scripted responses in order and
    records every request's messages."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)

    async def close(self):
        pass


def _client_with(responses):
    client = AnthropicClient(model="test-model", max_tokens=1000, api_key="test-key")
    client._client = ScriptedAPI(responses)
    return client


@pytest.mark.asyncio
async def test_no_tools_single_call_unchanged():
    client = _client_with([_response([_text_block("plain answer")], "end_turn")])
    result = await client.generate("prompt", 0.5)
    assert result.text == "plain answer"
    assert result.tool_rounds == 0
    assert result.input_tokens == 10 and result.output_tokens == 20


@pytest.mark.asyncio
async def test_tool_use_round_trip_executes_and_feeds_back():
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "test this"})], "tool_use"),
        _response([_text_block("final answer informed by tool")], "end_turn"),
    ]
    client = _client_with(responses)
    calls = []

    def executor(name, tool_input):
        calls.append((name, tool_input))
        return "tool says: verified"

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS, tool_executor=executor, max_tool_rounds=3,
    )

    assert calls == [("checker", {"input": "test this"})]
    assert result.text == "final answer informed by tool"
    assert result.tool_rounds == 1
    assert result.input_tokens == 20 and result.output_tokens == 40  # both rounds summed

    second_request = client._client.requests[1]
    tool_result_msg = second_request["messages"][-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert tool_result_msg["content"][0]["tool_use_id"] == "tu_1"
    assert "verified" in tool_result_msg["content"][0]["content"]


@pytest.mark.asyncio
async def test_round_budget_triggers_forced_finalization_not_prose_return():
    # Regression test for a failure mode observed in a real run: the model
    # wants tools forever; the old loop returned whatever prose preceded the
    # over-budget tool request ("Now let me test the tail bounds:"), which
    # can never parse as a candidate JSON. Now the pending call must be
    # refused via its tool_result and one final tool_choice="none" call must
    # extract the actual answer.
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "a"})], "tool_use"),
        _response([_tool_block("tu_2", "checker", {"input": "b"})], "tool_use"),
        _response([_text_block("Now let me test the tail bounds:"),
                   _tool_block("tu_3", "checker", {"input": "c"})], "tool_use"),
        _response([_text_block('{"final": "json answer"}')], "end_turn"),
    ]
    client = _client_with(responses)
    calls = []

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS,
        tool_executor=lambda n, i: calls.append((n, i)) or "ok",
        max_tool_rounds=2,
    )

    assert len(calls) == 2  # third tool request was NOT executed
    assert result.tool_rounds == 2
    assert result.text == '{"final": "json answer"}'  # NOT the mid-workflow prose
    assert result.input_tokens == 40 and result.output_tokens == 80  # all 4 calls billed

    finalize_request = client._client.requests[3]
    assert finalize_request["tool_choice"] == {"type": "none"}
    refusal = finalize_request["messages"][-1]["content"][0]
    assert refusal["type"] == "tool_result"
    assert refusal["tool_use_id"] == "tu_3"
    assert "budget exhausted" in refusal["content"].lower()


@pytest.mark.asyncio
async def test_finalization_refuses_every_pending_parallel_tool_call():
    # A single over-budget response can request several tools at once; the
    # API rejects the follow-up unless EVERY tool_use gets a tool_result.
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "a"})], "tool_use"),
        _response([_tool_block("tu_2", "checker", {"input": "b"}),
                   _tool_block("tu_3", "checker", {"input": "c"})], "tool_use"),
        _response([_text_block("final")], "end_turn"),
    ]
    client = _client_with(responses)

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS, tool_executor=lambda n, i: "ok", max_tool_rounds=1,
    )

    assert result.text == "final"
    refusals = client._client.requests[2]["messages"][-1]["content"]
    assert {r["tool_use_id"] for r in refusals} == {"tu_2", "tu_3"}
    assert all(r["type"] == "tool_result" for r in refusals)


@pytest.mark.asyncio
async def test_repeated_identical_tool_call_is_not_re_executed():
    # Observed verbatim on Claude Haiku 4.5: 5-6 tool rounds per candidate,
    # several re-running an equivalent run_code check instead of a
    # genuinely new investigation -- burning the whole round budget without
    # adding information. The second identical call must be served from
    # cache, not re-executed, and the model must be told it's a repeat.
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "same check"})], "tool_use"),
        _response([_tool_block("tu_2", "checker", {"input": "same check"})], "tool_use"),
        _response([_text_block("final")], "end_turn"),
    ]
    client = _client_with(responses)
    calls = []

    def executor(name, tool_input):
        calls.append((name, tool_input))
        return "verified: no counterexample"

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS, tool_executor=executor, max_tool_rounds=3,
    )

    assert calls == [("checker", {"input": "same check"})]  # executed only once
    assert result.text == "final"
    second_tool_result = client._client.requests[2]["messages"][-1]["content"][0]
    assert "already ran this exact check" in second_tool_result["content"]
    assert "verified: no counterexample" in second_tool_result["content"]  # cached output still surfaces


@pytest.mark.asyncio
async def test_stale_rounds_force_finalization_before_the_round_budget_is_hit():
    # Phase 2 cost control: 2 consecutive rounds that add nothing new (every
    # tool call in them already cache-served) force finalization immediately,
    # rather than waiting out the rest of a much larger max_tool_rounds --
    # directly targets the observed Haiku pattern of repeating an
    # already-answered check instead of investigating something new.
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "same check"})], "tool_use"),  # genuinely new
        _response([_tool_block("tu_2", "checker", {"input": "same check"})], "tool_use"),  # repeat #1 -> stale_rounds=1
        _response([_tool_block("tu_3", "checker", {"input": "same check"})], "tool_use"),  # repeat #2 -> stale_rounds=2
        _response([_tool_block("tu_4", "checker", {"input": "same check"})], "tool_use"),  # arrives with stale_rounds already at threshold -> refused outright
        _response([_text_block('{"final": "json"}')], "end_turn"),
    ]
    client = _client_with(responses)
    calls = []

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS,
        tool_executor=lambda n, i: calls.append((n, i)) or "verified", max_tool_rounds=10,
    )

    assert len(calls) == 1  # only the first call was ever genuinely executed
    assert result.text == '{"final": "json"}'
    finalize_request = client._client.requests[4]
    assert finalize_request["tool_choice"] == {"type": "none"}
    refusal = finalize_request["messages"][-1]["content"][0]
    assert refusal["tool_use_id"] == "tu_4"
    assert "forced closed early" in refusal["content"]


@pytest.mark.asyncio
async def test_a_genuinely_new_call_resets_the_stale_rounds_counter():
    # One repeat followed by a genuinely different call must NOT accumulate
    # toward the stale threshold -- only CONSECUTIVE stale rounds count.
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "check A"})], "tool_use"),
        _response([_tool_block("tu_2", "checker", {"input": "check A"})], "tool_use"),  # repeat -> stale_rounds=1
        _response([_tool_block("tu_3", "checker", {"input": "check B"})], "tool_use"),  # genuinely new -> resets to 0
        _response([_tool_block("tu_4", "checker", {"input": "check B"})], "tool_use"),  # repeat -> stale_rounds=1 again, not 2
        _response([_text_block("final")], "end_turn"),
    ]
    client = _client_with(responses)
    calls = []

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS,
        tool_executor=lambda n, i: calls.append((n, i)) or "ok", max_tool_rounds=10,
    )

    assert len(calls) == 2  # check A and check B each executed once
    assert result.text == "final"  # never force-finalized -- reached a natural end_turn


@pytest.mark.asyncio
async def test_different_inputs_to_the_same_tool_are_not_treated_as_repeats():
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "check A"})], "tool_use"),
        _response([_tool_block("tu_2", "checker", {"input": "check B"})], "tool_use"),
        _response([_text_block("final")], "end_turn"),
    ]
    client = _client_with(responses)
    calls = []

    result = await client.generate(
        "prompt", 0.5, tools=TOOLS,
        tool_executor=lambda n, i: calls.append((n, i)) or "ok", max_tool_rounds=3,
    )

    assert len(calls) == 2  # both genuinely executed, not treated as a repeat
    assert result.text == "final"


@pytest.mark.asyncio
async def test_no_cache_prefix_keeps_plain_string_content():
    client = _client_with([_response([_text_block("answer")], "end_turn")])
    await client.generate("prompt text", 0.5)
    assert client._client.requests[0]["messages"][0]["content"] == "prompt text"


@pytest.mark.asyncio
async def test_cache_prefix_splits_into_a_cached_block_and_remainder():
    client = _client_with([_response([_text_block("answer")], "end_turn")])
    prompt = "STABLE INSTRUCTIONS\n\nSeed concept to explore:\nsome seed"
    await client.generate(prompt, 0.5, cache_prefix="STABLE INSTRUCTIONS")

    content = client._client.requests[0]["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "STABLE INSTRUCTIONS", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "\n\nSeed concept to explore:\nsome seed"},
    ]


@pytest.mark.asyncio
async def test_cache_prefix_not_a_real_prefix_falls_back_to_plain_string():
    client = _client_with([_response([_text_block("answer")], "end_turn")])
    await client.generate("actual prompt", 0.5, cache_prefix="this text is not a prefix")
    assert client._client.requests[0]["messages"][0]["content"] == "actual prompt"


@pytest.mark.asyncio
async def test_executor_exception_becomes_tool_error_result_not_crash():
    responses = [
        _response([_tool_block("tu_1", "checker", {"input": "x"})], "tool_use"),
        _response([_text_block("recovered")], "end_turn"),
    ]
    client = _client_with(responses)

    def broken(name, tool_input):
        raise RuntimeError("tool exploded")

    result = await client.generate("prompt", 0.5, tools=TOOLS, tool_executor=broken)
    assert result.text == "recovered"
    fed_back = client._client.requests[1]["messages"][-1]["content"][0]["content"]
    assert "tool execution error" in fed_back
