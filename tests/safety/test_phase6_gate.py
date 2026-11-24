# SPDX-License-Identifier: Apache-2.0
"""PR 6-B: the Phase 6 action kinds and device families are rejected before mode handling.

ADR-0011 addendum: every ``ActionKind`` is either judged by invariants or rejected with its own
``safety.<kind>_not_supported`` key - in every mode, for every origin, with and without a known
device - and nothing is written, released or proposed. Devices of the new control variants (and
job-driven programs) get every action but a release rejected; their mode is capped at assisted
until the behaviour is confirmed (plan §0 decision 17).

PR 6-I moved ``charge`` from "not supported" to "judged" (``safety.flex``, E1-E9): a charger
takes only ``charge`` actions (and a release), and only a charger takes them.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import DeviceState
from suncore.core.decisions import (
    NOT_YET_SUPPORTED_KINDS,
    Action,
    ActionKind,
    BatteryAction,
    ChargeAction,
    Decision,
    DecisionKind,
    InverterLimitAction,
    LoadpointAction,
    ModeAction,
    OperatingMode,
    Origin,
    PowerSetpointAction,
    ReleaseAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ChargerControl,
    DeviceConfig,
    DeviceKind,
    LoadpointControl,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    ProgramSpec,
    SgContacts,
    SgReadyControl,
)
from suncore.core.site import EntityRef
from suncore.devices.drivers import create_driver
from suncore.devices.drivers.unsupported import UnsupportedDriver
from suncore.safety.gate import REASON_EMERGENCY_STOP, REASON_NOT_SUPPORTED
from suncore.safety.invariants import action_matches_state, action_turns_on, expected_power_w
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore
from tests.devices.drivers.fakes import FakeHA
from tests.safety.helpers import T0, make_intent, switch_device
from tests.safety.test_gate import AUTO, make_harness

CHARGE_ACTIONS: tuple[Action, ...] = (
    ChargeAction(current_a=16, phases=3),
    ChargeAction(current_a=0),
    ChargeAction(current_a=10, enabled=False),
)
LOADPOINT_ACTIONS: tuple[Action, ...] = (
    LoadpointAction(mode="now"),
    LoadpointAction(mode="smart", plan_soc_pct=80, plan_time=T0 + timedelta(hours=8)),
    LoadpointAction(mode="off"),
)
NEW_ACTIONS: tuple[Action, ...] = (
    InverterLimitAction(max_export_w=0),
    InverterLimitAction(max_ac_pct=60),
)

_PM = PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_000)


def charger_device(device_id: str = "wallbox", *, confirmed: bool = False) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="generic.wallbox",
        name="Wallbox",
        kind=DeviceKind.WALLBOX,
        entities={"current": EntityRef(entity_id=f"number.{device_id}_current")},
        power_model=_PM,
        control=ChargerControl(kind="charger", role="current", max_current_a=16),
        behaviour_confirmed=confirmed,
    )


def loadpoint_device() -> DeviceConfig:
    return DeviceConfig(
        id="evcc_lp",
        template_id="evcc.loadpoint",
        name="evcc",
        kind=DeviceKind.WALLBOX,
        power_model=_PM,
        control=LoadpointControl(kind="loadpoint", loadpoint_id=1),
    )


def sg_device() -> DeviceConfig:
    return DeviceConfig(
        id="hp",
        template_id="generic.heat_pump_sg_ready",
        name="Heat pump",
        kind=DeviceKind.HEAT_PUMP,
        entities={
            "sg_a": EntityRef(entity_id="switch.sg_a"),
            "sg_b": EntityRef(entity_id="switch.sg_b"),
        },
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=3000),
        control=SgReadyControl(
            kind="sg_ready",
            contact_a="sg_a",
            contact_b="sg_b",
            normal=SgContacts(a=False, b=False),
            intensified=SgContacts(a=True, b=False),
        ),
    )


def program_device() -> DeviceConfig:
    return DeviceConfig(
        id="washer",
        template_id="generic.program_plug",
        name="Washer",
        kind=DeviceKind.PROGRAM,
        entities={"switch": EntityRef(entity_id="switch.washer")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=2000),
        control=OnOffControl(kind="on_off", role="switch"),
        program=ProgramSpec(runtime_min=120, start_method="plug", resume_confirmed=True),
    )


PHASE6_DEVICES = (sg_device(),)
"""The Phase 6 device families still without a write path (the charger has one since 6-I, the
evcc loadpoint since 6-J, programs since 6-L: ``tests/safety/test_program_gate.py``)."""
EXPECTED_DEVICE_REASON = {
    "hp": "safety.sg_ready_not_supported",
}

DEVICE_ACTIONS: tuple[Action, ...] = (
    SwitchAction(on=True),
    SwitchAction(on=False),
    StepAction(step=1),
    StepAction(step=0),
    ModeAction(mode="heat"),
    PowerSetpointAction(power_w=2000),
    SetpointAction(setpoint_c=55, base_c=50),
    *CHARGE_ACTIONS,
    *LOADPOINT_ACTIONS,
    *NEW_ACTIONS,
)


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def test_every_action_kind_is_handled_or_rejected() -> None:
    """The ADR-0011 addendum test: every kind has a known fate (no silent fall-through)."""
    judged = {
        ActionKind.SWITCH,
        ActionKind.POWER_SETPOINT,
        ActionKind.SETPOINT,
        ActionKind.STEP,
        ActionKind.MODE,
        ActionKind.RELEASE,
        ActionKind.BATTERY,  # safety.battery (B1-B9), not_supported without an adapter
        ActionKind.CHARGE,  # safety.flex (E1-E9), PR 6-I
        ActionKind.LOADPOINT,  # safety.evcc (L1-L5), PR 6-J
    }
    assert judged | NOT_YET_SUPPORTED_KINDS == set(ActionKind)
    assert judged.isdisjoint(NOT_YET_SUPPORTED_KINDS)
    assert set(REASON_NOT_SUPPORTED) == NOT_YET_SUPPORTED_KINDS
    for kind, reason in REASON_NOT_SUPPORTED.items():
        assert reason.key == f"safety.{kind.value}_not_supported"
    assert {a.kind for a in NEW_ACTIONS} == NOT_YET_SUPPORTED_KINDS
    assert ActionKind.CHARGE not in REASON_NOT_SUPPORTED
    assert ActionKind.LOADPOINT not in REASON_NOT_SUPPORTED


@pytest.mark.parametrize("action", CHARGE_ACTIONS)
def test_charge_helpers_on_a_non_charger_are_explicit(action: ChargeAction) -> None:
    """A charge on another control is rejected by the gate; the helpers never guess power."""
    device = switch_device()
    assert action_turns_on(action, device) is (action.effective_current_a > 0)
    assert expected_power_w(action, device, None) is None
    assert action_matches_state(action, DeviceState(on=True), device) is False


@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(CHARGE_ACTIONS),
    origin=st.sampled_from(Origin),
    mode=st.sampled_from(OperatingMode),
    asset_id=st.sampled_from(("pool", "hp")),
)
def test_a_charge_on_a_non_charger_is_rejected_without_side_effects(
    action: Action, origin: Origin, mode: OperatingMode, asset_id: str
) -> None:
    h = make_harness(
        devices=(switch_device(), charger_device(), sg_device()),
        global_mode=mode,
        device_mode=mode,
    )
    decision = asyncio.run(h.gate.submit(make_intent(action, asset_id=asset_id, origin=origin)))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.charge_not_charger"]
    assert h.apply_calls == 0
    assert h.gate.proposals() == ()


@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(DEVICE_ACTIONS[:7]),
    origin=st.sampled_from(Origin),
    mode=st.sampled_from(OperatingMode),
)
def test_a_charger_rejects_every_action_but_charge_and_release(
    action: Action, origin: Origin, mode: OperatingMode
) -> None:
    h = make_harness(devices=(charger_device(),), global_mode=mode, device_mode=mode)
    decision = asyncio.run(h.gate.submit(make_intent(action, asset_id="wallbox", origin=origin)))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.charger_needs_charge"]
    assert h.apply_calls == 0


@pytest.mark.parametrize("action", [*NEW_ACTIONS, *LOADPOINT_ACTIONS], ids=repr)
def test_invariant_helpers_treat_new_kinds_explicitly(action: Action) -> None:
    device = switch_device()
    # a loadpoint's mode is a switch (off <-> on) since the 6-J re-review (R1)
    expected = action.mode != "off" if isinstance(action, LoadpointAction) else None
    assert action_turns_on(action, device) is expected
    assert expected_power_w(action, device, None) is None
    assert action_matches_state(action, DeviceState(on=True), device) is False


@settings(max_examples=150, deadline=None)
@given(
    action=st.sampled_from(NEW_ACTIONS),
    origin=st.sampled_from(Origin),
    global_mode=st.sampled_from(OperatingMode),
    device_mode=st.sampled_from(OperatingMode),
    stopped=st.booleans(),
    asset_id=st.sampled_from(("pool", "ghost", "battery", "inverter", "wallbox", "hp")),
)  # the charge kind is judged since 6-I: see the two tests above
def test_new_action_kinds_are_always_rejected_without_side_effects(  # noqa: PLR0917
    action: Action,
    origin: Origin,
    global_mode: OperatingMode,
    device_mode: OperatingMode,
    stopped: bool,
    asset_id: str,
) -> None:
    h = make_harness(
        devices=(switch_device(), charger_device(), sg_device()),
        global_mode=global_mode,
        device_mode=device_mode,
    )

    async def scenario() -> tuple[Decision, int]:
        if stopped:
            await h.gate.emergency_stop()
        releases = h.release_calls
        before = tuple(await h.store.all_runtimes())
        decision = await h.gate.submit(make_intent(action, asset_id=asset_id, origin=origin))
        assert tuple(await h.store.all_runtimes()) == before
        return decision, releases

    decision, releases = asyncio.run(scenario())
    assert decision.kind is DecisionKind.REJECTED
    assert not decision.wrote_hardware
    expected = REASON_EMERGENCY_STOP if stopped else REASON_NOT_SUPPORTED[action.kind]
    assert decision.reasons == (expected,)
    assert h.apply_calls == 0
    assert h.release_calls == releases
    assert h.gate.proposals() == ()


@settings(max_examples=150, deadline=None)
@given(
    device=st.sampled_from(PHASE6_DEVICES),
    action=st.sampled_from(DEVICE_ACTIONS),
    origin=st.sampled_from(Origin),
    mode=st.sampled_from(OperatingMode),
)
def test_phase6_devices_reject_every_action_but_a_release(
    device: DeviceConfig, action: Action, origin: Origin, mode: OperatingMode
) -> None:
    h = make_harness(devices=(device,), global_mode=mode, device_mode=mode)
    decision = asyncio.run(h.gate.submit(make_intent(action, asset_id=device.id, origin=origin)))
    assert decision.kind is DecisionKind.REJECTED
    if action.kind in NOT_YET_SUPPORTED_KINDS:
        assert keys(decision) == [REASON_NOT_SUPPORTED[action.kind].key]
    elif isinstance(action, ChargeAction):
        assert keys(decision) == ["safety.charge_not_charger"]
    elif isinstance(action, LoadpointAction) and device.id != "hp":
        assert keys(decision) == ["safety.loadpoint_not_loadpoint"]
    else:
        assert keys(decision) == [EXPECTED_DEVICE_REASON[device.id]]
    assert h.apply_calls == 0
    assert h.gate.proposals() == ()


@pytest.mark.parametrize(
    "device", [charger_device(), loadpoint_device(), *PHASE6_DEVICES], ids=lambda d: d.id
)
def test_a_release_of_a_phase6_device_writes_nothing(device: DeviceConfig) -> None:
    h = make_harness(devices=(device,))
    decision = asyncio.run(h.gate.submit(make_intent(ReleaseAction(), asset_id=device.id)))
    assert not any(r.key.endswith("_not_supported") for r in decision.reasons)
    assert h.apply_calls == 0


@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(DEVICE_ACTIONS),
    origin=st.sampled_from(Origin),
    mode=st.sampled_from(OperatingMode),
)
def test_a_loadpoint_rejects_every_action_but_loadpoint_and_release(
    action: Action, origin: Origin, mode: OperatingMode
) -> None:
    """6-J: an evcc loadpoint takes only ``loadpoint`` actions; the rest never reaches a driver."""
    if isinstance(action, LoadpointAction):
        return
    h = make_harness(devices=(loadpoint_device(),), global_mode=mode, device_mode=mode)
    decision = asyncio.run(h.gate.submit(make_intent(action, asset_id="evcc_lp", origin=origin)))
    assert decision.kind is DecisionKind.REJECTED
    expected = (
        REASON_NOT_SUPPORTED[action.kind].key
        if action.kind in NOT_YET_SUPPORTED_KINDS
        else "safety.charge_not_charger"
        if isinstance(action, ChargeAction)
        else "safety.loadpoint_needs_loadpoint"
    )
    assert keys(decision) == [expected]
    assert h.apply_calls == 0


@settings(max_examples=60, deadline=None)
@given(
    action=st.sampled_from(LOADPOINT_ACTIONS),
    origin=st.sampled_from(Origin),
    mode=st.sampled_from(OperatingMode),
)
def test_a_loadpoint_action_on_another_device_is_rejected(
    action: Action, origin: Origin, mode: OperatingMode
) -> None:
    h = make_harness(
        devices=(switch_device(), charger_device(), loadpoint_device()),
        global_mode=mode,
        device_mode=mode,
    )
    for asset_id, expected in (
        ("pool", "safety.loadpoint_not_loadpoint"),
        ("wallbox", "safety.charger_needs_charge"),
    ):
        decision = asyncio.run(h.gate.submit(make_intent(action, asset_id=asset_id, origin=origin)))
        assert decision.kind is DecisionKind.REJECTED
        assert keys(decision) == [expected]
    assert h.apply_calls == 0
    assert h.gate.proposals() == ()


def test_sg_ready_step_is_rejected_even_in_shadow() -> None:
    h = make_harness(devices=(sg_device(),), global_mode=OperatingMode.SHADOW)
    decision = asyncio.run(h.gate.submit(make_intent(StepAction(step=1), asset_id="hp")))
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.sg_ready_not_supported"]


def test_phase3_devices_are_unaffected() -> None:
    h = make_harness()
    decision = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert decision.kind is DecisionKind.ACCEPTED
    assert h.gate.modes_of("pool").effective is AUTO


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        (charger_device(), OperatingMode.ASSISTED),
        (charger_device(confirmed=True), AUTO),
        (sg_device(), OperatingMode.ASSISTED),
        (loadpoint_device(), OperatingMode.ASSISTED),
        (program_device(), OperatingMode.ASSISTED),
        (switch_device(), AUTO),
    ],
    ids=lambda v: getattr(v, "id", str(v)),
)
def test_unconfirmed_phase6_devices_are_capped_at_assisted(
    device: DeviceConfig, expected: OperatingMode
) -> None:
    h = make_harness(devices=(device,))
    assert h.gate.modes_of(device.id).effective is expected
    shadow = make_harness(devices=(device,), device_mode=OperatingMode.SHADOW)
    assert shadow.gate.modes_of(device.id).effective is OperatingMode.SHADOW  # never raised


def test_battery_actions_keep_their_own_rejection() -> None:
    h = make_harness()
    decision = asyncio.run(h.gate.submit(make_intent(BatteryAction(min_soc_pct=20))))
    assert keys(decision) == ["safety.battery_not_supported"]


@pytest.mark.parametrize(
    "device", [d for d in PHASE6_DEVICES if d.program is None], ids=lambda d: d.id
)
def test_a_release_with_a_baseline_fails_and_keeps_the_runtime(device: DeviceConfig) -> None:
    """Review A1: the placeholder driver cannot restore a baseline, so the release is
    ``failed`` and the runtime (baseline included) is kept instead of silently dropped."""
    ha = FakeHA()
    driver = create_driver(device, write=ha.write, states=ha.lookup)
    assert isinstance(driver, UnsupportedDriver)
    store = MemoryRuntimeStore()
    runtime = DeviceRuntime(
        device_id=device.id, baseline=SwitchAction(on=False), last_written=SwitchAction(on=True)
    )
    h = make_harness(devices=(device,), store=store, actuator_overrides={device.id: driver})

    async def scenario() -> Decision:
        await store.save_runtime(runtime)
        return await h.gate.release_device(device.id, REASON_EMERGENCY_STOP)

    decision = asyncio.run(scenario())
    assert decision.kind is DecisionKind.FAILED
    assert keys(decision)[0] == "safety.release_failed"
    kept = asyncio.run(store.load_runtime(device.id))
    assert kept is not None
    assert kept.baseline == SwitchAction(on=False)
    assert ha.calls == []
