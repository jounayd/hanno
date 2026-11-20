"""Attempts a real, complete Lean 4 proof of a statement that already
survived the triviality filter (hanno/lean/checker.py) -- i.e. compiles with
`:= by sorry` and isn't closed by `exact?`/`simp`. This is deliberately
separate from autoformalize.py: that step only ever produces a `sorry`
placeholder, it never tries to prove anything. This is the step that does.

An interactive loop, not one-shot: submit a proof attempt, get Lean's actual
error (or, if it compiles but still contains `sorry`, a note that the proof
is incomplete) fed back, retry -- mirroring hanno/lean/autoformalize.py's
retry shape exactly. A `sorry`-free statement that compiles is the strongest
automated signal available (Lean's kernel doesn't make mistakes); exhausting
max_attempts without one is an honest "couldn't prove it automatically", not
a rejection -- most hard, genuinely novel conjectures are expected to land
here rather than get a machine proof.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hanno.excavator.client import ModelClient
from hanno.lean.checker import (
    CheckResult,
    check_proof,
    ensure_mathlib_import,
    fix_deprecated_bigop_notation,
)
from hanno.skills.registry import SkillDefinition

# Observed verbatim on a real Haiku proof attempt for "n^13 - n divisible by
# 2730": the proof strategy (Fermat's Little Theorem per prime factor, cast
# into ZMod p) was correct, but `n ^ 13 - n` is NATURAL-NUMBER subtraction --
# it clamps at 0 rather than going negative -- and casting that into ZMod p
# does not commute with the cast the "obvious" way; the attempt failed on a
# defeq mismatch between `↑n ^ 13 - ↑n` and `↑(n ^ 13 - n)`. This is a known
# Mathlib community gotcha, not a math error and not a hallucinated name --
# it is specifically about how ℕ-subtraction interacts with casting.
KNOWN_FORMALIZATION_GOTCHAS = """A known Lean/Mathlib gotcha, not a math error -- watch for it:
Natural-number subtraction (a - b where a b : ℕ) clamps at 0 instead of going negative, so it does
NOT commute with casting into another ring/type (e.g. ZMod p) the "obvious" way -- `↑(a - b)` and
`↑a - ↑b` are not defeq in general. If your claim or proof involves `n ∣ (a - b)` and you need to
reason about it modulo p, prefer restating without subtraction: `a ≡ b [MOD n]` (`Nat.ModEq`,
defined via `%`, avoids the issue entirely) instead of `n ∣ (a - b)`. If you must keep the
subtraction, use `Nat.cast_sub` -- it requires you to first prove `b ≤ a`, which is exactly the
side condition the naive cast silently skips."""

# Observed verbatim on a real Haiku proof attempt for "n^17 - n divisible by
# 510": the proof strategy was entirely correct and every lemma name it used
# was real, but it committed `omega` -- a LINEAR-arithmetic-only decision
# procedure -- to a sub-goal (`n <= n ^ 17`) that involves exponentiation,
# which omega structurally cannot reason about at all, regardless of the
# fact being true. Not a hallucination (the tactic is real); not the
# subtraction/cast gotcha above (that part was handled correctly); a third,
# distinct failure mode: a real tactic committed to outside its own power.
# Generic fix, not a growing list of special cases: don't bet a sub-goal on
# one guessed tactic -- try several generic closers, or ask the
# lean_goal_solver tool (if available) to check via a real compile first.
TACTIC_SELECTION_GUIDANCE = """Do not commit a small arithmetic/algebraic sub-goal to a single guessed tactic --
`omega` in particular is LINEAR arithmetic only and cannot reason about exponentiation, multiplication
of variables, or most nonlinear terms at all, even when the fact is true. Instead, either (a) use Lean's
`first` combinator to try several generic closers in one line, e.g. `first | omega | decide | norm_num |
simp | positivity`, so whichever one actually applies is used automatically, or (b) if the lean_goal_solver
tool is available, call it on the exact standalone sub-goal BEFORE committing to a tactic in your proof --
it verifies via a real compile which tactic (if any) closes it, rather than you guessing."""

PROVE_PROMPT = (
    """You are proving a Lean 4 theorem against Mathlib. Produce a COMPLETE proof -- no `sorry` anywhere in your answer.

