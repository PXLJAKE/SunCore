# SPDX-License-Identifier: Apache-2.0
"""PR 6-I: ``charge`` through the safety gate (E1-E9 in context).

The gate is the only writer: a charge is judged with the EV mode (runtime lookup, else the
device default; an unreadable lookup is ``off``), the connection, the plan band (target), the
battery lock (E6) and the import limit. Shadow stays the default; a release restores the
baseline; nothing raises the car while the site's inputs are unknown.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from datetime import timedelta

import pytest

from suncore.core.control import (
    ControlSettings,
    DeviceBand,
    DeviceState,
    Plan,
    PlanInterval,
)
from suncore.core.decisions import (
    ChargeAction,
    Decision,
    DecisionKind,
    OperatingMode,
    ReleaseAction,
)
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
from suncore.safety.gate import GateInputs, SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore
from tests.safety.helpers import T0, FakeClock, SpyActuator, flows, make_intent, surplus

AUTO = OperatingMode.AUTOMATIC
IDLE = DeviceState(value=16.0, on=False, phases=3, connected=True)


def charger(*, confirmed: bool = True) -> DeviceConfig:
    return DeviceConfig(
        id="wb",
        template_id="generic.wallbox",
        name="Wallbox",
        kind=DeviceKind.WALLBOX,
        entities={
            "current": EntityRef(entity_id="number.wb_current"),
            "enable": EntityRef(entity_id="switch.wb_charging"),
        },
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040),
        control=ChargerControl(
            kind="charger", role="current", enable_role="enable", max_current_a=16
        ),
        ev=EvSpec(),
        behaviour_confirmed=confirmed,
    )


@dataclasses.dataclass
class EvHarness:
    gate: SafetyGate
    clock: FakeClock
    spy: SpyActuator
    store: MemoryRuntimeStore
    states: dict[str, DeviceState | None]


def make(
    *,
    mode: EvMode | Callable[[str], EvMode] = EvMode.NOW,
    state: DeviceState | None = IDLE,
    battery_present: bool = False,
    surplus_w: float = 0.0,
    grid_w: float = 300.0,
    import_limit_w: float | None = 17_000.0,
    device_mode: OperatingMode = AUTO,
    plan: Plan | None = None,
    device: DeviceConfig | None = None,
) -> EvHarness:
    clock = FakeClock()
    device = device or charger()
    spy = SpyActuator("wb", observed=ChargeAction(current_a=16, phases=3, enabled=False))
    states: dict[str, DeviceState | None] = {"wb": state}
    store = MemoryRuntimeStore()
    inputs = GateInputs(
        flows=flows(grid_w=grid_w, pv_w=0.0),
        surplus=surplus(surplus_w),
        import_limit_w=import_limit_w,
        battery_present=battery_present,
    )

    async def sink(_: Decision) -> None:
        return None

    lookup = mode if callable(mode) else (lambda _id: mode)  # type: ignore[return-value]
    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={"wb": spy},
        modes=StaticModes(AUTO, {"wb": device_mode}),
        devices={"wb": device}.get,
        states=states.get,
        inputs=lambda: dataclasses.replace(inputs, ts=clock.now),
        store=store,
        settings=ControlSettings(min_write_interval_s=5.0, default_settle_s=2.0),
        sleep=clock.sleep,
        ev_modes=lookup,
        plan=(lambda _now: plan) if plan is not None else None,
    )
    return EvHarness(gate, clock, spy, store, states)


def submit(h: EvHarness, action: ChargeAction | ReleaseAction) -> Decision:
    return asyncio.run(h.gate.submit(make_intent(action, asset_id="wb")))


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def test_now_without_a_battery_charges_from_the_grid_within_the_limit() -> None:
    h = make(mode=EvMode.NOW)
    decision = submit(h, ChargeAction(current_a=16))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    assert len(h.spy.applied) == 1


def test_e6_grid_power_needs_a_battery_lock() -> None:
    h = make(mode=EvMode.NOW, battery_present=True)
    decision = submit(h, ChargeAction(current_a=16))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.ev_battery_unlocked"]
    assert h.spy.applied == []


def test_g1_the_import_limit_holds_for_a_user_demanded_floor() -> None:
    h = make(mode=EvMode.NOW, grid_w=9000.0, import_limit_w=17_000.0)
    decision = submit(h, ChargeAction(current_a=16))  # 11 kW on top of 9 kW
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.grid_import_limit"]


@pytest.mark.parametrize("mode", [EvMode.PV, EvMode.TARGET])
def test_surplus_only_modes_get_no_grid_power(mode: EvMode) -> None:
    h = make(mode=mode, surplus_w=500.0)
    decision = submit(h, ChargeAction(current_a=6))  # 4140 W > 500 W surplus
    assert decision.kind is DecisionKind.REJECTED
    assert h.spy.applied == []


def test_target_uses_the_fresh_plan_band_only() -> None:
    start = T0 - timedelta(minutes=T0.minute % 15)
    band = DeviceBand(grid_power_max_w=8000, max_power_w=11_040)
    plan = Plan(
        created_at=T0,
        source="heuristic",
        intervals=(
            PlanInterval(start=start, end=start + timedelta(minutes=15), devices={"wb": band}),
        ),
    )
    h = make(mode=EvMode.TARGET, plan=plan)
    assert submit(h, ChargeAction(current_a=11)).kind is DecisionKind.ACCEPTED  # 7590 W
    h = make(mode=EvMode.TARGET, plan=plan)
    assert submit(h, ChargeAction(current_a=16)).kind is DecisionKind.REJECTED  # 11040 W
    stale = plan.model_copy(update={"created_at": T0 - timedelta(hours=3)})
    h = make(mode=EvMode.TARGET, plan=stale)
    assert submit(h, ChargeAction(current_a=6)).kind is DecisionKind.REJECTED


def test_e5_unplugged_or_off_never_raises_but_pauses() -> None:
    unplugged = DeviceState(value=16.0, on=False, phases=3, connected=False)
    h = make(mode=EvMode.NOW, state=unplugged)
    assert "safety.ev_not_connected" in keys(submit(h, ChargeAction(current_a=6)))
    h = make(mode=EvMode.OFF)
    assert "safety.ev_mode_off" in keys(submit(h, ChargeAction(current_a=6)))


def test_an_unreadable_mode_counts_as_off() -> None:
    def broken(_: str) -> EvMode:
        raise RuntimeError("flex_state unreadable")

    h = make(mode=broken)
    assert "safety.ev_mode_off" in keys(submit(h, ChargeAction(current_a=6)))


def test_e1_current_outside_the_range_is_rejected() -> None:
    h = make(mode=EvMode.NOW)
    decision = submit(h, ChargeAction(current_a=4))
    assert keys(decision) == ["safety.ev_current_range"]


def test_a_pause_is_accepted_even_with_unknown_inputs() -> None:
    charging = DeviceState(value=16.0, on=True, phases=3, connected=True)
    h = make(mode=EvMode.PV, state=charging)
    h.spy.observed = ChargeAction(current_a=16, phases=3, enabled=True)
    h.gate._inputs = lambda: None  # type: ignore[method-assign]  # nothing known
    decision = submit(h, ChargeAction(current_a=0))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)


def test_shadow_is_the_default_and_writes_nothing() -> None:
    h = make(mode=EvMode.NOW, device_mode=OperatingMode.SHADOW)
    decision = submit(h, ChargeAction(current_a=16))
    assert decision.kind is DecisionKind.SHADOW
    assert h.spy.applied == []


def test_e8_unconfirmed_behaviour_is_capped_at_assisted() -> None:
    h = make(mode=EvMode.NOW, device=charger(confirmed=False))
    decision = submit(h, ChargeAction(current_a=16))
    assert decision.kind is DecisionKind.PROPOSED
    assert h.spy.applied == []


def test_e9_back_to_normal_restores_the_baseline() -> None:
    h = make(mode=EvMode.NOW)
    assert submit(h, ChargeAction(current_a=16)).kind is DecisionKind.ACCEPTED
    runtime = asyncio.run(h.store.load_runtime("wb"))
    assert isinstance(runtime, DeviceRuntime)
    assert runtime.baseline == ChargeAction(current_a=16, phases=3, enabled=False)
    asyncio.run(h.gate.emergency_stop())
    assert h.spy.released == [ChargeAction(current_a=16, phases=3, enabled=False)]
