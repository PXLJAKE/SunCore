# SPDX-License-Identifier: Apache-2.0
"""Property tests of the flexible loads and grid limits (Phase 6 WP8, plan §4 "Planner").

Random Phase 4 situations (``helpers.situations``) gain a random EV (mode, SoC, arrival,
target), a random program job, a random heat pump and random grid limits (fuse, peak shaving,
§14a). Both the HiGHS model and the heuristic must produce schedules that pass the independent
feasibility check (energy balance, SoC corridor, every flexible-load rule, import, export and
§14a limits, no battery into the EV), and the solver's plan is never costlier than the
heuristic under the one cost function. ``SUNCORE_PLANNER_EXAMPLES`` raises the number of
examples for a local stress run.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from suncore.core.flex import EvMode, EvState, EvTarget
from suncore.core.grid import Para14aBudget, Para14aStatus, PeakShaving
from suncore.core.series import PLANNING_STEP, floor_to_step
from suncore.planner.flex import EvLive, FlexRequest, GridRequest, PlannerJob
from suncore.planner.heuristic import heuristic_schedule
from suncore.planner.model import build_problem, economics, objective, violations
from suncore.planner.run import run
from suncore.sim.planner_flex import flex_situation, heat_pump, job, outdoor, program, wallbox
from suncore.sim.planner_scenarios import Situation, situation_inputs
from tests.planner.helpers import situations

PROPS = settings(
    max_examples=int(os.environ.get("SUNCORE_PLANNER_EXAMPLES", "40")) // 2 or 1,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
TOL_W = 1.0


@st.composite
def grid_requests(draw: st.DrawFn, export_limit_w: float | None) -> GridRequest | None:
    """Random grid limits: fuse, peak shaving with a capacity price, §14a (or none)."""
    if not draw(st.booleans()):
        return None
    fuse = draw(st.floats(6_000, 30_000))
    peak = draw(st.booleans())
    status = draw(st.sampled_from([None, Para14aStatus.ACTIVE, Para14aStatus.UNKNOWN]))
    return GridRequest(
        fuse_limit_w=fuse,
        peak=PeakShaving(
            enabled=peak,
            peak_floor_w=min(fuse, 2_500.0),
            capacity_price_per_kw_month=draw(st.floats(0, 15)),
        ),
        month_peak_w=draw(st.one_of(st.none(), st.floats(0, 12_000))),
        static_export_limit_w=export_limit_w,
        para14a=Para14aBudget(Para14aStatus.INACTIVE)
        if status is None
        else Para14aBudget(status, 4_200.0 if status is Para14aStatus.ACTIVE else None),
        para14a_hold=timedelta(minutes=draw(st.sampled_from([15, 60]))),
    )


@st.composite
def flex_situations(draw: st.DrawFn) -> Situation:
    base = draw(situations())
    now = base.now
    winter = not base.summer
    devices = list(base.devices)
    ev_states: dict[str, EvState] = {}
    ev_live: dict[str, EvLive] = {}
    jobs = []
    temperatures: dict[str, float] = {}
    if draw(st.booleans()):
        para = draw(st.booleans())
        devices.append(wallbox(para14a=para))
        mode = draw(st.sampled_from(list(EvMode)))
        target = None
        if mode is EvMode.TARGET:
            by = now + timedelta(hours=draw(st.integers(1, 40)))
            target = draw(
                st.sampled_from(
                    [
                        EvTarget(soc_pct=draw(st.floats(30, 100)), by=by),
                        EvTarget(energy_wh=draw(st.floats(1_000, 40_000)), by=by),
                    ]
                )
            )
        ev_states["wallbox"] = EvState(
            device_id="wallbox", mode=mode, target=target, updated_at=now
        )
        connected = draw(st.sampled_from([True, False, None]))
        arrival = (
            now + timedelta(hours=draw(st.integers(0, 20)))
            if connected is False and draw(st.booleans())
            else None
        )
        ev_live["wallbox"] = EvLive(
            soc_pct=draw(st.one_of(st.none(), st.floats(0, 100))),
            connected=connected,
            arrival=arrival,
        )
    if draw(st.booleans()):
        profile = draw(st.lists(st.floats(0, 800), min_size=1, max_size=12))
        devices.append(program("washer", profile))
        jobs.append(job("washer", now, now + timedelta(hours=draw(st.integers(1, 30)))))
    hp = draw(st.booleans())
    if hp:
        devices.append(
            heat_pump(winter=winter, grid_preheat=draw(st.booleans()), para14a=draw(st.booleans()))
        )
        band = (20.0, 23.0) if winter else (45.0, 55.0)
        temperatures["heat_pump"] = draw(st.floats(band[0] - 1.0, band[1] + 0.5))
    grid = draw(grid_requests(base.export_limit_w))
    flex = FlexRequest(
        ev_states=ev_states,
        ev_live=ev_live,
        jobs=tuple(jobs),
        temperatures_c=temperatures,
        outdoor_c=outdoor(now, winter=winter) if hp else None,
    )
    return replace(base, devices=tuple(devices), flex=flex, grid_limits=grid)


@PROPS
@given(flex_situations())
def test_flexible_plans_are_feasible_and_never_worse_than_the_heuristic(
    situation: Situation,
) -> None:
    inputs = situation_inputs(situation)
    event(f"flexible loads: {len(inputs.flex_loads)}")
    heuristic = heuristic_schedule(inputs)
    assert violations(inputs, heuristic) == []
    result = run(inputs)
    assert violations(inputs, result.schedule) == []
    cost = objective(inputs, heuristic)
    assert result.objective <= cost + 1e-6 * max(1.0, abs(cost))
    for schedule in (result.schedule, heuristic):
        for load, flows in zip(inputs.flex_loads, schedule.flex, strict=True):
            if load.kind == "ev":
                # Never fed by the home battery (BRIEF §4.4, allow_discharge_to_ev absent).
                assert max(flows.battery_w, default=0.0) <= TOL_W
            if isinstance(load, PlannerJob):
                assert flows.start in load.candidates
                if load.deadline_met and not load.running:
                    assert flows.start is not None
                    end = inputs.starts[flows.start] + inputs.step * len(load.profile_w)
                    assert end <= load.deadline
    # The peak variable is at least every import of its month.
    if result.solver is not None and result.solver.x is not None and inputs.peak_price() > 0:
        _, layout = build_problem(inputs, economics(inputs))
        assert layout.flex is not None
        for t in range(inputs.n):
            peak = float(result.solver.x[layout.flex.pk(inputs.month_of(t))]) * 1000
            assert result.schedule.import_w(t) <= peak + TOL_W
    # Bands: grid power only where the plan permits it, never above the band's maximum.
    for interval in result.plan.intervals:
        for load in inputs.flex_loads:
            band = interval.devices[load.device_id]
            if band.grid_power_max_w is not None and band.max_power_w is not None:
                assert band.grid_power_max_w <= band.max_power_w + 1e-6


# --- the first slot is only what is left of it (7-K, docs/research/late-car-gap.md) ------------


@st.composite
def late_situations(draw: st.DrawFn) -> tuple[Situation, float]:
    """A random situation with a charging car, planned ``elapsed`` seconds into its quarter;
    optionally a program job armed at ``now`` (a start in slot 0 is a start now), a heat pump
    and grid limits (fuse, peak shaving, §14a)."""
    base = draw(situations())
    quarter = floor_to_step(base.now, PLANNING_STEP).astimezone(base.now.tzinfo)
    elapsed = draw(st.one_of(st.floats(0, 898.0), st.sampled_from([0.0, 450.0, 870.0, 898.0])))
    now = quarter + timedelta(seconds=elapsed)
    mode = draw(st.sampled_from([EvMode.TARGET, EvMode.NOW, EvMode.MIN_PV, EvMode.PV]))
    target = (
        EvTarget(
            soc_pct=draw(st.floats(40, 100)), by=now + timedelta(hours=draw(st.integers(1, 12)))
        )
        if mode is EvMode.TARGET
        else None
    )
    devices = [*base.devices, wallbox(para14a=draw(st.booleans()))]
    jobs = []
    if draw(st.booleans()):
        profile = draw(st.lists(st.floats(0, 2_000), min_size=1, max_size=8))
        devices.append(program("washer", profile))
        jobs.append(job("washer", now, now + timedelta(hours=draw(st.integers(1, 12)))))
    winter = not base.summer
    temperatures: dict[str, float] = {}
    hp = draw(st.booleans())
    if hp:
        devices.append(
            heat_pump(winter=winter, grid_preheat=draw(st.booleans()), para14a=draw(st.booleans()))
        )
        band = (20.0, 23.0) if winter else (45.0, 55.0)
        temperatures["heat_pump"] = draw(st.floats(band[0] - 1.0, band[1] + 0.5))
    flex = FlexRequest(
        ev_states={
            "wallbox": EvState(device_id="wallbox", mode=mode, target=target, updated_at=now)
        },
        ev_live={"wallbox": EvLive(soc_pct=draw(st.floats(0, 39)), connected=True)},
        jobs=tuple(jobs),
        temperatures_c=temperatures,
        outdoor_c=outdoor(now, winter=winter) if hp else None,
    )
    situation = replace(
        base,
        now=now,
        devices=tuple(devices),
        flex=flex,
        grid_limits=draw(grid_requests(base.export_limit_w)),
    )
    return situation, (quarter + PLANNING_STEP - now).total_seconds()


@PROPS
@given(late_situations())
def test_the_first_slot_books_no_more_energy_than_its_remaining_time_allows(
    case: tuple[Situation, float],
) -> None:
    """Energy booked in slot 0 <= max power x remaining hours, for the EV and the battery, in
    the LP and the heuristic alike; and the plan stays feasible under its own accounting."""
    situation, left_s = case
    inputs = situation_inputs(situation)
    event(f"grid limits: {inputs.grid is not None}")
    left_h = left_s / 3600.0
    assert inputs.hours[0] == pytest.approx(left_h, abs=1e-12)
    assert all(h == inputs.dt_h for h in inputs.hours[1:])
    heuristic = heuristic_schedule(inputs)
    result = run(inputs)
    battery = inputs.battery
    for schedule in (result.schedule, heuristic):
        assert violations(inputs, schedule) == []
        for load, flows in zip(inputs.flex_loads, schedule.flex, strict=True):
            booked = flows.power_w(0) * inputs.hours[0]
            assert booked <= load.max_w[0] * left_h + 1e-6
            event(f"{load.kind} start 0" if flows.start == 0 else load.kind)
            if isinstance(load, PlannerJob) and flows.start == 0:
                # started now: the whole run's energy is booked, none of it before now
                energy = sum(flows.power_w(t) * inputs.hours[t] for t in range(inputs.n))
                assert energy == pytest.approx(sum(load.profile_w) * inputs.dt_h, abs=0.5)
        if battery is not None:
            model = battery.model
            stored = schedule.soc_wh[0] - battery.soc_wh
            assert stored <= battery.max_charge_w * model.charge_efficiency * left_h + 1e-6
            assert -stored <= battery.max_discharge_w / model.discharge_efficiency * left_h + 1e-6
    cost = objective(inputs, heuristic)
    assert result.objective <= cost + 1e-6 * max(1.0, abs(cost))


def test_a_negligible_first_slot_still_solves_and_books_almost_nothing() -> None:
    base = flex_situation("ev_arrives_late")
    quarter = floor_to_step(base.now, PLANNING_STEP).astimezone(base.now.tzinfo)
    now = quarter + PLANNING_STEP - timedelta(microseconds=1)
    assert base.flex is not None
    flex = replace(base.flex, ev_live={"wallbox": EvLive(soc_pct=30.0, connected=True)})
    inputs = situation_inputs(replace(base, now=now, flex=flex))
    assert inputs.hours[0] == pytest.approx(1 / 3600)
    result = run(inputs)
    assert result.solver is not None
    assert result.solver.status in {"optimal", "time_limit"}
    assert violations(inputs, result.schedule) == []
    for load, flows in zip(inputs.flex_loads, result.schedule.flex, strict=True):
        assert flows.power_w(0) * inputs.hours[0] <= load.max_w[0] / 3600 + 1e-6
