"""hanno.lean.autoformalize: translating an informal candidate into Lean 4
syntax via a single one-shot Claude call (not the agentic tool loop) --
hermetic, no real API key or network call."""

import pytest

from hanno.excavator.client import GenerationResult
from hanno.lean.autoformalize import (
    AUTOFORMALIZE_PROMPT,
    RETRY_AFTER_DECLINE_PROMPT,
    STRENGTH_PRESERVATION_RULES,
    WORKED_EXAMPLES,
    autoformalize,
)


class FakeClaudeClient:
    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    async def generate(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int | None = None,
        tools=None,
        tool_executor=None,
        **kwargs,
    ) -> GenerationResult:
        self.calls += 1
        return GenerationResult(text=self._text, input_tokens=10, output_tokens=10)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_extracts_lean_code_from_fenced_block():
    client = FakeClaudeClient("```lean\ntheorem foo : 1 + 1 = 2 := by sorry\n```")
    candidate = {"core_claim": "1 + 1 = 2", "formal_statement": "1 + 1 = 2"}

    result = await autoformalize(candidate, client)

    assert result.lean_code == "import Mathlib\n\ntheorem foo : 1 + 1 = 2 := by sorry"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_untranslatable_response_yields_no_lean_code():
    client = FakeClaudeClient("UNTRANSLATABLE")
    candidate = {"core_claim": "vague hand-wavy idea", "formal_statement": "something something"}

    result = await autoformalize(candidate, client)

    assert result.lean_code is None
    assert result.raw_response == "UNTRANSLATABLE"


@pytest.mark.asyncio
async def test_lean4_fence_tag_is_recognized_same_as_lean():
    # Observed verbatim on DeepSeek-R1-14B: a complete, valid response fenced
    # as ```lean4 instead of ```lean (a reasonable, common tag for "the
    # Lean 4 language") was silently discarded as if UNTRANSLATABLE -- five
    # real responses in a row were never even seen, not five genuine declines.
    client = FakeClaudeClient("```lean4\ntheorem foo : 1 + 1 = 2 := by sorry\n```")
    candidate = {"core_claim": "1 + 1 = 2", "formal_statement": "1 + 1 = 2"}

    result = await autoformalize(candidate, client)

    assert result.lean_code == "import Mathlib\n\ntheorem foo : 1 + 1 = 2 := by sorry"


@pytest.mark.asyncio
async def test_response_without_code_block_yields_no_lean_code():
    client = FakeClaudeClient("sorry, I can't help with that")
    candidate = {"core_claim": "x", "formal_statement": "y"}

    result = await autoformalize(candidate, client)

    assert result.lean_code is None


@pytest.mark.asyncio
async def test_missing_mathlib_import_is_prepended():
    # Observed verbatim on a local DeepSeek-R1 distill on a retry-after-error
    # response: the model dropped the import line entirely, which caused
    # spurious "failed to synthesize instance LE ℕ" errors unrelated to the
    # actual proof.
    code = "theorem cube_minus_self_div_six (n : ℕ) (hn : 1 ≤ n) : 6 ∣ (n ^ 3 - n) := by\n  rw [Nat.mod_eq_zero_iff_dvd]\n  ring_nf\n  omega"
    client = FakeClaudeClient(f"```lean\n{code}\n```")
    candidate = {"core_claim": "c", "formal_statement": "f"}

    result = await autoformalize(candidate, client)

    assert result.lean_code is not None
    assert result.lean_code.startswith("import Mathlib")
    assert "cube_minus_self_div_six" in result.lean_code


@pytest.mark.asyncio
async def test_existing_mathlib_import_is_not_duplicated():
    code = "import Mathlib\n\ntheorem foo (n : ℕ) : n = n := by rfl"
    client = FakeClaudeClient(f"```lean\n{code}\n```")
    candidate = {"core_claim": "c", "formal_statement": "f"}

    result = await autoformalize(candidate, client)

    assert result.lean_code is not None
    assert result.lean_code.count("import Mathlib") == 1


def test_worked_examples_use_real_lean_syntax_and_appear_in_both_prompts():
    # Weaker/smaller models are more likely to decline UNTRANSLATABLE simply
    # from not knowing Lean 4/Mathlib syntax, not because the claim is
    # actually unformalizable -- few-shot examples are a cheap, well-known
    # fix for that. Both the prompt and its decline-retry variant must
    # carry them so a model that declines gets the same nudge on retry.
    assert "```lean" in WORKED_EXAMPLES
    assert ":= by sorry" in WORKED_EXAMPLES
    formatted_main = AUTOFORMALIZE_PROMPT.format(core_claim="c", formal_statement="f")
    formatted_retry = RETRY_AFTER_DECLINE_PROMPT.format(core_claim="c", formal_statement="f")
    assert WORKED_EXAMPLES in formatted_main
    assert WORKED_EXAMPLES in formatted_retry


def test_strength_preservation_rules_appear_in_both_prompts():
    # Two real faithfulness bugs observed in production: a claim naming a
    # specific constant got formalized as `exists C, ...` and vacuously
    # satisfied by C=1; a divisibility claim got formalized with the
    # relation backwards. Both compiled and passed the triviality filter.
    # These rules must reach the model on both the first attempt and the
    # decline-retry, not just one.
    formatted_main = AUTOFORMALIZE_PROMPT.format(core_claim="c", formal_statement="f")
    formatted_retry = RETRY_AFTER_DECLINE_PROMPT.format(core_claim="c", formal_statement="f")
    assert STRENGTH_PRESERVATION_RULES in formatted_main
    assert STRENGTH_PRESERVATION_RULES in formatted_retry
    assert "direction of every relation" in STRENGTH_PRESERVATION_RULES
