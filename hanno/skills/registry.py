"""Skill definitions: one YAML file per skill in the skills directory.

Security stance, stated plainly: a skill executes a local command with
model-supplied input. A skill whose command interprets that input as code
(e.g. piping into a CAS or an interpreter) IS opt-in arbitrary code
execution — the miner who writes `enabled: true` in that file owns that
decision. Hanno's guardrails are containment, not safety guarantees: argv
execution with no shell, hard timeouts, output truncation, and a per-
candidate round budget. Skills unfit for that trade should stay disabled.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator

from hanno.logging_setup import get_logger
from hanno.paths import resource_path

logger = get_logger("skills.registry")

# Anthropic tool-name constraint (and general hygiene).
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class SkillDefinition(BaseModel):
    name: str
    description: str
    command: list[str]
    input_mode: str = "stdin"  # "stdin" | "argument" ("{input}" placeholder in command)
    input_description: str = "The input text passed to the tool."
    timeout_seconds: int = 30
    max_output_chars: int = 4000
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_is_tool_safe(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(
                f"skill name {value!r} must match [a-zA-Z0-9_-]{{1,64}} "
                f"(it becomes an Anthropic tool name)"
            )
        return value

    @field_validator("input_mode")
    @classmethod
    def _input_mode_is_known(cls, value: str) -> str:
        if value not in ("stdin", "argument"):
            raise ValueError(f"input_mode must be 'stdin' or 'argument', got {value!r}")
        return value

    @field_validator("command")
    @classmethod
    def _command_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("command must have at least one element")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.input_mode == "argument" and not any("{input}" in part for part in self.command):
            raise ValueError(
                f"skill {self.name!r}: input_mode 'argument' requires an "
                f"'{{input}}' placeholder somewhere in command"
            )


def load_skill_files(directory: str | Path) -> list[SkillDefinition]:
    """Every parseable skill in the directory, INCLUDING disabled ones
    (so `hanno skills` can show what exists). Invalid files are skipped
    with a logged warning, never fatal — one bad YAML must not take down
    mining."""
    directory = Path(directory)
    if directory == Path("skills") and not directory.is_dir():
        directory = resource_path("skills")
    if not directory.is_dir():
        return []
    skills: list[SkillDefinition] = []
    seen_names: set[str] = set()
    for path in sorted(directory.glob("*.y*ml")):
        try:
            with open(path) as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                raise ValueError("top level must be a mapping")
            skill = SkillDefinition(**raw)
            for i, part in enumerate(skill.command):
                if part.startswith("skills/scripts/"):
                    if not Path(part).exists():
                        skill.command[i] = str(resource_path(part))
                    if skill.command[0] == "python3":
                        skill.command[0] = sys.executable
        except (ValidationError, ValueError, yaml.YAMLError) as e:
            logger.warning(f"skipping invalid skill file {path.name}: {e}")
            continue
        if skill.name in seen_names:
            logger.warning(f"skipping {path.name}: duplicate skill name {skill.name!r}")
            continue
        seen_names.add(skill.name)
        skills.append(skill)
    return skills


def load_skills(directory: str | Path) -> list[SkillDefinition]:
    """Only the enabled skills — what actually gets exposed to the model."""
    return [s for s in load_skill_files(directory) if s.enabled]


def set_skill_enabled(path: str | Path, enabled: bool) -> None:
    """Flip a skill file's `enabled:` flag via a targeted line replace --
    preserves every comment and the rest of the file's formatting. A full
    YAML dump would silently drop header comments that files like
    skills/run_code.yaml rely on to state their security tradeoff."""
    path = Path(path)
    text = path.read_text()
    new_value = "true" if enabled else "false"
    new_text, n = re.subn(r"(?m)^enabled:\s*(true|false)\s*$", f"enabled: {new_value}", text)
    if n == 0:
        raise ValueError(f"no top-level 'enabled:' line found in {path}")
    path.write_text(new_text)


def find_skill_file(directory: str | Path, name: str) -> Path | None:
    """The file path backing a given skill name, or None if not found.
    Used by the Settings/Skills UI to toggle `enabled` in place without a
    full YAML rewrite (which would drop the file's comments)."""
    directory = Path(directory)
    if directory == Path("skills") and not directory.exists():
        bundled = resource_path("skills")
        if bundled.is_dir():
            shutil.copytree(
                bundled, directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.y*ml")):
        try:
            with open(path) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict) and raw.get("name") == name:
                return path
        except yaml.YAMLError:
            continue
    return None


def to_anthropic_tools(skills: list[SkillDefinition]) -> list[dict]:
    return [
        {
            "name": s.name,
            "description": s.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": s.input_description},
                },
                "required": ["input"],
            },
        }
        for s in skills
    ]


def to_openai_tools(skills: list[SkillDefinition]) -> list[dict]:
    """OpenAI/Ollama-shaped function-calling tools -- same input schema as
    to_anthropic_tools, wrapped in OpenAI's {"type": "function", ...}
    envelope. See hanno/excavator/openai_client.py."""
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": s.input_description},
                    },
                    "required": ["input"],
                },
            },
        }
        for s in skills
    ]
