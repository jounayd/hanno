"""Real compiler checks using a core-only fixture; no Mathlib download or model calls."""

import shutil
import subprocess
from pathlib import Path

import pytest

from hanno.lean.prove import _check_target_proof, _is_complete_proof

pytestmark = pytest.mark.lean


@pytest.fixture(scope="module")
def lean_project(tmp_path_factory):
    if not shutil.which("elan") or not shutil.which("lake"):
        pytest.skip("elan and lake are required for the real compiler audit")
    installed = subprocess.run(
        ["elan", "toolchain", "list"], capture_output=True, text=True, check=True
    )
    toolchain = (
        (Path(__file__).resolve().parents[1] / "lean" / "lean-toolchain").read_text().strip()
    )
    if not any(
        line.split()[0] == toolchain for line in installed.stdout.splitlines() if line.strip()
    ):
        pytest.skip("pinned Lean toolchain missing; run `uv run poe lean:setup`")
    project = tmp_path_factory.mktemp("lean-proof-audit")
    (project / "lean-toolchain").write_text(toolchain + "\n")
    (project / "lakefile.toml").write_text('name = "proofaudit"\n[[lean_lib]]\nname = "Mathlib"\n')
    # The intentionally false imported axiom tests transitive dependency checks.
    (project / "Mathlib.lean").write_text("import Lean\naxiom fixtureFalse : False\n")
    subprocess.run(
        ["lake", "build", "Mathlib"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return project


@pytest.mark.parametrize(
    ("target", "proposal", "expected"),
    [
        ("False", "theorem target : False := by admit", False),
        ("False", "theorem target : False := fixtureFalse", False),
        ("False", "theorem target : True := by trivial", False),
        ("True", "theorem target : True := by trivial", True),
    ],
)
def test_kernel_audit_and_target_preservation(lean_project: Path, target, proposal, expected):
    source = "import Mathlib\n" + proposal
    check = _check_target_proof(
        source, f"import Mathlib\ntheorem target : {target} := by sorry", lean_project, 30
    )
    assert _is_complete_proof(source, check) is expected, check.stdout + check.stderr


@pytest.mark.parametrize(("tactic", "expected"), [("norm_num", True), ("native_decide", False)])
def test_mathlib_tactics_pass_through_the_axiom_audit(tactic, expected):
    project = Path(__file__).resolve().parents[1] / "data" / "mathlib_cache"
    if not (project / ".lake" / "packages" / "mathlib").is_dir():
        pytest.skip("Mathlib not installed; run `uv run poe lean:setup`")
    target = "import Mathlib\ntheorem target : (2 : ℕ) + 2 = 4 := by sorry"
    source = target.replace("by sorry", f"by {tactic}")
    check = _check_target_proof(source, target, project, 30)
    assert _is_complete_proof(source, check) is expected, check.stdout + check.stderr
