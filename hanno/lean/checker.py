"""Local Lean checker -- typechecks a statement and tests whether trivial
tactics (`exact?`, `simp`) close it (Step 6 pilot; see LeanVerifierConfig).

Per-file subprocess calls against a precompiled Mathlib cache, not the
REPL/server mode the config anticipates for the eventual real pipeline --
that's the natural next step if this pilot's numbers justify continuing.
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

TRIVIAL_TACTICS = ["exact?", "simp"]


def ensure_mathlib_import(code: str) -> str:
    """Observed on a local DeepSeek-R1 distill in both the triviality-check
    translation step (autoformalize.py) and the full proof-search step
    (prove.py): after fixing a tactic-level error on retry, the model
    sometimes drops the `import Mathlib` line entirely (focused on the
    error, not the file structure) -- which then cascades into spurious
    "failed to synthesize instance" errors on ordinary numerals/comparisons,
    unrelated to the actual proof attempt. Prepending a top-level import is
    always safe (Lean tolerates a redundant duplicate) and fully fixes the
    omission case outright, regardless of which model produced the code.
    Shared here rather than duplicated in both call sites."""
    if code.lstrip().startswith("import Mathlib"):
        return code
    return "import Mathlib\n\n" + code


# Observed verbatim on Claude Haiku, consistently across all 3 formalize
# attempts for a combinatorics claim (sum of cubes = square of sum): it
# wrote `∑ i in Finset.range (n + 1), i ^ 3` -- the older Mathlib big-operator
# binder syntax, deprecated in favor of `∑ i ∈ s, f i`. The model's training
# data clearly still favors `in`, and repeating the same mistake on every
# retry shows an advisory prompt rule alone isn't reliable here -- a
# mechanical, deterministic fix (the same philosophy as ensure_mathlib_import
# above) is more robust than hoping the model self-corrects. Anchored on the
# big-operator symbol itself so it can't misfire on an unrelated `in` (e.g.
# inside an identifier or an unrelated binder) elsewhere in the code.
_DEPRECATED_BIGOP_IN_RE = re.compile(r"([∑∏⋃⋂⨆⨅])(\s+[^,∈]+?)\s+in(?=\s)")


def fix_deprecated_bigop_notation(code: str) -> str:
    return _DEPRECATED_BIGOP_IN_RE.sub(r"\1\2 ∈", code)


@dataclass
class CheckResult:
    compiles: bool
    trivial_by: str | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


def classify_result(lean_code: str | None, check: CheckResult | None) -> str | None:
    """Maps a (lean_code, CheckResult) pair onto lean_verifications.result.
    Shared by hanno lean-pilot and the live until_promising mining check so
    the two never drift into different definitions of the same outcome.
    None means a genuine survivor: compiles, non-trivial, unresolved by this
    filter -- never VERIFIED here, that requires an actual prover."""
    if lean_code is None or check is None:
        return "UNTRANSLATABLE"
    if check.timed_out:
        return "TIMEOUT"
    if not check.compiles:
        return "MALFORMED"
    if check.trivial_by:
        return "KNOWN"
    return None


def _run_lean(project_dir: Path, source: str, timeout_s: int) -> tuple[bool, str, str, bool]:
    scratch = project_dir / f"_scratch_{uuid.uuid4().hex}.lean"
    scratch.write_text(source)
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(scratch)],
            cwd=project_dir,
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


def check_proof(lean_code: str, project_dir: Path, timeout_s: int) -> CheckResult:
    """Typechecks lean_code exactly as given, once -- no trivial-tactic
    substitution. Used both as check_statement's initial check and, on its
    own, by the proof-search loop (hanno/lean/prove.py), which submits real
    tactic attempts rather than a fixed `by sorry` scaffold."""
    # Must be absolute: _run_lean passes cwd=project_dir to subprocess.run AND
    # builds the scratch file path as project_dir/filename -- if project_dir
    # were relative, the two would compose into a doubled, nonexistent path
    # once the subprocess's cwd has already moved into project_dir.
    project_dir = project_dir.resolve()
    start = time.monotonic()

    ok, stdout, stderr, timed_out = _run_lean(project_dir, lean_code, timeout_s)
    return CheckResult(
        compiles=ok and not timed_out,
        trivial_by=None,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int((time.monotonic() - start) * 1000),
        timed_out=timed_out,
    )


def check_statement(lean_code: str, project_dir: Path, timeout_s: int) -> CheckResult:
    project_dir = project_dir.resolve()
    initial = check_proof(lean_code, project_dir, timeout_s)
    if not initial.compiles:
        return initial

    trivial_by = None
    start = time.monotonic()
    for tactic in TRIVIAL_TACTICS:
        variant = lean_code.replace("by sorry", f"by {tactic}", 1)
        t_ok, _, _, t_timed_out = _run_lean(project_dir, variant, timeout_s)
        if not t_timed_out and t_ok:
            trivial_by = tactic
            break

    return CheckResult(
        compiles=True,
        trivial_by=trivial_by,
        stdout=initial.stdout,
        stderr=initial.stderr,
        duration_ms=initial.duration_ms + int((time.monotonic() - start) * 1000),
    )