Statement:
```lean
{lean_code}
```

Write the complete theorem with a real proof, replacing the `sorry` body with actual tactics or a term-mode proof. If you cannot find a complete proof, respond with exactly CANNOT_PROVE and nothing else -- this is an honest, expected outcome for a hard or genuinely novel claim, not a failure to avoid.

If the proof naturally splits into independent parts (e.g. a divisibility claim that decomposes by prime factor, or a case split), state each part as its own `have partName : <statement> := by sorry` line -- one statement per line, ending in exactly `:= by sorry` -- then combine them at the end (e.g. `exact ...`). Each part gets solved independently and is more likely to succeed than one giant combined proof attempt. Only do this when the proof genuinely decomposes; a single short proof should stay a single proof.

"""
    + KNOWN_FORMALIZATION_GOTCHAS
    + """

"""
    + TACTIC_SELECTION_GUIDANCE
    + """

Output ONLY a single ```lean fenced code block (or the literal word CANNOT_PROVE) -- no prose before or after."""
)

RETRY_AFTER_DECLINE_PROMPT = """You previously declined to attempt a proof of this Lean 4 theorem, responding CANNOT_PROVE:

Statement:
```lean
{lean_code}
```

Please reconsider and make a genuine attempt. Try standard approaches for this kind of statement (relevant Mathlib lemmas, induction, `nlinarith`/`polyrith`/`decide` where applicable) before concluding it's out of reach. Only respond CANNOT_PROVE again if you've genuinely tried and it still resists.

Output ONLY a single ```lean fenced code block (or the literal word CANNOT_PROVE) -- no prose before or after."""

RETRY_AFTER_ERROR_PROMPT = (
    """Your previous Lean 4 proof attempt did not compile.

Statement:
```lean
{lean_code}
```

Previous attempt:
```lean
{previous_code}
```

Lean's error output:
{error_output}

Fix the proof and output a corrected, COMPLETE Lean 4 proof (no `sorry`). If you now believe this cannot be proven, respond with exactly CANNOT_PROVE and nothing else.

"""
    + KNOWN_FORMALIZATION_GOTCHAS
    + """

"""
    + TACTIC_SELECTION_GUIDANCE
    + """

