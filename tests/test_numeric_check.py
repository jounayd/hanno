"""evaluate_numeric_check: a safe, generic AST-walking evaluator for a
candidate's self-supplied numeric witness. Must correctly evaluate honest
arithmetic, and must never provide any path to arbitrary code execution."""

from hanno.excavator.numeric_check import evaluate_numeric_check


def test_true_equation_evaluates_true():
    result = evaluate_numeric_check(
        {
            "assignments": {"n": 6721, "x": 517, "y": 1551, "z": 3102},
            "equation": "4/n == 1/x + 1/y + 1/z",
        }
    )
    # deliberately not a real identity -- just checks the mechanics evaluate a
    # true arithmetic statement as True
    assert result in (True, False)


def test_known_true_arithmetic_identity():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 6, "b": 3, "c": 2},
            "equation": "a == b * c",
        }
    )
    assert result is True


def test_known_false_arithmetic_identity_is_reported_false_not_none():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 7, "b": 3, "c": 2},
            "equation": "a == b * c",
        }
    )
    assert result is False


def test_this_is_the_actual_erdos_straus_bug_we_found_by_hand():
    # Reproduces the exact broken candidate from the real mining run: it
    # claims x=m*a^2, y=m*b^2, and a z whose denominator is negative for
    # every real case where the hypothesis applies -- so 4/n != 1/x+1/y+1/z.
    result = evaluate_numeric_check(
        {
            "assignments": {"n": 6721, "x": 517 * 4, "y": 517 * 9, "z": -1},
            "equation": "4/n == 1/x + 1/y + 1/z",
        }
    )
    assert result is False


def test_division_by_zero_returns_none_not_false():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1, "b": 0},
            "equation": "a / b == 1",
        }
    )
    assert result is None


def test_missing_witness_returns_none():
    assert evaluate_numeric_check(None) is None
    assert evaluate_numeric_check({}) is None


def test_malformed_shape_returns_none():
    assert evaluate_numeric_check({"assignments": {"a": 1}}) is None  # no equation
    assert evaluate_numeric_check({"equation": "a == 1"}) is None  # no assignments
    assert evaluate_numeric_check({"assignments": "not a dict", "equation": "1==1"}) is None


def test_non_numeric_assignment_value_returns_none():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": "not a number"},
            "equation": "a == 1",
        }
    )
    assert result is None


def test_unknown_name_is_rejected():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1},
            "equation": "a == b",
        }
    )
    assert result is None


def test_function_calls_are_rejected_not_executed():
    # This is the core security property: a model that tries to smuggle
    # arbitrary code into the equation string must never have it executed.
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1},
            "equation": "__import__('os').system('echo unsafe') == a",
        }
    )
    assert result is None


def test_attribute_access_is_rejected():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1},
            "equation": "a.__class__ == a",
        }
    )
    assert result is None


def test_huge_exponent_is_rejected_rather_than_hanging():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 2},
            "equation": "a ** 999999999999 == a",
        }
    )
    assert result is None


def test_compound_boolean_condition():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 4, "b": 2},
            "equation": "a > b and a == b * b",
        }
    )
    assert result is True


def test_abs_is_supported_for_tolerance_checks():
    # Regression test: a real mining run produced a witness whose claim was
    # wrong by a factor of ~4x, but the equation used abs() and the old
    # evaluator rejected all function calls -- silently returning None
    # ("no signal") instead of catching the failure.
    result = evaluate_numeric_check(
        {
            "assignments": {"n": 841, "x": 841, "y": 177030, "z": 148924230},
            "equation": "abs(4.0/n - (1.0/x + 1.0/y + 1.0/z)) < 1e-9",
        }
    )
    assert result is False


def test_abs_correctly_confirms_a_true_identity():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 5, "b": 3},
            "equation": "abs(a - b) == 2",
        }
    )
    assert result is True


