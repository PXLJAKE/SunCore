# SPDX-License-Identifier: Apache-2.0
"""``planner_in_shadow``: the planner feeds the real controller; nothing is written (WP9).

The scenario runs the whole chain in simulated time: the simulated house, the
``PlanningService`` (real inputs, real HiGHS planner in its daemon thread, a plan every quarter
hour), the control stack with the real controller and gate, every device in shadow mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from suncore.app.planning import LiveReading, PlanningService, PlanningSettings, PlanningSnapshot
from suncore.core.control import BatteryBand, DeviceBand, Plan, PlanInterval
from suncore.core.decisions import BatteryAction, DecisionKind, OperatingMode
from suncore.sim.planner_scenarios import situation, situation_inputs
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import load_scenario
from tests.safety.helpers import make_intent
from tests.sim.helpers import catalog

pytestmark = pytest.mark.simulator

PLAN_EVERY_STEPS = 30  # 15 minutes at 30 s per step


class Observed:
    """What the test saw while the scenario ran."""

    def __init__(self) -> None:
        self.plans: list[Plan] = []
        self.followed_ticks = 0
        self.priority_ticks = 0
        self.battery_first_violations: list[str] = []
        self.battery_decision: DecisionKind | None = None
        self.battery_reasons: list[str] = []
        self.run_ticks = 0


async def play() -> tuple[RunResult, ScenarioRunner, PlanningService, Observed]:
    scenario = load_scenario("planner_in_shadow")
    runner = ScenarioRunner(scenario, catalog(), global_mode=OperatingMode.SHADOW)
    observed = Observed()

    def live() -> LiveReading:
        last = runner._steps[-1] if runner._steps else None
        return LiveReading(soc_pct=None if last is None else last.soc_pct)

    async def collect(now, previous):  # type: ignore[no-untyped-def]
        soc = live().soc_pct or 25.0
        inputs = situation_inputs(replace(situation("sunny_day"), now=now, soc_pct=soc))
        return PlanningSnapshot(replace(inputs, previous=previous))

    planning = PlanningService(collect, live, clock=runner.vt.clock, settings=PlanningSettings())
    planning.add_listener(lambda: observed.plans.append(planning.plan))  # type: ignore[arg-type]
    runner.plan_provider = planning.active_plan
    await runner.start()
    for index in range(scenario.steps):
        if index % PLAN_EVERY_STEPS == 0:
            await planning.tick()  # the daemon thread solves; simulated time stands still
        await runner.step(index)
        stack = runner.stack
        assert stack is not None
        last = stack.last
        if last is None:
            continue
        observed.run_ticks += 1
        plan = stack.current_plan(last.ts)
        band = plan.battery_band_at(last.ts)
        if plan.is_default:
            continue
        observed.followed_ticks += 1
        priority = band.priority_until_soc_pct
        if priority is not None and plan.interval_at(last.ts) is not None:
            observed.priority_ticks += 1
            soc = last.site.soc_pct
            if (soc is None or soc < priority) and last.site.charging_available:
                observed.battery_first_violations.append(f"{last.ts}: soc {soc} < {priority}")
    # A plan is present: the gate still rejects battery limits (BatteryAction, until WP10).
    assert runner.stack is not None
    decision = await runner.stack.gate.submit(
        make_intent(
            BatteryAction(grid_charge_w=3000, min_soc_pct=20),
            asset_id="rod",
            ts=runner.vt.now,
        )
    )
    observed.battery_decision = decision.kind
    observed.battery_reasons = [r.key for r in decision.reasons]
    result = await runner.finish()
    await runner.close()
    return result, runner, planning, observed


@pytest.fixture(scope="module")
def played() -> tuple[RunResult, ScenarioRunner, PlanningService, Observed]:
    return asyncio.run(play())


def test_a_plan_is_computed_every_quarter_hour_and_followed(
    played: tuple[RunResult, ScenarioRunner, PlanningService, Observed],
) -> None:
    result, _, planning, observed = played
    assert len(observed.plans) >= 20  # 6 h of quarter hours (the first run is a "start")
    assert {p.source for p in observed.plans} <= {"solver", "heuristic"}
    assert observed.followed_ticks > 0.9 * observed.run_ticks
    assert planning.status().consecutive_failures == 0
    assert result.scenario.name == "planner_in_shadow"


def test_the_controller_keeps_the_battery_first_below_the_plans_priority(
    played: tuple[RunResult, ScenarioRunner, PlanningService, Observed],
) -> None:
    _, _, _, observed = played
    assert observed.priority_ticks > 0, "the plan must carry a morning priority for this scenario"
    assert observed.battery_first_violations == []


def test_in_shadow_mode_nothing_is_written(
    played: tuple[RunResult, ScenarioRunner, PlanningService, Observed],
) -> None:
    result, _, _, _ = played
    assert result.of_kind(DecisionKind.ACCEPTED) == []
    for device_id in result.configs:
        assert result.transitions(device_id) == []
        assert not any(result.devices[device_id].on)
    assert result.of_kind(DecisionKind.SHADOW), "the controller followed the plan: 'would have'"


def test_the_gate_rejects_battery_limits_with_a_plan_present(
    played: tuple[RunResult, ScenarioRunner, PlanningService, Observed],
) -> None:
    _, _, planning, observed = played
    assert not planning.active_plan(planning.plan.created_at).is_default  # type: ignore[union-attr]
    assert observed.battery_decision is DecisionKind.REJECTED
    assert observed.battery_reasons == ["safety.battery_not_supported"]


def test_no_decision_of_the_run_carries_a_battery_action(
    played: tuple[RunResult, ScenarioRunner, PlanningService, Observed],
) -> None:
    """The controller never proposes battery limits (control-safety review m4); the only
    battery intent is the one this test submits to the gate itself (asset ``rod``)."""
    result, _, _, _ = played
    controller_made = [d for d in result.decisions if isinstance(d.intent.action, BatteryAction)]
    assert all(d.kind is DecisionKind.REJECTED for d in controller_made)
    assert all(d.intent.asset_id == "rod" for d in controller_made)
    assert len(controller_made) <= 1


async def _blocked_run() -> RunResult:
    scenario = load_scenario("planner_in_shadow")
    runner = ScenarioRunner(scenario, catalog(), global_mode=OperatingMode.SHADOW)
    start = runner.vt.now
    half = start + timedelta(hours=3)
    consumers_first = BatteryBand(charge_from_surplus=False)
    blocked = DeviceBand(allowed=False, max_power_w=0.0)
    plan = Plan(
        intervals=(
            PlanInterval(start=start - timedelta(hours=1), end=half, battery=consumers_first),
            PlanInterval(
                start=half,
                end=start + timedelta(hours=7),
                battery=consumers_first,
                devices={"rod": blocked, "pool": blocked},
            ),
        )
    )
    runner.plan_provider = lambda _now: plan
    await runner.start()
    for index in range(scenario.steps):
        await runner.step(index)
    result = await runner.finish()
    await runner.close()
    return result


def test_a_blocking_device_band_takes_effect_in_shadow() -> None:
    """``DeviceBand(allowed=False)``: the 'would have' level drops with ``plan_blocked``."""
    result = asyncio.run(_blocked_run())
    shadow = result.of_kind(DecisionKind.SHADOW)
    assert shadow
    blocked = [d for d in shadow if "controller.plan_blocked" in [r.key for r in d.intent.reasons]]
    assert blocked
    first_blocked = min(d.ts for d in blocked)
    # After the block no shadow decision raises a device again.
    assert all(
        "controller.plan_blocked" in [r.key for r in d.intent.reasons]
        for d in shadow
        if d.ts >= first_blocked
    )
    assert result.of_kind(DecisionKind.ACCEPTED) == []
