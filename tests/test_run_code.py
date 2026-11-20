"""run_code — the generic code-execution instrument. Must run honest model
code and return its output, report exceptions back instead of crashing,
enforce the wall-clock timeout, and block the ordinary network paths. The
containment here is best-effort, not a security proof — these tests check
that the guardrails engage, not that a hostile program is impossible."""

import subprocess
import sys

PY = sys.executable
SCRIPT = "skills/scripts/run_code.py"


def run_code(program: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, SCRIPT],
        input=program,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_runs_code_and_returns_printed_output():
    proc = run_code("print(sum(range(10)))")
    assert proc.returncode == 0
    assert "45" in proc.stdout


def test_a_real_verification_loop():
    # The intended use: search for a counterexample and report it.
    program = "bad = [n for n in range(2, 50) if (4*n) % 7 == 0]\nprint('hits:', bad)"
    proc = run_code(program)
    assert "hits:" in proc.stdout


def test_exception_is_reported_not_crashed():
    proc = run_code("x = 1 / 0")
    assert proc.returncode == 1
    assert "ZeroDivisionError" in proc.stdout
    assert "raised an exception" in proc.stdout


def test_no_output_is_flagged():
    proc = run_code("x = 2 + 2")  # computes but never prints
    assert proc.returncode == 0
    assert "no output" in proc.stdout


def test_if_name_main_block_runs():
    proc = run_code("if __name__ == '__main__':\n    print('main ran')")
    assert "main ran" in proc.stdout


def test_wall_clock_timeout_is_enforced_by_caller():
    # The runner (and this test) impose a wall-clock timeout; an infinite
    # loop must be killed rather than hang forever.
    import pytest

    with pytest.raises(subprocess.TimeoutExpired):
        run_code("while True:\n    pass", timeout=3)


def test_network_is_blocked():
    program = (
        "import socket\n"
        "try:\n"
        "    socket.socket()\n"
        "    print('NETWORK OPEN')\n"
        "except OSError as e:\n"
        "    print('blocked:', e)"
    )
    proc = run_code(program)
    assert "blocked:" in proc.stdout
    assert "NETWORK OPEN" not in proc.stdout
