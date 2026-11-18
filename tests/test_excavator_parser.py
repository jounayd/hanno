"""Defensive parsing of Excavator model output (Step 3 risk: format drift)."""

import json

from hanno.excavator.parser import _repair_invalid_backslash_escapes, parse_candidate_json

GOOD_PAYLOAD = {
    "domain": "mathematics",
    "core_claim": "For every finite abelian group G, there exists a natural embedding into a product of cyclic groups that preserves the fingerprint of its Sylow subgroups.",
    "formal_statement": "forall G : FiniteAbelianGroup, exists f : G -> Prod CyclicGroup, Injective f",
    "intuition": "This generalizes the fundamental theorem of finite abelian groups.",
    "connections": ["fundamental theorem of finite abelian groups", "Sylow theorems"],
    "self_assessed_novelty": "likely already known in some form",
    "identified_weaknesses": "may be a trivial restatement",
    "exploration_signal": "continue",
    "suggested_seeds": [],
    "self_rated_novelty": 0.3,
    "self_rated_rigor": 0.6,
    "claim_type": "structural",
}


def test_parses_clean_json():
    parsed, error = parse_candidate_json(json.dumps(GOOD_PAYLOAD))
    assert error is None
    assert parsed is not None
    assert parsed["core_claim"] == GOOD_PAYLOAD["core_claim"]


def test_parses_json_wrapped_in_markdown_fences():
    text = f"```json\n{json.dumps(GOOD_PAYLOAD)}\n```"
    parsed, error = parse_candidate_json(text)
    assert error is None
    assert parsed is not None
    assert parsed["domain"] == "mathematics"


def test_parses_json_with_leading_trailing_prose():
    text = f"Here is my answer:\n{json.dumps(GOOD_PAYLOAD)}\nHope that helps!"
    parsed, error = parse_candidate_json(text)
    assert error is None


def test_unescaped_latex_backslashes_are_repaired():
    # Observed verbatim on a local DeepSeek-R1 distill: LaTeX delimiters
    # written with a single backslash directly inside a JSON string value,
    # invalid per the JSON spec, alongside occasional correctly-doubled ones
    # in the same payload -- both must end up as one literal backslash.
    raw = (
        '{"domain": "mathematics", '
        '"core_claim": "For every integer \\( n \\geq 2 \\\\), the fraction \\( \\frac{4}{n} \\) '
        'can be expressed as a sum of three unit fractions depending on n mod 4.", '
        '"formal_statement": "f", "intuition": "i", "connections": [], '
        '"self_assessed_novelty": "s", "identified_weaknesses": "w", '
        '"exploration_signal": "continue", "suggested_seeds": [], '
        '"self_rated_novelty": 0.5, "self_rated_rigor": 0.5, "claim_type": "structural"}'
    )
    parsed, error = parse_candidate_json(raw)
    assert error is None
    assert parsed is not None
    assert r"\geq" in parsed["core_claim"]
    assert parsed is not None
    assert r"\frac" in parsed["core_claim"]


def test_repair_does_not_corrupt_latex_macros_starting_with_a_json_escape_letter():
    # \b \f \n \r \t \u are all technically legal JSON escapes, but \beta,
    # \frac, \neq, \rightarrow, \tau, \underline start with exactly those
    # letters -- a naive repair that treats those as "already valid" would
    # silently decode \frac into a formfeed control byte + "rac", destroying
    # the content instead of fixing the escaping.
    import json as _json

    for macro, expected in [
        (r"\beta", r"\beta"),
        (r"\frac", r"\frac"),
        (r"\neq", r"\neq"),
        (r"\rightarrow", r"\rightarrow"),
        (r"\tau", r"\tau"),
        (r"\underline", r"\underline"),
    ]:
        repaired = _repair_invalid_backslash_escapes(f'{{"x": "{macro}"}}')
        assert _json.loads(repaired)["x"] == expected


def test_repair_pass_is_not_used_when_json_is_already_valid():
    # A payload containing a literal backslash that json.dumps correctly
    # double-escapes must parse on the first attempt -- the repair fallback
    # should never even be needed.
    payload = dict(GOOD_PAYLOAD)
    payload["core_claim"] = payload["core_claim"] + " involving \\alpha and \\beta."
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert "\\alpha" in parsed["core_claim"]


def test_malformed_json_fails_gracefully():
    text = "{ this is not: valid json at all !!"
    parsed, error = parse_candidate_json(text)
    assert parsed is None
    assert error is not None
    assert "JSON decode error" in error


def test_missing_required_field_fails():
    payload = dict(GOOD_PAYLOAD)
    del payload["formal_statement"]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert parsed is None
    assert error is not None
    assert "missing required fields" in error


def test_core_claim_too_short_fails():
    payload = dict(GOOD_PAYLOAD)
    payload["core_claim"] = "too short"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert parsed is None
    assert error is not None
    assert "core_claim too short" in error


def test_connections_as_string_normalized_to_list():
    payload = dict(GOOD_PAYLOAD)
    payload["connections"] = "just one connection as a string"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["connections"] == ["just one connection as a string"]


def test_identified_weaknesses_as_list_normalized_to_string():
    # Observed on a local DeepSeek-R1 distill: emits a JSON array of separate
    # weakness strings instead of one string, which used to crash the sqlite
    # insert (list is not a bindable type) rather than just failing gracefully.
    payload = dict(GOOD_PAYLOAD)
    payload["identified_weaknesses"] = ["may be trivial", "unverified for infinite groups"]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["identified_weaknesses"] == "may be trivial; unverified for infinite groups"


