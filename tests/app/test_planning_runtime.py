# SPDX-License-Identifier: Apache-2.0
"""The planner in the runtime: demo plan, hand-over to the stack, events, stop order (WP9)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from suncore.api import create_app
from suncore.app.runtime import Runtime
from suncore.core.config import BatteryTarget, GoalsConfig
from suncore.core.control import Plan
from suncore.core.controlstate import OverrideCommand
from suncore.planner.run import PlannerRun, run
from tests.api.conftest import client_for
from tests.app.test_tariff_runtime import start_runtime, until


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    started = await start_runtime(tmp_path, "summer_day")
    try:
        yield started
    finally:
        await started.stop()


async def first_plan(runtime: Runtime) -> Plan:
    assert runtime.planning is not None
    planning = runtime.planning
    await until(lambda: planning.plan is not None, timeout_s=30.0)
    assert planning.plan is not None
    return planning.plan


async def test_the_demo_runs_the_planner_on_its_prices_and_forecasts(runtime: Runtime) -> None:
    plan = await first_plan(runtime)
    assert plan.source in ("solver", "heuristic")
    assert plan.intervals
    assert plan.assumptions["price_mode"] == "market"
    assert plan.assumptions["price_source"] == "tibber.price"
    assert plan.assumptions["pv_missing_slots"] < 96  # the forecast covers the first day or two
    async with client_for(create_app(runtime)) as client:
        body = (await client.get("/api/v1/plan")).json()
        status = (await client.get("/api/v1/plan/status")).json()
    assert body["plan"]["source"] == plan.source
    assert body["in_use"] is True
    assert body["plan"]["intervals"][0]["prices"]["import_price"] > 0
    assert status["last_outcome"] == "ok"


async def test_the_control_stack_reads_the_plan_through_the_provider(runtime: Runtime) -> None:
    plan = await first_plan(runtime)
    stack = runtime.stack
    assert stack is not None
    now = runtime.clock()
    assert stack.current_plan(now) is runtime.planning.active_plan(now)  # type: ignore[union-attr]
    assert not stack.current_plan(now).is_default
    assert runtime.planning.plan is plan or runtime.planning.plan.created_at >= plan.created_at  # type: ignore[union-attr]
    # A rebuilt stack (configuration change) keeps following the same planner.
    await runtime._rebuild_stack()
    assert runtime.stack is not stack
    assert not runtime.stack.current_plan(runtime.clock()).is_default  # type: ignore[union-attr]


async def test_the_demo_plan_is_deterministic(runtime: Runtime) -> None:
    await first_plan(runtime)
    now = runtime.clock()
    plans = []
    for _ in range(2):
        snapshot = await runtime._collect_planning(now, None)
        assert snapshot.inputs is not None
        plans.append(run(snapshot.inputs, budget_s=8.0).plan)
    assert plans[0].intervals == plans[1].intervals
    assert plans[0].source == plans[1].source


async def test_events_ask_for_a_plan(runtime: Runtime) -> None:
    await first_plan(runtime)
    planning = runtime.planning
    assert planning is not None
    seen: list[str] = []
    planning.request = seen.append  # type: ignore[method-assign,assignment]
    # prices, forecasts
    assert runtime.tariff is not None
    for listener in list(runtime.tariff._listeners):
        listener()
    assert runtime.pv_service is not None
    runtime.pv_service._notify()
    assert runtime.load_service is not None
    runtime.load_service._notify()
    assert seen == ["prices", "forecast", "forecast"]
    # configuration
    seen.clear()
    await runtime.put_section(
        "goals", GoalsConfig(battery_target=BatteryTarget(soc_pct=40.0, time="06:00"))
    )
    assert "config" in seen
    # operation changes do not matter for the plan
    seen.clear()
    await runtime.put_section("operation", runtime.modes.operation)
    assert seen == []
    # user override
    await runtime.override_device("pool_pump", OverrideCommand(action="pause"))
    assert "override" in seen


async def test_a_replan_through_the_runtime_is_queued(runtime: Runtime) -> None:
    await first_plan(runtime)
    ack = await runtime.replan()
    assert ack.accepted
    status = await runtime.plan_status()
    assert "manual" in status.pending or status.last_trigger == "manual"


async def test_a_hanging_planner_does_not_delay_the_release_on_stop(tmp_path: Path) -> None:
    runtime = await start_runtime(tmp_path, "summer_day")
    await first_plan(runtime)
    planning = runtime.planning
    assert planning is not None
    release = threading.Event()
    started = threading.Event()

    def hang(inputs: Any, budget: float, gap: float, previous: Plan | None) -> PlannerRun:
        started.set()
        release.wait(30)
        return run(inputs, budget_s=budget, mip_gap=gap, warm_start=previous)

    planning._solve = hang  # type: ignore[assignment]
    planning._settings = type(planning.settings)(min_event_gap_s=0.0, run_timeout_margin_s=60.0)
    planning.request("manual")
    await until(started.is_set, timeout_s=30.0)
    assert planning.status().running
    stack = runtime.stack
    assert stack is not None
    order: list[str] = []
    real_shutdown = stack.shutdown

    async def shutdown() -> None:
        order.append("release")
        await real_shutdown()

    stack.shutdown = shutdown  # type: ignore[method-assign]
    began = time.monotonic()
    await asyncio.wait_for(runtime.stop(), timeout=20.0)
    assert time.monotonic() - began < 15.0
    assert order == ["release"]
    task = runtime._planning_task
    assert task is None
    release.set()


async def test_stop_awaits_the_planning_task_after_the_devices_are_released(
    tmp_path: Path,
) -> None:
    runtime = await start_runtime(tmp_path, "summer_day")
    await first_plan(runtime)
    stack = runtime.stack
    task = runtime._planning_task
    assert stack is not None
    assert task is not None
    observed: list[bool] = []
    real_shutdown = stack.shutdown

    async def shutdown() -> None:
        await real_shutdown()
        # Cancelled early (as the other storage tasks) but only awaited once the release ran.
        observed.append(task.cancelling() > 0 or task.cancelled() or task.done())

    stack.shutdown = shutdown  # type: ignore[method-assign]
    await runtime.stop()
    assert observed == [True]
    assert task.done()
