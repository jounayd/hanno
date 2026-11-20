"""Check target preservation and the compiler-side axiom audit."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hanno.lean.checker import CheckResult
from hanno.lean.prove import _check_target_proof, _is_complete_proof, _lean_tokens


@pytest.mark.parametrize(
    "proposal",
    [
        "theorem target : True := by trivial",
        "axiom invented : False\ntheorem target : False := invented",
        "theorem target (h : False) : False := h",
    ],
)
def test_changed_goal_hypotheses_or_prefix_are_rejected_before_compile(proposal):
    with patch("hanno.lean.prove.check_proof") as compile_proof:
        check = _check_target_proof(proposal, "theorem target : False := by sorry", Path("."), 10)
    assert not check.compiles
    compile_proof.assert_not_called()


def test_complete_proof_compiles_with_a_transitive_axiom_audit():
    success = CheckResult(True, None, "", "", 1)
    with patch("hanno.lean.prove.check_proof", return_value=success) as compile_proof:
        check = _check_target_proof(
            "theorem target : True := by trivial",
            "theorem target : True := by sorry",
            Path("."),
            10,
        )
    assert _is_complete_proof("theorem target : True := by trivial", check)
    source = compile_proof.call_args.args[0]
    assert "Lean.collectAxioms `target" in source
    assert "throwError" in source


def test_admit_is_never_a_complete_proof_even_when_lean_compiles_it():
    assert not _is_complete_proof(
        "theorem target : False := by admit", CheckResult(True, None, "", "", 1)
    )


def test_header_comparison_preserves_string_contents_and_identifier_boundaries():
    assert _lean_tokens('"a b"') != _lean_tokens('"ab"')
    assert _lean_tokens("a b") != _lean_tokens("ab")
    assert _lean_tokens("a /- outer /- nested -/ comment -/ b") == ["a", "b"]
