"""Budget reservations cover individual API requests, including tool rounds."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from hanno.config import HannoConfig
from hanno.excavator.client_factory import build_client
from hanno.excavator.spend_tracker import RequestBudget, SpendLimitExceeded
from hanno.ledger.models import ExcavationCycle, Seed, now_iso
from hanno.ledger.repository import Ledger
from hanno.skills.registry import SkillDefinition


@pytest.fixture
def ledger(tmp_path):
    db = Ledger.connect(tmp_path / "budget.db")
    db.init_schema()
    yield db
    db.close()


def test_reservations_are_shared_across_connections_and_settlement_releases_headroom(tmp_path):
    path = tmp_path / "shared.db"
    first, second = Ledger.connect(path), Ledger.connect(path)
    first.init_schema()
    second.init_schema()
    try:
        request = {"model": "fixture", "max_tokens": 1000, "messages": []}
        one, two = RequestBudget(first, 0.05), RequestBudget(second, 0.05)
        call_id = one.reserve(request)
        with pytest.raises(SpendLimitExceeded):
            two.reserve(request)
        one.settle(call_id, 100, 10)
        assert second.get_today_spend_usd() == pytest.approx(0.00045)
        assert two.reserve(request)
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_parallel_requests_cannot_reuse_an_inflight_reservation(ledger):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 0.05
    clients = [
        build_client(config, api_key="fixture", max_tokens=1000, ledger=ledger) for _ in range(2)
    ]
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        started.set()
        await release.wait()
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=10),
            content=[SimpleNamespace(type="text", text="fixture")],
            stop_reason="end_turn",
        )

    for client in clients:
        client.__dict__["_client"] = SimpleNamespace(messages=SimpleNamespace(create=create))
    task = asyncio.create_task(clients[0].generate("prompt", 0.3))
    await started.wait()
    try:
        with pytest.raises(SpendLimitExceeded):
            await clients[1].generate("prompt", 0.3)
    finally:
        release.set()
        await task
    assert len(calls) == 1
    assert ledger.get_today_spend_usd() == pytest.approx(0.00045)


@pytest.mark.asyncio
async def test_each_tool_round_reserves_before_dispatch(ledger):
    config = HannoConfig()
    config.excavator.daily_spend_limit_usd = 0.04
    client = build_client(config, api_key="fixture", max_tokens=1000, ledger=ledger)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1000, output_tokens=1000),
            content=[
                SimpleNamespace(type="tool_use", name="check", id="one", input={"input": "fixture"})
            ],
            stop_reason="tool_use",
        )

    client.__dict__["_client"] = SimpleNamespace(messages=SimpleNamespace(create=create))
    skill = SkillDefinition(name="check", description="fixture", command=["unused"])
    with pytest.raises(SpendLimitExceeded):
        await client.generate("prompt", 0.3, tools=[skill], tool_executor=lambda *_: "checked")
    assert len(calls) == 1
    assert ledger.get_today_spend_usd() == pytest.approx(0.018)


def test_accounted_cycles_do_not_double_count_request_cost_and_history_is_append_only(ledger):
    seed = Seed(text="fixture", domain="mathematics")
    ledger.insert_seed(seed)
    budget = RequestBudget(ledger, 1)
    call_id = budget.reserve({"model": "fixture", "max_tokens": 1000})
    budget.settle(call_id, 100, 10)
    ledger.insert_excavation_cycle(
        ExcavationCycle(
            seed_id=seed.id,
            candidates_requested=1,
            candidates_parsed_ok=1,
            candidates_failed=0,
            total_cost_usd=0.00045,
            started_at=now_iso(),
            completed_at=now_iso(),
            usage_accounted=True,
        )
    )
    assert ledger.get_today_spend_usd() == pytest.approx(0.00045)
    for table in ("model_call_reservations", "model_call_settlements"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger._conn.execute(f"DELETE FROM {table}")
