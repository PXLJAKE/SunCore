# SPDX-License-Identifier: Apache-2.0
"""Grid invariants G1-G6 (plan WP6, ADR-0018; PR 6-F) as properties.

Hypothesis drives random actions, devices, flows and grid situations through the device
invariants (``safety.invariants``) and the battery invariants (``safety.battery``); the
properties state exactly when an intent may pass, so a check that allows more than the rule is
caught. Gate-level tests check the wiring: the cool-down memory, the §14a sum over the other
flagged devices and the shadow "would dim" decisions.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.balance import Flow
from suncore.core.control import DeviceState, GateInputs, PowerEstimate
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    OperatingMode,
    PowerSetpointAction,
    ReleaseAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig, DeviceKind
from suncore.core.grid import GridState, Para14aStatus
from suncore.core.signals import Quality
from suncore.safety import grid as g
from suncore.safety import invariants as inv
from suncore.safety.gate import SafetyGate
from suncore.safety.grid import GridSafety
from suncore.safety.invariants import InvariantContext
from suncore.safety.modes import StaticModes
from tests.safety.helpers import (
    SETTINGS,
    T0,
    FakeClock,
    SpyActuator,
    actions,
    climate_device,
    continuous_device,
    flows,
    make_intent,
    runtime,
    steps_device,
    surplus,
    switch_device,
)

DEVICES = (
    switch_device(),
    switch_device("plug").model_copy(update={"para14a": True}),
    continuous_device(),
    continuous_device("wallbox").model_copy(update={"para14a": True}),
    steps_device(),
    steps_device("hp").model_copy(update={"para14a": True, "kind": DeviceKind.HEAT_PUMP}),
    climate_device(),
)

grid_situations = st.builds(
    GridSafety,
    grid_state=st.sampled_from(list(GridState)),
    para14a_status=st.sampled_from(list(Para14aStatus)),
    para14a_budget_w=st.one_of(st.none(), st.floats(min_value=0, max_value=10_000)),
    para14a_others_w=st.one_of(st.none(), st.floats(min_value=0, max_value=10_000)),
    island_hold_s=st.one_of(st.just(0.0), st.just(math.inf), st.floats(0, 3600)),
    para14a_hold_s=st.one_of(st.just(0.0), st.floats(0, 3600)),
    fuse_limit_w=st.one_of(st.none(), st.floats(min_value=0, max_value=20_000)),
    inputs_fresh=st.booleans(),
)
grid_flows = st.builds(
    lambda grid, quality: dataclasses.replace(flows(grid), grid_w=Flow(grid, quality)),
    st.floats(min_value=-10_000, max_value=20_000),
    st.sampled_from([Quality.OK, Quality.OK, Quality.STALE, Quality.UNAVAILABLE]),
)


def context(
    action: object,
    device: DeviceConfig,
    grid: GridSafety,
    *,
    fl: object = None,
    state: DeviceState | None = None,
    estimate: PowerEstimate | None = None,
    rt: object = None,
) -> InvariantContext:
    return InvariantContext(
        action=action,  # type: ignore[arg-type]
        device=device,
        state=state,
        runtime=rt if rt is not None else runtime(device.id),  # type: ignore[arg-type]
        now=T0,
        settings=SETTINGS,
        flows=fl if fl is not None else flows(),  # type: ignore[arg-type]
        surplus=surplus(20_000),
        estimate=estimate,
        grid=grid,
    )


# --- G3-G6 on devices --------------------------------------------------------------------------


@settings(max_examples=500, deadline=None)
@given(actions, st.sampled_from(DEVICES), grid_situations, grid_flows)
def test_grid_situation_rejects_exactly_the_unsafe_increases(
    action: object, device: DeviceConfig, grid: GridSafety, fl: object
) -> None:
    c = context(action, device, grid, fl=fl)
    verdict = inv.check_grid_situation(c)
    increase = inv.is_load_increase(c)
    if not increase:
        assert verdict is None, "a reduction or a release never trips G3-G6 (G2)"
        return
    after = inv.expected_power_w(c.action, c.device, c.estimate)
    reading = c.flows.grid_w if c.flows is not None else None
    if grid.grid_state is GridState.ISLAND:
        assert verdict == g.REASON_ISLAND_NO_INCREASE  # G4
    elif grid.island_hold_s > 0:
        assert verdict is not None
        assert verdict.key == "safety.grid_cooldown"  # G6
    elif device.para14a and grid.para14a_hold_s > 0:
        assert verdict is not None
        assert verdict.key == "safety.para14a_cooldown"  # G6
    elif device.para14a and grid.para14a_status is Para14aStatus.UNKNOWN:
        assert verdict == g.REASON_PARA14A_UNKNOWN  # G5
    elif device.para14a and grid.para14a_status is Para14aStatus.ACTIVE:
        if grid.para14a_budget_w is None or grid.para14a_others_w is None or after is None:
            assert verdict == g.REASON_PARA14A_UNKNOWN
        elif grid.para14a_others_w + max(0.0, after) > grid.para14a_budget_w + 1.0:
            assert verdict is not None
            assert verdict.key == "safety.para14a_limit"  # G3
        else:
            assert verdict in (None, g.REASON_GRID_UNKNOWN)
    elif not grid.inputs_fresh:
        assert verdict == g.REASON_GRID_UNKNOWN  # G5
    elif reading is not None and (reading.value is None or reading.quality is not Quality.OK):
        assert verdict == g.REASON_GRID_UNKNOWN  # G5: stale grid power
    else:
        assert verdict is None


@settings(max_examples=300, deadline=None)
@given(st.sampled_from(DEVICES), grid_situations, grid_flows)
def test_a_release_and_a_switch_off_pass_every_grid_situation(
    device: DeviceConfig, grid: GridSafety, fl: object
) -> None:
    for action in (ReleaseAction(), SwitchAction(on=False), PowerSetpointAction(power_w=0)):
        c = context(action, device, grid, fl=fl)
        assert inv.check_grid_situation(c) is None


@settings(max_examples=300, deadline=None)
@given(
    st.floats(min_value=0, max_value=8000),
    st.floats(min_value=0, max_value=8000),
    st.floats(min_value=0, max_value=3000),
)
def test_para14a_sum_never_exceeds_the_budget(budget: float, others: float, power: float) -> None:
    """G3: whatever passes keeps the flagged devices' sum at or below the budget."""
    grid = GridSafety(
        para14a_status=Para14aStatus.ACTIVE, para14a_budget_w=budget, para14a_others_w=others
    )
    device = DEVICES[3]  # continuous, flagged
    c = context(PowerSetpointAction(power_w=power), device, grid)
    if inv.check_grid_situation(c) is None and inv.is_load_increase(c):
        assert others + power <= budget + 1.0


