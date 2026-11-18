"""Check relative Markdown file links without network access."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    broken: list[str] = []
    for path in [root / "README.md", root / "AGENTS.md", *sorted((root / "docs").glob("*.md"))]:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            if not (path.parent / unquote(parts.path)).exists():
                broken.append(f"{path.relative_to(root)}: {target}")
    if broken:
        raise SystemExit("Broken documentation links:\n" + "\n".join(broken))
    print("Documentation file links passed")


if __name__ == "__main__":
    main()
