#!/usr/bin/env python3
"""SymPy evaluator skill — the "Maple-like" computer algebra tool.

WARNING (read before enabling in sympy_eval.yaml): this script executes
model-written SymPy code with exec(). Enabling it means accepting arbitrary
code execution by the mining model on this machine, bounded only by the
skill's timeout and whatever OS-level sandboxing you run Hanno under. That
is a deliberate miner decision, not a default.

stdin: a Python/SymPy script. Whatever it prints is returned to the model.
Requires: pip install sympy
"""

import sys


def main() -> int:
    code = sys.stdin.read()
    try:
        import sympy  # noqa: F401 — imported so the script can use it directly
    except ImportError:
        print("error: sympy is not installed. Run: pip install sympy")
        return 2
    namespace = {"sympy": sympy}
    exec(code, namespace)  # noqa: S102 — the whole point of this opt-in skill
    return 0


if __name__ == "__main__":
    sys.exit(main())
