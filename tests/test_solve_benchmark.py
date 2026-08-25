"""hanno.solve.benchmark: loads the fixed Phase 0 solvable-seed benchmark
and runs each case through solve_claim(). Hermetic -- solve_claim is faked
at the hanno.solve.benchmark import site, so no real API key, network call,
or Lean toolchain is needed."""
from unittest.mock import patch

import pytest

from hanno.ledger.repository import Ledger
from hanno.solve import benchmark as benchmark_module
from hanno.solve.benchmark import BenchmarkCase, DEFAULT_BENCHMARK_PATH, load_benchmark_cases, run_benchmark
from hanno.solve.pipeline import SolveResult


@pytest.fixture
def ledger(tmp_path):
    l = Ledger.connect(tmp_path / "test.db")
    l.init_schema()
    yield l
    l.close()


def test_default_benchmark_file_loads_and_is_non_empty():
    cases = load_benchmark_cases()
    assert len(cases) >= 3
    assert all(isinstance(c, BenchmarkCase) for c in cases)
    assert all(c.claim and c.name and c.difficulty for c in cases)


def test_default_benchmark_path_points_at_the_real_fixture():
    assert DEFAULT_BENCHMARK_PATH.exists()
    assert DEFAULT_BENCHMARK_PATH.name == "solve_benchmark.yaml"


def _solved(candidate_id="c1"):
    return SolveResult(
        candidate_id=candidate_id, triviality_lean_code="import Mathlib\n\ntheorem foo : True := by sorry",
        triviality_result=None, refuted=False, counterexample_output=None,
        proved=True, proof_code="theorem foo : True := by trivial", discovery_id="d1",
        stage_reached="proved", input_tokens=1_000_000, output_tokens=1_000_000,
    )


class FakeClient:
    async def close(self):
        pass


@pytest.mark.asyncio
async def test_run_benchmark_creates_a_fresh_seed_per_case_and_reports_cost(ledger, tmp_path):
    cases = [
        BenchmarkCase(name="a", claim="claim A", difficulty="easy"),
        BenchmarkCase(name="b", claim="claim B", difficulty="hard"),
    ]
    with patch.object(benchmark_module, "solve_claim", return_value=_solved()) as mock_solve:
        results = await run_benchmark(
            cases, FakeClient(), tmp_path, ledger,
            lean_timeout_s=10, max_translation_attempts=3, max_proof_attempts=5,
            provider="anthropic",
        )

    assert mock_solve.call_count == 2
    assert len(results) == 2
    # 1M input + 1M output tokens at anthropic rates ($3/$15 per M) = $18.00
    assert results[0].cost_usd == pytest.approx(18.0)
    seeds_created = ledger._conn.execute(
        "SELECT text, submitter_id FROM seeds WHERE submitter_id = 'benchmark'"
    ).fetchall()
    assert len(seeds_created) == 2
    assert {row["text"] for row in seeds_created} == {"claim A", "claim B"}


@pytest.mark.asyncio
async def test_run_benchmark_reports_zero_cost_for_non_anthropic_provider(ledger, tmp_path):
    cases = [BenchmarkCase(name="a", claim="claim A", difficulty="easy")]
    with patch.object(benchmark_module, "solve_claim", return_value=_solved()):
        results = await run_benchmark(
            cases, FakeClient(), tmp_path, ledger,
            lean_timeout_s=10, max_translation_attempts=3, max_proof_attempts=5,
            provider="openai_compatible",
        )

    assert results[0].cost_usd == 0.0
