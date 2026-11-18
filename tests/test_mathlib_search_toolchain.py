"""Name grounding must use the compiler selected by the Mathlib project."""

import subprocess
from unittest.mock import patch

from skills.scripts import mathlib_search


def test_core_sources_follow_the_project_toolchain(tmp_path):
    source = tmp_path / "selected" / "src" / "lean"
    source.mkdir(parents=True)
    binary = tmp_path / "selected" / "bin" / "lean"
    with patch.object(
        mathlib_search.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, stdout=str(binary) + "\n"),
    ) as run:
        assert mathlib_search._lean_core_src_root() == source
    assert run.call_args.args[0] == ["elan", "which", "lean"]
    assert run.call_args.kwargs["cwd"] == mathlib_search.PROJECT_DIR


def test_missing_selected_toolchain_does_not_search_another_version():
    with patch.object(
        mathlib_search.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 1, stdout=""),
    ):
        assert mathlib_search._lean_core_src_root() is None
