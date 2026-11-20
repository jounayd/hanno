"""Smoke-test a wheel from outside the checkout: python -I /path/to/this/file."""

import argparse
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from hanno.config import DEFAULT_CONFIG_PATH, load_config
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition, load_skills
from hanno.skills.runner import run_skill
from hanno.solve.benchmark import load_benchmark_cases
from hanno.ui.server import app


def check_lean_skills(skills: dict[str, SkillDefinition]) -> None:
    for name in ("mathlib_search", "lean_goal_solver"):
        assert skills[name].command[0] == sys.executable, skills[name].command
    search = run_skill(skills["mathlib_search"], "omega")
    assert search.exit_code == 0 and "EXISTS" in search.stdout, search
    proof = run_skill(skills["lean_goal_solver"], "theorem goal (n : ℕ) : n + 1 > n")
    assert proof.exit_code == 0 and "WORKS" in proof.stdout, proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lean-project", type=Path, help="Also exercise Lean skills with this cache"
    )
    args = parser.parse_args()
    if args.lean_project:
        project = args.lean_project.resolve()
        assert (project / "lean-toolchain").is_file(), project
        os.environ["HANNO_LEAN_VERIFIER__MATHLIB_CACHE_PATH"] = str(project)
    assert DEFAULT_CONFIG_PATH.is_file(), DEFAULT_CONFIG_PATH
    config = load_config()
    assert config.excavator.model
    assert load_benchmark_cases()
    ledger = Ledger.connect("smoke.db")
    try:
        ledger.init_schema()
        assert ledger._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        ledger.close()
    skills = {skill.name: skill for skill in load_skills("skills")}
    assert {"run_code", "graceful_check", "mathlib_search", "lean_goal_solver"} <= skills.keys()
    result = run_skill(skills["graceful_check"], '{"edges": [[0, 1]], "labels": {"0": 0, "1": 1}}')
    assert result.exit_code == 0, result
    if args.lean_project:
        check_lean_skills(skills)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/config").status_code == 200
        assert len(client.get("/api/skills").json()) >= 4
        assert (
            client.post("/api/skills/graceful_check/toggle", json={"enabled": False}).status_code
            == 200
        )
        skill_state = {skill["name"]: skill for skill in client.get("/api/skills").json()}
        assert not skill_state["graceful_check"]["enabled"]
        assert skill_state["run_code"]["enabled"]
        assert Path("skills/graceful_check.yaml").is_file()
    if args.lean_project:
        check_lean_skills({skill.name: skill for skill in load_skills("skills")})
        print("Lean skill smoke check passed for bundled and copied scripts")
    print(f"Installed package smoke check passed in {Path.cwd()}")


if __name__ == "__main__":
    main()