def test_unflagged_devices_ignore_para14a() -> None:
    grid = GridSafety(para14a_status=Para14aStatus.ACTIVE, para14a_budget_w=0, para14a_others_w=0)
    assert inv.check_grid_situation(context(SwitchAction(on=True), DEVICES[0], grid)) is None
    unknown = GridSafety(para14a_status=Para14aStatus.UNKNOWN)
    assert inv.check_grid_situation(context(SwitchAction(on=True), DEVICES[0], unknown)) is None
    assert (
        inv.check_grid_situation(context(SwitchAction(on=True), DEVICES[1], unknown))
        == g.REASON_PARA14A_UNKNOWN
    )


def test_unknown_backup_state_is_connected() -> None:
    """``unknown`` / ``not_configured`` never apply island rules (plan §8, H1)."""
    for state in (GridState.UNKNOWN, GridState.NOT_CONFIGURED, GridState.CONNECTED):
        c = context(SwitchAction(on=True), DEVICES[0], GridSafety(grid_state=state))
        assert inv.check_grid_situation(c) is None


# --- G2: the fuse waiver ------------------------------------------------------------------------


def _min_run_ctx(device: DeviceConfig, grid_w: float, fuse: float | None) -> InvariantContext:
    return context(
        SwitchAction(on=False),
        device,
        GridSafety(fuse_limit_w=fuse),
        fl=flows(grid_w),
        state=DeviceState(on=True),
        rt=runtime(
            device.id,
            last_on_ts=T0 - timedelta(seconds=10),
            switch_times=[T0 - timedelta(seconds=10 + i) for i in range(10)],
        ),
    )


