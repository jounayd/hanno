"""One deep-verification status for the CLI and exported browser traces."""

from hanno.verification.counterexample import COUNTEREXAMPLE_MARKER


def verification_status(invocations: list[dict], verifications: list[dict]) -> str:
    counterexamples = [inv for inv in invocations if inv.get("phase") == "counterexample"]
    if any(
        inv["exit_code"] == 0
        and not inv["timed_out"]
        and any(
            line.startswith(COUNTEREXAMPLE_MARKER) for line in (inv["stdout"] or "").splitlines()
        )
        for inv in counterexamples
    ):
        return "REFUTED (counterexample found)"
    if any(v["result"] == "VERIFIED" for v in verifications):
        return "PROVEN (complete Lean proof found)"
    if counterexamples and any(
        v["verified_at"] > counterexamples[-1]["invoked_at"] for v in verifications
    ):
        return "unresolved (no counterexample, but no automated proof either)"
    return "not yet verified"
