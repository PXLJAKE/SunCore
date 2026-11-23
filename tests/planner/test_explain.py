# SPDX-License-Identifier: Apache-2.0
"""Band derivation details (PR 4-F review): hysteresis of ``tight`` against the previous plan."""

from __future__ import annotations

from dataclasses import replace

from suncore.core.control import DeviceBand, Plan, PlanInterval
from suncore.planner.explain import TIGHT_HOLD_PCT, TIGHT_PCT, slot_facts
from suncore.planner.model import economics
from suncore.planner.run import run
from suncore.sim.planner_scenarios import scenario_inputs


def test_a_slot_the_previous_plan_throttled_stays_tight_within_the_hold_margin() -> None:
    inputs = scenario_inputs("sunny_day")
    result = run(inputs)
    econ = economics(inputs)
    assert inputs.battery is not None
    k = next(i for i, g in enumerate(inputs.goals) if g.kind == "evening_target")
    slot = econ.goal_slots[k]
    assert slot is not None
    planned = inputs.battery.model.wh_to_soc(result.schedule.soc_wh[slot])
    # A target 2 % below the plan: not binding by the strict rule, binding with hysteresis.
    margin = (TIGHT_PCT + TIGHT_HOLD_PCT) / 2
    goals = list(inputs.goals)
    goals[k] = replace(goals[k], soc_pct=planned - margin)
    probe = replace(inputs, goals=tuple(goals))
    charging = [t for t in range(slot) if result.schedule.pv_charge_w[t] > 1.0]
    assert charging
    fresh = slot_facts(probe, economics(probe), result.schedule)
    assert not any(fresh[t].tight for t in charging)
    t = charging[0]
    start = inputs.starts[t]
    previous = Plan(
        intervals=(
            PlanInterval(
                start=start,
                end=start + inputs.step,
                devices={"ac_living": DeviceBand(allowed=False, max_power_w=0.0)},
            ),
        )
    )
    sticky = replace(probe, previous=previous)
    held = slot_facts(sticky, economics(sticky), result.schedule)
    assert held[t].tight
    assert not any(held[x].tight for x in charging if x != t)