@settings(max_examples=200, deadline=None)
@given(st.floats(min_value=-5000, max_value=20_000), st.floats(min_value=0, max_value=15_000))
def test_min_run_is_waived_only_above_the_fuse_for_non_thermal_kinds(
    grid_w: float, fuse: float
) -> None:
    pump = switch_device(min_run_s=600, max_switches_per_hour=4)
    compressor = pump.model_copy(update={"kind": DeviceKind.HEAT_PUMP, "id": "hp"})
    for device in (pump, compressor):
        c = _min_run_ctx(device, grid_w, fuse)
        run, rate = inv.check_min_run_pause(c), inv.check_switch_rate(c)
        waived = grid_w > fuse and device.kind is not DeviceKind.HEAT_PUMP
        if waived:
            assert run is None
            assert rate is None
        else:
            assert run is not None
            assert run.key == "safety.min_run"
            assert rate is not None


def test_the_waiver_needs_an_ok_reading_and_a_fuse_limit() -> None:
    pump = switch_device(min_run_s=600)
    stale = _min_run_ctx(pump, 9000, 3000)
    assert stale.flows is not None
    stale = dataclasses.replace(
        stale, flows=dataclasses.replace(stale.flows, grid_w=Flow(9000, Quality.STALE))
    )
    assert inv.check_min_run_pause(stale) is not None
    assert inv.check_min_run_pause(_min_run_ctx(pump, 9000, None)) is not None
    # The waiver never covers a switch-on (min pause stays).
    on = dataclasses.replace(
        _min_run_ctx(pump, 9000, 3000),
        action=SwitchAction(on=True),
        state=DeviceState(on=False),
        runtime=runtime("pool", last_off_ts=T0 - timedelta(seconds=5)),
    )
    on = dataclasses.replace(on, device=switch_device(min_pause_s=600))
    assert inv.check_min_run_pause(on) is not None


# --- battery (G3, G4, G6) ------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(st.booleans(), st.booleans(), grid_situations)
def test_battery_grid_rules(grid_charge: bool, lock: bool, grid: GridSafety) -> None:
    can_import = grid_charge or lock
    verdict = g.check_battery_grid(grid_charge=grid_charge, can_cause_import=can_import, grid=grid)
    if grid.grid_state is GridState.ISLAND and can_import:
        assert verdict == g.REASON_ISLAND_BATTERY
    elif not grid_charge:
        assert verdict is None
    elif grid.para14a_status is not Para14aStatus.INACTIVE:
        assert verdict == g.REASON_PARA14A_GRID_CHARGE
    elif grid.island_hold_s > 0 or grid.para14a_hold_s > 0:
        assert verdict is not None
    else:
        assert verdict is None


# --- the gate ----------------------------------------------------------------------------------


class _Inputs:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.value = GateInputs()

    def set(self, **update: object) -> None:
        base = {
            "flows": flows(-3000),
            "surplus": surplus(3000),
            "ts": self.clock.now,
            "estimates": {"wallbox": PowerEstimate(value_w=0, uncertainty_w=0)},
        }
        base.update(update)
        self.value = GateInputs(**base)  # type: ignore[arg-type]

    def __call__(self) -> GateInputs:
        return self.value


def _gate(
    mode: OperatingMode = OperatingMode.AUTOMATIC,
) -> tuple[SafetyGate, FakeClock, _Inputs, list[Decision], SpyActuator]:
    clock = FakeClock()
    decisions: list[Decision] = []

    async def sink(d: Decision) -> None:
        decisions.append(d)

    device = DEVICES[1]  # "plug", flagged, switch
    actuator = SpyActuator("plug", observed=SwitchAction(on=False))
    inputs = _Inputs(clock)
    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={"plug": actuator},
        modes=StaticModes(mode, {"plug": mode}),
        devices={"plug": device}.get,
        states=lambda _: DeviceState(on=False),
        inputs=inputs,
        sleep=clock.sleep,
    )
    return gate, clock, inputs, decisions, actuator


def _submit(gate: SafetyGate, clock: FakeClock, n: int = 1) -> Decision:
    intent = make_intent(SwitchAction(on=True), asset_id="plug", ts=clock.now, intent_id=f"i{n}")
    return asyncio.run(gate.submit(intent))


