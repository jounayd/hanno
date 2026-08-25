"""decompose_and_prove (EXCAVATOR_MIGRATION_PLAN.md Phase 6): when a proof
attempt already typechecks in the `have h1 : S1 := by sorry ... have hN : SN
:= by sorry <combine>` sketch shape, solve each stubbed sub-goal
independently instead of regenerating the whole combined proof from scratch
on any failure. Observed verbatim on a real Haiku attempt at "n^13 - n
divisible by 2730": four of five prime-factor sub-lemmas were fine, one bad
lemma name in the final combination step failed the ENTIRE proof, and the
existing retry loop would have regenerated everything from scratch.

Hermetic: ClaudeClient and check_proof are both faked, no real Lean
toolchain or API key needed."""
from pathlib import Path
from unittest.mock import patch

import pytest

from hanno.excavator.client import GenerationResult
from hanno.lean import prove as prove_module
from hanno.lean.checker import CheckResult
from hanno.lean.prove import (
    _build_subgoal_theorem,
    _extract_proof_body,
    _parse_have_sketch,
    _split_at_top_level,
    prove_with_retry,
)

TOP_LEVEL_CODE = "theorem six_dvd_six_n (n : ℕ) : 6 ∣ (6 * n) := by sorry"

SKETCH = """```lean
import Mathlib

theorem six_dvd_six_n (n : ℕ) : 6 ∣ (6 * n) := by
  have h2 : 2 ∣ (6 * n) := by sorry
  have h3 : 3 ∣ (6 * n) := by sorry
  omega
```"""

SUB_H2_SOLVED = "```lean\nimport Mathlib\n\ntheorem sub_h2 (n : ℕ) : 2 ∣ (6 * n) := by omega\n```"
SUB_H3_SOLVED = "```lean\nimport Mathlib\n\ntheorem sub_h3 (n : ℕ) (h2 : 2 ∣ (6 * n)) : 3 ∣ (6 * n) := by omega\n```"
SUB_H3_BAD = "```lean\nimport Mathlib\n\ntheorem sub_h3 (n : ℕ) (h2 : 2 ∣ (6 * n)) : 3 ∣ (6 * n) := by exact bogus_lemma\n```"
FALLBACK_WHOLE_PROOF = "```lean\nimport Mathlib\n\ntheorem six_dvd_six_n (n : ℕ) : 6 ∣ (6 * n) := by omega\n```"

PLAIN_ONE_SHOT_PROOF = "```lean\ntheorem foo : 1 + 1 = 2 := by norm_num\n```"


class ScriptedClaudeClient:
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


def _compiles():
    return CheckResult(compiles=True, trivial_by=None, stdout="", stderr="", duration_ms=100)


def _malformed():
    return CheckResult(compiles=False, trivial_by=None, stdout="error: unknown identifier bogus_lemma", stderr="", duration_ms=100)


# ---------------------------------------------------------------------------
# Parser unit tests -- pure functions, no mocking needed.
# ---------------------------------------------------------------------------

def test_split_at_top_level_skips_nested_markers():
    result = _split_at_top_level("(n : ℕ) : 2730 ∣ (n ^ 13 - n)", ":")
    assert result == ("(n : ℕ)", "2730 ∣ (n ^ 13 - n)", 8)


def test_split_at_top_level_returns_none_when_marker_absent():
    assert _split_at_top_level("no marker here", ":=") is None


def test_parse_have_sketch_recognizes_stub_haves_and_final_block():
    code = (
        "import Mathlib\n\n"
        "theorem foo (n : ℕ) : 6 ∣ (6 * n) := by\n"
        "  have h2 : 2 ∣ (6 * n) := by sorry\n"
        "  have h3 : 3 ∣ (6 * n) := by sorry\n"
        "  omega"
    )
    sketch = _parse_have_sketch(code)
    assert sketch is not None
    assert sketch.binders == "(n : ℕ)"
    assert sketch.goal == "6 ∣ (6 * n)"
    assert [(name, stmt) for name, stmt, _ in sketch.all_haves] == [
        ("h2", "2 ∣ (6 * n)"), ("h3", "3 ∣ (6 * n)"),
    ]
    assert sketch.final_block == "omega"