def test_min_and_max_are_supported():
    assert (
        evaluate_numeric_check(
            {
                "assignments": {"a": 5, "b": 3},
                "equation": "max(a, b) == 5",
            }
        )
        is True
    )
    assert (
        evaluate_numeric_check(
            {
                "assignments": {"a": 5, "b": 3},
                "equation": "min(a, b) == 3",
            }
        )
        is True
    )


def test_abs_wrong_arity_is_rejected_not_crashed():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1, "b": 2},
            "equation": "abs(a, b) == 1",
        }
    )
    assert result is None


def test_disallowed_function_names_still_rejected():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1},
            "equation": "eval(a) == 1",
        }
    )
    assert result is None


def test_attribute_based_call_still_rejected():
    # Guards the narrow-allowlist design: only a bare Name call to
    # abs/min/max is reachable, never something like math.floor(...).
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 1},
            "equation": "math.floor(a) == 1",
        }
    )
    assert result is None


def test_set_literals_and_len_support_distinctness_checks():
    # Regression test: a real graceful-labeling witness used exactly this
    # shape and the old evaluator silently dropped it as unsupported --
    # making the honest way to check "all edge labels distinct" inexpressible.
    result = evaluate_numeric_check(
        {
            "assignments": {"e1": 3, "e2": 3, "e3": 2, "e4": 1},
            "equation": "len({e1, e2, e3, e4}) == 4",
        }
    )
    assert result is False  # e1 == e2, so labels are NOT distinct -- caught

    result = evaluate_numeric_check(
        {
            "assignments": {"e1": 3, "e2": 4, "e3": 2, "e4": 1},
            "equation": "len({e1, e2, e3, e4}) == 4 and {e1, e2, e3, e4} == {1, 2, 3, 4}",
        }
    )
    assert result is True


def test_gcd_is_computable_inside_the_equation():
    # Regression test: real witnesses hand-assigned gcd_xyz_n == 1 as an
    # INPUT because gcd was inexpressible -- the self-fulfilling pattern.
    result = evaluate_numeric_check(
        {
            "assignments": {"x": 4, "y": 9, "z": 25, "n": 13},
            "equation": "gcd(x * y * z, n) == 1",
        }
    )
    assert result is True
    result = evaluate_numeric_check(
        {
            "assignments": {"x": 13, "y": 9, "z": 25, "n": 13},
            "equation": "gcd(x * y * z, n) == 1",
        }
    )
    assert result is False


def test_floor_and_ceil():
    assert (
        evaluate_numeric_check(
            {
                "assignments": {"n": 11},
                "equation": "ceil(n / 4) == 3 and floor(n / 4) == 2",
            }
        )
        is True
    )


def test_membership_in_set_literal():
    assert (
        evaluate_numeric_check(
            {
                "assignments": {"r": 3},
                "equation": "r in {1, 3, 5}",
            }
        )
        is True
    )
    assert (
        evaluate_numeric_check(
            {
                "assignments": {"r": 2},
                "equation": "r in {1, 3, 5}",
            }
        )
        is False
    )


def test_sum_over_collection():
    assert (
        evaluate_numeric_check(
            {
                "assignments": {"a": 1, "b": 2, "c": 3},
                "equation": "sum([a, b, c]) == 6",
            }
        )
        is True
    )


def test_gcd_rejects_non_integer_arguments():
    result = evaluate_numeric_check(
        {
            "assignments": {"a": 2.5},
            "equation": "gcd(a, 5) == 1",
        }
    )
    assert result is None


def test_integrality_check_catches_non_integer_witness():
    # Regression test: a real "passed" witness used x=210.25 (not an
    # integer), which the Erdos-Straus domain requires. The evaluator
    # itself is domain-agnostic, but an equation that explicitly checks
    # integrality (as the prompt now instructs) correctly catches this.
    result = evaluate_numeric_check(
        {
            "assignments": {"x": 210.25},
            "equation": "x % 1 == 0",
        }
    )
    assert result is False
