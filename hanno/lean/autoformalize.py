"""Translates a candidate's informal formal_statement into an actual Lean 4
statement using the configured model and optional grounding tools. Compiler
errors feed into bounded retries before the statement reaches verification.

Hanno's formal_statement field is informal prose ("expressed in
domain-appropriate formal language" -- see prompts.py), not Lean syntax, so
this step is required before any Lean check can run at all.

`autoformalize_with_retry` was added after the first pilot run showed that
first-attempt failures were dominated by two fixable causes, not by the
underlying claims being unformalizable: (1) the model guessing specific,
stale Mathlib submodule paths (e.g. `Mathlib.NumberTheory.ArithmeticFunction`,
since split into a directory of files) instead of the umbrella `import
Mathlib`, which is confirmed to always resolve; and (2) the model declining
with UNTRANSLATABLE on claims that were in fact concrete and single-statement.
Both get a real retry with the actual Lean error (or a nudge) fed back,
up to LeanVerifierConfig.max_translation_attempts.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hanno.excavator.client import ModelClient
from hanno.lean.checker import (
    CheckResult,
    check_statement,
    ensure_mathlib_import,
    fix_deprecated_bigop_notation,
)
from hanno.skills.registry import SkillDefinition

WORKED_EXAMPLES = """Two worked examples of the expected style (real Lean 4 / Mathlib syntax):

Example 1 -- a universally-quantified divisibility claim:
```lean
import Mathlib

theorem cube_minus_self_div_six (n : ℕ) (hn : 0 < n) : 6 ∣ (n ^ 3 - n) := by sorry
```

Example 2 -- an existence/construction claim:
```lean
import Mathlib

theorem exists_unit_fraction_decomposition (n : ℕ) (hn : 2 ≤ n) :
    ∃ x y z : ℕ, 0 < x ∧ 0 < y ∧ 0 < z ∧ (4 : ℚ) / n = 1 / x + 1 / y + 1 / z := by sorry
```

