# SPDX-License-Identifier: Apache-2.0
"""Heat pump scenarios of PR 6-K, closed-loop and planner-level.

* ``heat_pump_sg_ready_surplus``: SG-Ready contacts on the real stack (controller, gate,
  driver, fake HA) - judged by the simulated heat pump's physics: the states its contacts
  selected, the store temperature, the timing of every write;
* ``heat_pump_dhw_lift``: a ``water_heater`` set-point lift, never below the user's target,
  never above the comfort maximum (the release restoring the set-point is a driver test);
* the thermal pipeline: simulator history -> RC fit (``forecast.thermal``) -> the 6-H planner
  with the learned model, ``grid_preheat`` off and on.

Simulator only: the maintainer has no heat pump (``UNVERIFIED`` on hardware).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from suncore.core.decisions import Decision, DecisionKind, ReleaseAction, SetpointAction, StepAction
from suncore.core.devices import ComfortBand, ThermalSpec
from suncore.core.sgready import SG_RESTART_K
from suncore.core.thermal import CopCurve, CopPoint, model_usable
from suncore.forecast.thermal import Rows, learn, with_learned
from suncore.planner.flex import FlexRequest, PlannerThermal
from suncore.planner.flexmodel import thermal_temps
from suncore.planner.run import run
from suncore.sim.devices import HeatPumpParams, SimHeatPump
from suncore.sim.planner_flex import heat_pump, outdoor
from suncore.sim.planner_scenarios import SUMMER, TZ, Situation, situation_inputs
from suncore.sim.runner import RunResult
from tests.sim.helpers import run_named

pytestmark = pytest.mark.simulator

TOL_W = 1.0


def _s(result: RunResult, when: datetime) -> float:
    return (when - result.scenario.start).total_seconds()


def _writes(result: RunResult, device_id: str) -> list[Decision]:
    return [d for d in result.accepted(device_id) if not isinstance(d.intent.action, ReleaseAction)]


def _keys(d: Decision) -> list[str]:
    return [r.key for r in d.intent.reasons]


# --- SG-Ready ------------------------------------------------------------------------------------


def test_sg_ready_never_writes_block_or_forced_and_keeps_the_comfort_maximum() -> None:
    result, runner = run_named("heat_pump_sg_ready_surplus")
    sim = runner.sim_devices["heat_pump"]
    assert isinstance(sim, SimHeatPump)
    assert sim.states_seen <= {2, 3}  # never state 1 (block) nor state 4 (not allowed)
    assert 3 in sim.states_seen
    assert result.of_kind(DecisionKind.REJECTED) == []
    steps = [d.intent.action for d in _writes(result, "heat_pump")]
    assert all(isinstance(a, StepAction) and a.step in (0, 1) for a in steps)
    # the heat pump would heat to 58 °C in state 3: SunCore ends it at the comfort maximum
    assert sim.max_seen_c <= 55.0 + 0.3
    assert any(
        "controller.heat_pump.failsafe" in _keys(d)
        and d.intent.reasons[0].args.get("cause") == "heat_limit"
        for d in _writes(result, "heat_pump")
    )


def test_sg_ready_dwell_switch_limit_and_restart_hysteresis() -> None:
    result, _ = run_named("heat_pump_sg_ready_surplus")
    writes = _writes(result, "heat_pump")
    config = result.configs["heat_pump"]
    for before, after in itertools.pairwise(writes):
        gap = (after.intent.ts - before.intent.ts).total_seconds()
        if "controller.heat_pump.failsafe" not in _keys(after):
            assert gap >= 600 - result.step_s, (before, after)  # min dwell (fail-safe exempt)
    limit = config.max_switches_per_hour
    assert limit == 3
    for i, first in enumerate(writes):
        window = [d for d in writes[i:] if d.intent.ts - first.intent.ts < timedelta(hours=1)]
        assert len(window) <= limit
    # after the heat limit no intensifying while the store is above max_c - SG_RESTART_K
    log = result.devices["heat_pump"]
    limit_at = next(
        _s(result, d.intent.ts)
        for d in writes
        if d.intent.reasons and d.intent.reasons[0].args.get("cause") == "heat_limit"
    )
    for d in writes:
        at = _s(result, d.intent.ts)
        if at > limit_at and isinstance(d.intent.action, StepAction) and d.intent.action.step:
            temp = log.temp_c[int(at // result.step_s)]
            assert temp is not None
            assert temp <= 55.0 - SG_RESTART_K + 0.1


def test_sg_ready_cloud_and_unknown_probe_return_to_state_2() -> None:
    result, _ = run_named("heat_pump_sg_ready_surplus")
    writes = _writes(result, "heat_pump")
    at = [(_s(result, d.intent.ts), d) for d in writes]
    # the cloud at 3600 s: back to normal within the deficit back-off
    cloud = [d for s, d in at if 3600 <= s < 3600 + 300]
    assert cloud
    assert isinstance(cloud[0].intent.action, StepAction)
    assert cloud[0].intent.action.step == 0
    # the probe is unavailable 5400-6000 s: state 2 at once, no intensifying meanwhile
    probe = [d for s, d in at if 5400 <= s < 5400 + 3 * result.step_s]
    assert probe
    assert probe[0].intent.reasons[0].args == {"cause": "temperature_unknown"}
    assert not [
        d
        for s, d in at
        if 5400 <= s < 6000 and isinstance(d.intent.action, StepAction) and d.intent.action.step
    ]


def test_sg_ready_in_shadow_mode_writes_nothing() -> None:
    _, runner = run_named("heat_pump_sg_ready_surplus", shadow=True)
    sim = runner.sim_devices["heat_pump"]
    assert isinstance(sim, SimHeatPump)
    assert sim.states_seen == {2}


# --- set-point lift ----------------------------------------------------------------------------


def test_dhw_lift_stays_between_the_user_target_and_the_comfort_maximum() -> None:
    result, runner = run_named("heat_pump_dhw_lift")
    sim = runner.sim_devices["dhw"]
    assert isinstance(sim, SimHeatPump)
    assert result.of_kind(DecisionKind.REJECTED) == []
    lifts = [d.intent.action for d in _writes(result, "dhw")]
    assert lifts
    for action in lifts:
        assert isinstance(action, SetpointAction)
        assert 50.0 <= action.setpoint_c <= 55.0
        assert action.base_c == 50.0
    assert max(a.setpoint_c for a in lifts if isinstance(a, SetpointAction)) == 55.0
    assert sim.max_seen_c <= 55.0 + 0.3
    # the cloud (1500-3300 s) takes the lift back down to the user's target
    during = [
        d.intent.action
        for d in _writes(result, "dhw")
        if 1500 <= _s(result, d.intent.ts) < 3300 and isinstance(d.intent.action, SetpointAction)
    ]
    assert during
    assert during[-1].setpoint_c == 50.0


# --- the thermal pipeline: history -> fit -> plan ------------------------------------------------

SIM_COP = CopCurve(points=(CopPoint(outdoor_c=-12.0, cop=1.48), CopPoint(outdoor_c=40.0, cop=5.64)))
"""The simulated heat pump's COP (3.0 at 7 °C, +0.08 per K) as a curve for the learner."""


