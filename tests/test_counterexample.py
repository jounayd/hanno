"""search_for_counterexample: generates a Python stress-test program and
runs it through the sandboxed run_code skill executor, looking for the
COUNTEREXAMPLE FOUND marker. Hermetic: ClaudeClient and run_skill are both
faked, no real subprocess execution or API key needed."""

from unittest.mock import patch

import pytest

from hanno.excavator.client import GenerationResult
from hanno.skills.registry import SkillDefinition
from hanno.skills.runner import SkillResult
from hanno.verification import counterexample as ce_module
from hanno.verification.counterexample import _extract_program, search_for_counterexample

STATEMENT = "For all n >= 2, 4/n = 1/x + 1/y + 1/z has a positive integer solution."
LEAN_CODE = "theorem foo (n : Nat) (hn : n >= 2) : True := by sorry"


class FakeClaudeClient:
    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    async def generate(
        self, prompt: str, temperature: float, max_tokens: int | None = None, **kwargs
    ) -> GenerationResult:
        self.calls += 1
        return GenerationResult(text=self._text, input_tokens=10, output_tokens=10)

    async def close(self) -> None:
        pass


@pytest.fixture
def skill():
    return SkillDefinition(
        name="run_code",
        description="test",
        command=["python3", "run_code.py"],
        input_mode="stdin",
        timeout_seconds=25,
        max_output_chars=6000,
        enabled=True,
    )


def test_extract_program_strips_accidental_fence():
    assert _extract_program("```python\nprint(1)\n```") == "print(1)"


def test_extract_program_passes_through_bare_code():
    assert _extract_program("print(1)") == "print(1)"


@pytest.mark.asyncio
async def test_counterexample_found_marks_refuted(skill):
    client = FakeClaudeClient("for n in range(2, 100):\n    pass")
    fake_result = SkillResult(
        stdout="COUNTEREXAMPLE FOUND: n=7, no solution exists",
        stderr="",
        exit_code=0,
        duration_ms=500,
    )
    with patch.object(ce_module, "run_skill", return_value=fake_result):
        result = await search_for_counterexample(STATEMENT, LEAN_CODE, client, skill)

    assert result.refuted is True
    assert client.calls == 1


@pytest.mark.asyncio
async def test_no_counterexample_found_is_not_refuted(skill):
    client = FakeClaudeClient("for n in range(2, 1000):\n    pass")
    fake_result = SkillResult(
        stdout="NO COUNTEREXAMPLE FOUND after testing 998 cases",
        stderr="",
        exit_code=0,
        duration_ms=800,
    )
    with patch.object(ce_module, "run_skill", return_value=fake_result):
        result = await search_for_counterexample(STATEMENT, LEAN_CODE, client, skill)

    assert result.refuted is False


@pytest.mark.asyncio
async def test_program_strips_fence_before_running(skill):
    client = FakeClaudeClient(
        "```python\nprint('NO COUNTEREXAMPLE FOUND after testing 10 cases')\n```"
    )
    fake_result = SkillResult(
        stdout="NO COUNTEREXAMPLE FOUND after testing 10 cases",
        stderr="",
        exit_code=0,
        duration_ms=100,
    )
    captured = {}

    def fake_run_skill(skill_def, program):
        captured["program"] = program
        return fake_result

    with patch.object(ce_module, "run_skill", side_effect=fake_run_skill):
        result = await search_for_counterexample(STATEMENT, LEAN_CODE, client, skill)

    assert "```" not in captured["program"]
    assert result.refuted is False
