"""Actively tries to refute a candidate that survived the Lean triviality
filter, before spending the much more expensive proof-search step on it. A
computer finding one real counterexample is a definitive, immediate
rejection -- no human needed for that verdict.

Not Lean-specific (lives outside hanno/lean/): this is a plain numeric
stress test, generated as a Python program and run through the existing
sandboxed `run_code` skill (hanno/skills/runner.py), the same executor
already governing that skill for live mining -- reused directly here, not
new execution risk.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from hanno.excavator.client import AnthropicClient
from hanno.skills.registry import SkillDefinition
from hanno.skills.runner import SkillResult, run_skill

COUNTEREXAMPLE_PROMPT = """You are stress-testing a mathematical claim by searching for a counterexample.

Core claim: {statement}

Formal Lean statement (for precise reference -- you don't need to write Lean, this is just the exact claim):
```lean
{lean_code}
```

Write a complete Python program that tests this claim computationally across as many concrete cases as you reasonably can (aim for hundreds or thousands where feasible) -- vary the free parameters across a wide range, including edge cases: small values, boundary conditions, and specifically the residue classes or conditions the claim depends on.

If your program finds ANY case where the claim's hypotheses hold but its conclusion fails, print "COUNTEREXAMPLE FOUND:" followed by the specific values and what failed, then stop. If no counterexample turns up after your search, print "NO COUNTEREXAMPLE FOUND after testing <N> cases" with the actual count you tested.

Output ONLY the Python program -- no prose before or after, no markdown fences."""

_CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
# Colon-anchored: "NO COUNTEREXAMPLE FOUND after testing N cases" (the
# negative-result message the prompt also asks for) contains "COUNTEREXAMPLE
# FOUND" as a literal substring -- the trailing colon is what the prompt
# instructs for the positive case specifically, and is what distinguishes them.
COUNTEREXAMPLE_MARKER = "COUNTEREXAMPLE FOUND:"


def _extract_program(text: str) -> str:
    """The model is asked for bare code, but strip an accidental fence
    rather than fail the whole check over a formatting slip."""
    text = text.strip()
    match = _CODE_FENCE_RE.search(text)
    return match.group(1).strip() if match else text


@dataclass
class CounterexampleResult:
    refuted: bool
    program: str
    skill_result: SkillResult
    input_tokens: int = 0
    output_tokens: int = 0


async def search_for_counterexample(
    statement: str, lean_code: str, client: AnthropicClient, skill: SkillDefinition,
) -> CounterexampleResult:
    prompt = COUNTEREXAMPLE_PROMPT.format(statement=statement, lean_code=lean_code)
    generation = await client.generate(prompt, temperature=0.3, max_tokens=2000)
    program = _extract_program(generation.text)

    # to_thread: run_skill blocks on a subprocess call (up to skill.timeout_seconds),
    # same reasoning as the Lean checker's to_thread wrapping.
    skill_result = await asyncio.to_thread(run_skill, skill, program)
    refuted = COUNTEREXAMPLE_MARKER in skill_result.stdout

    return CounterexampleResult(
        refuted=refuted, program=program, skill_result=skill_result,
        input_tokens=generation.input_tokens, output_tokens=generation.output_tokens,
    )
