# SPDX-License-Identifier: Apache-2.0
"""PR 6-J: ``loadpoint`` actions through the safety gate (L1-L5 in context).

The gate is the only writer of an evcc loadpoint: values in range (L1), ``now`` only inside the
user's floor or a planned grid interval and never with an unlocked home battery (L2, E6),
nothing raised while evcc's state is unknown (L2), shadow by default, a release restores the
baseline (L5).
"""

from __future__ import annotations

import asyncio
import dataclasses
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
    Decision,
    DecisionKind,
    LoadpointAction,
    OperatingMode,
    ReleaseAction,
)
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    LoadpointControl,
    PowerModel,
    PowerModelKind,
)
from suncore.core.flex import EvMode
from suncore.safety.gate import REASON_EMERGENCY_STOP, GateInputs, SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore
from tests.safety.helpers import T0, FakeClock, SpyActuator, flows, make_intent, surplus

AUTO = OperatingMode.AUTOMATIC
OFF_AT_10 = DeviceState(mode="off", max_current_a=10.0, connected=True, on=False, value=0.0)
BASELINE = LoadpointAction(mode="off", max_current_a=10.0)


def loadpoint(*, confirmed: bool = True) -> DeviceConfig:
    return DeviceConfig(
        id="lp",
        template_id="evcc.loadpoint",
        name="evcc",
        kind=DeviceKind.WALLBOX,
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040),
        control=LoadpointControl(kind="loadpoint", loadpoint_id=1),
        behaviour_confirmed=confirmed,
    )


class EvccSpy(SpyActuator):
    """A spy that also reports evcc's own ``minCurrent`` (the ``LoadpointLimits`` protocol)."""

    minimum: float | None = 6.0

    def min_current_a(self) -> float | None:
        return self.minimum


@dataclasses.dataclass
class Harness:
    gate: SafetyGate
    clock: FakeClock
    spy: EvccSpy
    store: MemoryRuntimeStore
    states: dict[str, DeviceState | None]


def make(
    *,
    mode: EvMode = EvMode.PV,
    state: DeviceState | None = OFF_AT_10,
    battery_present: bool = False,
    grid_w: float = 300.0,
    import_limit_w: float | None = 17_000.0,
    device_mode: OperatingMode = AUTO,
    plan: Plan | None = None,
) -> Harness:
    clock = FakeClock()
    spy = EvccSpy("lp", observed=BASELINE)
    states: dict[str, DeviceState | None] = {"lp": state}
    store = MemoryRuntimeStore()
    inputs = GateInputs(
        flows=flows(grid_w=grid_w, pv_w=0.0),
        surplus=surplus(0.0),
        import_limit_w=import_limit_w,
        battery_present=battery_present,
    )

    async def sink(_: Decision) -> None:
        return None

    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={"lp": spy},
        modes=StaticModes(AUTO, {"lp": device_mode}),
        devices={"lp": loadpoint()}.get,
        states=states.get,
        inputs=lambda: dataclasses.replace(inputs, ts=clock.now),
        store=store,
        settings=ControlSettings(min_write_interval_s=5.0, default_settle_s=2.0),
        sleep=clock.sleep,
        ev_modes=lambda _id: mode,
        plan=(lambda _now: plan) if plan is not None else None,
    )
    return Harness(gate, clock, spy, store, states)


def submit(h: Harness, action: LoadpointAction | ReleaseAction) -> Decision:
    return asyncio.run(h.gate.submit(make_intent(action, asset_id="lp", ts=h.clock.now)))


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def test_smart_follows_the_surplus_and_is_written() -> None:
    h = make(mode=EvMode.PV)
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=16.0))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    assert [i.action for i in h.spy.applied] == [LoadpointAction(mode="smart", max_current_a=16.0)]


def test_now_in_the_users_now_mode_is_accepted_without_a_battery() -> None:
    h = make(mode=EvMode.NOW)
    decision = submit(h, LoadpointAction(mode="now", max_current_a=16.0))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)


@pytest.mark.parametrize("mode", [EvMode.PV, EvMode.MIN_PV, EvMode.TARGET])
def test_now_without_an_allowance_is_refused(mode: EvMode) -> None:
    h = make(mode=mode)
    decision = submit(h, LoadpointAction(mode="now", max_current_a=16.0))
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.evcc_now_not_allowed" in keys(decision)
    assert h.spy.applied == []


