"""OpenAICompatibleClient: covers Ollama, LM Studio, vLLM, OpenAI itself, and
any other server speaking the same /v1/chat/completions wire format,
including its real multi-turn tool-calling loop (mirrors AnthropicClient's
loop shape using this provider's own wire format). Hermetic -- the
underlying AsyncOpenAI client is replaced with a fake, no real network call."""
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from hanno.excavator.openai_client import OpenAICompatibleClient
from hanno.skills.registry import SkillDefinition


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


class FakeChat:
    def __init__(self, completions):
        self.completions = completions


class FakeAsyncOpenAI:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = FakeChat(self.completions)
        self.closed = False

    async def close(self):
        self.closed = True


class FakeMessage:
    """Mimics the real SDK's pydantic ChatCompletionMessage just enough:
    plain attribute access for reads, and model_dump() for the one place
    the client re-serializes it back into the conversation history."""

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=False):
        d = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        if exclude_none:
            d = {k: v for k, v in d.items() if v is not None}
        return d


class FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=arguments)


def _response(content, prompt_tokens=10, completion_tokens=20, finish_reason="stop", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=FakeMessage(content, tool_calls), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _tool_call_response(call_id, name, arguments, **kwargs):
    return _response(
        content=None, finish_reason="tool_calls",
        tool_calls=[FakeToolCall(call_id, name, arguments)], **kwargs,
    )


def _client_with(responses):
    client = OpenAICompatibleClient(
        model="deepseek-r1-14b-hanno", max_tokens=2000, base_url="http://localhost:11434/v1",
    )
    fake = FakeAsyncOpenAI(responses)
    client._client = fake
    return client, fake


def _skill():
    return SkillDefinition(name="checker", description="d", command=["true"], input_description="i")


@pytest.mark.asyncio
async def test_generate_maps_response_to_generation_result():
    client, fake = _client_with([_response("a candidate", prompt_tokens=15, completion_tokens=42)])

    result = await client.generate("prompt", temperature=0.7)

    assert result.text == "a candidate"
    assert result.input_tokens == 15
    assert result.output_tokens == 42
    assert result.tool_rounds == 0
    assert fake.completions.requests[0]["model"] == "deepseek-r1-14b-hanno"
    assert fake.completions.requests[0]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_reasoning_trace_passes_through_as_plain_text():
    # DeepSeek-R1-style models emit <think> tags as regular output, not a
    # separate API field -- this client must not try to parse or strip them.
    client, fake = _client_with([_response("<think>reasoning here</think>final answer")])

    result = await client.generate("prompt", temperature=0.7)

    assert result.text == "<think>reasoning here</think>final answer"


@pytest.mark.asyncio
async def test_thinking_budget_tokens_is_a_no_op():
    client, fake = _client_with([_response("answer")])

    await client.generate("prompt", temperature=0.7, thinking_budget_tokens=8000)

    assert "thinking" not in fake.completions.requests[0]
    assert "thinking_budget_tokens" not in fake.completions.requests[0]


@pytest.mark.asyncio
async def test_missing_usage_defaults_to_zero_tokens():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=FakeMessage("answer"), finish_reason="stop")], usage=None,
    )
    client, fake = _client_with([response])

    result = await client.generate("prompt", temperature=0.7)

    assert result.input_tokens == 0
    assert result.output_tokens == 0


@pytest.mark.asyncio
async def test_close_closes_underlying_client():
    client, fake = _client_with([])

    await client.close()

    assert fake.closed is True


@pytest.mark.asyncio
async def test_tool_call_round_trip_executes_and_feeds_back_result():
    client, fake = _client_with([
        _tool_call_response("call_1", "checker", '{"input": "verify this"}'),
        _response("final answer after checking"),
    ])
    executed = {}

    def executor(name, tool_input):
        executed["name"] = name
        executed["input"] = tool_input
        return "check passed"

    result = await client.generate("prompt", temperature=0.7, tools=[_skill()], tool_executor=executor)

    assert result.text == "final answer after checking"
    assert result.tool_rounds == 1
    assert executed == {"name": "checker", "input": {"input": "verify this"}}
    second_request_messages = fake.completions.requests[1]["messages"]
    tool_result_msgs = [m for m in second_request_messages if m.get("role") == "tool"]
    assert len(tool_result_msgs) == 1
    assert tool_result_msgs[0]["content"] == "check passed"
    assert tool_result_msgs[0]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_tool_round_budget_exhaustion_forces_finalization():
    client, fake = _client_with([
        _tool_call_response("call_1", "checker", '{"input": "a"}'),
        _tool_call_response("call_2", "checker", '{"input": "b"}'),
        _response("forced final answer"),
    ])

    result = await client.generate(
        "prompt", temperature=0.7, tools=[_skill()], tool_executor=lambda n, i: "ok", max_tool_rounds=1,
    )

    assert result.text == "forced final answer"
    assert fake.completions.requests[2]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_stale_rounds_force_finalization_before_the_round_budget_is_hit():
    # Parity with AnthropicClient: 2 consecutive rounds that add nothing new
    # force finalization immediately rather than waiting out a much larger
    # max_tool_rounds.
    client, fake = _client_with([
        _tool_call_response("call_1", "checker", '{"input": "same check"}'),  # genuinely new
        _tool_call_response("call_2", "checker", '{"input": "same check"}'),  # repeat -> stale_rounds=1
        _tool_call_response("call_3", "checker", '{"input": "same check"}'),  # repeat -> stale_rounds=2
        _tool_call_response("call_4", "checker", '{"input": "same check"}'),  # arrives at threshold -> refused outright
        _response("forced final answer"),
    ])
    calls = []

    result = await client.generate(
        "prompt", temperature=0.7, tools=[_skill()],
        tool_executor=lambda n, i: calls.append((n, i)) or "verified", max_tool_rounds=10,
    )

    assert len(calls) == 1  # only the first call was ever genuinely executed
    assert result.text == "forced final answer"
    finalize_request = fake.completions.requests[4]
    assert finalize_request["tool_choice"] == "none"
    refusal = finalize_request["messages"][-1]
    assert refusal["tool_call_id"] == "call_4"
    assert "forced closed early" in refusal["content"]


