"""prove_with_retry: the interactive proof-search loop (distinct from
autoformalize_with_retry, which only ever produces a `:= by sorry`
placeholder -- this is the step that tries to actually prove the statement).
Hermetic: ClaudeClient and check_proof are both faked, no real Lean
toolchain or API key needed."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hanno.excavator.client import GenerationResult
from hanno.lean import prove as prove_module
from hanno.lean.checker import CheckResult
from hanno.lean.prove import (
    KNOWN_FORMALIZATION_GOTCHAS,
    PROVE_PROMPT,
    RETRY_AFTER_ERROR_PROMPT,
    prove_with_retry,
)

LEAN_CODE = "theorem foo : 1 + 1 = 2 := by sorry"


class ScriptedClaudeClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    async def generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int | None = None,
        tools=None,
        tool_executor=None,
        **kwargs,
    ) -> GenerationResult:
        self.prompts.append(prompt)
        text = self._responses[self.calls]
        self.calls += 1
        return GenerationResult(text=text, input_tokens=10, output_tokens=10)

    async def close(self) -> None:
        pass


def _compiles():
    return CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=100)


def _malformed():
    return CheckResult(
        compiles=False, trivial_by=None, stdout="error: bad", stderr="", duration_ms=100
    )


def _cast_subtraction_defeq_error():
    # The exact error shape observed on the real Haiku attempt.
    stdout = (
        "error: 'show' tactic failed, pattern\n"
        "  ↑n ^ 13 - ↑n = 0\n"
        "is not definitionally equal to target\n"
        "  ↑(n ^ 13 - n) = 0\n"
    )
    return CheckResult(compiles=False, trivial_by=None, stdout=stdout, stderr="", duration_ms=100)


def _timed_out():
    return CheckResult(
        compiles=False, trivial_by=None, stdout="", stderr="", duration_ms=100, timed_out=True
    )


@pytest.mark.asyncio
async def test_complete_proof_on_first_attempt_succeeds():
    client = ScriptedClaudeClient(["```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```"])
    with patch.object(prove_module, "check_proof", return_value=_compiles()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    assert result.attempts == 1
    assert result.proof_code is not None
    assert "sorry" not in result.proof_code
    assert client.calls == 1


@pytest.mark.asyncio
async def test_cast_subtraction_defeq_error_triggers_a_targeted_hint():
    client = ScriptedClaudeClient(
        [
            "```lean\ntheorem foo : 1 + 1 = 2 := by exact bad\n```",
            "```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```",
        ]
    )
    check_sequence = [_cast_subtraction_defeq_error(), _compiles()]
    with patch.object(prove_module, "check_proof", side_effect=check_sequence):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    retry_prompt = client.prompts[1]
    assert "natural-subtraction/cast gotcha" in retry_prompt
    assert "Nat.ModEq" in retry_prompt


@pytest.mark.asyncio
async def test_unrelated_compile_error_does_not_trigger_the_cast_hint():
    client = ScriptedClaudeClient(
        [
            "```lean\ntheorem foo : 1 + 1 = 2 := by exact bad_tactic\n```",
            "```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```",
        ]
    )
    check_sequence = [_malformed(), _compiles()]
    with patch.object(prove_module, "check_proof", side_effect=check_sequence):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    retry_prompt = client.prompts[1]
    assert "natural-subtraction/cast gotcha" not in retry_prompt


def test_known_formalization_gotchas_appear_in_prompt_and_retry():
    # Observed verbatim on a real Haiku proof attempt for n^13 - n divisible
    # by 2730: the strategy was correct, but casting a NATURAL-NUMBER
    # subtraction into ZMod p failed on a defeq mismatch -- a known Mathlib
    # gotcha, not a math error. Must be surfaced both proactively (first
    # attempt) and reactively (after a compile error, where it's most useful).
    formatted_main = PROVE_PROMPT.format(lean_code="x")
    formatted_retry = RETRY_AFTER_ERROR_PROMPT.format(
        lean_code="x", previous_code="y", error_output="z"
    )
    assert KNOWN_FORMALIZATION_GOTCHAS in formatted_main
    assert KNOWN_FORMALIZATION_GOTCHAS in formatted_retry
    assert "Nat.ModEq" in KNOWN_FORMALIZATION_GOTCHAS
    assert "Nat.cast_sub" in KNOWN_FORMALIZATION_GOTCHAS


@pytest.mark.asyncio
async def test_lean4_fence_tag_is_recognized_same_as_lean():
    # Observed verbatim on DeepSeek-R1-14B: a complete, real proof attempt
    # (Fermat's Little Theorem case-split, finish_reason=stop, not truncated)
    # fenced as ```lean4 instead of ```lean was silently discarded as if it
    # were a literal CANNOT_PROVE -- across all retry attempts, since the
    # model consistently used this tag. Root-caused by replaying the exact
    # prompt against the real model and inspecting its raw response.
    client = ScriptedClaudeClient(["```lean4\ntheorem foo : 1 + 1 = 2 := by norm_num\n```"])
    with patch.object(prove_module, "check_proof", return_value=_compiles()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    assert client.calls == 1


@pytest.mark.asyncio
async def test_declines_then_succeeds_on_retry():
    client = ScriptedClaudeClient(
        [
            "CANNOT_PROVE",
            "```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```",
        ]
    )
    with patch.object(prove_module, "check_proof", return_value=_compiles()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    assert result.attempts == 2
    assert client.calls == 2
    # Phase 0 instrumentation: tokens accumulate across every attempt in the
    # loop, not just the final one -- this is what makes real cost
    # accounting for the deep-verification stage possible at all.
    assert result.input_tokens == 20  # 10 per call x 2 calls
    assert result.output_tokens == 20


@pytest.mark.asyncio
async def test_compiles_but_still_has_sorry_is_not_a_proof_and_retries():
    client = ScriptedClaudeClient(
        [
            "```lean\ntheorem foo : 1 + 1 = 2 := by sorry\n```",  # compiles, still incomplete
            "```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```",  # real proof
        ]
    )
    with patch.object(prove_module, "check_proof", return_value=_compiles()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    assert result.attempts == 2
    assert client.calls == 2


@pytest.mark.asyncio
async def test_does_not_compile_then_fixed_with_error_feedback():
    client = ScriptedClaudeClient(
        [
            "```lean\ntheorem foo : 1 + 1 = 2 := by exact bad_tactic\n```",
            "```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```",
        ]
    )
    check_sequence = [_malformed(), _compiles()]
    with patch.object(prove_module, "check_proof", side_effect=check_sequence):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_exhausts_max_attempts_never_proved():
    client = ScriptedClaudeClient(
        [
            "```lean\nattempt 1\n```",
            "```lean\nattempt 2\n```",
            "```lean\nattempt 3\n```",
        ]
    )
    with patch.object(prove_module, "check_proof", return_value=_malformed()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.proved is False
    assert result.attempts == 3
    assert client.calls == 3


@pytest.mark.asyncio
async def test_timeout_stops_immediately_without_retrying():
    client = ScriptedClaudeClient(["```lean\ntheorem foo : 1 + 1 = 2 := by slow_tactic\n```"])
    with patch.object(prove_module, "check_proof", return_value=_timed_out()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is False
    assert result.check is not None
    assert result.check.timed_out is True
    assert client.calls == 1


@pytest.mark.asyncio
async def test_missing_mathlib_import_in_proof_attempt_is_prepended():
    # Observed verbatim on a local DeepSeek-R1 distill: a proof attempt
    # (case-split via omega + simp) dropped the import line entirely,
    # causing spurious "failed to synthesize instance HSub ℕ ℕ" errors
    # unrelated to the actual proof. Same fix as autoformalize.py, shared
    # via hanno.lean.checker.ensure_mathlib_import.
    code = "theorem foo : 1 + 1 = 2 := by norm_num"
    client = ScriptedClaudeClient([f"```lean\n{code}\n```"])
    with patch.object(prove_module, "check_proof", return_value=_compiles()):
        result = await prove_with_retry(LEAN_CODE, client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proof_code is not None
    assert result.proof_code.startswith("import Mathlib")
    assert result.proof_code is not None
    assert "theorem foo : 1 + 1 = 2 := by norm_num" in result.proof_code
