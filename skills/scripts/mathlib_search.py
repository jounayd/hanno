#!/usr/bin/env python3
"""mathlib_search — grounds lemma AND tactic names against the real, vendored
Mathlib and Lean core source instead of letting the model recall (and
sometimes hallucinate) a name from training data.

Observed failures this tool directly targets:
1. A proof attempt invoked `Nat.dvd_of_dvd_of_dvd_mul_left`, a LEMMA that does
   not exist in Mathlib, and burned the entire retry budget on a proof built
   around it.
2. A separate attempt invoked `decide_mod_cast`, a TACTIC that does not exist
   either -- a different kind of Lean declaration (tactics are introduced via
   `syntax`/`elab`/`macro ... : tactic`, not `theorem`/`lemma`/`def`), so a
   lemma-only search would have missed it.
Both classes of hallucination end the same way: a real proof strategy fails
on a fabricated name, not a math error. This tool lets the model check a
candidate name (lemma OR tactic), or search by concept, BEFORE betting a
proof attempt on it.

stdin: a plain-text query. Two modes, auto-detected:
  - A single dotted/underscored identifier (e.g. "Nat.Coprime.dvd_of_dvd" or
    "decide_mod_cast") -- exact-name lookup first (against BOTH lemmas and
    tactics); if not found, falls back to fuzzy token-overlap ranking so the
    model sees the closest real names instead of a bare "not found".
  - Multiple words (e.g. "nat dvd mul coprime") -- keyword search: ranks
    declarations by how many query tokens appear in the (namespace-qualified)
    name, case-insensitive.
stdout: up to 15 matches as "name [kind] -- relative/path:line -- <decl line>".
"""
import pathlib
import re
import sys

MATHLIB_ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "mathlib_cache" / ".lake" / "packages" / "mathlib" / "Mathlib"
MAX_RESULTS = 15

_DECL_RE = re.compile(r"^(theorem|lemma|def|instance|abbrev)\s+([A-Za-z_][A-Za-z0-9_'.]*)")
_NAMESPACE_RE = re.compile(r"^namespace\s+(\S+)")
_END_RE = re.compile(r"^end(?:\s+(\S+))?\s*$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_'.]*\??$")  # trailing '?' allowed: Lean's own "search" tactics (exact?, apply?, rewrite?) use it

# Tactics are a different Lean construct from theorems/lemmas -- introduced
# via `syntax`/`elab`/`macro ... : tactic`, with the actual invocation
# keyword as the first quoted string literal in the declaration (often
# followed by a trailing space inside the quotes, e.g. `"exact "` -- stripped
# below). The `: tactic` marker frequently isn't on the SAME line as the
# opening keyword (e.g. `elab (name := normNum)\n    "norm_num" ... : tactic
# =>`, split across two lines to fit long argument lists) -- a window of a
# few following lines is joined before checking, rather than requiring a
# single-line match.
_TACTIC_START_RE = re.compile(r"^\s*(?:syntax|elab|macro)\b")
_TACTIC_MARKER_RE = re.compile(r":\s*tactic\b")
_QUOTED_STRING_RE = re.compile(r'"([^"]*)"')
_TACTIC_DECL_WINDOW = 5  # lines to look ahead for the ": tactic" marker


def _iter_declarations():
    """Yields (qualified_name, path, lineno, decl_line) for every
    theorem/lemma/def/instance/abbrev in the vendored Mathlib tree, tracking
    a namespace stack so names come out reasonably fully-qualified (e.g.
    `Nat.Coprime.foo`, not just `foo`) -- an approximation (doesn't handle
    `open`/anonymous sections specially) but far more useful than bare names."""
    for path in MATHLIB_ROOT.rglob("*.lean"):
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        ns_stack: list[str] = []
        rel = path.relative_to(MATHLIB_ROOT.parent)
        for i, line in enumerate(lines):
            ns_match = _NAMESPACE_RE.match(line)
            if ns_match:
                ns_stack.append(ns_match.group(1))
                continue
            end_match = _END_RE.match(line)
            if end_match and ns_stack:
                ns_stack.pop()
                continue
            decl_match = _DECL_RE.match(line)
            if decl_match:
                bare_name = decl_match.group(2)
                qualified = ".".join(ns_stack + [bare_name]) if ns_stack else bare_name
                yield qualified, rel, i + 1, line.strip()


