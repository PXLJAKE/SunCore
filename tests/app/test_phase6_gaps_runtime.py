# SPDX-License-Identifier: Apache-2.0
"""PR 6-O: what the runtime feeds into the notification rules and the HA entity values.

The sources only read; they get a stub grid monitor and a stub planner where the control loop
would overwrite the real ones every tick.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from suncore.app.ha_entities import SUPPORTED, RuntimeEntitySource
from suncore.app.notifications import RuntimeObservationSource, grid_flags
from suncore.app.runtime import Runtime
from suncore.core.control import DeviceState, Plan, PlanInterval
from suncore.core.controlstate import BatteryControlState, DeviceControlState
from suncore.core.decisions import BatteryAction, Reason
from suncore.core.devices import DeviceKind
from suncore.core.flex import EvMode, EvState, EvTarget, JobState, ProgramJob
from suncore.core.grid import GridState, Para14aStatus
from suncore.ha.mqtt.catalogue import spec_by_key


def monitor(grid: GridState | None, para: Para14aStatus | None, peak: float | None = None) -> Any:
    return SimpleNamespace(grid_state=grid, para14a_status=para, month_peak_w=lambda _now: peak)


@pytest.mark.parametrize(
    ("grid", "para", "expected"),
    [
        (GridState.ISLAND, Para14aStatus.ACTIVE, (True, True)),
        (GridState.CONNECTED, Para14aStatus.INACTIVE, (False, False)),
        (GridState.NOT_CONFIGURED, Para14aStatus.INACTIVE, (False, False)),
        (GridState.UNKNOWN, Para14aStatus.UNKNOWN, (None, None)),
        (None, None, (None, None)),
    ],
)
def test_grid_flags_map_the_monitor_without_guessing(
    grid: GridState | None, para: Para14aStatus | None, expected: tuple[bool | None, bool | None]
) -> None:
    assert grid_flags(monitor(grid, para)) == expected
    assert grid_flags(None) == (None, None)


async def test_the_observation_carries_island_and_para14a(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RuntimeObservationSource(demo_runtime)
    now = demo_runtime.clock()
    stack = demo_runtime.stack
    assert stack is not None
    monkeypatch.setattr(stack, "grid_monitor", monitor(GridState.ISLAND, Para14aStatus.ACTIVE))
    obs = source.observe(now)
    assert (obs.island, obs.para14a) == (True, True)
    monkeypatch.setattr(stack, "grid_monitor", monitor(GridState.UNKNOWN, Para14aStatus.UNKNOWN))
    obs = source.observe(now)
    assert (obs.island, obs.para14a) == (None, None)


def fake_plan(runtime: Runtime, *, fresh: bool, shortfall: bool) -> Plan:
    now = runtime.clock()
    reasons = (
        (
            Reason(
                key="planner.ev.target_shortfall",
                args={"device": "wb", "need_kwh": 20.0, "planned_kwh": 14.5, "time": "07:00"},
            ),
        )
        if shortfall
        else ()
    )
    return Plan(
        created_at=now if fresh else now - timedelta(hours=3),
        intervals=(PlanInterval(start=now, end=now + timedelta(minutes=15), reasons=reasons),),
    )


@pytest.mark.parametrize(
    ("fresh", "shortfall", "expected"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
async def test_ev_targets_report_the_shortfall_only_of_a_plan_made_after_the_target(
    demo_runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
    fresh: bool,
    shortfall: bool,
    expected: bool,
) -> None:
    now = demo_runtime.clock()
    state = EvState(
        device_id="wb",
        mode=EvMode.TARGET,
        target=EvTarget(soc_pct=80.0, by=now + timedelta(hours=8)),
        updated_at=now - timedelta(minutes=1),
    )
    monkeypatch.setitem(demo_runtime._ev_states, "wb", state)
    plan = fake_plan(demo_runtime, fresh=fresh, shortfall=shortfall)
    monkeypatch.setattr(
        demo_runtime,
        "planning",
        SimpleNamespace(
            plan=plan, status=lambda *_a, **_k: SimpleNamespace(consecutive_failures=0)
        ),
    )
    [target] = RuntimeObservationSource(demo_runtime).observe(now).ev_targets
    assert target.device_id == "wb"
    assert state.target is not None
    assert target.by == state.target.by
    assert target.shortfall is expected
    if expected:
        assert (target.need_kwh, target.planned_kwh) == (20.0, 14.5)


async def test_only_chargers_in_target_mode_with_a_future_deadline_are_observed(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = demo_runtime.clock()
    target = EvTarget(soc_pct=80.0, by=now + timedelta(hours=1))
    states = {
        "pv": EvState(device_id="pv", mode=EvMode.PV, updated_at=now),
        "late": EvState(
            device_id="late",
            mode=EvMode.TARGET,
            target=EvTarget(soc_pct=80.0, by=now - timedelta(minutes=1)),
            updated_at=now,
        ),
        "ok": EvState(device_id="ok", mode=EvMode.TARGET, target=target, updated_at=now),
    }
    for key, value in states.items():
        monkeypatch.setitem(demo_runtime._ev_states, key, value)
    observed = RuntimeObservationSource(demo_runtime).observe(now).ev_targets
    assert [t.device_id for t in observed] == ["ok"]
    assert observed[0].shortfall is False


# --- HA entity values --------------------------------------------------------------------


async def test_grid_values_are_read_from_the_monitor(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = demo_runtime.stack
    assert stack is not None
    source = RuntimeEntitySource(demo_runtime)
    island = monitor(GridState.ISLAND, Para14aStatus.ACTIVE, 7400)
    monkeypatch.setattr(stack, "grid_monitor", island)
    assert source._grid_values() == {
        "island": True,
        "para14a_active": True,
        "grid_state": "island",
        "month_peak": 7400,
    }
    monkeypatch.setattr(stack, "grid_monitor", monitor(GridState.CONNECTED, Para14aStatus.ACTIVE))
    values = source._grid_values()
    assert (values["island"], values["grid_state"]) == (False, "limited")
    monkeypatch.setattr(stack, "grid_monitor", monitor(GridState.CONNECTED, Para14aStatus.INACTIVE))
    assert source._grid_values()["grid_state"] == "normal"
    assert "month_peak" not in source._grid_values()
    monkeypatch.setattr(stack, "grid_monitor", monitor(GridState.UNKNOWN, Para14aStatus.UNKNOWN))
    assert source._grid_values() == {}  # no source: no state, HA shows "unknown"


async def test_ev_values_follow_the_stored_state_and_the_charger(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RuntimeEntitySource(demo_runtime)
    now = demo_runtime.clock()
    dev = DeviceControlState.model_construct(
        device_id="wb",
        name="Wallbox",
        kind=DeviceKind.WALLBOX,
        state=DeviceState(connected=True, phases=3, soc_pct=41.5),
    )
    assert source._flex_values(dev) == {
        "wb_ev_mode_state": "off",
        "wb_ev_plugged": True,
        "wb_ev_phases": 3,
        "wb_ev_vehicle_soc": 41.5,
    }
    by = now + timedelta(hours=6)
    monkeypatch.setitem(
        demo_runtime._ev_states,
        "wb",
        EvState(
            device_id="wb",
            mode=EvMode.TARGET,
            target=EvTarget(energy_wh=12_500, by=by),
            updated_at=now,
        ),
    )
    values = source._flex_values(dev)
    assert values["wb_ev_mode_state"] == "target"
    assert values["wb_ev_target_energy_state"] == 12.5
    assert values["wb_ev_target_time_state"] == by.isoformat()
    assert "wb_ev_target_soc_state" not in values


async def test_heat_pump_and_program_values(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RuntimeEntitySource(demo_runtime)
    hp = DeviceControlState.model_construct(
        device_id="hp",
        name="Heat pump",
        kind=DeviceKind.HEAT_PUMP,
        state=DeviceState(step=3, setpoint_c=52.0),
        base_setpoint_c=48.0,
    )
    assert source._flex_values(hp) == {"hp_hp_sg_state": 3, "hp_hp_lift": 4.0}
    unknown = DeviceControlState.model_construct(
        device_id="hp",
        name="Heat pump",
        kind=DeviceKind.HEAT_PUMP,
        state=DeviceState(),
        base_setpoint_c=None,
    )
    assert source._flex_values(unknown) == {}

    now = demo_runtime.clock()
    washer = DeviceControlState.model_construct(
        device_id="washer", name="Washer", kind=DeviceKind.PROGRAM, state=None
    )
    monkeypatch.setattr(demo_runtime.program_jobs, "current", lambda _id: None)
    assert source._flex_values(washer) == {"washer_job_state": "idle"}
    job = ProgramJob(
        id="job-1",
        device_id="washer",
        state=JobState.SCHEDULED,
        deadline=now + timedelta(hours=8),
        armed_at=now,
        planned_start=now + timedelta(hours=3),
    )
    monkeypatch.setattr(demo_runtime.program_jobs, "current", lambda _id: job)
    assert source._flex_values(washer) == {
        "washer_job_state": "armed",
        "washer_planned_start": (now + timedelta(hours=3)).isoformat(),
    }


def test_battery_values_say_what_suncore_holds() -> None:
    def battery(held: BatteryAction | None, *, under: bool) -> BatteryControlState:
        return BatteryControlState.model_construct(held=held, under_control=under)

    values = RuntimeEntitySource._battery_values
    assert values(None) == {}
    assert values(battery(None, under=False)) == {"battery_action": "idle", "battery_lease": "none"}
    assert values(battery(BatteryAction(grid_charge_w=2000.0), under=True)) == {
        "battery_action": "charge",
        "battery_lease": "held",
    }
    assert values(battery(BatteryAction(max_discharge_w=0.0), under=True)) == {
        "battery_action": "hold",
        "battery_lease": "held",
    }
    # a stale `held` of a battery SunCore let go of does not count
    stale = values(battery(BatteryAction(max_discharge_w=0.0), under=False))
    assert stale["battery_action"] == "idle"


def test_controls_that_need_the_wp4_command_path_are_not_announced() -> None:
    """A select, number or button without a handler would be a control that does nothing: the
    EV, program, comfort and battery-setting controls stay out of ``SUPPORTED``."""
    withheld = {
        "ev_mode",
        "target_soc",
        "target_energy",
        "target_time",
        "comfort_min",
        "comfort_max",
        "arm",
        "cancel",
        "finish_by",
        "battery_strategy",
        "battery_reserve",
        "battery_evening_target",
    }
    assert not withheld & SUPPORTED
    known_commands = {
        "auto",
        "replan",
        "emergency_stop_button",
        "emergency_stop_clear",
        "device_auto",
        "device_boost",
        "device_pause",
        "device_resume",
        "device_boost_minutes",
    }
    for key in SUPPORTED:
        spec = spec_by_key(key)
        assert spec is not None
        assert not spec.command or key in known_commands, key