@pytest.mark.asyncio
async def test_repeated_identical_tool_call_is_not_re_executed():
    # Parity with AnthropicClient (hanno/excavator/client.py): a repeated
    # identical tool call must be served from cache, not re-executed, with
    # the model told it's a repeat.
    client, fake = _client_with([
        _tool_call_response("call_1", "checker", '{"input": "same check"}'),
        _tool_call_response("call_2", "checker", '{"input": "same check"}'),
        _response("final"),
    ])
    calls = []

    def executor(name, tool_input):
        calls.append((name, tool_input))
        return "verified: no counterexample"

    result = await client.generate(
        "prompt", temperature=0.7, tools=[_skill()], tool_executor=executor, max_tool_rounds=3,
    )

    assert calls == [("checker", {"input": "same check"})]  # executed only once
    assert result.text == "final"
    second_tool_result = fake.completions.requests[2]["messages"][-1]
    assert second_tool_result["role"] == "tool"
    assert "already ran this exact check" in second_tool_result["content"]
    assert "verified: no counterexample" in second_tool_result["content"]


@pytest.mark.asyncio
async def test_different_inputs_to_the_same_tool_are_not_treated_as_repeats():
    client, fake = _client_with([
        _tool_call_response("call_1", "checker", '{"input": "check A"}'),
        _tool_call_response("call_2", "checker", '{"input": "check B"}'),
        _response("final"),
    ])
    calls = []

    result = await client.generate(
        "prompt", temperature=0.7, tools=[_skill()],
        tool_executor=lambda n, i: calls.append((n, i)) or "ok", max_tool_rounds=3,
    )

    assert len(calls) == 2  # both genuinely executed, not treated as a repeat
    assert result.text == "final"


@pytest.mark.asyncio
async def test_malformed_tool_arguments_json_does_not_crash():
    client, fake = _client_with([
        _tool_call_response("call_1", "checker", "not valid json{{{"),
        _response("answer despite malformed args"),
    ])
    received = {}

    def executor(name, tool_input):
        received["input"] = tool_input
        return "ok"

    result = await client.generate("prompt", temperature=0.7, tools=[_skill()], tool_executor=executor)

    assert result.text == "answer despite malformed args"
    assert received["input"] == {"input": "not valid json{{{"}


@pytest.mark.asyncio
async def test_tools_rejected_by_backend_retries_without_them():
    fake_response = httpx.Response(400, request=httpx.Request("POST", "http://localhost:11434/v1"))
    tool_error = BadRequestError(
        message="400: model 'deepseek-r1' does not support tools", response=fake_response, body=None,
    )

    class FlakyCompletions:
        def __init__(self, responses):
            self._responses = list(responses)
            self.requests = []
            self.calls = 0

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            self.calls += 1
            if self.calls == 1:
                raise tool_error
            return self._responses.pop(0)

    client = OpenAICompatibleClient(model="deepseek-r1", max_tokens=2000, base_url="http://localhost:11434/v1")
    completions = FlakyCompletions([_response("answer without tools")])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = await client.generate("prompt", temperature=0.7, tools=[_skill()], tool_executor=lambda n, i: "ok")

    assert result.text == "answer without tools"
    assert completions.calls == 2
    assert "tools" in completions.requests[0]
    assert "tools" not in completions.requests[1]
    assert client._tools_unsupported is True


@pytest.mark.asyncio
async def test_unrelated_bad_request_error_is_not_swallowed():
    fake_response = httpx.Response(400, request=httpx.Request("POST", "http://localhost:11434/v1"))
    other_error = BadRequestError(message="400: invalid model name", response=fake_response, body=None)

    class AlwaysFailingCompletions:
        async def create(self, **kwargs):
            raise other_error

    client = OpenAICompatibleClient(model="does-not-exist", max_tokens=2000, base_url="http://localhost:11434/v1")
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=AlwaysFailingCompletions()))

    with pytest.raises(BadRequestError):
        await client.generate("prompt", temperature=0.7)
