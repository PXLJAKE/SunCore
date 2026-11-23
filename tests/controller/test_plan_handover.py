# SPDX-License-Identifier: Apache-2.0
"""The plan hand-over (Phase 4 WP9): a plan can only restrict the devices and move the surplus.

* ``priority_until_soc_pct`` is judged against the live SoC of every tick;
* device bands never force a device on and never raise a level above what the plan-less
  controller reaches (property test);
* a plan interval that does not cover ``now`` leaves the Phase 3 goal rules in charge;
* the controller never proposes a battery action, whatever the battery band says (the gate
  rejects them anyway until WP10: ``tests/safety/test_gate.py``, the scenario test in
  ``tests/sim/test_planner_shadow.py``).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.controller import Controller, ControllerInputs
from suncore.core.config import BatteryTarget, GoalsConfig
from suncore.core.control import (
    BandPreference,
    BatteryBand,
    DeviceBand,
    DeviceState,
    Plan,
    PlanInterval,
    PowerEstimate,
)
from suncore.core.decisions import BatteryAction, Decision, DecisionKind, Intent, OperatingMode
from tests.safety.helpers import T0, flows, steps_device, switch_device

GOALS = GoalsConfig(climate_before_export=True)
HEATER = steps_device("heater")
EST = PowerEstimate(value_w=0.0, uncertainty_w=5.0)
STEP_W = (0.0, 1000.0, 3000.0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def plan_of(
    battery: BatteryBand | None = None,
    device: DeviceBand | None = None,
    *,
    start: datetime = T0,
    hours: float = 24.0,
) -> Plan:
    return Plan(
        intervals=(
            PlanInterval(
                start=start,
                end=start + timedelta(hours=hours),
                battery=battery or BatteryBand(),
                devices={} if device is None else {"heater": device},
            ),
        )
    )


def accepted(intent: Intent) -> Decision:
    return Decision(
        intent=intent, kind=DecisionKind.ACCEPTED, ts=intent.ts, mode=OperatingMode.AUTOMATIC
    )


def run_heater(
    plan: Plan, *, grid_w: float, soc_pct: float = 50.0, battery_w: float = 0.0, seconds: int = 240
) -> tuple[int, list[Intent]]:
    """Tick a step heater on a constant surplus; returns its final step and all intents."""
    controller = Controller([HEATER])
    step = 0
    sent: list[Intent] = []
    for k in range(seconds // 5):
        result = controller.step(
            at(5 * k),
            ControllerInputs(
                flows=flows(grid_w=grid_w, battery_w=battery_w, soc_pct=soc_pct),
                states={"heater": DeviceState(step=step)},
                estimates={"heater": EST},
                goals=GOALS,
                plan=plan,
            ),
        )
        for intent in result.intents:
            controller.report(accepted(intent))
            step = intent.action.step  # type: ignore[union-attr]
            sent.append(intent)
    return step, sent


def test_priority_judges_the_live_soc_every_tick() -> None:
    """Battery first below the priority, then the surplus may go to the consumers."""
    band = BatteryBand(charge_from_surplus=False, priority_until_soc_pct=60.0)
    controller = Controller([HEATER])

    def charging(soc: float) -> bool:
        result = controller.step(
            at(0),
            ControllerInputs(
                flows=flows(grid_w=0.0, battery_w=3000.0, soc_pct=soc),
                states={"heater": DeviceState(step=0)},
                estimates={"heater": EST},
                goals=GOALS,
                plan=plan_of(band),
            ),
        )
        return result.site.charging_available

    assert not charging(40.0)
    assert not charging(59.9)
    assert charging(60.0)
    assert not charging(30.0)  # the SoC fell again (a deviation from the plan): battery first


def test_below_the_priority_the_heater_gets_nothing_from_the_battery_share() -> None:
    surplus_from_battery = {"grid_w": 0.0, "battery_w": 3500.0}
    band = BatteryBand(charge_from_surplus=False, priority_until_soc_pct=80.0)
    low, _ = run_heater(plan_of(band), soc_pct=50.0, **surplus_from_battery)
    high, _ = run_heater(plan_of(band), soc_pct=85.0, **surplus_from_battery)
    assert low == 0  # the charging power stays with the battery
    assert high >= 1


def test_a_plan_that_ran_out_leaves_the_goal_rules_in_charge() -> None:
    """Outside every interval the band is ``None``: the Phase 3 goal decides, not the empty band."""
    goals = GoalsConfig(battery_target=BatteryTarget(soc_pct=80.0, time="06:00"))
    ran_out = plan_of(
        BatteryBand(charge_from_surplus=False), start=T0 - timedelta(hours=2), hours=1
    )
    controller = Controller([HEATER])
    result = controller.step(
        at(0),
        ControllerInputs(
            flows=flows(grid_w=0.0, battery_w=3000.0, soc_pct=50.0),
            states={"heater": DeviceState(step=0)},
            estimates={"heater": EST},
            goals=goals,
            plan=ran_out,
        ),
    )
    assert not result.site.charging_available  # the target (80 %) blocks it, as in Phase 3
    assert result.site.target_soc_pct == 80.0


def test_the_controller_never_proposes_a_battery_action() -> None:
    band = BatteryBand(
        min_power_w=0.0,
        max_power_w=3000.0,
        min_soc_pct=12.0,
        max_soc_pct=95.0,
        grid_charge_max_w=2500.0,
        discharge_lock=True,
        priority_until_soc_pct=70.0,
        charge_from_surplus=False,
    )
    for grid_w in (-4000.0, 0.0, 2000.0):
        for soc in (10.0, 50.0, 99.0):
            _, sent = run_heater(plan_of(band), grid_w=grid_w, soc_pct=soc, battery_w=1000.0)
            assert not [i for i in sent if isinstance(i.action, BatteryAction)]


@dataclass(frozen=True)
class Case:
    allowed: bool
    max_power_w: float | None
    preference: BandPreference
    grid_w: float
    soc: float
    from_surplus: bool
    priority: float | None


CASES = st.builds(
    Case,
    allowed=st.booleans(),
    max_power_w=st.one_of(st.none(), st.floats(min_value=0.0, max_value=6000.0)),
    preference=st.sampled_from(list(BandPreference)),
    grid_w=st.floats(min_value=-6000.0, max_value=3000.0),
    soc=st.floats(min_value=0.0, max_value=100.0),
    from_surplus=st.booleans(),
    priority=st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0)),
)


@settings(max_examples=60, deadline=None)
@given(case=CASES)
def test_a_plan_never_raises_a_device_above_the_plan_less_controller(case: Case) -> None:
    """Device bands only restrict; ``prefer_on`` forces nothing. The battery band only moves the
    surplus share; the comparison below isolates the device band's effect."""
    device = DeviceBand(
        allowed=case.allowed, max_power_w=case.max_power_w, preference=case.preference
    )
    battery = BatteryBand(
        charge_from_surplus=case.from_surplus, priority_until_soc_pct=case.priority
    )
    world = {"grid_w": case.grid_w, "soc_pct": case.soc, "battery_w": 1500.0}
    free, _ = run_heater(plan_of(battery), **world)
    restricted, sent = run_heater(plan_of(battery, device), **world)
    assert restricted <= free
    cap = 2
    if not case.allowed:
        cap = 0
    elif case.max_power_w is not None:
        cap = max((k for k, w in enumerate(STEP_W) if w <= case.max_power_w), default=0)
    assert restricted <= cap
    # Every command on the way stayed inside the cap too (no overshoot that is taken back).
    assert all(i.action.step <= cap for i in sent)  # type: ignore[union-attr]