Note the style: explicit typed binders (`n : ℕ`), hypotheses as named arguments, standard
Mathlib notation (`∣` for divisibility, `∃`/`∧` for existence claims), and every proof body
is exactly `by sorry` -- these examples state claims, they do not prove them."""

# Directly targets two real faithfulness bugs observed in production: (1) a
# claim naming a SPECIFIC constant (e.g. "C(k) = the product of primes p
# where (p-1)|(k-1)") got formalized as `∃ C, ...` and then trivially
# satisfied by C=1 -- a vacuous witness that abandons the claim's actual
# content while still nominally "matching" it; (2) a divisibility claim got
# formalized with the relation backwards (`a ∣ b` written when the claim
# said b divides a). Both compiled and passed the triviality filter; neither
# was faithful. See EXCAVATOR_MIGRATION_PLAN.md Phase 1/3.
STRENGTH_PRESERVATION_RULES = """Faithfulness rules -- violating any of these produces a statement that
compiles but does not mean what the claim says, which is worse than failing to translate at all:
- If the claim names a SPECIFIC object, constant, or formula (e.g. "the product of primes p where
  (p-1) | (k-1)"), state that exact object in Lean. Do NOT replace it with an existential quantifier
  over an unconstrained variable (e.g. do not write `∃ C, ...` when the claim defines a particular C) --
  that is trivially satisfiable by a witness the claim never intended (C=1 divides everything) and
  throws away the entire content of the claim.
- Preserve the direction of every relation exactly. If the claim says "A divides B", write `A ∣ B`,
  never `B ∣ A` -- these are different statements and Lean cannot tell you swapped them.
- Do not weaken hypotheses, broaden quantifiers, or generalize the claim into a related-but-different
  statement, even a strictly more general one. Formalize exactly the claim given, not a stronger or
  weaker relative of it."""

AUTOFORMALIZE_PROMPT = (
    """You are translating an informal mathematical claim into a formal Lean 4 theorem statement, for automated checking against Mathlib.

Core claim: {core_claim}

Informal formal statement: {formal_statement}

"""
    + WORKED_EXAMPLES
    + """

"""
    + STRENGTH_PRESERVATION_RULES
    + """

Write a single Lean 4 `theorem` (or `example`) statement that captures this claim exactly, in the same style as the examples above. Use a single `import Mathlib` line -- do not import specific submodules, since their exact file paths change frequently and you may not have precise, current knowledge of Mathlib's layout. End the proof with exactly `:= by sorry` -- do not attempt to prove it. If the claim cannot be faithfully expressed as a Lean 4 statement (too vague, not actually mathematical, or relies on undefined terms), respond with exactly the text UNTRANSLATABLE and nothing else.

Output ONLY a single ```lean fenced code block (or the literal word UNTRANSLATABLE) -- no prose before or after."""
)

RETRY_AFTER_DECLINE_PROMPT = (
    """You previously declined to translate this mathematical claim into Lean 4, responding UNTRANSLATABLE:

Core claim: {core_claim}

Informal formal statement: {formal_statement}

"""
    + WORKED_EXAMPLES
    + """

"""
    + STRENGTH_PRESERVATION_RULES
    + """

Please reconsider and make a genuine attempt, even if it requires some interpretation or simplification of the claim -- the examples above show this is usually a mechanical restatement, not a research problem. Only respond UNTRANSLATABLE again if this is truly impossible to state as a single Lean theorem (for example, it describes a multi-step research program rather than one checkable claim).

Write a single Lean 4 `theorem` (or `example`) statement. Use a single `import Mathlib` line -- do not import specific submodules. End the proof with exactly `:= by sorry`.

Output ONLY a single ```lean fenced code block (or the literal word UNTRANSLATABLE) -- no prose before or after."""
)

RETRY_AFTER_ERROR_PROMPT = """Your previous Lean 4 translation did not compile against Mathlib.

Core claim: {core_claim}

Previous attempt:
```lean
{previous_code}
```

Lean's error output:
{error_output}

Fix the issues and output a corrected Lean 4 statement ending in exactly `:= by sorry`. Use a single `import Mathlib` line rather than specific submodule imports. If, after considering the error, you believe this claim genuinely cannot be expressed in Lean, respond with exactly UNTRANSLATABLE and nothing else.

Output ONLY a single ```lean fenced code block (or the literal word UNTRANSLATABLE) -- no prose before or after."""

# lean4? and IGNORECASE: observed verbatim on DeepSeek-R1-14B during
# prove.py's identical extraction (see hanno/lean/prove.py) -- it fences
# valid output as ```lean4 (a reasonable, common tag for "the Lean 4
# language") instead of ```lean, which the stricter regex missed entirely,
# silently discarding a real response as if it were UNTRANSLATABLE.
_CODE_BLOCK_RE = re.compile(r"```lean4?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class AutoformalizeResult:
    lean_code: str | None
    raw_response: str
    input_tokens: int = 0
    output_tokens: int = 0


def _extract(text: str, input_tokens: int = 0, output_tokens: int = 0) -> AutoformalizeResult:
    text = text.strip()
    if "UNTRANSLATABLE" in text:
        return AutoformalizeResult(
            lean_code=None,
            raw_response=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    match = _CODE_BLOCK_RE.search(text)
    if not match:
        return AutoformalizeResult(
            lean_code=None,
            raw_response=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    code = ensure_mathlib_import(fix_deprecated_bigop_notation(match.group(1).strip()))
    return AutoformalizeResult(
        lean_code=code, raw_response=text, input_tokens=input_tokens, output_tokens=output_tokens
    )


async def autoformalize(
    candidate: dict,
    client: ModelClient,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> AutoformalizeResult:
    prompt = AUTOFORMALIZE_PROMPT.format(
        core_claim=candidate.get("core_claim") or "",
        formal_statement=candidate.get("formal_statement") or "",
    )
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=1500,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


async def _retry_after_decline(
    candidate: dict,
    client: ModelClient,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> AutoformalizeResult:
    prompt = RETRY_AFTER_DECLINE_PROMPT.format(
        core_claim=candidate.get("core_claim") or "",
        formal_statement=candidate.get("formal_statement") or "",
    )
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=1500,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


async def _retry_after_error(
    candidate: dict,
    client: ModelClient,
    previous_code: str,
    error_output: str,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> AutoformalizeResult:
    prompt = RETRY_AFTER_ERROR_PROMPT.format(
        core_claim=candidate.get("core_claim") or "",
        previous_code=previous_code,
        error_output=error_output.strip()[:2000],
    )
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=1500,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


@dataclass
class AutoformalizeWithCheckResult:
    lean_code: str | None
    check: CheckResult | None
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0


async def autoformalize_with_retry(
    candidate: dict,
    client: ModelClient,
    project_dir: Path,
    timeout_s: int,
    max_attempts: int,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> AutoformalizeWithCheckResult:
    """Translate, check, and on a fixable failure retry with real feedback
    (the actual Lean error, or a nudge past an over-eager UNTRANSLATABLE),
    up to max_attempts. Stops immediately on a compile success or a timeout
    (retrying a timeout on the same statement is not expected to help).
    `tools`/`tool_executor` (e.g. the mathlib_search grounding skill) are
    entirely optional -- omitting them reproduces today's one-shot,
    tool-free behavior exactly."""
    attempt = 1
    total_input = 0
    total_output = 0
    af = await autoformalize(candidate, client, tools, tool_executor)
    total_input += af.input_tokens
    total_output += af.output_tokens

    while True:
        if af.lean_code is None:
            if attempt >= max_attempts:
                return AutoformalizeWithCheckResult(
                    lean_code=None,
                    check=None,
                    attempts=attempt,
                    input_tokens=total_input,
                    output_tokens=total_output,
                )
            attempt += 1
            af = await _retry_after_decline(candidate, client, tools, tool_executor)
            total_input += af.input_tokens
            total_output += af.output_tokens
            continue

        # to_thread: check_statement blocks on a ~45-60s subprocess call.
        # run_iterative_mining is awaited directly inside hanno ui's FastAPI
        # event loop, so calling it synchronously here would freeze the
        # entire UI server for that duration on every live Lean check.
        check = await asyncio.to_thread(check_statement, af.lean_code, project_dir, timeout_s)
        if check.compiles or check.timed_out or attempt >= max_attempts:
            return AutoformalizeWithCheckResult(
                lean_code=af.lean_code,
                check=check,
                attempts=attempt,
                input_tokens=total_input,
                output_tokens=total_output,
            )

        attempt += 1
        af = await _retry_after_error(
            candidate, client, af.lean_code, check.stdout, tools, tool_executor
        )
        total_input += af.input_tokens
        total_output += af.output_tokens
