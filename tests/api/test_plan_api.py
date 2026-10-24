# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/plan``, ``/plan/status``, ``/plan/replan`` and the live ``plan`` message (WP9)."""

from __future__ import annotations

from datetime import datetime

from websockets.asyncio.client import connect

from suncore.api import create_app
from suncore.app.planning import PlanningService
from tests.api.conftest import FakeServices, client_for, serving
from tests.api.test_control_api import recv
from tests.app.planning_helpers import World


def with_planner(services: FakeServices) -> tuple[World, PlanningService]:
    world = World()
    services.planning = PlanningService(world.collect, world.live, clock=world.clock)
    return world, services.planning


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def test_no_plan_yet_is_the_default_plan_with_a_reason(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/plan")
    assert r.status_code == 200
    body = r.json()
    assert body["plan"]["source"] == "default"
    assert body["plan"]["intervals"] == []
    assert body["in_use"] is False
    assert body["fallback"]["key"] == "planner.status.no_plan"


async def test_the_plan_carries_intervals_flows_prices_bands_reasons_and_stats(
    services: FakeServices,
) -> None:
    _, planning = with_planner(services)
    await planning.tick()
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/plan", params={"hours": 6})
    body = r.json()
    plan = body["plan"]
    assert plan["source"] in ("solver", "heuristic")
    assert plan["stats"]["status"]
    assert plan["created_at"]
    assert body["stale"] is False
    assert body["in_use"] is True
    first = plan["intervals"][0]
    assert {"expected", "prices", "battery", "devices", "reasons"} <= set(first)
    assert first["expected"]["soc_pct"] is not None
    assert plan["reasons"]
    assert len(plan["intervals"]) <= 6 * 4 + 1


async def test_the_horizon_is_validated(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        assert (await client.get("/api/v1/plan", params={"hours": 0})).status_code == 422
        assert (await client.get("/api/v1/plan", params={"hours": 49})).status_code == 422


async def test_a_stale_plan_is_flagged_and_not_in_use(services: FakeServices) -> None:
    world, planning = with_planner(services)
    await planning.tick()
    world.clock.advance(3 * 3600)
    async with client_for(create_app(services)) as client:
        body = (await client.get("/api/v1/plan")).json()
    assert body["stale"] is True
    assert body["in_use"] is False
    assert body["fallback"]["key"] == "planner.status.stale"


async def test_status_reports_the_last_run_and_the_next(services: FakeServices) -> None:
    _, planning = with_planner(services)
    await planning.tick()
    async with client_for(create_app(services)) as client:
        body = (await client.get("/api/v1/plan/status")).json()
    assert body["last_outcome"] == "ok"
    assert body["last_trigger"] == "start"
    assert body["last_duration_s"] >= 0
    assert body["consecutive_failures"] == 0
    assert parse(body["next_scheduled_at"]) == parse(body["last_started_at"]).replace(
        minute=15, second=20
    )
    assert body["plan"]["intervals"] > 0
    assert body["running"] is False


async def test_replan_is_accepted_and_debounced(services: FakeServices) -> None:
    _, planning = with_planner(services)
    await planning.tick()
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/plan/replan")
        again = await client.post("/api/v1/plan/replan")
        status = (await client.get("/api/v1/plan/status")).json()
    assert r.status_code == 202
    assert r.json()["accepted"] is True
    assert r.json()["earliest_start_at"] == again.json()["earliest_start_at"]  # merged
    assert status["pending"] == ["manual"]
    assert ("replan", None) in services.calls


async def test_replan_without_a_planner_is_a_catalogue_error(services: FakeServices) -> None:
    async def broken() -> object:
        raise RuntimeError("not built")

    services.replan = broken  # type: ignore[method-assign,assignment]
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/plan/replan")
    assert r.status_code == 409
    assert r.json()["detail"] == "api.error.planning_unavailable"


async def test_plan_and_status_without_a_planner_are_catalogue_errors(
    services: FakeServices,
) -> None:
    """Review m8: like replan, not a 500."""

    async def broken(*_a: object) -> object:
        raise RuntimeError("not built")

    services.plan_view = broken  # type: ignore[method-assign,assignment]
    services.plan_status = broken  # type: ignore[method-assign,assignment]
    async with client_for(create_app(services)) as client:
        plan = await client.get("/api/v1/plan")
        status = await client.get("/api/v1/plan/status")
    for r in (plan, status):
        assert r.status_code == 409
        assert r.json()["detail"] == "api.error.planning_unavailable"


async def test_the_live_feed_announces_a_new_plan_without_the_intervals(
    services: FakeServices,
) -> None:
    _, planning = with_planner(services)
    app = create_app(services, live_min_interval_s=0.05, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        assert (await recv(ws))["type"] == "snapshot"
        await planning.tick()
        msg = await recv(ws)
        while msg["type"] != "plan":
            msg = await recv(ws)
    summary = msg["plan"]
    assert summary["source"] in ("solver", "heuristic")
    assert summary["intervals"] > 0
    assert summary["in_use"] is True
    assert "plan" not in summary  # no interval data in the feed
    assert "reasons" in summary
