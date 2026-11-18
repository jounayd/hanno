"""Build and smoke-test a wheel in an isolated, locked environment."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from hanno.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lean", action="store_true", help="Also run real Lean/Mathlib skills")
    args = parser.parse_args()
    lean_project = (
        Path(load_config().lean_verifier.mathlib_cache_path).resolve() if args.lean else None
    )
    if lean_project and not (lean_project / "lean-toolchain").is_file():
        parser.error("Lean project missing; run `uv run poe lean:setup` first")
    with tempfile.TemporaryDirectory(prefix="hanno-package-check-") as directory:
        temporary = Path(directory)
        dist = temporary / "dist"
        environment = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(temporary / "venv"))
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=ROOT, check=True)
        subprocess.run(
            ["uv", "sync", "--locked", "--no-install-project"],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        python = temporary / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        wheel = next(dist.glob("*.whl"))
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            cwd=ROOT,
            check=True,
        )
        work = temporary / "work"
        work.mkdir()
        command = [str(python), "-I", str(ROOT / "scripts" / "check_installed.py")]
        if lean_project:
            command.extend(["--lean-project", str(lean_project)])
        subprocess.run(command, cwd=work, check=True)


if __name__ == "__main__":
    main()
