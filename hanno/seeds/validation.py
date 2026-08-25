"""Seed validation — rule-based specificity/domain checks for MVP Step 2.

Honest simplification (per mvp-strategy-spec.md Step 2 risk note): AI-based
seed validation (full rubric: specificity, scope, domain assignability) is
deferred to Step 5 once Sieve infrastructure exists. This module does
length bounds and domain-keyword presence only. Concept-level duplicate
detection is handled separately by the Fingerprint Engine
(hanno/fingerprint/engine.py), not here — see SeedManager.submit.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_WORDS = 10
MAX_WORDS = 200

# Keyword lists derived from General-spec.txt Section 3 sub-domain lists.
# Used only as a coarse "does this plausibly belong to this domain" signal —
# not a semantic check.
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "mathematics": [
        "number theory", "algebra", "topology", "analysis", "combinatorics",
        "graph theory", "logic", "foundations", "category theory",
        "differential geometry", "probability", "information theory",
        "theorem", "conjecture", "proof", "lemma", "manifold", "group",
        "ring", "field", "functor", "homomorphism", "set", "function",
        "space", "metric", "measure", "integral", "derivative",
    ],
    "science": [
        "physics", "chemistry", "biology", "neuroscience", "cognitive science",
        "ecology", "materials science", "hypothesis", "mechanism", "particle",
        "condensed matter", "cosmological", "evolutionary", "molecular",
        "systems biology", "prediction", "experiment", "phenomenon",
    ],
    "philosophy": [
        "philosophy", "epistemology", "metaphysics", "philosophy of mind",
        "philosophy of language", "decision theory", "ethics",
        "philosophy of physics", "philosophy of biology", "argument",
        "premise", "conclusion", "concept", "thought experiment",
        "consciousness", "knowledge", "belief", "truth", "meaning",
    ],
}
DOMAIN_KEYWORDS["cross-domain"] = (
    DOMAIN_KEYWORDS["mathematics"] + DOMAIN_KEYWORDS["science"] + DOMAIN_KEYWORDS["philosophy"]
)


@dataclass
class ValidationResult:
    ok: bool
    feedback: str | None = None


def validate_seed_text(text: str, domain: str) -> ValidationResult:
    word_count = len(text.split())

    if word_count < MIN_WORDS:
        return ValidationResult(
            ok=False,
            feedback=f"too short: seed has {word_count} words, minimum is {MIN_WORDS}. "
                      f"Add more specificity about the direction or structure.",
        )
    if word_count > MAX_WORDS:
        return ValidationResult(
            ok=False,
            feedback=f"too long: seed has {word_count} words, maximum is {MAX_WORDS}. "
                      f"Narrow to a single conceptual direction.",
        )

    if domain not in DOMAIN_KEYWORDS:
        return ValidationResult(
            ok=False,
            feedback=f"unknown domain '{domain}'. Must be one of: "
                      f"{', '.join(k for k in DOMAIN_KEYWORDS if k != 'cross-domain')}, cross-domain.",
        )

    keywords = DOMAIN_KEYWORDS[domain]
    text_lower = text.lower()
    if not any(kw in text_lower for kw in keywords):
        return ValidationResult(
            ok=False,
            feedback=f"too broad: no recognizable {domain} sub-domain or structure keyword found. "
                      f"Specify a sub-domain or specific structure (e.g. one of: "
                      f"{', '.join(keywords[:5])}, ...).",
        )

    return ValidationResult(ok=True)