Output ONLY a single ```lean fenced code block (or the literal word CANNOT_PROVE) -- no prose before or after."""
)

RETRY_AFTER_INCOMPLETE_PROMPT = """Your previous Lean 4 proof attempt compiled, but still contains `sorry` -- it is not a complete proof.

Statement:
```lean
{lean_code}
```

Previous (incomplete) attempt:
```lean
{previous_code}
```

Finish the proof: replace every remaining `sorry` with real tactics or terms. If you now believe this cannot be proven, respond with exactly CANNOT_PROVE and nothing else.

Output ONLY a single ```lean fenced code block (or the literal word CANNOT_PROVE) -- no prose before or after."""

# lean4? and IGNORECASE: observed verbatim on DeepSeek-R1-14B -- it fences
# valid, complete proof attempts as ```lean4 (a reasonable, common tag for
# "the Lean 4 language") instead of ```lean, which the stricter regex missed
# entirely. Every attempt then silently parsed as proof_code=None, treated
# identically to a literal CANNOT_PROVE -- five real proof attempts were
# discarded unseen, not five genuine declines.
_CODE_BLOCK_RE = re.compile(r"```lean4?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class ProveAttempt:
    proof_code: str | None
    raw_response: str
    input_tokens: int = 0
    output_tokens: int = 0


def _extract(text: str, input_tokens: int = 0, output_tokens: int = 0) -> ProveAttempt:
    text = text.strip()
    if "CANNOT_PROVE" in text:
        return ProveAttempt(
            proof_code=None,
            raw_response=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    match = _CODE_BLOCK_RE.search(text)
    if not match:
        return ProveAttempt(
            proof_code=None,
            raw_response=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    code = ensure_mathlib_import(fix_deprecated_bigop_notation(match.group(1).strip()))
    return ProveAttempt(
        proof_code=code, raw_response=text, input_tokens=input_tokens, output_tokens=output_tokens
    )


async def _first_attempt(
    lean_code: str,
    client: ModelClient,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> ProveAttempt:
    prompt = PROVE_PROMPT.format(lean_code=lean_code)
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=3000,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


async def _retry_after_decline(
    lean_code: str,
    client: ModelClient,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> ProveAttempt:
    prompt = RETRY_AFTER_DECLINE_PROMPT.format(lean_code=lean_code)
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=3000,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


def _looks_like_cast_subtraction_defeq_error(error_output: str) -> bool:
    """Heuristic detector for the specific Lean error class observed on a
    real Haiku proof attempt: casting a NATURAL-NUMBER subtraction into
    another ring/type (e.g. ZMod p) fails a defeq check, since `↑(a - b)`
    and `↑a - ↑b` are not defeq in general for ℕ (subtraction clamps at 0).
    Not a precise parser -- a false positive just adds a generically useful
    hint, which is harmless; the point is to surface KNOWN_FORMALIZATION_GOTCHAS'
    advice exactly when its symptoms (a defeq failure involving a cast and a
    subtraction) are actually present, not on every retry regardless."""
    return (
        "not definitionally equal" in error_output and "↑" in error_output and "-" in error_output
    )


async def _retry_after_error(
    lean_code: str,
    client: ModelClient,
    previous_code: str,
    error_output: str,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> ProveAttempt:
    prompt = RETRY_AFTER_ERROR_PROMPT.format(
        lean_code=lean_code,
        previous_code=previous_code,
        error_output=error_output.strip()[:2000],
    )
    if _looks_like_cast_subtraction_defeq_error(error_output):
        prompt += (
            "\n\nThis error looks like the natural-subtraction/cast gotcha specifically: "
            "`↑(a - b)` and `↑a - ↑b` are not defeq for natural-number subtraction. "
            "Reformulate using `Nat.ModEq` (`a ≡ b [MOD n]`, defined via `%`, no subtraction "
            "involved) instead of `n ∣ (a - b)`, or use `Nat.cast_sub` after first proving `b ≤ a`."
        )
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=3000,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


async def _retry_after_incomplete(
    lean_code: str,
    client: ModelClient,
    previous_code: str,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> ProveAttempt:
    prompt = RETRY_AFTER_INCOMPLETE_PROMPT.format(lean_code=lean_code, previous_code=previous_code)
    result = await client.generate(
        prompt,
        temperature=0.2,
        max_tokens=3000,
        tools=tools,
        tool_executor=tool_executor,
    )
    return _extract(result.text, result.input_tokens, result.output_tokens)


@dataclass
class ProofResult:
    proved: bool
    proof_code: str | None
    check: CheckResult | None
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0


def _is_complete_proof(proof_code: str, check: CheckResult) -> bool:
    return check.compiles and not {"sorry", "admit"}.intersection(_lean_tokens(proof_code))


def _lean_tokens(code: str) -> list[str]:
    """Compare Lean headers without changing strings, identifiers, or nested comments."""
    cleaned: list[str] = []
    i = 0
    depth = 0
    quoted = False
    while i < len(code):
        pair = code[i : i + 2]
        if depth:
            if pair == "/-":
                depth += 1
                i += 2
            elif pair == "-/":
                depth -= 1
                i += 2
            else:
                i += 1
        elif quoted:
            cleaned.append(code[i])
            if code[i] == "\\" and i + 1 < len(code):
                i += 1
                cleaned.append(code[i])
            elif code[i] == '"':
                quoted = False
            i += 1
        elif pair == "/-":
            cleaned.append(" ")
            depth = 1
            i += 2
        elif pair == "--":
            end = code.find("\n", i)
            i = len(code) if end == -1 else end
        else:
            quoted = code[i] == '"'
            cleaned.append(code[i])
            i += 1
    return re.findall(r'"(?:\\.|[^"\\])*"|«[^»]*»|[^\W\d]\w*|\d+|\S', "".join(cleaned))


_DECLARATION_RE = re.compile(r"\b(?:theorem\s+(?P<name>[^\s(:{]+)|example\b)")


def _proof_header(code: str) -> tuple[list[str], str | None] | None:
    declaration = _DECLARATION_RE.search(code)
    if declaration is None:
        return None
    split = _split_at_top_level(code[declaration.end() :], ":=")
    if split is None:
        return None
    prefix = code[: declaration.start()] + "theorem __target__ " + split[0]
    return _lean_tokens(ensure_mathlib_import(prefix)), declaration.group("name")


def _check_target_proof(
    proof_code: str,
    target_code: str,
    project_dir: Path,
    timeout_s: int,
) -> CheckResult:
    target = _proof_header(target_code)
    proposed = _proof_header(proof_code)
    if target is None or proposed is None or target[0] != proposed[0]:
        return CheckResult(
            False,
            None,
            "Proof changed the target statement, hypotheses, or imports. Keep the original header.",
            "",
            0,
        )
    if {"sorry", "admit"}.intersection(_lean_tokens(proof_code)):
        # A type-correct sketch may still be decomposed; _is_complete_proof
        # rejects it until all stubs are replaced and the axiom audit passes.
        return check_proof(proof_code, project_dir, timeout_s)
    audited_code = proof_code
    name = proposed[1]
    if name is None:
        name = "hanno_audit_" + uuid.uuid4().hex
        declaration = _DECLARATION_RE.search(audited_code)
        assert declaration is not None
        audited_code = (
            audited_code[: declaration.start()]
            + f"theorem {name}"
            + audited_code[declaration.end() :]
        )
    # Lean checks the proof's transitive dependencies, including admissions
    # introduced by tactics. A warning-free compile alone is insufficient.
    audited_code += f"""

