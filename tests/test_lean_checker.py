"""hanno.lean.checker: typecheck + exact?/simp triviality check via `lake env
lean` subprocess calls. Hermetic -- subprocess.run is stubbed, so this suite
never needs the real Lean toolchain (that only runs in the manual pilot)."""
from pathlib import Path
from unittest.mock import patch

from hanno.lean import checker


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_statement_that_fails_to_typecheck_is_malformed(tmp_path):
    with patch.object(checker.subprocess, "run", return_value=FakeCompletedProcess(1, stderr="error: unknown identifier")):
        result = checker.check_statement("theorem foo : Bogus := by sorry", tmp_path, timeout_s=10)

    assert result.compiles is False
    assert result.trivial_by is None
    assert not result.timed_out


def test_statement_closed_by_exact_is_trivial(tmp_path):
    # First call (the `sorry` typecheck) succeeds; second call (`exact?` variant) also succeeds.
    with patch.object(checker.subprocess, "run", return_value=FakeCompletedProcess(0)):
        result = checker.check_statement("theorem foo : True := by sorry", tmp_path, timeout_s=10)

    assert result.compiles is True
    assert result.trivial_by == "exact?"


def test_statement_that_survives_both_tactics_is_not_trivial(tmp_path):
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        # Call 1: sorry typecheck succeeds. Calls 2-3: exact?/simp both fail.
        return FakeCompletedProcess(0) if calls["n"] == 1 else FakeCompletedProcess(1, stderr="failed")

    with patch.object(checker.subprocess, "run", side_effect=fake_run):
        result = checker.check_statement("theorem foo : NonTrivial := by sorry", tmp_path, timeout_s=10)

    assert result.compiles is True
    assert result.trivial_by is None
    assert calls["n"] == 3


def test_timeout_is_reported_and_does_not_raise(tmp_path):
    import subprocess as real_subprocess

    with patch.object(
        checker.subprocess, "run",
        side_effect=real_subprocess.TimeoutExpired(cmd="lake", timeout=10, output="", stderr=""),
    ):
        result = checker.check_statement("theorem foo : True := by sorry", tmp_path, timeout_s=10)

    assert result.timed_out is True
    assert result.compiles is False


def test_scratch_file_is_cleaned_up(tmp_path):
    with patch.object(checker.subprocess, "run", return_value=FakeCompletedProcess(0)):
        checker.check_statement("theorem foo : True := by sorry", tmp_path, timeout_s=10)

    assert list(tmp_path.glob("_scratch_*.lean")) == []


def test_relative_project_dir_does_not_double_up_the_scratch_path(tmp_path, monkeypatch):
    # Regression: subprocess.run(cwd=project_dir) changes the subprocess's
    # working directory to project_dir -- if project_dir were relative and
    # the scratch file path also embedded it (project_dir/filename), the two
    # would compose into project_dir/project_dir/filename once inside the
    # subprocess, and `lean` would report the file missing.
    monkeypatch.chdir(tmp_path)
    relative_dir = Path(".")
    captured = {}

    def fake_run(cmd, cwd=None, **kwargs):
        captured["cwd"] = cwd
        captured["file_arg"] = cmd[-1]
        return FakeCompletedProcess(0)

    with patch.object(checker.subprocess, "run", side_effect=fake_run):
        checker.check_statement("theorem foo : True := by sorry", relative_dir, timeout_s=10)

    assert captured["cwd"].is_absolute()
    assert Path(captured["file_arg"]).is_absolute()
    assert Path(captured["file_arg"]).parent == captured["cwd"]


def test_ensure_mathlib_import_prepends_when_missing():
    code = "theorem foo : 1 + 1 = 2 := by norm_num"
    result = checker.ensure_mathlib_import(code)
    assert result == "import Mathlib\n\n" + code


def test_ensure_mathlib_import_does_not_duplicate_when_present():
    code = "import Mathlib\n\ntheorem foo : 1 + 1 = 2 := by norm_num"
    result = checker.ensure_mathlib_import(code)
    assert result == code
    assert result.count("import Mathlib") == 1


def test_fix_deprecated_bigop_notation_rewrites_sum_in_to_sum_element_of():
    code = "theorem foo (n : ℕ) : ∑ i in Finset.range (n + 1), i ^ 3 = 0 := by sorry"
    result = checker.fix_deprecated_bigop_notation(code)
    assert result == "theorem foo (n : ℕ) : ∑ i ∈ Finset.range (n + 1), i ^ 3 = 0 := by sorry"


def test_fix_deprecated_bigop_notation_rewrites_prod_too():
    code = "∏ i in Finset.range n, i"
    result = checker.fix_deprecated_bigop_notation(code)
    assert result == "∏ i ∈ Finset.range n, i"


def test_fix_deprecated_bigop_notation_leaves_current_syntax_untouched():
    code = "∑ i ∈ Finset.range (n + 1), i ^ 3"
    result = checker.fix_deprecated_bigop_notation(code)
    assert result == code


def test_fix_deprecated_bigop_notation_leaves_unrelated_in_untouched():
    code = "theorem foo (n : ℕ) (h : n ∈ Finset.range 10) : n < 10 := by simp_all"
    result = checker.fix_deprecated_bigop_notation(code)
    assert result == code
