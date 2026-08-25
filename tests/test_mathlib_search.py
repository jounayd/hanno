"""mathlib_search -- grounds lemma AND tactic names against the real,
vendored Mathlib and Lean core source so a proof attempt can check a name
exists before betting on it. Skipped entirely if the local Mathlib checkout
isn't set up (see LOCAL_MODEL_SETUP.md) -- this tool has nothing to search
without it."""
import subprocess
import sys
from pathlib import Path

import pytest

PY = sys.executable
SCRIPT = "skills/scripts/mathlib_search.py"
MATHLIB_ROOT = Path("data/mathlib_cache/.lake/packages/mathlib/Mathlib")

pytestmark = pytest.mark.skipif(
    not MATHLIB_ROOT.is_dir(), reason="local Mathlib checkout not present -- see LOCAL_MODEL_SETUP.md"
)


def search(query: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, SCRIPT], input=query,
        capture_output=True, text=True, timeout=timeout,
    )


def test_exact_match_on_a_real_lemma_name():
    proc = search("dvd_mul_of_dvd_left")
    assert proc.returncode == 0
    assert "EXISTS" in proc.stdout
    assert "dvd_mul_of_dvd_left" in proc.stdout
    assert "Divisibility/Basic.lean" in proc.stdout


def test_hallucinated_name_reports_not_found_with_real_alternatives():
    # The exact lemma name observed hallucinated in a real Haiku proof
    # attempt on n^13 - n divisible by 2730 -- must not report EXISTS, and
    # must surface real, similarly-named alternatives instead of nothing.
    proc = search("Nat.dvd_of_dvd_of_dvd_mul_left")
    assert proc.returncode == 0
    assert "NOT FOUND" in proc.stdout
    assert "EXISTS" not in proc.stdout
    assert "dvd" in proc.stdout.lower()  # real alternatives surfaced, not an empty report


def test_exact_match_on_a_real_core_tactic_name():
    proc = search("omega")
    assert proc.returncode == 0
    assert "EXISTS" in proc.stdout
    assert "[tactic]" in proc.stdout
    assert "Init/Tactics.lean" in proc.stdout


def test_exact_match_on_a_real_mathlib_tactic_with_a_multiline_declaration():
    # norm_num's declaration splits the opening `elab (name := normNum)` and
    # the `"norm_num" ... : tactic` marker across two lines -- must not
    # require both on a single line.
    proc = search("norm_num")
    assert proc.returncode == 0
    assert "EXISTS" in proc.stdout
    assert "[tactic]" in proc.stdout


def test_exact_match_on_a_trailing_question_mark_tactic():
    # exact?/apply? are real Lean "search" tactics using a trailing '?' --
    # confirmed to fall through to noisy fuzzy-only search before this fix,
    # since the identifier regex didn't allow '?'.
    proc = search("exact?")
    assert proc.returncode == 0
    assert "EXISTS" in proc.stdout
    assert "[tactic]" in proc.stdout


def test_hallucinated_tactic_name_reports_not_found():
    # The exact tactic name observed hallucinated in a real Haiku proof
    # attempt on the same n^13 - n claim (a different failure class than the
    # hallucinated-lemma case above -- tactics are a different Lean
    # declaration shape entirely).
    proc = search("decide_mod_cast")
    assert proc.returncode == 0
    assert "NOT FOUND" in proc.stdout
    assert "EXISTS" not in proc.stdout


def test_keyword_search_ranks_by_token_overlap():
    proc = search("nat dvd coprime mul")
    assert proc.returncode == 0
    assert "Coprime" in proc.stdout
    assert "dvd" in proc.stdout.lower()


def test_empty_query_is_rejected_cleanly():
    proc = search("")
    assert proc.returncode == 1
    assert "empty query" in proc.stdout


def test_completes_within_a_reasonable_time():
    # A broad, low-selectivity query still has to scan the whole tree --
    # confirm it stays well under the skill's configured 20s timeout.
    proc = search("n dvd", timeout=15)
    assert proc.returncode == 0
