"""Executes one skill invocation: argv subprocess (never a shell), hard
timeout, output truncation. The formatted result string is what the model
sees as the tool_result."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from hanno.skills.registry import SkillDefinition


@dataclass
class SkillResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool = False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated: {len(text) - limit} more chars]"


def run_skill(skill: SkillDefinition, input_text: str) -> SkillResult:
    if skill.input_mode == "argument":
        argv = [part.replace("{input}", input_text) for part in skill.command]
        stdin_data = None
    else:
        argv = list(skill.command)
        stdin_data = input_text

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=skill.timeout_seconds,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return SkillResult(
            stdout=_truncate(proc.stdout or "", skill.max_output_chars),
            stderr=_truncate(proc.stderr or "", skill.max_output_chars),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - started) * 1000)
        return SkillResult(
            stdout="", stderr=f"timed out after {skill.timeout_seconds}s",
            exit_code=-1, duration_ms=duration_ms, timed_out=True,
        )
    except (OSError, ValueError) as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        return SkillResult(
            stdout="", stderr=f"failed to execute: {e}",
            exit_code=-1, duration_ms=duration_ms,
        )


def format_for_model(result: SkillResult) -> str:
    parts = [f"exit_code: {result.exit_code}"]
    if result.timed_out:
        parts.append("TIMED OUT")
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    if not result.stdout and not result.stderr:
        parts.append("(no output)")
    return "\n".join(parts)