def test_now_in_a_planned_grid_interval_of_target_is_accepted_within_the_band() -> None:
    start = T0 - timedelta(minutes=T0.minute % 15)
    band = DeviceBand(grid_power_max_w=8000, max_power_w=11_040)
    plan = Plan(
        created_at=T0,
        source="heuristic",
        intervals=(
            PlanInterval(start=start, end=start + timedelta(minutes=15), devices={"lp": band}),
        ),
    )
    h = make(mode=EvMode.TARGET, plan=plan)
    assert submit(h, LoadpointAction(mode="now", max_current_a=11.0)).kind is (
        DecisionKind.ACCEPTED
    )  # 7590 W
    h = make(mode=EvMode.TARGET, plan=plan)
    assert submit(h, LoadpointAction(mode="now", max_current_a=16.0)).kind is (
        DecisionKind.REJECTED
    )  # 11040 W > 8000 W


def test_e6_now_needs_a_battery_lock() -> None:
    h = make(mode=EvMode.NOW, battery_present=True)
    decision = submit(h, LoadpointAction(mode="now", max_current_a=16.0))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.ev_battery_unlocked"]
    assert h.spy.applied == []


def test_g1_the_import_limit_holds_for_now() -> None:
    h = make(mode=EvMode.NOW, grid_w=9000.0, import_limit_w=17_000.0)
    decision = submit(h, LoadpointAction(mode="now", max_current_a=16.0))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.grid_import_limit"]


def test_a_mode_off_user_gets_no_raise_but_a_lowering() -> None:
    h = make(mode=EvMode.OFF)
    assert keys(submit(h, LoadpointAction(mode="smart", max_current_a=10.0))) == [
        "safety.ev_mode_off"
    ]
    h = make(mode=EvMode.OFF, state=DeviceState(mode="smart", max_current_a=10.0, connected=True))
    h.spy.observed = LoadpointAction(mode="smart", max_current_a=10.0)
    assert submit(h, LoadpointAction(mode="off", max_current_a=10.0)).kind is DecisionKind.ACCEPTED


def test_an_unplugged_vehicle_gets_no_raise() -> None:
    state = OFF_AT_10.model_copy(update={"connected": False})
    h = make(mode=EvMode.PV, state=state)
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=16.0))
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.ev_not_connected" in keys(decision)


def test_an_unknown_evcc_state_blocks_every_raise() -> None:
    h = make(mode=EvMode.PV, state=None)
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=16.0))
    assert decision.kind is DecisionKind.REJECTED
    assert h.spy.applied == []


@pytest.mark.parametrize("current", [5.0, 5.9, 16.2, 32.0])
def test_l1_max_current_outside_the_range_is_refused(current: float) -> None:
    h = make(mode=EvMode.PV)
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=current))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.evcc_current_range"]


def test_l1_evccs_own_minimum_raises_the_floor() -> None:
    h = make(mode=EvMode.PV)
    h.spy.minimum = 8.0
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=7.0))
    assert keys(decision) == ["safety.evcc_current_range"]


@pytest.mark.parametrize("limit", [0.0, 0.4])
def test_l1_limit_soc_below_1_is_refused(limit: float) -> None:
    h = make(mode=EvMode.PV)
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=10.0, limit_soc_pct=limit))
    assert keys(decision) == ["safety.evcc_limit_range"]


def test_l1_a_vehicle_plan_is_refused() -> None:
    h = make(mode=EvMode.PV)
    action = LoadpointAction(
        mode="smart", max_current_a=10.0, plan_soc_pct=80.0, plan_time=T0 + timedelta(hours=8)
    )
    assert keys(submit(h, action)) == ["safety.evcc_plan_unverified"]
    assert h.spy.applied == []


def test_shadow_is_the_default_and_writes_nothing() -> None:
    h = make(mode=EvMode.PV, device_mode=OperatingMode.SHADOW)
    decision = submit(h, LoadpointAction(mode="smart", max_current_a=16.0))
    assert decision.kind is not DecisionKind.ACCEPTED
    assert not decision.wrote_hardware
    assert h.spy.applied == []


def test_a_release_restores_the_takeover_baseline() -> None:
    h = make(mode=EvMode.PV)
    assert submit(h, LoadpointAction(mode="smart", max_current_a=16.0)).kind is (
        DecisionKind.ACCEPTED
    )
    decision = asyncio.run(h.gate.release_device("lp", REASON_EMERGENCY_STOP))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    assert h.spy.released == [BASELINE]
