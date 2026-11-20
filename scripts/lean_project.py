"""Prepare or check Hanno's pinned Lean/Mathlib integration project."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "lean"
PROJECT = ROOT / "data" / "mathlib_cache"
FILES = ("lean-toolchain", "lakefile.toml", "lake-manifest.json", "Smoke.lean")


def run(command: list[str], cwd: Path = PROJECT) -> str:
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE)
    print(result.stdout, end="", flush=True)
    result.check_returncode()
    return result.stdout


def check_files() -> None:
    for name in FILES:
        target = PROJECT / name
        if not target.is_file() or target.read_bytes() != (TEMPLATE / name).read_bytes():
            raise SystemExit(
                f"{target} differs from the pinned template or is missing. "
                "Run `uv run poe lean:setup` for a new project. Move an older project "
                "aside first; setup never overwrites different files."
            )


def check() -> None:
    check_files()
    toolchain = (TEMPLATE / "lean-toolchain").read_text().strip()
    installed = run(["elan", "toolchain", "list"], ROOT)
    if not any(line.split()[0] == toolchain for line in installed.splitlines() if line.strip()):
        raise SystemExit("Pinned Lean toolchain is missing; run `uv run poe lean:setup`.")
    manifest = json.loads((TEMPLATE / "lake-manifest.json").read_text())
    for package in manifest["packages"]:
        directory = PROJECT / ".lake" / "packages" / package["name"]
        if not directory.is_dir():
            raise SystemExit(f"Missing {package['name']}; run `uv run poe lean:setup`.")
        actual = run(["git", "rev-parse", "HEAD"], directory).strip()
        if actual != package["rev"]:
            raise SystemExit(f"{directory} is not at its locked revision.")
        run(["git", "diff", "--exit-code", "HEAD", "--"], directory)
    run(["lake", "env", "lean", "Smoke.lean"])
    print("Pinned Lean/Mathlib project passed")


def setup() -> None:
    PROJECT.mkdir(parents=True, exist_ok=True)
    # Validate all existing files before writing any, preserving local projects.
    for name in FILES:
        target = PROJECT / name
        if target.exists() and target.read_bytes() != (TEMPLATE / name).read_bytes():
            raise SystemExit(f"Refusing to overwrite {target}; move the older project aside first.")
    for name in FILES:
        shutil.copyfile(TEMPLATE / name, PROJECT / name)
    toolchain = (TEMPLATE / "lean-toolchain").read_text().strip()
    installed = run(["elan", "toolchain", "list"], ROOT)
    if not any(line.split()[0] == toolchain for line in installed.splitlines() if line.strip()):
        run(["elan", "toolchain", "install", toolchain], ROOT)
    # Lake restores the committed transitive revisions; do not run `lake update` here.
    run(["lake", "exe", "cache", "get"])
    check()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "check"))
    args = parser.parse_args()
    for executable in ("git", "elan", "lake"):
        if not shutil.which(executable):
            parser.error(f"{executable} is required; see docs/development.md#lean-and-mathlib")
    try:
        if args.action == "setup":
            setup()
        else:
            check()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Command failed ({error.returncode}): {' '.join(error.cmd)}") from error


if __name__ == "__main__":
    main()