run_elab do
  let axioms ← Lean.collectAxioms `{name}
  for axiomName in axioms do
    unless [``propext, ``Classical.choice, ``Quot.sound].contains axiomName do
      throwError "untrusted proof axiom: {{axiomName}}"
"""
    return check_proof(audited_code, project_dir, timeout_s)


# ---------------------------------------------------------------------------
# Decomposition prover (EXCAVATOR_MIGRATION_PLAN.md Phase 6): observed
# verbatim on a real Haiku proof attempt for "n^13 - n divisible by 2730" --
# the model's own natural strategy was to state a `have` per prime factor
# (Fermat's Little Theorem case-by-case) then combine via CRT, but ONE bad
# lemma name/tactic in the combination step failed the ENTIRE proof, and the
# retry loop regenerated the whole thing from scratch every time, burning the
# full retry budget re-deriving the four sub-lemmas that were already fine.
# Instead: when an attempt already typechecks in this `have ... := by sorry`
# shape, solve each stubbed sub-goal independently (its own small,
# recursive prove_with_retry call, cheap because the sub-statement is
# simple) and only regenerate what actually failed.
# ---------------------------------------------------------------------------

_THEOREM_NAME_RE = re.compile(r"\btheorem\s+(\S+)")
_HAVE_RE = re.compile(r"[ \t]*\bhave\s+(\w+)\s*:")
_STUB_RE = re.compile(r"\s*(by\s+sorry\b|sorry\b)")


def _split_at_top_level(text: str, marker: str) -> tuple[str, str, int] | None:
    """First occurrence of `marker` at bracket depth 0 -- the boundary
    between a theorem's binders/goal/proof, regardless of how the
    surrounding text is formatted. Returns (left.strip(), right.strip(),
    absolute index of the marker within `text`), or None if never found.
    For marker==':' this skips actual ':=' occurrences (checks the next
    char isn't '=')."""
    depth = 0
    opens, closes = "([{⟨", ")]}⟩"
    i, n, mlen = 0, len(text), len(marker)
    while i <= n - mlen:
        c = text[i]
        if c in opens:
            depth += 1
        elif c in closes:
            depth -= 1
        elif depth == 0 and text[i : i + mlen] == marker:
            if marker == ":" and text[i : i + 2] == ":=":
                i += 1
                continue
            return text[:i].strip(), text[i + mlen :].strip(), i
        i += 1
    return None


def _extract_proof_body(code: str) -> str | None:
    """The text after a theorem's own top-level ':=' -- i.e. just the proof,
    stripped of the `import Mathlib` / `theorem ... :=` header, regardless of
    how the model formatted the statement."""
    m = _THEOREM_NAME_RE.search(code)
    if not m:
        return None
    split = _split_at_top_level(code[m.end() :], ":=")
    if split is None:
        return None
    _, body, _ = split
    return body or None


@dataclass
class HaveSketch:
    binders: str
    goal: str
    # Every `have` in encountered order: (name, statement, stub_span). stub_span
    # is None for a `have` the model already gave its own real proof (e.g.
    # `:= by norm_num`) -- left untouched in the assembled proof, but still a
    # legitimate hypothesis for any LATER stub subgoal, since the sketch as a
    # whole already typechecked (including that have's real proof) before
    # decomposition was ever attempted.
    all_haves: list[tuple[str, str, tuple[int, int] | None]]
    final_block: str


def _parse_have_sketch(code: str) -> HaveSketch | None:
    """Recognizes the `have h1 : S1 := by sorry ... have hN : SN := by sorry
    <combining code>` shape inside a theorem's proof. Returns None for any
    ordinary (non-decomposed) proof attempt -- the caller falls back to
    today's whole-proof retry in that case, so a query that doesn't match
    this shape costs nothing extra."""
    name_match = _THEOREM_NAME_RE.search(code)
    if not name_match:
        return None
    header_split = _split_at_top_level(code[name_match.end() :], ":=")
    if header_split is None:
        return None
    header, proof_body, assign_rel_idx = header_split
    binders_goal = _split_at_top_level(header, ":")
    if binders_goal is None:
        return None
    binders, goal, _ = binders_goal
    if not proof_body.startswith("by"):
        return None

    body_abs_start = name_match.end() + assign_rel_idx + 2  # +2 for ':='
    have_matches = list(_HAVE_RE.finditer(code, body_abs_start))
    if not have_matches:
        return None

    all_haves: list[tuple[str, str, tuple[int, int] | None]] = []
    last_consumed_end = None
    for hm in have_matches:
        stmt_split = _split_at_top_level(code[hm.end() :], ":=")
        if stmt_split is None:
            return None
        _, _, stmt_assign_rel = stmt_split
        stmt_assign_abs = hm.end() + stmt_assign_rel + 2
        statement = code[hm.end() : stmt_assign_abs - 2].strip()
        stub_match = _STUB_RE.match(code, stmt_assign_abs)
        if stub_match:
            all_haves.append((hm.group(1), statement, (stub_match.start(1), stub_match.end(1))))
            last_consumed_end = stub_match.end(1)
        else:
            # Not a stub -- the model already gave this `have` its own real,
            # short proof (e.g. `:= by norm_num`). Left completely untouched
            # in the assembled proof (only recorded stub spans are ever
            # spliced), but still recorded as a real prior fact -- the whole
            # sketch (including this have's real proof) already typechecked
            # before decomposition was attempted. Only supported inline (ends
            # at the next newline) -- a multi-line non-stub have is rare
            # enough, and safe enough to just bail on (falls back to the
            # whole-proof retry), that tracking it isn't worth the added
            # parsing complexity.
            newline_idx = code.find("\n", stmt_assign_abs)
            if newline_idx == -1:
                return None
            all_haves.append((hm.group(1), statement, None))
            last_consumed_end = newline_idx

    if not any(span is not None for _, _, span in all_haves):
        return None  # every have already had its own proof -- nothing to decompose

    final_block = code[last_consumed_end:].strip()
    if not final_block:
        return None  # nothing left to combine the haves with -- degenerate sketch

    return HaveSketch(binders=binders, goal=goal, all_haves=all_haves, final_block=final_block)


def _build_subgoal_theorem(
    name: str, binders: str, prior_haves: list[tuple[str, str]], goal_stmt: str
) -> str:
    hyp_parts = " ".join(f"({hname} : {hstmt})" for hname, hstmt in prior_haves)
    signature = f"{binders} {hyp_parts}".strip() if hyp_parts else binders
    return f"import Mathlib\n\ntheorem {name} {signature} : {goal_stmt} := by sorry"


async def decompose_and_prove(
    lean_code: str,
    client: ModelClient,
    project_dir: Path,
    timeout_s: int,
    max_subgoal_attempts: int,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
) -> ProofResult:
    """`lean_code` is an attempt that ALREADY typechecks in the `have ... :=
    by sorry` sketch shape (checked by the caller before calling this).
    Solves each stubbed sub-goal independently via a recursive
    prove_with_retry call (small budget each -- the sub-statement is simple,
    so this is cheap), then splices the solved bodies back into the sketch
    and does one final compile check. Returns proved=False (never raises) if
    parsing fails or any sub-goal doesn't resolve -- the caller falls back to
    the existing whole-proof retry path in that case, so this is purely
    additive and never a regression versus today's one-shot behavior."""
    sketch = _parse_have_sketch(lean_code)
    if sketch is None:
        return ProofResult(proved=False, proof_code=None, check=None, attempts=0)

    total_input = 0
    total_output = 0
    solved: dict[int, str] = {}  # index into sketch.all_haves -> replacement body
    for i, (hname, hstmt, stub_span) in enumerate(sketch.all_haves):
        if stub_span is None:
            continue  # already had its own real proof -- nothing to solve, just usable as a prior hypothesis below
        prior = [(n, s) for n, s, _ in sketch.all_haves[:i]]
        subgoal_code = _build_subgoal_theorem(f"sub_{hname}", sketch.binders, prior, hstmt)
        sub_result = await prove_with_retry(
            subgoal_code,
            client,
            project_dir,
            timeout_s,
            max_subgoal_attempts,
            tools=tools,
            tool_executor=tool_executor,
        )
        total_input += sub_result.input_tokens
        total_output += sub_result.output_tokens
        if not sub_result.proved or sub_result.proof_code is None:
            return ProofResult(
                proved=False,
                proof_code=None,
                check=None,
                attempts=len(solved) + 1,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        body = _extract_proof_body(sub_result.proof_code)
        if body is None:
            return ProofResult(
                proved=False,
                proof_code=None,
                check=None,
                attempts=len(solved) + 1,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        solved[i] = body

    # Splice each solved body in for its "by sorry"/"sorry" stub, working
    # from the END of the string backward so earlier stub offsets don't
    # shift as replacements are applied.
    assembled = lean_code
    for i in sorted(solved, reverse=True):
        stub = sketch.all_haves[i][2]
        assert stub is not None
        stub_start, stub_end = stub
        assembled = assembled[:stub_start] + solved[i] + assembled[stub_end:]

    check = await asyncio.to_thread(
        _check_target_proof, assembled, lean_code, project_dir, timeout_s
    )
    if _is_complete_proof(assembled, check):
        return ProofResult(
            proved=True,
            proof_code=assembled,
            check=check,
            attempts=len(solved),
            input_tokens=total_input,
            output_tokens=total_output,
        )
    return ProofResult(
        proved=False,
        proof_code=None,
        check=check,
        attempts=len(solved),
        input_tokens=total_input,
        output_tokens=total_output,
    )


async def prove_with_retry(
    lean_code: str,
    client: ModelClient,
    project_dir: Path,
    timeout_s: int,
    max_attempts: int,
    tools: list[SkillDefinition] | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
    max_subgoal_attempts: int = 2,
) -> ProofResult:
    """lean_code is the ALREADY-SURVIVED statement (compiles with `by sorry`,
    not closed by exact?/simp). Tries to replace the sorry with a genuine,
    complete proof, retrying with real feedback on a fixable failure. Stops
    on success, a timeout, or max_attempts. `tools`/`tool_executor` (e.g. the
    mathlib_search grounding skill) are entirely optional -- omitting them
    reproduces today's one-shot, tool-free behavior exactly.

    Whenever an attempt already typechecks in the `have ... := by sorry`
    sketch shape, tries decompose_and_prove on it BEFORE falling back to
    regenerating the whole proof from scratch -- a genuine one-shot attempt
    (no have-sketch) never reaches this branch, so this costs nothing extra
    on the common case."""
    attempt = 1
    total_input = 0
    total_output = 0
    attempt_result = await _first_attempt(lean_code, client, tools, tool_executor)
    total_input += attempt_result.input_tokens
    total_output += attempt_result.output_tokens

    while True:
        if attempt_result.proof_code is None:
            if attempt >= max_attempts:
                return ProofResult(
                    proved=False,
                    proof_code=None,
                    check=None,
                    attempts=attempt,
                    input_tokens=total_input,
                    output_tokens=total_output,
                )
            attempt += 1
            attempt_result = await _retry_after_decline(lean_code, client, tools, tool_executor)
            total_input += attempt_result.input_tokens
            total_output += attempt_result.output_tokens
            continue

        # to_thread: check_proof blocks on a ~45-60s subprocess call, same
        # reasoning as autoformalize_with_retry.
        check = await asyncio.to_thread(
            _check_target_proof, attempt_result.proof_code, lean_code, project_dir, timeout_s
        )

        if _is_complete_proof(attempt_result.proof_code, check):
            return ProofResult(
                proved=True,
                proof_code=attempt_result.proof_code,
                check=check,
                attempts=attempt,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        if check.timed_out or attempt >= max_attempts:
            return ProofResult(
                proved=False,
                proof_code=attempt_result.proof_code,
                check=check,
                attempts=attempt,
                input_tokens=total_input,
                output_tokens=total_output,
            )

        if check.compiles:
            decomp = await decompose_and_prove(
                attempt_result.proof_code,
                client,
                project_dir,
                timeout_s,
                max_subgoal_attempts,
                tools,
                tool_executor,
            )
            total_input += decomp.input_tokens
            total_output += decomp.output_tokens
            if decomp.proved:
                return ProofResult(
                    proved=True,
                    proof_code=decomp.proof_code,
                    check=decomp.check,
                    attempts=attempt,
                    input_tokens=total_input,
                    output_tokens=total_output,
                )

        attempt += 1
        if check.compiles:
            attempt_result = await _retry_after_incomplete(
                lean_code, client, attempt_result.proof_code, tools, tool_executor
            )
            total_input += attempt_result.input_tokens
            total_output += attempt_result.output_tokens
        else:
            attempt_result = await _retry_after_error(
                lean_code, client, attempt_result.proof_code, check.stdout, tools, tool_executor
            )
            total_input += attempt_result.input_tokens
            total_output += attempt_result.output_tokens