def test_gate_island_then_cooldown_then_increase() -> None:
    gate, clock, inputs, _, actuator = _gate()
    inputs.set(grid_state=GridState.ISLAND)
    assert _submit(gate, clock).reasons == (g.REASON_ISLAND_NO_INCREASE,)
    clock.advance(30)
    inputs.set(grid_state=GridState.UNKNOWN)  # unknown after island: still no increase
    d = _submit(gate, clock, 2)
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.grid_cooldown"
    clock.advance(30)
    inputs.set(grid_state=GridState.CONNECTED)
    d = _submit(gate, clock, 3)
    assert d.reasons[0] == g.Reason(key="safety.grid_cooldown", args={"remaining_s": 120})
    clock.advance(SETTINGS.safe_state_cooldown_s + 1)
    inputs.set(grid_state=GridState.CONNECTED)
    assert _submit(gate, clock, 4).kind is DecisionKind.ACCEPTED
    assert len(actuator.applied) == 1


def test_gate_para14a_budget_counts_the_other_flagged_devices() -> None:
    gate, clock, inputs, _, _ = _gate()
    active = {
        "para14a_status": Para14aStatus.ACTIVE,
        "para14a_budget_w": 1500.0,
        "para14a_devices": frozenset({"plug", "wallbox"}),
    }
    inputs.set(**active, estimates={"wallbox": PowerEstimate(value_w=600, uncertainty_w=0)})
    d = _submit(gate, clock)
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0] == g.Reason(
        key="safety.para14a_limit", args={"budget_w": 1500, "total_w": 1600}
    )
    inputs.set(**active, estimates={})  # the other device's power is unknown
    assert _submit(gate, clock, 2).reasons[0] == g.REASON_PARA14A_UNKNOWN
    inputs.set(**active, estimates={"wallbox": PowerEstimate(value_w=400, uncertainty_w=50)})
    assert _submit(gate, clock, 3).kind is DecisionKind.ACCEPTED


def test_gate_para14a_end_starts_the_cooldown() -> None:
    gate, clock, inputs, _, _ = _gate()
    inputs.set(para14a_status=Para14aStatus.UNKNOWN)
    assert _submit(gate, clock).reasons[0] == g.REASON_PARA14A_UNKNOWN
    clock.advance(10)
    inputs.set(para14a_status=Para14aStatus.INACTIVE)
    assert _submit(gate, clock, 2).reasons[0].key == "safety.para14a_cooldown"
    clock.advance(SETTINGS.safe_state_cooldown_s)
    inputs.set()
    assert _submit(gate, clock, 3).kind is DecisionKind.ACCEPTED


def test_gate_shadow_logs_would_dim() -> None:
    """Shadow mode: nothing is written, the decision says what §14a would have rejected."""
    gate, clock, inputs, decisions, actuator = _gate(OperatingMode.SHADOW)
    inputs.set(para14a_status=Para14aStatus.ACTIVE, para14a_budget_w=500.0)
    d = _submit(gate, clock)
    assert d.kind is DecisionKind.SHADOW
    assert [r.key for r in d.reasons] == ["safety.shadow_mode", "safety.para14a_limit"]
    assert actuator.applied == []
    assert decisions == [d]


def test_gate_stale_snapshot_rejects_increases() -> None:
    gate, clock, inputs, _, _ = _gate()
    inputs.set()
    clock.advance(SETTINGS.inputs_max_age_ticks * SETTINGS.tick_s + 1)
    d = _submit(gate, clock)
    assert d.kind is DecisionKind.REJECTED
    assert g.REASON_GRID_UNKNOWN in d.reasons


def test_gate_memory_survives_a_rebuild() -> None:
    gate, clock, inputs, _, _ = _gate()
    inputs.set(grid_state=GridState.ISLAND)
    asyncio.run(gate.poll())
    successor = SafetyGate(
        clock=clock, sink=gate._sink, inputs=inputs, predecessor=gate, sleep=clock.sleep
    )
    assert successor.grid_memory.island_seen
    successor.seed_grid_memory(dataclasses.replace(successor.grid_memory, island_seen=False))
    assert not successor.grid_memory.island_seen
