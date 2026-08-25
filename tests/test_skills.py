"""Miner-registered skills: YAML registry loading, safe subprocess execution,
and the Anthropic tool-definition mapping."""
import sys
import textwrap

from hanno.skills.registry import SkillDefinition, load_skill_files, load_skills, to_anthropic_tools
from hanno.skills.runner import format_for_model, run_skill

PY = sys.executable


def _write(dirpath, name, content):
    p = dirpath / name
    p.write_text(textwrap.dedent(content))
    return p


# ---------- registry ----------

def test_loads_valid_skill_and_skips_disabled(tmp_path):
    _write(tmp_path, "echo.yaml", """
        name: echo
        description: Echo the input back.
        command: ["cat"]
        enabled: true
    """)
    _write(tmp_path, "off.yaml", """
        name: switched_off
        description: A disabled skill.
        command: ["cat"]
        enabled: false
    """)
    all_skills = load_skill_files(tmp_path)
    enabled = load_skills(tmp_path)
    assert {s.name for s in all_skills} == {"echo", "switched_off"}
    assert [s.name for s in enabled] == ["echo"]


def test_invalid_skill_file_is_skipped_not_fatal(tmp_path):
    _write(tmp_path, "broken.yaml", "just a string, not a mapping")
    _write(tmp_path, "bad_name.yaml", """
        name: "has spaces!"
        description: Invalid tool name.
        command: ["cat"]
    """)
    _write(tmp_path, "good.yaml", """
        name: good_one
        description: Fine.
        command: ["cat"]
    """)
    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["good_one"]


def test_argument_mode_requires_placeholder(tmp_path):
    _write(tmp_path, "noplace.yaml", """
        name: noplace
        description: Missing placeholder.
        command: ["echo", "fixed"]
        input_mode: argument
    """)
    assert load_skills(tmp_path) == []


def test_missing_directory_is_empty_not_error(tmp_path):
    assert load_skills(tmp_path / "does_not_exist") == []


def test_anthropic_tool_mapping():
    skill = SkillDefinition(name="check_thing", description="Checks a thing.", command=["cat"])
    tools = to_anthropic_tools([skill])
    assert tools == [{
        "name": "check_thing",
        "description": "Checks a thing.",
        "input_schema": {
            "type": "object",
            "properties": {"input": {"type": "string", "description": skill.input_description}},
            "required": ["input"],
        },
    }]


# ---------- runner ----------

def test_stdin_mode_pipes_input():
    skill = SkillDefinition(
        name="upper", description="d",
        command=[PY, "-c", "import sys; print(sys.stdin.read().upper())"],
    )
    res = run_skill(skill, "hello hanno")
    assert res.exit_code == 0
    assert "HELLO HANNO" in res.stdout


def test_argument_mode_substitutes_placeholder():
    skill = SkillDefinition(
        name="argecho", description="d",
        command=[PY, "-c", "import sys; print('got:' + sys.argv[1])", "{input}"],
        input_mode="argument",
    )
    res = run_skill(skill, "xyz")
    assert res.exit_code == 0
    assert "got:xyz" in res.stdout


def test_timeout_is_enforced():
    skill = SkillDefinition(
        name="sleepy", description="d",
        command=[PY, "-c", "import time; time.sleep(30)"],
        timeout_seconds=1,
    )
    res = run_skill(skill, "")
    assert res.timed_out is True
    assert res.exit_code == -1


def test_output_truncation():
    skill = SkillDefinition(
        name="chatty", description="d",
        command=[PY, "-c", "print('x' * 10000)"],
        max_output_chars=100,
    )
    res = run_skill(skill, "")
    assert len(res.stdout) < 200
    assert "truncated" in res.stdout


def test_nonexistent_command_reports_error_not_crash():
    skill = SkillDefinition(name="ghost", description="d", command=["definitely-not-a-real-binary-xyz"])
    res = run_skill(skill, "")
    assert res.exit_code == -1
    assert "failed to execute" in res.stderr


def test_format_for_model_includes_exit_and_output():
    skill = SkillDefinition(
        name="both", description="d",
        command=[PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
    )
    formatted = format_for_model(run_skill(skill, ""))
    assert "exit_code: 3" in formatted
    assert "out" in formatted
    assert "err" in formatted