def _history(days: int = 20) -> tuple[Rows, HeatPumpParams]:
    """15-minute means of a simulated heat pump: normal operation, intensified some
    afternoons (as SunCore would), outdoor temperature from a daily cosine."""
    params = HeatPumpParams(
        volume_l=800.0,
        initial_temp_c=48.0,
        min_off_s=0.0,
        loss_w_per_k=6.0,
        draw_w=(150.0,) * 24,  # a steady draw: the hourly profile would alias the outdoor wave
    )
    sim = SimHeatPump("hp", params)
    start = datetime(2026, 6, 1, tzinfo=TZ)
    sim.advance(start, 20.0)
    temps: list[float | None] = []
    powers: list[float | None] = []
    outs: list[float | None] = []
    t = start
    for q in range(days * 96):
        acc_t = acc_p = acc_o = 0.0
        hour = (q % 96) / 4
        sim.contacts = [False, q // 96 % 2 == 0 and 12 <= hour < 15]
        for _ in range(15):
            t += timedelta(minutes=1)
            out = params.ambient_c + 0.5 * math.cos(2 * math.pi * (hour - 15) / 24)
            sim.advance(t, out)
            acc_t += sim.temp_c
            acc_p += sim.power_w()
            acc_o += out
        temps.append(acc_t / 15)
        powers.append(acc_p / 15)
        outs.append(acc_o / 15)
    return Rows(tuple(temps), tuple(powers), tuple(outs)), params


def _plan(grid_preheat: bool, rows: Rows) -> tuple[list[float], float, list[float]]:
    fit = learn(rows, now=datetime(2026, 6, 21, tzinfo=TZ), cop=SIM_COP)
    assert fit is not None
    assert model_usable(fit)
    base = heat_pump(winter=False, grid_preheat=grid_preheat)
    device = base.model_copy(
        update={
            "comfort": ComfortBand(min_c=45.0, max_c=55.0),
            "thermal": ThermalSpec(cop=SIM_COP, grid_preheat=grid_preheat),
        }
    )
    planned = with_learned([device], {device.id: fit})[0]
    now = datetime(SUMMER.year, SUMMER.month, SUMMER.day, 6, tzinfo=TZ)
    situation = Situation(
        now=now,
        devices=(planned,),
        flex=FlexRequest(temperatures_c={device.id: 46.0}, outdoor_c=outdoor(now, winter=False)),
    )
    inputs = situation_inputs(situation)
    result = run(inputs)
    index, thermal = next(
        (i, load) for i, load in enumerate(inputs.flex_loads) if isinstance(load, PlannerThermal)
    )
    flows = result.schedule.flex[index]
    power = [flows.power_w(t) for t in range(inputs.n)]
    temps = thermal_temps(thermal, power)
    extra_grid = [flows.grid_w[t] + flows.battery_w[t] - thermal.base_w[t] for t in range(inputs.n)]
    assert min(temps) >= thermal.min_c - 0.1
    assert max(temps) <= thermal.max_c + 0.1
    return power, result.objective, extra_grid


def test_learned_model_from_simulator_history_drives_the_plan() -> None:
    rows, params = _history()
    fit = learn(rows, now=datetime(2026, 6, 21, tzinfo=TZ), cop=SIM_COP)
    assert fit is not None
    assert model_usable(fit)
    true_capacity = params.volume_l * 1.163
    assert fit.model.capacity_wh_per_k == pytest.approx(true_capacity, rel=0.25)
    power_off, objective_off, extra_off = _plan(False, rows)
    _, objective_on, _ = _plan(True, rows)
    assert sum(power_off) > 0  # the store is heated within its band
    assert max(extra_off) <= TOL_W  # without grid_preheat no heat beyond the base from the grid
    assert objective_on <= objective_off + 1e-6  # the opt-in can only help the objective


def test_history_rows_match_the_learner_contract() -> None:
    rows, _ = _history(days=2)
    assert len(rows.temp_c) == len(rows.power_w) == len(rows.outdoor_c) == 192
    assert replace(rows) == rows
