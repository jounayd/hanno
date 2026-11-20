"""Prompt variants for the Excavator — Mathematics domain only (Step 3).

Five universal variants + three mathematics-specific variants, per
General-spec.txt Section 5. Science/philosophy-specific variants are
added in Steps 9-10.
"""

from __future__ import annotations

REQUIRED_JSON_FIELDS = [
    "domain",
    "core_claim",
    "formal_statement",
    "intuition",
    "connections",
    "self_assessed_novelty",
    "identified_weaknesses",
    "exploration_signal",
    "suggested_seeds",
    "self_rated_novelty",
    "self_rated_rigor",
    "claim_type",
]

_NOVELTY_AND_EXPLORATION_INSTRUCTIONS = """
Before answering, rigorously check the idea against standard references,
textbooks, and well-known results in the area — do not overclaim novelty in
self_assessed_novelty; if it is likely a restatement of known work, say so
plainly.

Then judge the seed direction itself, based only on this one idea:
- If this idea reveals two or more genuinely distinct, independently fruitful
  sub-directions worth pursuing separately, set "exploration_signal" to
  "branch_point" and list each sub-direction as a self-contained 10-200 word
  seed description in "suggested_seeds".
- If instead this idea suggests the seed's direction is exhausted — no
  further non-trivial claims seem reachable from it — set
  "exploration_signal" to "dead_end", explain why in
  "identified_weaknesses", and optionally suggest one adjacent pivot
  direction in "suggested_seeds" (or leave it empty if none comes to mind).
- Otherwise set "exploration_signal" to "continue" and leave "suggested_seeds"
  empty.
Most candidates should be "continue" — only signal a branch or dead end when
the evidence genuinely calls for it. The goal is to explore this idea in
every logical direction on its own merits, and only surface a need for new
human-submitted seeds when the current one has branched or run dry.

Also report two numeric self-ratings, each a float from 0.0 to 1.0:
- "self_rated_novelty": your honest confidence this is NOT already known —
  0.0 means "this is almost certainly a restatement of standard results",
  1.0 means "I can find no trace of this in the literature I know."
- "self_rated_rigor": your honest confidence the reasoning/algebra in
  formal_statement is actually correct, not merely plausible-sounding —
  0.0 means "I have not verified this and it may well be wrong", 1.0 means
  "I have checked this carefully step by step and believe it holds." Be
  skeptical of your own work here; an unverified derivation should score low
  even if it looks elegant.
These two numbers are used to decide which kinds of prompts and settings are
actually producing good candidates, so rate honestly rather than
optimistically — inflated scores make that feedback loop worthless.

Classify your claim as "claim_type", exactly one of:
- "construction": your claim exhibits explicit objects or numbers — a
  labeling, a solution triple, a parametric formula that can be instantiated
  at a specific value. A "numeric_check" witness is REQUIRED for this type,
  and skipping it costs score.
- "asymptotic": a big-O / growth-rate / density / limit claim. One numeric
  example CANNOT verify such a claim, so set "numeric_check" to null. A
  witness that merely re-verifies your own arithmetic on numbers you invented
  is worthless and is ignored by scoring — it earns nothing.
- "structural": a qualitative framework, reformulation, or connection with
  no specific objects or quantities to check. Set "numeric_check" to null.

For "construction" claims, provide the witness as "numeric_check":
{{"assignments": {{"<name>": <number>, ...}}, "equation": "<boolean
expression>"}}. Rules that make a witness count:
- "assignments" must contain ONLY the primitive inputs of your example —
  e.g. n and the exhibited objects (vertex labels, a solution triple).
  Every derived quantity (a gcd, an edge label, a remainder) must be
  COMPUTED INSIDE the equation, never precomputed and handed in as an
  assignment. A witness whose equation only confirms that numbers you
  invented are consistent with each other verifies nothing.
- The equation may use those names, numbers, the operators
  + - * / ** % // == != < > <= >= in and/or, the functions abs(), min(),
  max(), len(), sum(), gcd(), floor(), ceil(), and set/list literals of
  numbers — e.g. distinctness is "len({{e1, e2, e3}}) == 3", membership is
  "x in {{1, 2, 3}}". No other functions, no attribute access.
- The check must be one that would FAIL if your claim were wrong. If your
  claim requires values to be positive integers, check it explicitly
  ("x % 1 == 0 and x > 0") — a non-integer like 210.25 satisfying the
  arithmetic is not a valid witness for an integer claim.
Do not fabricate a witness just to fill the field, and do not mislabel an
asymptotic claim as "construction" — an honest null on the right type beats
a hollow witness every time.
""".strip()