def _lean_core_src_root() -> pathlib.Path | None:
    """The active elan toolchain's own Lean source (distinct from Mathlib) --
    this is where core tactics like `omega`, `decide`, `exact`, `rfl` are
    declared; Mathlib's own Tactic/ directory covers the rest (`ring`,
    `norm_num`, `linarith`, ...). Discovered dynamically since the toolchain
    directory name is version-specific. None if no toolchain is installed
    locally (tactic verification is then skipped gracefully)."""
    elan_toolchains = pathlib.Path.home() / ".elan" / "toolchains"
    if not elan_toolchains.is_dir():
        return None
    for toolchain_dir in elan_toolchains.iterdir():
        src_lean = toolchain_dir / "src" / "lean"
        if src_lean.is_dir():
            return src_lean
    return None


def _iter_tactic_declarations(root: pathlib.Path):
    """Yields (tactic_name, path, lineno, decl_line) for every tactic
    introduced under `root` via `syntax`/`elab`/`macro ... : tactic` -- the
    invocation keyword is the first quoted string literal in the
    declaration, searched across a small window of following lines since
    the `: tactic` marker often isn't on the same line as the opening
    keyword for longer argument lists."""
    for path in root.rglob("*.lean"):
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not _TACTIC_START_RE.match(line):
                continue
            window_lines = lines[i:i + _TACTIC_DECL_WINDOW]
            window_text = "\n".join(window_lines)
            if not _TACTIC_MARKER_RE.search(window_text):
                continue
            quoted = _QUOTED_STRING_RE.search(window_text)
            if not quoted:
                continue
            name = quoted.group(1).strip()
            if not name:
                continue
            try:
                rel = path.relative_to(root.parent)
            except ValueError:
                rel = path
            yield name, rel, i + 1, line.strip()


def _tokens(name: str) -> set[str]:
    # Split on '.', '_', and camelCase boundaries so "dvdOfDvdMul" and
    # "Nat.dvd_of_dvd" both tokenize into comparable {"dvd", "of", "mul", ...}.
    parts = re.split(r"[._']+", name)
    tokens: set[str] = set()
    for part in parts:
        for sub in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", part):
            if sub:
                tokens.add(sub.lower())
    return tokens


def main() -> int:
    query = sys.stdin.read().strip()
    if not query:
        print("[mathlib_search: empty query -- provide a lemma/tactic name or keywords]")
        return 1
    if not MATHLIB_ROOT.is_dir():
        print(f"[mathlib_search: Mathlib source not found at {MATHLIB_ROOT} -- is the Lean toolchain set up?]")
        return 1

    is_single_identifier = _IDENT_RE.match(query) is not None

    exact_matches = []
    ranked: list[tuple[int, str, str, pathlib.Path, int, str]] = []
    query_tokens = _tokens(query)
    query_bare = query.rsplit(".", 1)[-1]

    for qualified, rel, lineno, decl_line in _iter_declarations():
        bare = qualified.rsplit(".", 1)[-1]
        if is_single_identifier and (qualified == query or bare == query_bare):
            exact_matches.append(("lemma/theorem", qualified, rel, lineno, decl_line))
            continue
        overlap = len(query_tokens & _tokens(qualified))
        if overlap > 0:
            ranked.append((overlap, "lemma/theorem", qualified, rel, lineno, decl_line))

    core_src = _lean_core_src_root()
    tactic_roots = [r for r in (MATHLIB_ROOT, core_src) if r is not None]
    for root in tactic_roots:
        for name, rel, lineno, decl_line in _iter_tactic_declarations(root):
            if is_single_identifier and name == query:
                exact_matches.append(("tactic", name, rel, lineno, decl_line))
                continue
            overlap = len(query_tokens & _tokens(name))
            if overlap > 0:
                ranked.append((overlap, "tactic", name, rel, lineno, decl_line))

    if exact_matches:
        print(f"EXISTS -- found {len(exact_matches)} exact match(es) for {query!r}:")
        for kind, name, rel, lineno, decl_line in exact_matches[:MAX_RESULTS]:
            print(f"  {name} [{kind}] -- {rel}:{lineno} -- {decl_line}")
        return 0

    ranked.sort(key=lambda r: -r[0])
    if not ranked:
        print(f"NOT FOUND -- no lemma or tactic in Mathlib/Lean matches {query!r} (exact or by keyword). Do not guess this name; search a different, simpler keyword instead.")
        return 0

    print(f"NOT FOUND as an exact name -- {query!r} does not exist in Mathlib/Lean as a lemma or tactic. Closest real declarations by keyword overlap:")
    for _, kind, name, rel, lineno, decl_line in ranked[:MAX_RESULTS]:
        print(f"  {name} [{kind}] -- {rel}:{lineno} -- {decl_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
