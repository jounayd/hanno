"""Lean skills must work when their scripts are installed away from the project."""

import io
import runpy
import shutil
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["mathlib_search.py", "lean_goal_solver.py"])
@pytest.mark.parametrize("custom_path", [False, True])
def test_relocated_skill_uses_the_working_project(
    tmp_path, monkeypatch, capsys, script, custom_path
):
    root = Path(__file__).resolve().parents[1]
    installed = tmp_path / "installed" / "share" / "hanno" / "skills" / "scripts" / script
    installed.parent.mkdir(parents=True)
    shutil.copyfile(root / "skills" / "scripts" / script, installed)
    work = tmp_path / "work"
    work.mkdir()
    project = work / ("custom-project" if custom_path else "data/mathlib_cache")
    mathlib = project / ".lake" / "packages" / "mathlib" / "Mathlib"
    mathlib.mkdir(parents=True)
    (mathlib / "Fixture.lean").write_text("theorem fixture_lemma : True := by trivial\n")
    monkeypatch.chdir(work)
    if custom_path:
        monkeypatch.setenv("HANNO_LEAN_VERIFIER__MATHLIB_CACHE_PATH", str(project))
    else:
        monkeypatch.delenv("HANNO_LEAN_VERIFIER__MATHLIB_CACHE_PATH", raising=False)
    namespace = runpy.run_path(str(installed))
    main = namespace["main"]
    if script == "mathlib_search.py":
        monkeypatch.setattr("sys.stdin", io.StringIO("fixture_lemma"))
        assert main() == 0
        assert "EXISTS" in capsys.readouterr().out
    else:
        monkeypatch.setattr("sys.stdin", io.StringIO("theorem goal : True"))

        def compile_goal(source, timeout_s):
            assert "theorem goal : True := by omega" in source
            return True, "", "", False

        monkeypatch.setitem(main.__globals__, "_run_lean", compile_goal)
        assert main() == 0
        assert "WORKS" in capsys.readouterr().out
