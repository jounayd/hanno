#!/usr/bin/env python3
"""run_code — the GENERIC verification instrument.

Instead of a human pre-authoring a tool for every problem, the mining model
writes a short Python program to test whatever its current idea needs (check
a construction, search for a counterexample, compute an example, run a small
simulation) and this executes it and returns whatever it printed. One tool,
every domain.

stdin: a complete Python program.
stdout: the program's own stdout (then, if it failed, a trailing STDERR/
        exit-code note).

CONTAINMENT, NOT A SECURITY BOUNDARY. This runs arbitrary model-written code.
The protections below (CPU/address-space/file-size rlimits, a blocked
network, a throwaway working directory, plus the runner's wall-clock timeout
and output cap) raise the cost of an accident or a trivial escape — they do
NOT make hostile code safe. The miner who sets enabled:true accepts that, and
the honest recommendation is to run Hanno itself inside a container or VM.
"""

import io
import os
import sys
import tempfile
import traceback

# Tunables (best-effort; some are no-ops on platforms without `resource`).
CPU_SECONDS = 10
ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024  # 1 GiB
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB


def _apply_rlimits() -> None:
    try:
        import resource
    except ImportError:
        return  # e.g. Windows — rely on the runner's wall-clock timeout only
    for name, limit in (
        ("RLIMIT_CPU", CPU_SECONDS),
        ("RLIMIT_AS", ADDRESS_SPACE_BYTES),
        ("RLIMIT_FSIZE", MAX_FILE_BYTES),
    ):
        res = getattr(resource, name, None)
        if res is None:
            continue
        try:
            soft, hard = resource.getrlimit(res)
            new_hard = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(res, (min(limit, new_hard), new_hard))
        except (ValueError, OSError):
            pass  # platform refused this particular limit — skip it


def _block_network() -> None:
    """Replace socket construction so the model's code cannot phone home.
    Not airtight (a truly hostile program has other avenues), but it stops
    the ordinary `requests`/`urllib`/`socket` paths cold."""
    import socket

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise OSError("network access is disabled in run_code")

    socket.socket = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    if hasattr(socket, "create_server"):
        socket.create_server = _blocked  # type: ignore[assignment]


def main() -> int:
    code = sys.stdin.read()

    workdir = tempfile.mkdtemp(prefix="hanno_run_")
    try:
        os.chdir(workdir)
    except OSError:
        pass

    _apply_rlimits()
    _block_network()

    # Full builtins on purpose — restricting them is not a real boundary and
    # only breaks honest code; the rlimits/network/tempdir/timeout are the
    # actual containment. __name__ == "__main__" so `if __name__ ...` runs.
    namespace = {"__name__": "__main__", "__builtins__": __builtins__}

    stdout_buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = stdout_buf
    error_text = None
    try:
        exec(compile(code, "<model_code>", "exec"), namespace)  # noqa: S102 — the whole point
    except SystemExit:
        pass
    except BaseException:  # noqa: BLE001 — report any failure back to the model
        error_text = traceback.format_exc()
    finally:
        sys.stdout = real_stdout

    output = stdout_buf.getvalue()
    if output:
        real_stdout.write(output)
        if not output.endswith("\n"):
            real_stdout.write("\n")
    if error_text is not None:
        real_stdout.write("\n[run_code: the program raised an exception]\n")
        real_stdout.write(error_text)
        return 1
    if not output:
        real_stdout.write(
            "[run_code: the program produced no output — did you print your result?]\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
