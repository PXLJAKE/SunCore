# SPDX-License-Identifier: Apache-2.0
"""Heat-pump invariants H1-H5 (PR 6-K, plan WP11): SG-Ready states, dwell, probe, lift, fail-safe.

Unit cases per rule, hypothesis properties over random actions and situations, and the gate path
of an SG-Ready device (judged since 6-K instead of rejected as not supported).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.control import DeviceState, GateInputs, PowerEstimate
from suncore.core.decisions import (
    Action,
    DecisionKind,
    ModeAction,
    ReleaseAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ComfortBand,
    DeviceConfig,
    DeviceKind,
    PowerModel,
    PowerModelKind,
    SetpointControl,
    SgContacts,
    SgReadyControl,
    behaviour_mode_capped,
)
from suncore.core.grid import GridState, Para14aStatus
from suncore.core.sgready import (
    FailsafeCause,
    contact_path,
    heat_pump_failsafe,
    sg_step_of,
)
from suncore.core.signals import Quality
from suncore.core.site import EntityRef
from suncore.safety.grid import GridSafety
from suncore.safety.invariants import InvariantContext, evaluate, expected_power_w
from suncore.safety.runtime import DeviceRuntime
from tests.safety.helpers import SETTINGS, T0, flows, make_intent, surplus
from tests.safety.test_gate import make_harness

NORMAL = SgContacts(a=False, b=False)
INTENSIFIED = SgContacts(a=False, b=True)
FORCED = SgContacts(a=True, b=True)
BLOCK = SgContacts(a=True, b=False)
MAX_C = 55.0


def sg_device(
    *,
    allow_forced: bool = False,
    probe: bool = True,
    para14a: bool = False,
    min_run_s: int = 0,
    max_switches_per_hour: int | None = None,
) -> DeviceConfig:
    entities = {
        "contact_a": EntityRef(entity_id="switch.hp_a"),
        "contact_b": EntityRef(entity_id="switch.hp_b"),
    }
    if probe:
        entities["temperature"] = EntityRef(entity_id="sensor.hp_tank")
    return DeviceConfig(
        id="hp",
        template_id="generic.heat_pump_sg_ready",
        name="Heat pump",
        kind=DeviceKind.HEAT_PUMP,
        entities=entities,
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=2000),
        control=SgReadyControl(
            kind="sg_ready",
            contact_a="contact_a",
            contact_b="contact_b",
            normal=NORMAL,
            intensified=INTENSIFIED,
            forced=FORCED,
            allow_forced=allow_forced,
        ),
        comfort=ComfortBand(min_c=40.0, max_c=MAX_C),
        para14a=para14a,
        min_run_s=min_run_s,
        max_switches_per_hour=max_switches_per_hour,
        behaviour_confirmed=True,
    )


def lift_device(*, para14a: bool = False) -> DeviceConfig:
    return DeviceConfig(
        id="dhw",
        template_id="generic.heat_pump_dhw_setpoint",
        name="Hot water",
        kind=DeviceKind.HEAT_PUMP,
        entities={"water_heater": EntityRef(entity_id="water_heater.dhw")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=1500),
        control=SetpointControl(kind="setpoint", role="water_heater", shift_max_c=8, step_c=1),
        comfort=ComfortBand(min_c=45.0, max_c=60.0),
        para14a=para14a,
    )


def ctx(
    action: Action,
    device: DeviceConfig,
    *,
    step: int | None = 0,
    temperature_c: float | None = 45.0,
    rt: DeviceRuntime | None = None,
    grid: GridSafety | None = None,
    surplus_w: float = 5000.0,
) -> InvariantContext:
    state = DeviceState(
        step=step, on=None if step is None else step > 0, temperature_c=temperature_c
    )
    if isinstance(device.control, SetpointControl):
        state = DeviceState(on=True, mode="heat", setpoint_c=50.0)
    return InvariantContext(
        action=action,
        device=device,
        state=state,
        runtime=rt or DeviceRuntime(device_id=device.id),
        now=T0,
        settings=SETTINGS,
        flows=flows(grid_w=-surplus_w),
        surplus=surplus(surplus_w),
        estimate=PowerEstimate(value_w=0.0, uncertainty_w=0.0, quality=Quality.OK),
        grid=grid or GridSafety(),
    )


def keys(reasons: tuple[object, ...]) -> list[str]:
    return [r.key for r in reasons]  # type: ignore[attr-defined]


# --- core rules ---------------------------------------------------------------------------------


def test_steps_map_to_states_and_state_1_has_none() -> None:
    control = sg_device(allow_forced=True).control
    assert isinstance(control, SgReadyControl)
    assert sg_step_of(control, NORMAL) == 0
    assert sg_step_of(control, INTENSIFIED) == 1
    assert sg_step_of(control, FORCED) == 2
    assert sg_step_of(control, BLOCK) is None
    no_forced = sg_device().control
    assert isinstance(no_forced, SgReadyControl)
    assert sg_step_of(no_forced, FORCED) is None  # state 4 is not writable without opt-in


@given(
    a=st.sampled_from([NORMAL, INTENSIFIED, FORCED, BLOCK]),
    b=st.sampled_from([NORMAL, INTENSIFIED, FORCED]),
    forced=st.booleans(),
)
def test_contact_paths_never_pass_the_block(a: SgContacts, b: SgContacts, forced: bool) -> None:
    control = sg_device(allow_forced=forced).control
    assert isinstance(control, SgReadyControl)
    path = contact_path(control, a, b)
    if path is None:
        return
    current = a
    for pair in path:
        assert (pair.a != current.a) + (pair.b != current.b) == 1  # one contact at a time
        current = pair
        if pair != b:
            assert pair in control.steps  # an intermediate pair is a writable state
        assert pair != BLOCK
    assert current == b or not path


def test_failsafe_causes() -> None:
    device = sg_device(para14a=True)
    ok = DeviceState(step=1, temperature_c=45.0)
    assert heat_pump_failsafe(device, ok, island=False, para14a_dimmed=False) is None
    assert (
        heat_pump_failsafe(device, DeviceState(step=1), island=False, para14a_dimmed=False)
        is FailsafeCause.TEMPERATURE_UNKNOWN
    )
    hot = DeviceState(step=1, temperature_c=MAX_C)
    assert heat_pump_failsafe(device, hot, island=False, para14a_dimmed=False) is (
        FailsafeCause.HEAT_LIMIT
    )
    assert heat_pump_failsafe(device, ok, island=False, para14a_dimmed=True) is (
        FailsafeCause.PARA14A
    )
    assert heat_pump_failsafe(device, ok, island=True, para14a_dimmed=False) is (
        FailsafeCause.ISLAND
    )
    unflagged = sg_device()
    assert heat_pump_failsafe(unflagged, ok, island=False, para14a_dimmed=True) is None
    assert heat_pump_failsafe(sg_device(probe=False), ok, island=False, para14a_dimmed=False) is (
        FailsafeCause.NO_PROBE
    )
    assert heat_pump_failsafe(lift_device(para14a=True), None, island=False, para14a_dimmed=True)
    assert heat_pump_failsafe(lift_device(), None, island=False, para14a_dimmed=False) is None


# --- H1 / H2 ------------------------------------------------------------------------------------


def test_h1_only_writable_states() -> None:
    device = sg_device()
    assert "safety.sg_action_invalid" in keys(evaluate(ctx(SwitchAction(on=True), device)))
    assert "safety.sg_action_invalid" in keys(evaluate(ctx(ModeAction(mode="sg:10"), device)))
    assert "safety.sg_state_invalid" in keys(evaluate(ctx(StepAction(step=3), device)))
    assert evaluate(ctx(StepAction(step=1), device)) == ()


def test_h2_state_4_only_with_allow_forced() -> None:
    assert "safety.sg_forced_not_allowed" in keys(evaluate(ctx(StepAction(step=2), sg_device())))
    assert evaluate(ctx(StepAction(step=2), sg_device(allow_forced=True))) == ()


# --- H3 -----------------------------------------------------------------------------------------


def test_h3_dwell_between_state_changes() -> None:
    device = sg_device(allow_forced=True)
    recent = DeviceRuntime(device_id="hp", last_write_ts=T0 - timedelta(seconds=300))
    reasons = evaluate(ctx(StepAction(step=2), device, step=1, rt=recent))
    assert keys(reasons) == ["safety.sg_dwell"]
    assert reasons[0].args == {"remaining_s": 300, "state": 4}
    old = DeviceRuntime(device_id="hp", last_write_ts=T0 - timedelta(seconds=600))
    assert evaluate(ctx(StepAction(step=2), device, step=1, rt=old)) == ()


# --- H4 -----------------------------------------------------------------------------------------


def test_h4_probe_needed_and_heat_limit() -> None:
    assert "safety.sg_needs_probe" in keys(
        evaluate(ctx(StepAction(step=1), sg_device(probe=False)))
    )
    assert "safety.heat_temperature_unknown" in keys(
        evaluate(ctx(StepAction(step=1), sg_device(), temperature_c=None))
    )
    assert "safety.heat_limit" in keys(
        evaluate(ctx(StepAction(step=1), sg_device(), temperature_c=MAX_C))
    )


def test_h4_lift_never_below_base_nor_outside_the_band() -> None:
    device = lift_device()
    below = SetpointAction(setpoint_c=48.0, base_c=50.0)
    assert "safety.lift_below_base" in keys(evaluate(ctx(below, device)))
    above = SetpointAction(setpoint_c=62.0, base_c=50.0)
    assert "safety.comfort_band" in keys(evaluate(ctx(above, device)))
    assert evaluate(ctx(SetpointAction(setpoint_c=55.0, base_c=50.0), device)) == ()


# --- H5 -----------------------------------------------------------------------------------------


DIMMED = GridSafety(para14a_status=Para14aStatus.ACTIVE, para14a_budget_w=10_000.0)
ISLAND = GridSafety(grid_state=GridState.ISLAND)


def test_h5_no_increase_while_dimmed_or_in_island() -> None:
    flagged = sg_device(para14a=True)
    reasons = evaluate(ctx(StepAction(step=1), flagged, grid=DIMMED))
    assert "safety.heat_pump_failsafe" in keys(reasons)
    assert "safety.heat_pump_failsafe" in keys(
        evaluate(ctx(StepAction(step=1), flagged, grid=ISLAND))
    )
    lift = SetpointAction(setpoint_c=55.0, base_c=50.0)
    assert "safety.heat_pump_failsafe" in keys(
        evaluate(ctx(lift, lift_device(para14a=True), grid=DIMMED))
    )


def test_h5_failsafe_return_ignores_dwell_min_run_and_switch_limit() -> None:
    device = sg_device(para14a=True, min_run_s=3600, max_switches_per_hour=1)
    busy = DeviceRuntime(
        device_id="hp",
        last_on_ts=T0 - timedelta(seconds=60),
        last_write_ts=T0 - timedelta(seconds=60),
        switch_times=(T0 - timedelta(seconds=60),),
    )
    down = StepAction(step=0)
    # Without a cause, the protections hold the heat pump in state 3.
    held = keys(evaluate(ctx(down, device, step=1, rt=busy)))
    assert "safety.sg_dwell" in held
    assert "safety.min_run" in held
    # Every fail-safe cause returns it to state 2 at once.
    for situation in (
        {"temperature_c": None},
        {"temperature_c": MAX_C + 1},
        {"grid": DIMMED},
        {"grid": ISLAND},
    ):
        assert evaluate(ctx(down, device, step=1, rt=busy, **situation)) == ()  # type: ignore[arg-type]


@settings(max_examples=400, deadline=None)
@given(
    step=st.integers(min_value=0, max_value=4),
    current=st.one_of(st.none(), st.integers(min_value=0, max_value=2)),
    forced=st.booleans(),
    probe=st.booleans(),
    temperature=st.one_of(st.none(), st.floats(min_value=0, max_value=90)),
    since_write=st.one_of(st.none(), st.integers(min_value=0, max_value=1200)),
    island=st.booleans(),
    dimmed=st.booleans(),
)
def test_property_h1_h5(  # noqa: PLR0917
    step: int,
    current: int | None,
    forced: bool,
    probe: bool,
    temperature: float | None,
    since_write: int | None,
    island: bool,
    dimmed: bool,
) -> None:
    device = sg_device(allow_forced=forced, probe=probe, para14a=True)
    rt = DeviceRuntime(
        device_id="hp",
        last_write_ts=None if since_write is None else T0 - timedelta(seconds=since_write),
    )
    grid = GridSafety(
        grid_state=GridState.ISLAND if island else GridState.CONNECTED,
        para14a_status=Para14aStatus.ACTIVE if dimmed else Para14aStatus.INACTIVE,
        para14a_budget_w=100_000.0,
    )
    reasons = evaluate(
        ctx(
            StepAction(step=step), device, step=current, temperature_c=temperature, rt=rt, grid=grid
        )
    )
    if not reasons:
        # H1/H2: only writable states pass, state 4 only with the opt-in.
        assert step in (0, 1) or (step == 2 and forced)
        if step > 0 and current != step:
            assert probe  # H4: a probe and a temperature below the maximum
            assert temperature is not None
            assert temperature < MAX_C
            assert not island
            assert not dimmed
            assert since_write is None or since_write >= 600 or current == step  # H3
    # A release is never judged here.
    assert evaluate(ctx(ReleaseAction(), device, step=current, rt=rt, grid=grid)) == ()


def test_expected_power_of_sg_steps() -> None:
    device = sg_device(allow_forced=True)
    assert expected_power_w(StepAction(step=0), device, None) == 0.0
    assert expected_power_w(StepAction(step=1), device, None) == 2000.0
    assert expected_power_w(StepAction(step=2), device, None) == 2000.0


# --- the gate -----------------------------------------------------------------------------------


def test_gate_writes_state_3_on_surplus_and_rejects_state_1() -> None:
    device = sg_device()
    h = make_harness(
        devices=(device,),
        observed={"hp": StepAction(step=0)},
        inputs=GateInputs(flows=flows(), surplus=surplus(5000.0), ts=T0),
    )
    h.states["hp"] = DeviceState(step=0, on=False, temperature_c=45.0)
    decision = asyncio.run(h.gate.submit(make_intent(StepAction(step=1), asset_id="hp")))
    assert decision.kind is DecisionKind.ACCEPTED, decision.reasons
    bad = asyncio.run(h.gate.submit(make_intent(StepAction(step=3), asset_id="hp")))
    assert bad.kind is DecisionKind.REJECTED


def test_gate_unconfirmed_sg_device_is_capped_at_assisted() -> None:
    device = sg_device().model_copy(update={"behaviour_confirmed": False})
    h = make_harness(devices=(device,), observed={"hp": StepAction(step=0)})
    h.states["hp"] = DeviceState(step=0, on=False, temperature_c=45.0)
    decision = asyncio.run(h.gate.submit(make_intent(StepAction(step=1), asset_id="hp")))
    assert decision.kind is DecisionKind.PROPOSED


def test_restart_hysteresis_after_the_comfort_maximum() -> None:
    from suncore.core.sgready import SG_RESTART_K, sg_restart_c  # noqa: PLC0415

    device = sg_device()
    assert device.comfort is not None
    restart = device.comfort.max_c - SG_RESTART_K
    assert sg_restart_c(device, DeviceState(step=0, temperature_c=restart + 0.5)) == restart
    assert sg_restart_c(device, DeviceState(step=0, temperature_c=restart)) is None
    assert sg_restart_c(device, DeviceState(step=0)) is None  # unknown: a fail-safe cause
    assert sg_restart_c(lift_device(), DeviceState(temperature_c=60.0)) is None


def test_gate_unconfirmed_lift_is_capped_at_assisted() -> None:
    """Review 6-K A2: the heat pump's set-point lift is a Phase 6 family as well."""
    device = lift_device()
    assert behaviour_mode_capped(device)
    assert not behaviour_mode_capped(device.model_copy(update={"behaviour_confirmed": True}))
    h = make_harness(
        devices=(device,),
        observed={"dhw": SetpointAction(setpoint_c=50.0, base_c=50.0)},
        inputs=GateInputs(flows=flows(), surplus=surplus(5000.0), ts=T0),
    )
    h.states["dhw"] = DeviceState(on=True, mode="heat", setpoint_c=50.0)
    lift = SetpointAction(setpoint_c=52.0, base_c=50.0)
    decision = asyncio.run(h.gate.submit(make_intent(lift, asset_id="dhw")))
    assert decision.kind is DecisionKind.PROPOSED