def test_parse_have_sketch_keeps_already_solved_haves_as_prior_hypotheses():
    # Mirrors the real Haiku CRT attempt: a coprimality fact already proved
    # inline (`by norm_num`, not a stub) must still be usable as a
    # hypothesis for a LATER stub sub-goal that depends on it.
    code = (
        "import Mathlib\n\n"
        "theorem foo (n : ℕ) : 6 ∣ (6 * n) := by\n"
        "  have h2 : 2 ∣ (6 * n) := by sorry\n"
        "  have cop : Nat.Coprime 2 3 := by norm_num\n"
        "  have h6 : 6 ∣ (6 * n) := by sorry\n"
        "  exact h6"
    )
    sketch = _parse_have_sketch(code)
    assert sketch is not None
    names_and_kinds = [(name, span is not None) for name, _, span in sketch.all_haves]
    assert names_and_kinds == [("h2", True), ("cop", False), ("h6", True)]


def test_parse_have_sketch_returns_none_for_a_plain_proof():
    assert _parse_have_sketch("theorem foo : 1 + 1 = 2 := by norm_num") is None


def test_parse_have_sketch_returns_none_when_every_have_already_solved():
    code = (
        "theorem foo (n : ℕ) : True := by\n"
        "  have h : 1 = 1 := by rfl\n"
        "  trivial"
    )
    assert _parse_have_sketch(code) is None


def test_build_subgoal_theorem_includes_prior_haves_as_hypotheses():
    result = _build_subgoal_theorem("sub_h3", "(n : ℕ)", [("h2", "2 ∣ (6 * n)")], "3 ∣ (6 * n)")
    assert result == "import Mathlib\n\ntheorem sub_h3 (n : ℕ) (h2 : 2 ∣ (6 * n)) : 3 ∣ (6 * n) := by sorry"


def test_extract_proof_body_finds_text_after_top_level_assign():
    assert _extract_proof_body("import Mathlib\n\ntheorem foo (n : ℕ) : 2 ∣ n := by decide") == "by decide"


# ---------------------------------------------------------------------------
# End-to-end decomposition via prove_with_retry (hermetic: check_proof faked).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decomposition_solves_each_subgoal_independently_and_assembles():
    client = ScriptedClaudeClient([SKETCH, SUB_H2_SOLVED, SUB_H3_SOLVED])
    with patch.object(prove_module, "check_proof", side_effect=[_compiles(), _compiles(), _compiles(), _compiles()]):
        result = await prove_with_retry(TOP_LEVEL_CODE, client, Path("."), timeout_s=10, max_attempts=3)

    assert result.proved is True
    assert client.calls == 3  # sketch + 2 sub-goals -- never re-generated the whole combined proof
    assert "h2 : 2 ∣ (6 * n) := by omega" in result.proof_code
    assert "h3 : 3 ∣ (6 * n) := by omega" in result.proof_code
    assert "sorry" not in result.proof_code


@pytest.mark.asyncio
async def test_decomposition_falls_back_to_whole_proof_retry_when_a_subgoal_fails():
    # h3 never resolves (both its own attempts are bad) -- decomposition
    # gives up cleanly and the OUTER loop falls back to today's existing
    # whole-proof retry, which succeeds. Never worse than pre-decomposition
    # behavior, just tries the cheaper decomposed path first.
    client = ScriptedClaudeClient([
        SKETCH, SUB_H2_SOLVED, SUB_H3_BAD, "CANNOT_PROVE", FALLBACK_WHOLE_PROOF,
    ])
    with patch.object(
        prove_module, "check_proof",
        side_effect=[_compiles(), _compiles(), _malformed(), _compiles()],
    ):
        result = await prove_with_retry(
            TOP_LEVEL_CODE, client, Path("."), timeout_s=10, max_attempts=2, max_subgoal_attempts=2,
        )

    assert result.proved is True
    assert result.proof_code == "import Mathlib\n\ntheorem six_dvd_six_n (n : ℕ) : 6 ∣ (6 * n) := by omega"
    assert client.calls == 5


@pytest.mark.asyncio
async def test_a_plain_one_shot_proof_never_attempts_decomposition():
    # No have-sketch shape at all -- decompose_and_prove must short-circuit
    # via _parse_have_sketch returning None, costing zero extra check_proof
    # calls or model calls versus today's behavior.
    client = ScriptedClaudeClient([PLAIN_ONE_SHOT_PROOF])
    with patch.object(prove_module, "check_proof", return_value=_compiles()) as mock_check:
        result = await prove_with_retry("theorem foo : 1 + 1 = 2 := by sorry", client, Path("."), timeout_s=10, max_attempts=5)

    assert result.proved is True
    assert client.calls == 1
    assert mock_check.call_count == 1
