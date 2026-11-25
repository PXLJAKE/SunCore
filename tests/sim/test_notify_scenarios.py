# SPDX-License-Identifier: Apache-2.0
"""Closed loop for the notification wiring of PR 6-O: the grid monitor of a running control
stack feeds the production mapping (``grid_flags``) into the rule engine, and the planner's
real EV plans feed the "target at risk" rule.

Judged by the messages a user would get: one start and one end per island or §14a event, nothing
for an unreadable signal, nothing for a plan that meets the target, one message for a target
that cannot be met.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest

from suncore.app.notifications import RuntimeObservationSource, grid_flags
from suncore.core.decisions import OperatingMode
from suncore.core.flex import EvMode, EvState, EvTarget
from suncore.notify import Category, EvTargetObs, NotifyEvent, Observation, RuleEngine
from suncore.planner.run import run
from suncore.sim.planner_flex import flex_situation
from suncore.sim.planner_scenarios import situation_inputs
from suncore.sim.runner import ScenarioRunner
from suncore.sim.scenario import load_scenario
from tests.sim.helpers import catalog

pytestmark = pytest.mark.simulator


async def grid_events(name: str) -> tuple[list[tuple[int, NotifyEvent]], list[tuple[int, str]]]:
    """Play ``name`` and evaluate the rules after every step; returns the events with their
    step offset (s) and the dismissed keys."""
    scenario = load_scenario(name)
    runner = ScenarioRunner(scenario, catalog(), global_mode=OperatingMode.AUTOMATIC)
    engine = RuleEngine()
    events: list[tuple[int, NotifyEvent]] = []
    resolved: list[tuple[int, str]] = []
    try:
        await runner.start()
        for index in range(scenario.steps):
            await runner.step(index)
            assert runner.stack is not None
            island, para14a = grid_flags(runner.stack.grid_monitor)
            offset = index * scenario.step_s
            evaluation = engine.evaluate(
                Observation(
                    now=scenario.start + timedelta(seconds=offset),
                    emergency_stop=False,
                    global_mode="automatic",
                    island=island,
                    para14a=para14a,
                )
            )
            events += [(offset, e) for e in evaluation.events]
            resolved += [(offset, key) for key in evaluation.resolved]
        await runner.finish()
    finally:
        await runner.close()
    return events, resolved


async def test_an_island_gives_one_start_and_one_end_and_an_unreadable_backup_gives_none() -> None:
    import asyncio  # noqa: PLC0415

    events, resolved = await asyncio.wait_for(grid_events("grid_outage_island"), 600)
    assert [(t, e.key) for t, e in events] == [
        (events[0][0], "island:1"),
        (events[1][0], "island_end:1"),
    ]
    start, end = events[0][0], events[1][0]
    assert 1800 <= start <= 1800 + 60  # the outage begins at 1800 s
    assert 5400 + 120 <= end <= 5400 + 400  # the end waits until the grid stayed back
    assert all(e.category is Category.SAFETY for _, e in events)
    assert [key for _, key in resolved] == ["island:1"]
    # the backup entity is unavailable from 9000 s to 9600 s: no message, neither way
    assert not [t for t, _ in events if 9000 <= t < 10800]


async def test_para14a_gives_one_start_and_one_end_through_flicker_and_an_unavailable_signal() -> (
    None
):
    import asyncio  # noqa: PLC0415

    events, _ = await asyncio.wait_for(grid_events("para14a_dimming"), 600)
    assert [e.key for _, e in events] == ["para14a:1", "para14a_end:1"]
    assert 1800 <= events[0][0] <= 1800 + 60
    # the signal is unavailable from 7200 s to 7800 s: that is no end and no new start
    assert not [t for t, _ in events if 7200 <= t < 7800]


# --- EV target at risk, with the real planner -------------------------------------------------


def plan_shortfall(
    by_hours_after_arrival: float | None,
) -> tuple[float | None, float | None] | None:
    """Plan the late-car situation (arrives 23:00 with 30 %, 80 % wanted) and read the plan's
    shortfall the way the application does; ``None`` = the plan meets the target."""
    situation = flex_situation("ev_arrives_late")
    if by_hours_after_arrival is not None:
        assert situation.flex is not None
        arrival = situation.flex.ev_live["wallbox"].arrival
        assert arrival is not None
        state = EvState(
            device_id="wallbox",
            mode=EvMode.TARGET,
            target=EvTarget(soc_pct=80.0, by=arrival + timedelta(hours=by_hours_after_arrival)),
            updated_at=situation.now,
        )
        situation = replace(situation, flex=replace(situation.flex, ev_states={"wallbox": state}))
    result = run(situation_inputs(situation))
    return RuntimeObservationSource._shortfall(result.plan.intervals, "wallbox")


def replay(
    shortfall: tuple[float | None, float | None] | None,
    by_offset: timedelta,
    *,
    minutes: int = 90,
    on_step: Callable[[int], tuple[float | None, float | None] | None] | None = None,
) -> list[NotifyEvent]:
    """Feed the rule engine the same plan result every replan (5 min) for ``minutes``."""
    situation = flex_situation("ev_arrives_late")
    engine = RuleEngine()
    out: list[NotifyEvent] = []
    for step in range(minutes // 5):
        now = situation.now + timedelta(minutes=5 * step)
        current = shortfall if on_step is None else on_step(step)
        need, planned = (None, None) if current is None else current
        target = EvTargetObs(
            "wallbox",
            "Wallbox",
            situation.now + by_offset,
            current is not None,
            need,
            planned,
        )
        out += engine.evaluate(
            Observation(now=now, emergency_stop=False, global_mode="automatic", ev_targets=[target])
        ).events
    return out


def test_the_planned_late_car_meets_its_target_and_nobody_is_alarmed() -> None:
    assert plan_shortfall(None) is None
    assert replay(None, timedelta(hours=13)) == []


def test_an_impossible_target_is_announced_once_with_the_planned_energy() -> None:
    shortfall = plan_shortfall(0.5)  # 30 % to 80 % within half an hour of arrival
    assert shortfall is not None, "the planner reports the missed target"
    events = replay(shortfall, timedelta(hours=5, minutes=30))
    assert [e.category for e in events] == [Category.EV_TARGET]
    [event] = events
    assert event.category is Category.EV_TARGET
    assert event.reason.key in {"notify.ev_target.at_risk", "notify.ev_target.at_risk_short"}
    assert event.reason.args["device"] == "Wallbox"
    # a longer replay (more replans with the same answer) stays at one message
    longer = replay(shortfall, timedelta(hours=5, minutes=30), minutes=240)
    assert len([e for e in longer if e.category is Category.EV_TARGET]) == 1


def test_a_shortfall_that_a_replan_removes_within_the_hold_time_is_never_announced() -> None:
    shortfall = plan_shortfall(0.5)
    assert shortfall is not None
    assert (
        replay(
            shortfall,
            timedelta(hours=5),
            on_step=lambda step: shortfall if step % 3 == 0 else None,  # flickers every replan
        )
        == []
    )