def test_h4_lift_base_is_the_users_baseline_not_the_controllers_base() -> None:
    """Review 6-K A3: a controller ``base_c`` below the user's set-point does not lower H4."""
    device = lift_device()
    rt = DeviceRuntime(
        device_id="dhw",
        baseline=SetpointAction(setpoint_c=50.0, base_c=50.0),
        base_setpoint_c=46.0,  # what the gate copied from the controller's last intent
    )
    sneaky = SetpointAction(setpoint_c=47.0, base_c=46.0)
    assert "safety.lift_below_base" in keys(evaluate(ctx(sneaky, device, rt=rt)))
    assert evaluate(ctx(SetpointAction(setpoint_c=52.0, base_c=46.0), device, rt=rt)) == ()
    # without a set-point baseline the runtime's base applies
    plain = DeviceRuntime(device_id="dhw", base_setpoint_c=50.0)
    assert "safety.lift_below_base" in keys(evaluate(ctx(sneaky, device, rt=plain)))


def test_a_lift_never_switches_the_heat_pump_on() -> None:
    """Review 6-K A5: ``allow_switch_on`` is a climate option; a lift device rejects it."""
    payload = {
        **lift_device().model_dump(mode="json"),
        "allow_switch_on": True,
        "switch_on_hvac_mode": "heat",
    }
    with pytest.raises(ValidationError, match="allow_switch_on"):
        DeviceConfig.model_validate(payload)
