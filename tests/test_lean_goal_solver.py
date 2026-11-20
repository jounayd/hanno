"""lean_goal_solver -- the generic tactic-selection oracle: tries a battery
of standard Lean closing tactics against a stuck sub-goal via real
compilation and reports which one (if any) actually works, instead of a
model guessing a single tactic. Skipped entirely if the local Lean
toolchain/Mathlib checkout isn't set up (see docs/development.md)."""

import subprocess
import sys
from pathlib import Path

import pytest

PY = sys.executable
SCRIPT = "skills/scripts/lean_goal_solver.py"
MATHLIB_CACHE = Path("data/mathlib_cache")

pytestmark = [
    pytest.mark.lean,
    pytest.mark.skipif(
        not MATHLIB_CACHE.is_dir(),
        reason="local Lean project not present -- run `uv run poe lean:setup`",
    ),
]


# Real Lean subprocess timing under this machine's actual load varies a lot
# run to run (observed: the same "quick" query took ~8s once and >60s
# another time) -- every call gets a generous, uniform timeout rather than
# per-case tuning, since the variance is about system load, not this tool's
# own logic (each individual tactic is still bounded by its own
# TIMEOUT_PER_TACTIC inside the script).
CALL_TIMEOUT = 180


def solve(statement: str, timeout: int = CALL_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, SCRIPT],
        input=statement,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_a_goal_closable_by_norm_num_reports_works_and_stops_early():
    proc = solve("theorem goal (n : ℕ) : n + 1 > n")
    assert proc.returncode == 0
    assert "WORKS" in proc.stdout
    # early exit: only tactics up to and including the winner should print
    assert "positivity: fails" not in proc.stdout


def test_the_exact_real_failure_reports_none_work_honestly():
    # The exact goal observed failing in a real Haiku proof attempt: omega
    # cannot reason about exponentiation at all, so it (and the other
    # generic automation tactics) genuinely cannot close this without a
    # case split first -- must be reported honestly, not papered over.
    proc = solve("theorem goal (n : ℕ) : n ≤ n ^ 17")
    assert proc.returncode == 0
    assert "NONE" in proc.stdout
    assert "WORKS" not in proc.stdout
    assert "omega" in proc.stdout


def test_strips_an_existing_sorry_proof_body():
    proc = solve("theorem goal (n : ℕ) : n + 1 > n := by sorry")
    assert proc.returncode == 0
    assert "WORKS" in proc.stdout


def test_empty_input_is_rejected_cleanly():
    proc = solve("")
    assert proc.returncode == 1
    assert "empty input" in proc.stdout
