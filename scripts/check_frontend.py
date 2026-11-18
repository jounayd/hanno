"""Check the plain browser scripts using the Node runtime bundled with dev tools."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    for path in sorted((root / "hanno/ui/static").glob("*.js")):
        subprocess.run([sys.executable, "-m", "nodejs_wheel", "--check", str(path)], check=True)
    print("Browser JavaScript syntax passed")


if __name__ == "__main__":
    main()
