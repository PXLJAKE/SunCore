# SPDX-License-Identifier: Apache-2.0
"""Flexible loads and grid limits in the planner (Phase 6 WP8, plan §4 "Planner").

Scenario tests on ``suncore.sim.planner_flex`` (LP and heuristic, same constraints and cost
function) and unit tests of the builders in ``suncore.planner.flex``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.control import BandPreference
from suncore.core.devices import DeviceConfig
from suncore.core.flex import EvMode, EvState, EvTarget, JobState, ProgramJob
from suncore.core.grid import Para14aBudget, Para14aStatus, PeakShaving
from suncore.core.series import Grid, planning_grid
from suncore.core.thermal import LearnedThermal, ThermalModel
from suncore.planner import reasons as rk
from suncore.planner.flex import (
    EvLive,
    FlexBuild,
    FlexRequest,
    GridRequest,
    PlannerEv,
    PlannerJob,
    PlannerThermal,
    build_flex,
    build_grid,
    thermal_base_w,
)
from suncore.planner.flexmodel import ev_delivered_wh, thermal_temps
from suncore.planner.heuristic import heuristic_schedule
from suncore.planner.inputs import PLANNER_HORIZON, PlannerInputs, device_max_power_w
from suncore.planner.model import build_problem, economics, objective, violations
from suncore.planner.run import PlannerRun, run
from suncore.sim.planner_flex import (
    FLEX_SCENARIOS,
    flex_inputs,
    flex_situation,
    heat_pump,
    job,
    outdoor,
    program,
    wallbox,
)
from suncore.sim.planner_scenarios import TZ, reference_situation, situation_inputs

TOL = 1e-6
TOL_W = 1.0


def _local(inputs: PlannerInputs, t: int) -> datetime:
    return inputs.starts[t].astimezone(TZ)


def _keys(result: PlannerRun) -> set[str]:
    keys = {r.key for r in result.plan.reasons}
    for interval in result.plan.intervals:
        keys |= {r.key for r in interval.reasons}
    return keys


def _ev(inputs: PlannerInputs) -> tuple[int, PlannerEv]:
    for i, load in enumerate(inputs.flex_loads):
        if isinstance(load, PlannerEv):
            return i, load
    raise AssertionError("no EV")


def _thermal(inputs: PlannerInputs) -> tuple[int, PlannerThermal]:
    for i, load in enumerate(inputs.flex_loads):
        if isinstance(load, PlannerThermal):
            return i, load
    raise AssertionError("no thermal load")


@pytest.mark.parametrize("name", FLEX_SCENARIOS)
def test_scenarios_are_feasible_and_never_worse_than_the_heuristic(name: str) -> None:
    inputs = flex_inputs(name)
    result = run(inputs)
    heuristic = heuristic_schedule(inputs)
    assert result.source == "solver"
    assert result.solver is not None
    assert result.solver.status == "optimal"
    assert violations(inputs, result.schedule) == []
    assert violations(inputs, heuristic) == []
    assert result.objective <= objective(inputs, heuristic) + TOL * max(1.0, abs(result.objective))
    # Every interval explains itself; the flexible loads get bands.
    for interval in result.plan.intervals:
        assert interval.reasons
        for load in inputs.flex_loads:
            assert load.device_id in interval.devices


def test_ev_arrives_late_charges_in_the_cheap_night_and_meets_the_target() -> None:
    """BRIEF §4.14 "Auto kommt spät": arrives 23:00 with 30 %, 80 % by 07:00."""
    inputs = flex_inputs("ev_arrives_late")
    result = run(inputs)
    econ = economics(inputs)
    i, ev = _ev(inputs)
    assert econ.flex is not None
    flows = result.schedule.flex[i]
    assert ev.need_by_wh is not None
    assert ev_delivered_wh(ev, flows, econ.flex.surplus_p10_w, inputs.dt_h) >= ev.need_by_wh - 1
    for t in range(inputs.n):
        local = _local(inputs, t)
        band = result.plan.intervals[t].devices[ev.device_id]
        if flows.power_w(t) > TOL_W:
            assert local >= datetime(2026, 12, 8, 23, tzinfo=TZ)
            assert 1 <= local.hour < 5  # the cheap window only
            assert band.grid_power_max_w is not None
            assert band.grid_power_max_w <= (band.max_power_w or 0) + TOL
        else:
            assert band.grid_power_max_w is None
        if local < datetime(2026, 12, 8, 23, tzinfo=TZ):
            assert not band.allowed
    # Heuristic: the same cheap slots.
    heuristic = heuristic_schedule(inputs).flex[i]
    for t in range(inputs.n):
        if heuristic.power_w(t) > TOL_W:
            assert 1 <= _local(inputs, t).hour < 5


def test_ev_pv_mode_never_draws_from_the_grid_and_keeps_the_6a_floor() -> None:
    inputs = flex_inputs("ev_pv_only")
    result = run(inputs)
    i, ev = _ev(inputs)
    assert ev.mode is EvMode.PV
    for schedule in (result.schedule, heuristic_schedule(inputs)):
        flows = schedule.flex[i]
        assert max(flows.grid_w) <= TOL_W
        assert max(flows.battery_w) <= TOL_W
        for t in range(ev.binary_slots):
            p = flows.power_w(t)
            assert p <= TOL_W or p >= ev.min_w - TOL_W
    assert all(
        interval.devices[ev.device_id].grid_power_max_w is None
        for interval in result.plan.intervals
    )
    assert rk.EV_SURPLUS in _keys(result)


def test_program_starts_once_in_the_sun_and_finishes_by_its_deadline() -> None:
    inputs = flex_inputs("program_deadline")
    result = run(inputs)
    job_load = inputs.jobs[0]
    starts = [t for t, iv in enumerate(result.plan.intervals) if iv.devices["washer"].start]
    assert len(starts) == 1
    start = starts[0]
    end = inputs.starts[start] + inputs.step * len(job_load.profile_w)
    assert end <= job_load.deadline
    assert 9 <= _local(inputs, start).hour < 16  # midday PV, not the morning
    for t in range(start):
        assert not result.plan.intervals[t].devices["washer"].allowed
    # The whole profile runs on surplus on a sunny day.
    flows = result.schedule.flex[0]
    assert sum(flows.grid_w) <= TOL_W
    assert rk.JOB_START in _keys(result)


def test_program_with_an_infeasible_deadline_starts_now_and_says_so() -> None:
    inputs = flex_inputs("program_deadline_infeasible")
    result = run(inputs)
    assert not inputs.jobs[0].deadline_met
    assert result.plan.intervals[0].devices["washer"].start
    assert rk.JOB_DEADLINE_INFEASIBLE in {r.key for r in result.plan.reasons}


def test_heat_pump_keeps_its_band_and_preheats_from_the_grid_only_when_allowed() -> None:
    off = flex_inputs("heat_pump_preheat")
    on = flex_inputs("heat_pump_preheat_grid")
    plans = {}
    for label, inputs in (("off", off), ("on", on)):
        result = run(inputs)
        i, th = _thermal(inputs)
        flows = result.schedule.flex[i]
        temps = thermal_temps(th, [flows.power_w(t) for t in range(inputs.n)])
        assert min(temps) >= th.min_c - 0.1
        assert max(temps) <= th.max_c + 0.1
        plans[label] = (result, i, th, flows, temps)
    _, _, th_off, flows_off, _ = plans["off"]
    for t in range(off.n):
        assert flows_off.grid_w[t] + flows_off.battery_w[t] <= th_off.base_w[t] + TOL_W
    result_on, _, th_on, _, temps_on = plans["on"]
    # With grid pre-heating the store is warmer before the 17:00 price peak and it pays.
    before_peak = next(
        t for t in range(on.n) if _local(on, t) >= datetime(2026, 12, 8, 17, tzinfo=TZ)
    )
    assert temps_on[before_peak - 1] > plans["off"][4][before_peak - 1] + 0.3
    assert result_on.objective <= plans["off"][0].objective + TOL
    permitted = [
        iv.devices[th_on.device_id].grid_power_max_w
        for iv in result_on.plan.intervals
        if iv.devices[th_on.device_id].grid_power_max_w is not None
    ]
    assert permitted  # the band permits grid power only for the pre-heating
    assert rk.THERMAL_PREHEAT_GRID in _keys(result_on)


def test_peak_variable_holds_every_import_and_the_limit() -> None:
    inputs = flex_inputs("peak_shaving")
    result = run(inputs)
    assert result.solver is not None
    assert result.solver.x is not None
    econ = economics(inputs)
    _, layout = build_problem(inputs, econ)
    assert layout.flex is not None
    peak_kw = float(result.solver.x[layout.flex.pk(0)])
    for t in range(inputs.n):
        imported = result.schedule.import_w(t)
        assert imported <= peak_kw * 1000 + TOL_W
        limit = inputs.import_limit_at(t)
        assert limit is not None
        assert imported <= limit + TOL_W
    assert peak_kw * 1000 >= 6_000 - TOL_W  # the month's peak already reached
    assert rk.GRID_PEAK in {r.key for r in result.plan.reasons}
    i, ev = _ev(inputs)
    assert ev.need_by_wh is not None
    assert econ.flex is not None
    got = ev_delivered_wh(ev, result.schedule.flex[i], econ.flex.surplus_p10_w, inputs.dt_h)
    assert got >= ev.need_by_wh - 1


def test_para14a_budget_holds_and_blocks_grid_charging() -> None:
    inputs = flex_inputs("para14a_dimming")
    result = run(inputs)
    dimmed = [t for t in range(inputs.n) if inputs.para14a_at(t) is not None]
    assert dimmed
    for schedule in (result.schedule, heuristic_schedule(inputs)):
        for t in dimmed:
            flagged = sum(
                f.power_w(t)
                for load, f in zip(inputs.flex_loads, schedule.flex, strict=True)
                if load.para14a
            )
            assert flagged <= 4_200 + TOL_W
            assert schedule.grid_charge_w[t] <= TOL_W
            assert inputs.grid_charge_at(t) == 0.0
    assert rk.GRID_PARA14A in {r.key for r in result.plan.reasons}


def test_full_house_meets_deadlines_with_binaries() -> None:
    inputs = flex_inputs("full_house")
    result = run(inputs)
    assert result.plan.stats is not None
    assert result.plan.stats.n_binaries > 0
    econ = economics(inputs)
    assert econ.flex is not None
    for load, flows in zip(inputs.flex_loads, result.schedule.flex, strict=True):
        if isinstance(load, PlannerJob):
            assert flows.start is not None
            end = inputs.starts[flows.start] + inputs.step * len(load.profile_w)
            assert end <= load.deadline
        if isinstance(load, PlannerEv) and load.need_by_wh is not None:
            got = ev_delivered_wh(load, flows, econ.flex.surplus_p10_w, inputs.dt_h)
            assert got >= load.need_by_wh - 1


def test_without_flexible_inputs_the_phase4_model_is_unchanged() -> None:
    inputs = situation_inputs(reference_situation())
    assert inputs.flex_loads == ()
    assert inputs.grid is None
    econ = economics(inputs)
    problem, layout = build_problem(inputs, econ)
    assert layout.flex is None
    assert econ.flex is None
    assert problem.integrality == ()
    assert problem.n_cols == layout.n_base


def test_demanded_loads_above_the_import_limit_are_penalised_and_explained() -> None:
    situation = flex_situation("ev_arrives_late")
    now = situation.now
    tight = replace(
        situation,
        flex=FlexRequest(
            ev_states={"wallbox": EvState(device_id="wallbox", mode=EvMode.NOW, updated_at=now)},
            ev_live={"wallbox": EvLive(soc_pct=30.0, connected=True)},
        ),
        grid_limits=GridRequest(fuse_limit_w=8_000.0),
    )
    inputs = situation_inputs(tight)
    result = run(inputs)
    assert violations(inputs, result.schedule) == []
    assert result.schedule.import_w(0) > 8_000.0
    assert rk.GRID_IMPORT_LIMIT_EXCEEDED in {r.key for r in result.plan.intervals[0].reasons}
    # Discretionary grid flows (grid charging) never use the slack.
    assert result.schedule.grid_charge_w[0] <= TOL_W


def test_warm_start_from_the_previous_plan_keeps_the_objective() -> None:
    inputs = flex_inputs("full_house")
    first = run(inputs)
    second = run(inputs, warm_start=first.plan)
    assert second.objective == pytest.approx(first.objective, rel=1e-4, abs=1e-4)


def test_a_scheduled_job_keeps_its_start_when_nothing_is_cheaper() -> None:
    situation = flex_situation("program_deadline")
    inputs = situation_inputs(situation)
    first = run(inputs)
    start = first.schedule.flex[0].start
    assert start is not None
    armed = situation.flex.jobs[0] if situation.flex is not None else None
    assert armed is not None
    scheduled = armed.model_copy(
        update={"state": JobState.SCHEDULED, "planned_start": inputs.starts[start]}
    )
    again = situation_inputs(replace(situation, flex=FlexRequest(jobs=(scheduled,))))
    assert again.jobs[0].previous_start == start
    assert run(again).schedule.flex[0].start == start


# --- builders ----------------------------------------------------------------------------------

NOW = datetime(2026, 12, 8, 18, tzinfo=TZ)


def _build(devices: list[DeviceConfig], request: FlexRequest) -> tuple[FlexBuild, Grid]:
    grid = planning_grid(NOW, PLANNER_HORIZON)
    power = {d.id: device_max_power_w(d) for d in devices}
    return build_flex(devices, request, now=NOW, grid=grid, tz=TZ, max_power=power), grid


def test_ev_without_connection_or_arrival_is_left_out_with_a_reason() -> None:
    built, _ = _build([wallbox()], FlexRequest(ev_live={"wallbox": EvLive(connected=False)}))
    assert built.evs == ()
    assert "wallbox" in built.claimed
    assert [r.key for r in built.reasons] == [rk.EV_NOT_CONNECTED]


def test_ev_modes_set_floors_and_grid_caps() -> None:
    def ev(mode: EvMode, departure: datetime | None = None) -> PlannerEv:
        target = EvTarget(soc_pct=80.0, by=NOW + timedelta(hours=10))
        state = EvState(
            device_id="wallbox",
            mode=mode,
            target=target if mode is EvMode.TARGET else None,
            updated_at=NOW,
        )
        live = EvLive(soc_pct=50.0, connected=True, departure=departure)
        request = FlexRequest(ev_states={"wallbox": state}, ev_live={"wallbox": live})
        built, _ = _build([wallbox()], request)
        return built.evs[0]

    now_ev = ev(EvMode.NOW)
    assert now_ev.floor_w[0] == pytest.approx(11_040.0)
    assert now_ev.grid_max_w[0] == pytest.approx(11_040.0)
    # 50 % -> 100 % of 60 kWh at 90 %: 33.3 kWh, the floors never exceed it.
    assert sum(now_ev.floor_w) * 0.25 <= (now_ev.need_total_wh or 0) + TOL
    min_pv = ev(EvMode.MIN_PV)
    assert min_pv.floor_w[0] == pytest.approx(min_pv.min_w)
    assert min_pv.grid_max_w[0] == pytest.approx(min_pv.min_w)
    pv = ev(EvMode.PV)
    assert max(pv.grid_max_w) == 0.0
    assert max(pv.floor_w) == 0.0
    target = ev(EvMode.TARGET)
    # 50 % -> 80 % of 60 kWh is 18 kWh in the car, 20 kWh from the charger at 90 %.
    assert target.need_by_wh == pytest.approx(20_000.0)
    assert target.deadline_slot == 39  # 18:00 + 10 h: the slot ending at 04:00
    assert all(g == 11_040.0 for g in target.grid_max_w[: target.deadline_slot + 1])
    assert all(g == 0 for g in target.grid_max_w[target.deadline_slot + 1 :])
    departed = ev(EvMode.PV, departure=NOW + timedelta(hours=2))
    assert sum(1 for m in departed.max_w if m > 0) == 8


def test_ev_off_and_unknown_need() -> None:
    off = FlexRequest(
        ev_states={"wallbox": EvState(device_id="wallbox", mode=EvMode.OFF, updated_at=NOW)}
    )
    built, _ = _build([wallbox()], off)
    assert built.evs == ()
    unknown = FlexRequest(
        ev_states={"wallbox": EvState(device_id="wallbox", mode=EvMode.NOW, updated_at=NOW)},
        ev_live={"wallbox": EvLive(connected=True)},
    )
    built, _ = _build([wallbox()], unknown)
    ev = built.evs[0]
    assert ev.need_total_wh is None
    assert sum(1 for f in ev.floor_w if f > 0) == 1  # only the coming slot
    assert rk.EV_NEED_UNKNOWN in [r.key for r in built.reasons]


def test_jobs_running_ignored_and_without_job() -> None:
    washer = program("washer", (500.0, 100.0, 100.0, 300.0))
    deadline = NOW + timedelta(hours=10)
    running = ProgramJob(
        id="w1",
        device_id="washer",
        state=JobState.RUNNING,
        deadline=deadline,
        armed_at=NOW - timedelta(hours=1),
        started_at=NOW - timedelta(minutes=20),
    )
    second = job("washer", NOW, deadline + timedelta(hours=1))
    built, _ = _build([washer], FlexRequest(jobs=(second, running)))
    planned = built.jobs[0]
    assert planned.running
    assert planned.candidates == (0,)
    assert planned.profile_w == (400.0, 400.0, 1200.0)  # 20 min done: from the 2nd quarter
    assert [r.key for r in built.reasons] == [rk.JOB_IGNORED]
    nothing, _ = _build([washer], FlexRequest())
    assert nothing.jobs == ()
    assert "washer" in nothing.claimed  # never planned as continuous surplus energy


def test_thermal_falls_back_to_energy_only_with_its_cause() -> None:
    bad = heat_pump()
    assert bad.thermal is not None
    poor = bad.thermal.model_copy(
        update={
            "learned": LearnedThermal(
                model=ThermalModel(capacity_wh_per_k=1000.0, loss_w_per_k=100.0),
                nrmse=0.5,
                history_days=30,
                fitted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        }
    )
    cases = [
        (bad.model_copy(update={"thermal": poor}), FlexRequest(), "model"),
        (heat_pump(), FlexRequest(outdoor_c=outdoor(NOW, winter=True)), "temperature"),
        (heat_pump(), FlexRequest(temperatures_c={"heat_pump": 21.0}), "outdoor"),
    ]
    for device, request, cause in cases:
        built, _ = _build([device], request)
        assert built.thermals == ()
        assert device.id not in built.claimed  # stays an energy-only device
        assert [r.args.get("cause") for r in built.reasons] == [cause]


def test_thermal_base_holds_the_minimum_exactly() -> None:
    model = ThermalModel(capacity_wh_per_k=10_000.0, loss_w_per_k=180.0)
    temps = [-5.0] * 24
    cop = [2.5] * 24
    base = thermal_base_w(
        model, temp0_c=21.0, min_c=20.0, outdoor_c=temps, cop=cop, max_w=5_000.0, dt_s=900.0
    )
    temp = 21.0
    for p, out_c in zip(base, temps, strict=True):
        temp = model.step(temp, out_c, p * 2.5, 900.0)
        assert temp >= 20.0 - 1e-6
    assert temp == pytest.approx(20.0, abs=1e-6)
    assert base[0] == 0.0  # still above the minimum at first


def test_build_grid_resolves_limits_per_slot() -> None:
    now = datetime(2026, 11, 30, 20, tzinfo=TZ)
    grid = planning_grid(now, PLANNER_HORIZON)
    request = GridRequest(
        fuse_limit_w=17_000.0,
        peak=PeakShaving(enabled=True, peak_floor_w=2_500.0, capacity_price_per_kw_month=5.0),
        month_peak_w=7_000.0,
        static_export_limit_w=13_500.0,
        export_dynamic=True,
        export_adapter_verified=True,
        para14a=Para14aBudget(Para14aStatus.UNKNOWN),
    )
    prices = [-0.01 if t % 2 else 0.08 for t in range(grid.count)]
    built = build_grid(request, now=now, grid=grid, tz=TZ, export_price=prices)
    starts = grid.starts()
    for t, at in enumerate(starts):
        november = at.astimezone(TZ).month == 11
        # The peak resets at the local month boundary (December starts at the floor).
        assert built.import_limits_w[t] == (7_000.0 if november else 2_500.0)
        assert built.export_limits_w[t] == (0.0 if prices[t] < 0 else 13_500.0)
        assert built.grid.month_of_slot[t] == (0 if november else 1)
    assert built.grid.month_floor_w == (7_000.0, 0.0)
    # Unknown §14a: the flagged devices get no budget in the coming quarter hour.
    assert built.grid.para14a_w[0] == 0.0
    assert all(v is None for v in built.grid.para14a_w[1:])
    assert [r.key for r in built.reasons] == [rk.GRID_PARA14A_UNKNOWN]
    unverified = build_grid(
        replace(request, export_adapter_verified=False),
        now=now,
        grid=grid,
        tz=TZ,
        export_price=prices,
    )
    assert set(unverified.export_limits_w) == {13_500.0}


def test_flexible_band_kinds() -> None:
    inputs = flex_inputs("program_deadline")
    result = run(inputs)
    start = result.schedule.flex[0].start
    assert start is not None
    band = result.plan.intervals[start].devices["washer"]
    assert band.start
    assert band.preference is BandPreference.PREFER_ON
    after = result.plan.intervals[start + len(inputs.jobs[0].profile_w)].devices["washer"]
    assert after.allowed
    assert not after.start