_OUTPUT_FORMAT_INSTRUCTIONS = f"""
{_NOVELTY_AND_EXPLORATION_INSTRUCTIONS}

Respond with a single JSON object only — no prose before or after, no markdown
code fences. The JSON object must have exactly these fields:

{{
  "domain": "mathematics",
  "core_claim": "<the central novel assertion, precisely stated>",
  "formal_statement": "<expressed in domain-appropriate formal language>",
  "intuition": "<non-technical explanation of why this might be true or interesting>",
  "connections": ["<known idea this relates to>", "..."],
  "self_assessed_novelty": "<your own estimate of whether this is already known>",
  "identified_weaknesses": "<your own critique of this candidate>",
  "exploration_signal": "<one of: continue | branch_point | dead_end>",
  "suggested_seeds": ["<0+ follow-up seed directions per the rule above>"],
  "self_rated_novelty": <float 0.0-1.0>,
  "self_rated_rigor": <float 0.0-1.0>,
  "claim_type": "<one of: construction | asymptotic | structural>",
  "numeric_check": {{"assignments": {{"<name>": <number>}}, "equation": "<expr>"}} or null
}}
""".strip()

UNIVERSAL_VARIANTS: dict[str, str] = {
    "direct_conjecture": f"""
You are exploring the space of mathematical ideas. State a single, specific
mathematical claim that is non-obvious, formally statable, and connects to
existing mathematical knowledge in a non-trivial way. Prioritize precision
and formal correctness over creativity.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
    "framework_proposal": f"""
You are exploring the space of mathematical ideas. Propose a new conceptual
framework, definition, or structural approach that generalizes or unifies
existing mathematical ideas. Prioritize generativity and scope.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
    "connection_discovery": f"""
You are exploring the space of mathematical ideas. Identify a non-obvious
structural connection between two areas of mathematics (or between
mathematics and another domain). Prioritize bridging and synthesis.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
    "adversarial_insight": f"""
You are exploring the space of mathematical ideas. Identify something
surprising, counterintuitive, or contrary to common mathematical
assumptions that is nevertheless defensible. Prioritize challenging
existing positions or intuitions.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
    "open_problem_framing": f"""
You are exploring the space of mathematical ideas. Identify and precisely
formulate an open question or problem that is not currently well-posed in
the literature. Prioritize question generation over answer generation.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
}

MATHEMATICS_VARIANTS: dict[str, str] = {
    "proof_technique_proposal": f"""
You are exploring approaches to known open problems in mathematics.
Propose a novel proof technique or approach for attacking a known
unsolved problem. State clearly which problem, and what is novel about
the approach.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
    "counterexample_construction": f"""
You are exploring the space of mathematical ideas. Propose a candidate
counterexample to a known conjecture. State the conjecture, the proposed
counterexample construction, and why it might work.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
    "generalization": f"""
You are exploring the space of mathematical ideas. Propose a formal
generalization of a known theorem — a version that relaxes its
hypotheses or extends its conclusion to a broader setting.

{_OUTPUT_FORMAT_INSTRUCTIONS}
""".strip(),
}

ALL_VARIANTS: dict[str, str] = {**UNIVERSAL_VARIANTS, **MATHEMATICS_VARIANTS}

# A second, independent axis from prompt_variant: variant controls the TASK
# framing ("state a claim" vs "propose a framework"), but does nothing to
# stop every variant from reaching for the same well-known strategic angle
# on a heavily-studied problem (observed in practice: mod-840-style residue
# constructions dominating every variant). Rotating a distinct strategic
# angle across candidate slots forces real diversity by construction,
# with zero extra latency or API cost.
STEERING_HINTS: list[str] = [
    "Focus on a density or probabilistic argument: what fraction of cases are "
    "covered by known methods, and can you bound or characterize the exceptional set?",
    "Focus on an algebraic or algebraic-geometric framing: treat the problem as a "
    "variety, curve, or algebraic structure rather than case-by-case arithmetic.",
    "Focus on a computational or algorithmic angle: propose a search strategy, "
    "algorithm, or complexity observation rather than a closed-form construction.",
    "Focus on transplanting a proof technique or structural idea from a DIFFERENT, "
    "already-solved problem in a related area.",
    "Focus specifically on the hardest, most resistant special cases — what makes "
    "them different from the cases that already have known constructions?",
    "Focus on searching for a counterexample or an obstruction, rather than a "
    "construction that resolves the claim in the affirmative.",
    "Focus on a generalization or relaxation of the problem (more terms, a broader "
    "domain, weaker hypotheses) that might be more tractable than the original.",
    "Focus on a connection to a branch of mathematics that hasn't obviously been "
    "applied to this problem before.",
    "Focus on critiquing the most standard, well-known approach to this problem — "
    "identify precisely where and why it breaks down.",
    "Focus on a structural or definitional reframing: propose a different way of "
    "categorizing the objects involved that might reveal a hidden pattern.",
]


def build_prompt(
    seed_text: str,
    variant: str,
    already_explored: list[str] | None = None,
    steering_hint: str | None = None,
    available_tools: list[tuple[str, str]] | None = None,
) -> str:
    if variant not in ALL_VARIANTS:
        raise ValueError(f"unknown prompt variant: {variant}")
    instructions = ALL_VARIANTS[variant]
    parts = [instructions]
    if steering_hint:
        parts.append(f"For this attempt specifically: {steering_hint}")
    if available_tools:
        tool_lines = "\n".join(f"- {name}: {desc}" for name, desc in available_tools)
        parts.append(
            "You have real verification tools available in this conversation — USE "
            "them to actually test your idea before finalizing it (compute the "
            "example, check the construction, hunt for a counterexample), the way a "
            "programmer runs code instead of assuming it works. Let the results "
            "honestly shape self_rated_rigor, identified_weaknesses, and your "
            "numeric_check: an idea you tested with a tool and confirmed deserves "
            "higher rigor than one you did not test, and an idea a tool refuted must "
            "be reported as refuted, not quietly reworded.\n"
            f"Available tools:\n{tool_lines}"
        )
    if already_explored:
        listed = "\n".join(f"- {claim}" for claim in already_explored)
        parts.append(
            "The following core claims have already been explored for this seed — "
            "do not restate them. Actively look for a genuinely different angle; if "
            "you do end up building on one of them, say so explicitly in "
            f"identified_weaknesses rather than presenting it as independent:\n{listed}"
        )
    parts.append(f"Seed concept to explore:\n{seed_text}")
    return "\n\n".join(parts)


def build_refined_seed_text(original_seed_text: str, winning_candidate: dict) -> str:
    """The working seed_text for the next iteration of iterative mining
    (hanno mine --depth): narrows in on a specific promising candidate from
    the previous iteration instead of re-exploring the whole original seed
    from scratch."""
    return (
        f"{original_seed_text}\n\n"
        f"This is a refinement round, building on the most promising direction found "
        f"in an earlier iteration (path_score={winning_candidate.get('path_score', 0):.2f}):\n"
        f"Core claim: {winning_candidate.get('core_claim', '')}\n"
        f"Formal statement: {winning_candidate.get('formal_statement', '')}\n"
        f"Identified weaknesses so far: {winning_candidate.get('identified_weaknesses', '')}\n\n"
        f"Deepen, rigorously extend, formalize further, or directly resolve the "
        f"weaknesses in THIS SPECIFIC idea — do not propose an unrelated new claim."
    )