@settings(max_examples=40, deadline=None)
@given(
    battery_w=st.floats(min_value=0.0, max_value=4000.0),
    soc=st.floats(min_value=0.0, max_value=100.0),
    priority=st.floats(min_value=0.0, max_value=100.0),
    from_surplus=st.booleans(),
)
def test_below_its_priority_a_plan_gives_the_heater_no_more_than_no_plan(
    battery_w: float, soc: float, priority: float, from_surplus: bool
) -> None:
    band = BatteryBand(charge_from_surplus=from_surplus, priority_until_soc_pct=priority)
    planned, _ = run_heater(plan_of(band), grid_w=0.0, soc_pct=soc, battery_w=battery_w)
    plain, _ = run_heater(Plan.default(), grid_w=0.0, soc_pct=soc, battery_w=battery_w)
    if soc < priority:
        assert planned <= plain


def test_a_default_plan_is_the_phase_3_controller() -> None:
    no_plan, _ = run_heater(Plan.default(), grid_w=-6000.0)
    open_plan, _ = run_heater(plan_of(BatteryBand(), DeviceBand()), grid_w=-6000.0)
    assert no_plan == open_plan == 2


# --- flapping plan bands (control-safety review m1) ----------------------------------------


def _flapping(
    *, min_run_s: int = 0, min_pause_s: int = 0, max_switches: int | None = None, hours: int = 3
) -> list[tuple[float, bool]]:
    """A switch under steady surplus while the plan alternates allowed/blocked every 15 min."""
    device = switch_device(
        "pool", min_run_s=min_run_s, min_pause_s=min_pause_s, max_switches_per_hour=max_switches
    )
    intervals = []
    for k in range(hours * 4):
        start = T0 + timedelta(minutes=15 * k)
        band = DeviceBand() if k % 2 == 0 else DeviceBand(allowed=False, max_power_w=0.0)
        intervals.append(
            PlanInterval(start=start, end=start + timedelta(minutes=15), devices={"pool": band})
        )
    plan = Plan(intervals=tuple(intervals))
    controller = Controller([device])
    on = False
    switches: list[tuple[float, bool]] = []
    for k in range(hours * 3600 // 5):
        now = at(5 * k)
        result = controller.step(
            now,
            ControllerInputs(
                flows=flows(grid_w=-4000.0 + (1000.0 if on else 0.0), battery_w=0.0, soc_pct=50.0),
                states={"pool": DeviceState(on=on)},
                estimates={"pool": PowerEstimate(value_w=1000.0 if on else 0.0, uncertainty_w=5.0)},
                goals=GOALS,
                plan=plan,
            ),
        )
        for intent in result.intents:
            controller.report(accepted(intent))
            on = bool(getattr(intent.action, "on", on))
            switches.append(((now - T0).total_seconds(), on))
    return switches


def _per_hour(switches: list[tuple[float, bool]]) -> list[int]:
    hours = int(max((s for s, _ in switches), default=0) // 3600) + 1
    return [sum(1 for s, _ in switches if h * 3600 <= s < (h + 1) * 3600) for h in range(hours)]


def test_flapping_bands_respect_the_switch_limit() -> None:
    default_limit = 6  # ControlSettings.default_max_switches_per_hour
    assert all(n <= default_limit for n in _per_hour(_flapping()))
    limited = _flapping(max_switches=2)
    assert limited
    assert all(n <= 2 for n in _per_hour(limited))


def test_flapping_bands_respect_the_minimum_run_and_pause() -> None:
    switches = _flapping(min_run_s=1200, min_pause_s=600)
    assert switches
    for (t0, on0), (t1, _) in itertools.pairwise(switches):
        assert t1 - t0 >= (1200 if on0 else 600)