def test_identified_weaknesses_list_plus_core_claim_truncation_stays_a_string():
    payload = dict(GOOD_PAYLOAD)
    payload["identified_weaknesses"] = ["one", "two"]
    payload["core_claim"] = "x" * 4001
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert isinstance(parsed["identified_weaknesses"], str)
    assert parsed is not None
    assert parsed["identified_weaknesses"] == "one; two [core_claim truncated: exceeded max length]"


def test_branch_point_keeps_suggested_seeds():
    payload = dict(GOOD_PAYLOAD)
    payload["exploration_signal"] = "branch_point"
    payload["suggested_seeds"] = [
        "explore the p-group case separately",
        "explore the infinite case",
    ]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["exploration_signal"] == "branch_point"
    assert parsed is not None
    assert parsed["suggested_seeds"] == [
        "explore the p-group case separately",
        "explore the infinite case",
    ]


def test_dead_end_with_no_suggested_seed_is_fine():
    payload = dict(GOOD_PAYLOAD)
    payload["exploration_signal"] = "dead_end"
    payload["suggested_seeds"] = []
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["exploration_signal"] == "dead_end"
    assert parsed is not None
    assert parsed["suggested_seeds"] == []


def test_continue_signal_discards_any_suggested_seeds():
    payload = dict(GOOD_PAYLOAD)
    payload["exploration_signal"] = "continue"
    payload["suggested_seeds"] = ["should be dropped"]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["suggested_seeds"] == []


def test_invalid_exploration_signal_coerced_to_continue():
    payload = dict(GOOD_PAYLOAD)
    payload["exploration_signal"] = "something the model made up"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["exploration_signal"] == "continue"


def test_missing_exploration_signal_fails_required_field_check():
    payload = dict(GOOD_PAYLOAD)
    del payload["exploration_signal"]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert parsed is None
    assert error is not None
    assert "missing required fields" in error


def test_self_rated_scores_pass_through():
    parsed, error = parse_candidate_json(json.dumps(GOOD_PAYLOAD))
    assert error is None
    assert parsed is not None
    assert parsed["self_rated_novelty"] == 0.3
    assert parsed is not None
    assert parsed["self_rated_rigor"] == 0.6


def test_self_rated_scores_clamped_to_unit_range():
    payload = dict(GOOD_PAYLOAD)
    payload["self_rated_novelty"] = 1.7
    payload["self_rated_rigor"] = -0.5
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["self_rated_novelty"] == 1.0
    assert parsed is not None
    assert parsed["self_rated_rigor"] == 0.0


def test_non_numeric_self_rated_score_defaults_to_neutral():
    payload = dict(GOOD_PAYLOAD)
    payload["self_rated_novelty"] = "very novel"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["self_rated_novelty"] == 0.5


def test_missing_self_rated_novelty_fails_required_field_check():
    payload = dict(GOOD_PAYLOAD)
    del payload["self_rated_novelty"]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert parsed is None
    assert error is not None
    assert "missing required fields" in error


def test_claim_type_passes_through():
    payload = dict(GOOD_PAYLOAD)
    payload["claim_type"] = "construction"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["claim_type"] == "construction"


def test_invalid_claim_type_coerced_to_structural():
    payload = dict(GOOD_PAYLOAD)
    payload["claim_type"] = "something the model made up"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["claim_type"] == "structural"


def test_missing_claim_type_fails_required_field_check():
    payload = dict(GOOD_PAYLOAD)
    del payload["claim_type"]
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert parsed is None
    assert error is not None
    assert "missing required fields" in error


def test_numeric_check_absent_defaults_to_none():
    parsed, error = parse_candidate_json(json.dumps(GOOD_PAYLOAD))
    assert error is None
    assert parsed is not None
    assert parsed["numeric_check"] is None


def test_numeric_check_null_stays_none():
    payload = dict(GOOD_PAYLOAD)
    payload["numeric_check"] = None
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["numeric_check"] is None


def test_numeric_check_dict_passes_through_unvalidated():
    payload = dict(GOOD_PAYLOAD)
    payload["numeric_check"] = {"assignments": {"n": 7}, "equation": "n > 0"}
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["numeric_check"] == {"assignments": {"n": 7}, "equation": "n > 0"}


def test_malformed_numeric_check_does_not_fail_the_candidate():
    payload = dict(GOOD_PAYLOAD)
    payload["numeric_check"] = "not even a dict"
    parsed, error = parse_candidate_json(json.dumps(payload))
    assert error is None
    assert parsed is not None
    assert parsed["numeric_check"] is None


def test_drifted_temperature_style_output_with_extra_commentary():
    # Simulates the strategy doc's explicit warning: high-temperature (1.2)
    # outputs drift from the requested format with extra commentary.
    text = (
        "Sure! I've been thinking about this creatively. Let me present an "
        "unconventional idea:\n\n"
        f"{json.dumps(GOOD_PAYLOAD)}\n\n"
        "Let me know if you'd like me to explore this further!"
    )
    parsed, error = parse_candidate_json(text)
    assert error is None
    assert parsed is not None
    assert parsed["core_claim"] == GOOD_PAYLOAD["core_claim"]
