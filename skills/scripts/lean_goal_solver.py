#!/usr/bin/env python3
"""lean_goal_solver -- for a stuck sub-goal, tries a battery of standard Lean
closing tactics via REAL compilation and reports which ones actually work,
instead of the model guessing a single tactic and betting a whole proof
attempt on it being the right one.

The generic complement to mathlib_search: that tool grounds NAME lookup
(does this lemma/tactic exist) against real source; this tool grounds TACTIC
SELECTION (does this specific tactic close THIS specific goal) against a
real compile. Observed failure this directly targets: an attempt used
`omega` (a linear-arithmetic-only decision procedure) to try to prove
`n <= n ^ 17` -- a true fact, but omega cannot reason about exponentiation
at all, so it was never going to work regardless of the underlying math
being correct. Rather than hand-enumerating every possible "tactic X can't
handle goal shape Y" trap (an unbounded list across all of mathematics),
this asks Lean's own automation directly, the same way a human would try
`omega <;> decide <;> simp <;> exact?` when unsure which one applies.

stdin: a complete Lean theorem/goal statement, WITHOUT a proof body -- e.g.
  theorem goal (n : ℕ) : n ≤ n ^ 17
Include every hypothesis the goal needs as an explicit named argument (the
same way a decompose_and_prove sub-goal is built) -- this must typecheck as
a STANDALONE statement. A trailing `:= by sorry` (or any existing proof) is
stripped automatically if present.
stdout: which tactic (if any) closes the goal, verified by a real compile,
trying cheap/likely tactics first and stopping at the first success; for
`exact?` specifically, the actual term it found, if any.
"""

import pathlib
import re
import subprocess
import sys
import time
import uuid

from hanno.config import load_config

# Runtime data belongs to the working project, not the installed script directory.
PROJECT_DIR = pathlib.Path(load_config().lean_verifier.mathlib_cache_path).resolve()
# Deliberately tight: these are decision procedures / fast automation, not
# open-ended search -- if one hasn't resolved a small goal in 20s it is
# almost always stuck rather than about to succeed (observed verbatim:
# `omega` ran the full length of a 45s timeout on a genuinely nonlinear goal
# it can never close, rather than failing fast).
TIMEOUT_PER_TACTIC = 20

# Ordered cheap-and-likely -> expensive-and-general, so early exit on the
# first success keeps the common case fast. exact? and apply? perform a real
# library-wide search and are the slowest -- tried last.
CANDIDATE_TACTICS = ["omega", "decide", "norm_num", "simp", "positivity", "exact?"]

_TRAILING_PROOF_RE = re.compile(r":=\s*by\b.*$|:=(?!\s*by\b).*$", re.DOTALL)


def _strip_existing_proof(statement: str) -> str:
    return _TRAILING_PROOF_RE.sub("", statement).strip()


def _run_lean(source: str, timeout_s: int) -> tuple[bool, str, str, bool]:
    scratch = PROJECT_DIR / f"_scratch_goalsolve_{uuid.uuid4().hex}.lean"
    scratch.write_text(source)
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(scratch)],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        stdout = (
            e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        )
        stderr = (
            e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode(errors="replace")
        )
        return False, stdout, stderr, True
    finally:
        scratch.unlink(missing_ok=True)


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(
            "[lean_goal_solver: empty input -- provide a standalone Lean theorem statement, no proof body]"
        )
        return 1
    if not PROJECT_DIR.is_dir():
        print(
            f"[lean_goal_solver: Lean project not found at {PROJECT_DIR} -- is the toolchain set up?]"
        )
        return 1

    statement = _strip_existing_proof(raw)
    if not statement:
        print(
            "[lean_goal_solver: nothing left after stripping the proof body -- provide the statement, e.g. 'theorem goal (n : ℕ) : n ≤ n ^ 17']"
        )
        return 1
    if "import Mathlib" not in statement:
        statement = "import Mathlib\n\n" + statement

    results: list[tuple[str, str, float, str | None]] = []
    for tactic in CANDIDATE_TACTICS:
        source = f"{statement} := by {tactic}"
        start = time.monotonic()
        ok, stdout, _stderr, timed_out = _run_lean(source, TIMEOUT_PER_TACTIC)
        elapsed = time.monotonic() - start
        if timed_out:
            results.append((tactic, "TIMEOUT", elapsed, None))
            continue
        if ok:
            suggestion = None
            if tactic in ("exact?", "apply?"):
                m = re.search(rf"Try this:\s*{re.escape(tactic.rstrip('?'))} (.+)", stdout)
                if m:
                    suggestion = m.group(1).strip()
            results.append((tactic, "WORKS", elapsed, suggestion))
            break  # early exit -- found a working tactic, no need to try the rest
        results.append((tactic, "fails", elapsed, None))

    working = next((r for r in results if r[1] == "WORKS"), None)
    if working:
        tactic, _, elapsed, suggestion = working
        print(f"WORKS -- `{tactic}` closes this goal (verified by a real compile, {elapsed:.1f}s).")
        if suggestion:
            print(f"  exact? found: exact {suggestion}")
        print(
            f"Use `by {tactic}` for this exact sub-goal in your proof -- do not substitute a different tactic for it."
        )
        return 0

    print(
        "NONE of the standard closing tactics worked on this goal (each verified by a real compile, not guessed):"
    )
    for tactic, status, elapsed, _ in results:
        print(f"  {tactic}: {status} ({elapsed:.1f}s)")
    print(
        "This sub-goal needs a real proof strategy, not generic automation -- try induction, "
        "search mathlib_search for a specific lemma that applies, or decompose it further."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
