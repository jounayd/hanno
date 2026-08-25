"""autoformalize_with_retry: the state machine that retries a fixable
translation/compile failure with real feedback (the actual Lean error, or a
nudge past an over-eager UNTRANSLATABLE), up to max_attempts -- added after
the first pilot run showed most failures were fixable, not fundamental.
Hermetic: ClaudeClient and check_statement are both faked."""
from pathlib import Path
from unittest.mock import patch

import pytest

from hanno.excavator.client import GenerationResult
from hanno.lean import autoformalize as af_module
from hanno.lean.autoformalize import autoformalize_with_retry
from hanno.lean.checker import CheckResult

CANDIDATE = {"core_claim": "1 + 1 = 2", "formal_statement": "1 + 1 = 2"}


class ScriptedClaudeClient:
    """Returns each response in `responses`, in order, one per generate() call."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    async def generate(
        self, prompt: str, temperature: float, max_tokens: int | None = None,
        tools=None, tool_executor=None,
    ) -> GenerationResult:
        text = self._responses[self.calls]
        self.calls += 1
        return GenerationResult(text=text, input_tokens=10, output_tokens=10)


def _compiles(trivial_by=None):
    return CheckResult(compiles=True, trivial_by=trivial_by, stdout="", stderr="", duration_ms=100)


def _malformed():
    return CheckResult(compiles=False, trivial_by=None, stdout="error: bad", stderr="", duration_ms=100)


def _timed_out():
    return CheckResult(compiles=False, trivial_by=None, stdout="", stderr="", duration_ms=100, timed_out=True)


@pytest.mark.asyncio
async def test_succeeds_on_first_attempt_does_not_retry():
    client = ScriptedClaudeClient(["```lean\ntheorem foo : True := by sorry\n```"])
    with patch.object(af_module, "check_statement", return_value=_compiles()):
        result = await autoformalize_with_retry(CANDIDATE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.attempts == 1
    assert result.check.compiles is True
    assert client.calls == 1


@pytest.mark.asyncio
async def test_untranslatable_then_succeeds_on_retry():
    client = ScriptedClaudeClient(["UNTRANSLATABLE", "```lean\ntheorem foo : True := by sorry\n```"])
    with patch.object(af_module, "check_statement", return_value=_compiles()):
        result = await autoformalize_with_retry(CANDIDATE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.attempts == 2
    assert result.lean_code == "import Mathlib\n\ntheorem foo : True := by sorry"
    assert client.calls == 2
    # Phase 0 instrumentation: accumulates across every attempt, not just
    # the final one -- needed for real cost accounting on the deep-
    # verification stage.
    assert result.input_tokens == 20
    assert result.output_tokens == 20


@pytest.mark.asyncio
async def test_malformed_then_fixed_on_retry_with_error_feedback():
    client = ScriptedClaudeClient([
        "```lean\ntheorem foo : Bogus := by sorry\n```",
        "```lean\ntheorem foo : True := by sorry\n```",
    ])
    check_sequence = [_malformed(), _compiles()]
    with patch.object(af_module, "check_statement", side_effect=check_sequence):
        result = await autoformalize_with_retry(CANDIDATE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.attempts == 2
    assert result.check.compiles is True
    assert client.calls == 2


@pytest.mark.asyncio
async def test_exhausts_max_attempts_on_persistent_untranslatable():
    client = ScriptedClaudeClient(["UNTRANSLATABLE", "UNTRANSLATABLE", "UNTRANSLATABLE"])
    result = await autoformalize_with_retry(CANDIDATE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.attempts == 3
    assert result.lean_code is None
    assert result.check is None
    assert client.calls == 3


@pytest.mark.asyncio
async def test_exhausts_max_attempts_on_persistent_malformed():
    client = ScriptedClaudeClient([
        "```lean\ntheorem foo : Bogus1 := by sorry\n```",
        "```lean\ntheorem foo : Bogus2 := by sorry\n```",
        "```lean\ntheorem foo : Bogus3 := by sorry\n```",
    ])
    with patch.object(af_module, "check_statement", return_value=_malformed()):
        result = await autoformalize_with_retry(CANDIDATE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.attempts == 3
    assert result.check.compiles is False
    assert client.calls == 3


@pytest.mark.asyncio
async def test_timeout_stops_immediately_without_retrying():
    client = ScriptedClaudeClient(["```lean\ntheorem foo : Slow := by sorry\n```"])
    with patch.object(af_module, "check_statement", return_value=_timed_out()):
        result = await autoformalize_with_retry(CANDIDATE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.attempts == 1
    assert result.check.timed_out is True
    assert client.calls == 1
