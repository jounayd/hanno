"""Phase 0 (EXCAVATOR_MIGRATION_PLAN.md): loads the fixed benchmark seed set
(benchmarks/solve_benchmark.yaml) and runs each claim through solve_claim(),
reporting solve-rate and real $ cost per case. This is the yardstick every
later migration phase (2-6) is measured against -- before/after each change,
the only thing that should move on this benchmark is the solve-rate and the
cost, never the claim wording (that's fixed on purpose -- see the YAML
file's own header comment)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from hanno.excavator.client import ModelClient
from hanno.excavator.spend_tracker import estimate_cost_usd
from hanno.ledger.models import Seed
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition
from hanno.solve.pipeline import SolveResult, solve_claim

DEFAULT_BENCHMARK_PATH = Path(__file__).resolve().parent.parent.parent / "benchmarks" / "solve_benchmark.yaml"


@dataclass
class BenchmarkCase:
    name: str
    claim: str
    difficulty: str


def load_benchmark_cases(path: Path = DEFAULT_BENCHMARK_PATH) -> list[BenchmarkCase]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [BenchmarkCase(**entry) for entry in data.get("benchmarks", [])]


@dataclass
class BenchmarkCaseResult:
    case: BenchmarkCase
    solve_result: SolveResult
    cost_usd: float


async def run_benchmark(
    cases: list[BenchmarkCase],
    client: ModelClient,
    project_dir: Path,
    ledger: Ledger,
    lean_timeout_s: int,
    max_translation_attempts: int,
    max_proof_attempts: int,
    provider: str,
    model_name: str = "solve-mode",
    run_code_skill: SkillDefinition | None = None,
    mathlib_search_skill: SkillDefinition | None = None,
    lean_goal_solver_skill: SkillDefinition | None = None,
    prover_client: ModelClient | None = None,
) -> list[BenchmarkCaseResult]:
    """Each case gets its own fresh Seed row (type="ai-generated",
    submitter_id="benchmark") so every benchmark run leaves a clean,
    independent ledger trail rather than reusing/mutating a prior run's
    seed -- consistent with how every other seed in this project is created.

    Cost estimation note: if `prover_client` uses a DIFFERENT provider than
    `client`, the reported cost still applies the single `provider` rate to
    every token -- SolveResult doesn't currently break tokens down by which
    client generated them. Accurate only when both roles share one provider;
    a known, accepted simplification for this pass."""
    results: list[BenchmarkCaseResult] = []
    for case in cases:
        seed = Seed(
            text=case.claim, domain="mathematics", type="ai-generated", submitter_id="benchmark",
        )
        ledger.insert_seed(seed)
        solve_result = await solve_claim(
            case.claim, seed.id, client, project_dir, ledger,
            lean_timeout_s, max_translation_attempts, max_proof_attempts,
            model_name=model_name, run_code_skill=run_code_skill,
            mathlib_search_skill=mathlib_search_skill, lean_goal_solver_skill=lean_goal_solver_skill,
            prover_client=prover_client,
        )
        cost = estimate_cost_usd(solve_result.input_tokens, solve_result.output_tokens, provider)
        results.append(BenchmarkCaseResult(case=case, solve_result=solve_result, cost_usd=cost))
    return results
