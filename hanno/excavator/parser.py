"""Defensive parsing of raw model output into structured Candidate fields.

Per General-spec.txt Section 5 (OUTPUT QUALITY CONTROL) and the strategy
doc's Step 3 risk note: models drift from requested JSON format, especially
at high temperature. Parse defensively, never raise — always return enough
information to persist the raw output and the parse outcome. Every raw
candidate is persisted regardless of parse success (Rule 2: failures are data).
"""

from __future__ import annotations

import json
import re

from hanno.excavator.prompts import REQUIRED_JSON_FIELDS

MIN_CORE_CLAIM_CHARS = 20
MAX_CORE_CLAIM_CHARS = 4000

VALID_EXPLORATION_SIGNALS = ("continue", "branch_point", "dead_end")
VALID_CLAIM_TYPES = ("construction", "asymptotic", "structural")

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Observed verbatim on a local DeepSeek-R1 distill: it writes inline LaTeX
# like \( n \geq 2 \) directly in JSON string values with a single,
# un-escaped backslash -- invalid JSON -- though it's inconsistent and
# sometimes doubles a backslash correctly in the same payload. This repair
# pass walks backslash+char pairs and doubles any backslash not already part
# of a genuine JSON escape, recovering the model's intended literal
# backslash (LaTeX markup) instead of failing the whole candidate over a
# formatting tic rather than a content problem.
#
# Only "\\", "\"", and "\/" are ever treated as an already-valid single-char
# escape here -- NOT \b \f \n \r \t, even though those are technically legal
# JSON. In this domain they are essentially always the first letter of a
# LaTeX macro (\beta, \frac, \forall, \neq, \nabla, \rightarrow, \tau,
# \times, \underline...), never a genuinely intended control character;
# treating them as "valid" would silently decode \frac into a literal
# formfeed byte + "rac" instead of preserving the text.
_ESCAPE_TOKEN_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.DOTALL)
_ALWAYS_VALID_SINGLE_ESCAPES = frozenset('"\\/')


def _strip_code_fences(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text.strip())


def _extract_json_object(text: str) -> str:
    """Find the first top-level {...} block in text, tolerating leading/trailing prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _repair_invalid_backslash_escapes(text: str) -> str:
    def _fix(match: re.Match) -> str:
        token = match.group(1)
        if len(token) == 5 or token in _ALWAYS_VALID_SINGLE_ESCAPES:
            return match.group(0)  # genuine \uXXXX, \\, \", or \/ -- leave as-is
        return "\\\\" + token  # everything else: double the backslash

    return _ESCAPE_TOKEN_RE.sub(_fix, text)


def parse_candidate_json(raw_text: str) -> tuple[dict | None, str | None]:
    """Returns (parsed_dict, error). parsed_dict is None on failure."""
    cleaned = _strip_code_fences(raw_text)
    candidate_json = _extract_json_object(cleaned)
    try:
        parsed = json.loads(candidate_json)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_repair_invalid_backslash_escapes(candidate_json))
        except json.JSONDecodeError as e:
            return None, f"JSON decode error: {e}"

    if not isinstance(parsed, dict):
        return None, "parsed output is not a JSON object"

    missing = [f for f in REQUIRED_JSON_FIELDS if f not in parsed]
    if missing:
        return None, f"missing required fields: {', '.join(missing)}"

    core_claim = str(parsed.get("core_claim", ""))
    if len(core_claim) < MIN_CORE_CLAIM_CHARS:
        return None, f"core_claim too short ({len(core_claim)} chars, min {MIN_CORE_CLAIM_CHARS})"

    # identified_weaknesses is a plain string column in the ledger -- some
    # models (observed on a local DeepSeek-R1 distill) emit it as a JSON list
    # of separate weakness strings instead. Normalize to a string BEFORE the
    # truncation-note append below, which assumes string `+=` semantics.
    # Join rather than reject: this is cosmetic drift on a critique field,
    # not a reason to fail the whole candidate (Rule 2 still applies, but
    # only to genuine parse failures).
    weaknesses = parsed.get("identified_weaknesses")
    if isinstance(weaknesses, list):
        parsed["identified_weaknesses"] = "; ".join(str(w) for w in weaknesses)
    elif weaknesses is not None and not isinstance(weaknesses, str):
        parsed["identified_weaknesses"] = str(weaknesses)

    if len(core_claim) > MAX_CORE_CLAIM_CHARS:
        parsed["core_claim"] = core_claim[:MAX_CORE_CLAIM_CHARS]
        parsed.setdefault("identified_weaknesses", "")
        parsed["identified_weaknesses"] += " [core_claim truncated: exceeded max length]"

    connections = parsed.get("connections")
    if isinstance(connections, str):
        parsed["connections"] = [connections]
    elif not isinstance(connections, list):
        parsed["connections"] = []

    # Defensive normalization, not a hard-fail condition: a model that drifts
    # on this meta-field still produced a usable candidate (Rule 2: failures
    # are data, but a cosmetic miss on the exploration signal isn't a failure).
    if parsed.get("exploration_signal") not in VALID_EXPLORATION_SIGNALS:
        parsed["exploration_signal"] = "continue"

    suggested_seeds = parsed.get("suggested_seeds")
    if isinstance(suggested_seeds, str):
        suggested_seeds = [suggested_seeds]
    elif not isinstance(suggested_seeds, list):
        suggested_seeds = []
    if parsed["exploration_signal"] == "continue":
        suggested_seeds = []
    parsed["suggested_seeds"] = [str(s) for s in suggested_seeds if str(s).strip()]

    for field in ("self_rated_novelty", "self_rated_rigor"):
        try:
            raw_value = parsed.get(field)
            value = float(raw_value) if raw_value is not None else 0.5
        except (TypeError, ValueError):
            value = (
                0.5  # neutral default: a drifted/missing self-rating shouldn't fail the candidate
            )
        parsed[field] = max(0.0, min(1.0, value))

    # A drifted claim_type coerces to "structural" — the neutral type with no
    # score bonus reachable, so drift can't be exploited for a better score.
    if parsed.get("claim_type") not in VALID_CLAIM_TYPES:
        parsed["claim_type"] = "structural"

    # Optional — not in REQUIRED_JSON_FIELDS. Shape is validated at
    # evaluation time (hanno.excavator.numeric_check); a malformed or absent
    # witness just means "no numeric_check", not a parse failure.
    numeric_check = parsed.get("numeric_check")
    parsed["numeric_check"] = numeric_check if isinstance(numeric_check, dict) else None

    return parsed, None
