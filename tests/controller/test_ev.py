# SPDX-License-Identifier: Apache-2.0
"""PR 6-I: ``controller.ev`` - charger levels, the mode directive (floors, caps, fail-safe),
phase switching with dwell and limits, and the battery's EV lock (E6).

Deterministic, time injected; the closed-loop evidence (switch counts under cloud flicker,
no SunCore import beyond the mode's allowance) is in ``tests/sim/test_ev_scenarios.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from suncore.controller.battery import BatteryIntents
from suncore.controller.ev import (
    PHASE_DWELL_S,
    EvMachine,
    EvTick,
    battery_holds_lock,
    charger_levels,
    charger_observed_level,
)
from suncore.controller.levels import ConfigQuantiser
from suncore.core.battery import BatteryAction
from suncore.core.control import DeviceBand, DeviceState
from suncore.core.decisions import ChargeAction, Decision, DecisionKind, OperatingMode
from suncore.core.devices import (
    ChargerControl,
    DeviceConfig,
    DeviceKind,
    EvSpec,
    PowerModel,
    PowerModelKind,
)
from suncore.core.flex import EvMode
from suncore.core.site import EntityRef
from tests.controller.test_battery_intents import inputs, plan

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def charger(*, switching: bool = False, **control: Any) -> DeviceConfig:
    entities = {"current": EntityRef(entity_id="number.wb_current")}
    if switching:
        entities["phases"] = EntityRef(entity_id="select.wb_phases")
    return DeviceConfig(
        id="wb",
        template_id="generic.wallbox",
        name="wb",
        kind=DeviceKind.WALLBOX,
        entities=entities,
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040),
        control=ChargerControl(
            kind="charger",
            role="current",
            max_current_a=16,
            phases_role="phases" if switching else None,
            phase_option_1="1" if switching else None,
            phase_option_3="3" if switching else None,
            phase_switching=switching,
            **control,
        ),
        ev=EvSpec(),
        hysteresis_w=300,
    )


def state(*, on: bool = False, current: float = 16, phases: int = 3, **kw: Any) -> DeviceState:
    return DeviceState(value=current, on=on, phases=phases, connected=True, **kw)  # type: ignore[arg-type]


def tick(mode: EvMode = EvMode.PV, **kw: Any) -> EvTick:
    base: dict[str, Any] = {
        "mode": mode,
        "band": None,
        "battery_locked": True,
        "site_trusted": True,
        "available_w": 0.0,
    }
    base.update(kw)
    return EvTick(**base)


def table(device: DeviceConfig | None = None, st_: DeviceState | None = None) -> Any:
    device = device or charger()
    t = charger_levels(device, st_ or state(), ConfigQuantiser({device.id: device}))
    assert t is not None
    return t


# --- levels -----------------------------------------------------------------------------


def test_levels_are_pause_then_min_to_max_in_one_amp_steps() -> None:
    t = table()
    currents = [lv.action.current_a for lv in t.levels]
    assert currents == [0.0, *range(6, 17)]
    assert t.level(1).power_w == 6 * 230 * 3
    assert charger_observed_level(t, state(on=False)) == 0
    assert charger_observed_level(t, state(on=True, current=10.4)) == 5  # 10 A


def test_levels_follow_the_observed_phases_and_need_them_known() -> None:
    device = charger(switching=True)
    one = table(device, state(phases=1))
    assert one.level(1).power_w == 6 * 230
    assert one.level(1).action == ChargeAction(current_a=6, phases=1)
    unknown = DeviceState(value=16, on=False, phases=None, connected=True)
    assert charger_levels(device, unknown, ConfigQuantiser({"wb": device})) is None


# --- the directive ----------------------------------------------------------------------


@given(mode=st.sampled_from(EvMode), locked=st.booleans(), trusted=st.booleans())
def test_floor_and_cap_by_mode(mode: EvMode, locked: bool, trusted: bool) -> None:
    t = table()
    band = DeviceBand(grid_power_max_w=7600, max_power_w=11_040)
    d = EvMachine(charger()).directive(
        T0, t, state(), 3, tick(mode, band=band, battery_locked=locked, site_trusted=trusted)
    )
    if mode is EvMode.OFF:
        assert d.cap == 0
        return
    if not trusted:
        assert d.cap == 1  # fail-safe: no increase, fall back to the minimum
        assert d.floor == 0
        return
    if mode is EvMode.PV:
        assert d.floor == 0
        assert not d.lock_wanted
        return
    assert d.lock_wanted  # E6: the battery should be held while grid power is allowed
    if not locked:
        assert d.floor == 0  # wait for the lock, never draw from the house battery
        return
    expected = {EvMode.NOW: t.top, EvMode.MIN_PV: 1, EvMode.TARGET: 6}[mode]  # 11 A = 7590 W
    assert d.floor == expected


def test_unplugged_caps_at_zero_and_a_stale_band_permits_nothing() -> None:
    t = table()
    unplugged = DeviceState(value=16, on=False, phases=3, connected=False)
    d = EvMachine(charger()).directive(T0, t, unplugged, 0, tick(EvMode.NOW))
    assert d.cap == 0
    d = EvMachine(charger()).directive(T0, t, state(), 0, tick(EvMode.TARGET, band=None))
    assert d.floor == 0
    assert not d.lock_wanted


# --- phase switching --------------------------------------------------------------------


def run_phases(available: list[float], *, step_s: int = 10, start_phases: int = 3) -> EvMachine:
    device = charger(switching=True)
    machine = EvMachine(device)
    phases = start_phases
    now = T0
    for watts in available:
        st_ = state(phases=phases)
        t = table(device, st_)
        machine.directive(now, t, st_, 0, tick(available_w=watts))
        intent = machine.phase_intent(now, st_, 0, None, machine_busy=False)
        if intent is not None:
            assert isinstance(intent.action, ChargeAction)
            assert intent.action.current_a == 0  # E2: only paused
            machine.report(
                Decision(
                    intent=intent, kind=DecisionKind.ACCEPTED, ts=now, mode=OperatingMode.AUTOMATIC
                )
            )
            phases = intent.action.phases or phases
        now += timedelta(seconds=step_s)
    return machine


def test_switch_to_one_phase_only_after_the_dwell() -> None:
    short = run_phases([2500.0] * (int(PHASE_DWELL_S) // 10 - 2))
    assert short.switch_times == []
    long = run_phases([2500.0] * (int(PHASE_DWELL_S) // 10 + 20))
    assert len(long.switch_times) == 1


def test_no_switch_in_now_or_with_unknown_surplus() -> None:
    device = charger(switching=True)
    machine = EvMachine(device)
    now = T0
    for _ in range(100):
        t = table(device, state())
        machine.directive(now, t, state(), 0, tick(EvMode.NOW, available_w=500.0))
        machine.directive(now, t, state(), 0, tick(available_w=None))
        now += timedelta(seconds=10)
    assert machine.switching_to is None


@given(
    pattern=st.lists(st.sampled_from((1500.0, 6000.0)), min_size=10, max_size=40),
    seconds=st.integers(min_value=20, max_value=200),
)
def test_flicker_never_exceeds_the_phase_switch_limit(pattern: list[float], seconds: int) -> None:
    """Oscillation is a bug: surplus flickering across the threshold for two hours."""
    available = [w for w in pattern for _ in range(seconds // 10)] * 4
    machine = run_phases(available[:720])
    times = machine.switch_times
    limit = charger(switching=True).control
    assert isinstance(limit, ChargerControl)
    for index, t in enumerate(times):
        within = [u for u in times[index:] if u - t < timedelta(hours=1)]
        assert len(within) <= limit.max_phase_switches_per_hour


# --- E6: the battery's EV lock ----------------------------------------------------------


def test_the_battery_locks_for_the_ev_also_without_a_fresh_plan() -> None:
    p = BatteryIntents()
    stale = plan(T0, created=T0 - timedelta(hours=3))
    action, reasons = p.wanted(T0, stale, inputs(), 60, ev_lock=True)
    assert action == BatteryAction(max_discharge_w=0)
    assert [r.key for r in reasons] == ["controller.battery.ev_lock"]
    action, _ = p.wanted(T0, stale, inputs(), 60, ev_lock=False)
    assert action is None
    # The plan's grid charging wins (charging is not discharging into the car).
    action, _ = p.wanted(T0, plan(T0, grid_charge_max_w=2000), inputs(), 60, ev_lock=True)
    assert action == BatteryAction(grid_charge_w=2000)


def test_only_a_real_lock_unlocks_grid_power_for_the_car() -> None:
    assert battery_holds_lock(BatteryAction(max_discharge_w=0), None)
    assert battery_holds_lock(BatteryAction(min_soc_pct=55), 55.3)
    assert not battery_holds_lock(BatteryAction(min_soc_pct=55), None)
    assert not battery_holds_lock(None, 50)
