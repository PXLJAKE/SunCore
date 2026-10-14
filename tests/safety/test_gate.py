# SPDX-License-Identifier: Apache-2.0
"""Tests for the Phase 3 safety gate: modes, proposals, holds, write path, fail-safe.

Coroutines run under ``asyncio.run`` so hypothesis controls each example's loop. The
clock and the settle wait are injected; nothing here touches the wall clock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.control import ControlSettings, DeviceState
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    ReleaseAction,
    SetpointAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig, OverridePolicy
from suncore.core.signals import Quality, Sample
from suncore.core.site import EntityRef, GridConfig, PvArray, SiteConfig
from suncore.core.units import Unit
from suncore.safety.actuator import Actuator
from suncore.safety.gate import (
    MAX_CONSECUTIVE_FAILURES,
    REASON_EMERGENCY_STOP,
    REASON_SHADOW_MODE,
    GateInputs,
    SafetyGate,
    Sleeper,
)
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore
from suncore.safety.watchdog import SITE_SCOPE, ScopeState
from tests.safety.helpers import (
    T0,
    FakeClock,
    SpyActuator,
    climate_device,
    flows,
    make_intent,
    surplus,
    switch_device,
)

AUTO = OperatingMode.AUTOMATIC
ASSISTED = OperatingMode.ASSISTED
SHADOW = OperatingMode.SHADOW


@dataclass
class Harness:
    gate: SafetyGate
    clock: FakeClock
    actuators: dict[str, SpyActuator]
    modes: StaticModes
    states: dict[str, DeviceState | None]
    store: MemoryRuntimeStore
    inputs: GateInputs
    emitted: list[Decision] = field(default_factory=list)

    @property
    def apply_calls(self) -> int:
        return sum(len(a.applied) for a in self.actuators.values())

    @property
    def release_calls(self) -> int:
        return sum(len(a.released) for a in self.actuators.values())

    def kinds(self) -> list[str]:
        return [d.kind.value for d in self.emitted]


def make_harness(
    devices: tuple[DeviceConfig, ...] = (switch_device(), climate_device()),
    *,
    global_mode: OperatingMode = AUTO,
    device_mode: OperatingMode = AUTO,
    store: MemoryRuntimeStore | None = None,
    settings: ControlSettings | None = None,
    inputs: GateInputs | None = None,
    observed: dict[str, Action | None] | None = None,
    modes: StaticModes | None = None,
    sleep: Sleeper | None = None,
) -> Harness:
    clock = FakeClock()
    actuators = {d.id: SpyActuator(d.id) for d in devices}
    for d in devices:
        actuators[d.id].observed = (observed or {}).get(d.id, SwitchAction(on=False))
    states: dict[str, DeviceState | None] = {
        d.id: DeviceState(on=False, mode="off") for d in devices
    }
    by_id = {d.id: d for d in devices}
    modes = (
        modes if modes is not None else StaticModes(global_mode, dict.fromkeys(by_id, device_mode))
    )
    store = store if store is not None else MemoryRuntimeStore()
    emitted: list[Decision] = []
    gate_inputs = (
        inputs if inputs is not None else GateInputs(flows=flows(), surplus=surplus(5000.0, 100.0))
    )

    async def sink(decision: Decision) -> None:
        emitted.append(decision)

    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators=actuators,
        modes=modes,
        devices=by_id.get,
        states=states.get,
        inputs=lambda: gate_inputs,
        store=store,
        settings=settings or ControlSettings(min_write_interval_s=5.0, default_settle_s=2.0),
        sleep=sleep if sleep is not None else clock.sleep,
    )
    return Harness(gate, clock, actuators, modes, states, store, gate_inputs, emitted)


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _drop(_: Decision) -> None:
    return None


# --- defaults: no provider, no store -> shadow and nothing written ---------------------


def test_spy_satisfies_the_actuator_protocol() -> None:
    assert isinstance(SpyActuator("x"), Actuator)


def test_default_gate_is_all_shadow() -> None:
    gate = SafetyGate(clock=FakeClock(), sink=_drop, actuators={"pool": SpyActuator("pool")})
    assert gate.mode is SHADOW
    assert gate.modes_of("pool").effective is SHADOW
    assert gate.modes_of("unknown").effective is SHADOW
    d = asyncio.run(gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.SHADOW
    assert d.reasons == (REASON_SHADOW_MODE,)
    assert d.mode is SHADOW
    assert d.schema_version == 2


def test_gate_holds_actuators_but_does_not_expose_them() -> None:
    h = make_harness()
    assert h.gate.asset_ids == frozenset({"pool", "ac"})
    for name in (n for n in dir(h.gate) if not n.startswith("_")):
        value = getattr(h.gate, name)
        assert not isinstance(value, Actuator), name


def test_actuator_mapping_is_copied_and_read_only() -> None:
    source: dict[str, Actuator] = {"pool": SpyActuator("pool")}
    gate = SafetyGate(clock=FakeClock(), sink=_drop, actuators=source)
    source["other"] = SpyActuator("other")
    assert gate.asset_ids == frozenset({"pool"})


def test_naive_clock_is_refused() -> None:
    gate = SafetyGate(clock=lambda: datetime(2026, 1, 1), sink=_drop)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(gate.submit(make_intent(SwitchAction(on=True))))


def test_sink_failure_propagates_and_writes_nothing() -> None:
    h = make_harness()

    async def failing_sink(_: Decision) -> None:
        msg = "decision log unavailable"
        raise RuntimeError(msg)

    gate = SafetyGate(clock=h.clock, sink=failing_sink, actuators=h.actuators)
    with pytest.raises(RuntimeError, match="decision log unavailable"):
        asyncio.run(gate.submit(make_intent(SwitchAction(on=True))))
    assert h.apply_calls == 0


def test_shadow_decisions_carry_the_invariants_that_would_have_fired() -> None:
    h = make_harness(
        global_mode=SHADOW, inputs=GateInputs(flows=flows(grid_w=300.0), surplus=surplus(0.0))
    )
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.SHADOW
    assert [r.key for r in d.reasons] == ["safety.shadow_mode", "safety.grid_importing"]
    assert h.apply_calls == 0


# --- effective mode ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("global_mode", "device_mode", "expected"),
    [
        (SHADOW, AUTO, DecisionKind.SHADOW),
        (AUTO, SHADOW, DecisionKind.SHADOW),
        (ASSISTED, AUTO, DecisionKind.PROPOSED),
        (AUTO, ASSISTED, DecisionKind.PROPOSED),
        (AUTO, AUTO, DecisionKind.ACCEPTED),
    ],
)
def test_effective_mode_is_the_minimum(
    global_mode: OperatingMode, device_mode: OperatingMode, expected: DecisionKind
) -> None:
    h = make_harness(global_mode=global_mode, device_mode=device_mode)
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is expected
    assert h.apply_calls == (1 if expected is DecisionKind.ACCEPTED else 0)


def test_unknown_device_in_automatic_is_rejected() -> None:
    h = make_harness(modes=StaticModes(AUTO, {"ghost": AUTO}))
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True), asset_id="ghost")))
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.unknown_device"


# --- the automatic write path ------------------------------------------------------------


def test_accepted_write_records_baseline_switch_and_read_back() -> None:
    h = make_harness()
    intent = make_intent(SwitchAction(on=True))
    d = asyncio.run(h.gate.submit(intent))
    assert d.kind is DecisionKind.ACCEPTED
    assert d.wrote_hardware
    assert d.observed == SwitchAction(on=True)
    assert d.mode is AUTO
    assert d.reasons == intent.reasons
    assert d.ts == T0 + timedelta(seconds=2)  # after the injected settle wait
    spy = h.actuators["pool"]
    assert spy.applied == [intent]
    assert spy.read_backs == 2  # baseline + read-back
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert rt.baseline == SwitchAction(on=False)
    assert rt.under_control
    assert rt.last_written == SwitchAction(on=True)
    assert rt.last_write_ts == T0
    assert rt.last_write_context_id == "ctx-1"
    assert rt.last_on_ts == T0
    assert rt.switch_times == (T0,)
    assert asyncio.run(h.store.load_runtime("pool")) == rt


def test_write_only_on_change_is_rejected_before_the_actuator() -> None:
    h = make_harness()
    h.states["pool"] = DeviceState(on=True)
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.write_no_change"
    assert h.apply_calls == 0


def test_invariant_violation_rejects_with_every_reason() -> None:
    h = make_harness(inputs=GateInputs(flows=flows(grid_w=400.0), surplus=surplus(0.0)))
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.REJECTED
    assert "safety.grid_importing" in [r.key for r in d.reasons]
    assert h.apply_calls == 0


def test_second_write_within_the_interval_is_rate_limited() -> None:
    h = make_harness()  # interval 5 s, settle 2 s: the second write comes 2 s after the first

    async def two() -> tuple[Decision, Decision]:
        first = await h.gate.submit(make_intent(SwitchAction(on=True)))
        h.states["pool"] = DeviceState(on=True)
        second = await h.gate.submit(
            make_intent(SwitchAction(on=False), intent_id="i-2", ts=h.clock.now)
        )
        return first, second

    first, second = asyncio.run(two())
    assert first.kind is DecisionKind.ACCEPTED
    assert second.kind is DecisionKind.REJECTED
    assert second.reasons[0].key == "safety.rate_limited"
    assert h.apply_calls == 1


def test_stale_intent_is_dropped_first() -> None:
    h = make_harness()
    old = make_intent(SwitchAction(on=True), ts=T0 - timedelta(seconds=31))
    d = asyncio.run(h.gate.submit(old))
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.intent_stale"
    assert h.apply_calls == 0


def test_missing_actuator_is_rejected() -> None:
    h = make_harness()
    gate = SafetyGate(
        clock=h.clock,
        sink=_drop,
        modes=h.modes,
        devices={"pool": switch_device()}.get,
        states=h.states.get,
        inputs=lambda: h.inputs,
    )
    d = asyncio.run(gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.no_actuator"


def test_unknown_state_at_takeover_is_rejected_and_frees_the_slot() -> None:
    h = make_harness(observed={"pool": None})
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.state_unknown"
    assert h.apply_calls == 0
    h.actuators["pool"].observed = SwitchAction(on=False)
    again = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-2")))
    assert again.kind is DecisionKind.ACCEPTED  # the rate-limit slot was not consumed


def test_write_failure_yields_failed_and_three_in_a_row_trip_the_device() -> None:
    h = make_harness()
    h.actuators["pool"].fail_apply = RuntimeError("boom")
    decisions: list[Decision] = []

    async def go() -> None:
        for n in range(MAX_CONSECUTIVE_FAILURES):
            decisions.append(
                await h.gate.submit(
                    make_intent(SwitchAction(on=True), intent_id=f"i-{n}", ts=h.clock.now)
                )
            )
            h.clock.advance(5)

    asyncio.run(go())
    assert [d.kind for d in decisions] == [DecisionKind.FAILED] * MAX_CONSECUTIVE_FAILURES
    assert decisions[0].reasons[0].key == "safety.write_failed"
    assert decisions[0].wrote_hardware
    assert h.gate.watchdog.state("pool", h.clock.now) is ScopeState.SAFE
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    paused = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-x", ts=h.clock.now))
    )
    assert paused.kind is DecisionKind.REJECTED
    assert paused.reasons[0].key == "safety.repeated_failures"
    assert "ac" not in [d.intent.asset_id for d in h.emitted if d.kind is DecisionKind.ACCEPTED]


def test_read_back_mismatch_is_failed_with_the_observed_state() -> None:
    h = make_harness()
    h.actuators["pool"].follow = False  # the device ignores the write
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.FAILED
    assert d.reasons[0].key == "safety.readback_mismatch"
    assert d.observed == SwitchAction(on=False)
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert rt.last_written is None
    assert rt.last_write_ts == T0


def test_setpoint_write_records_the_base_setpoint() -> None:
    h = make_harness(observed={"ac": SetpointAction(setpoint_c=22.0, base_c=22.0)})
    h.states["ac"] = DeviceState(on=True, mode="cool", setpoint_c=22.0)
    intent = make_intent(SetpointAction(setpoint_c=23.0, base_c=22.0), asset_id="ac")
    d = asyncio.run(h.gate.submit(intent))
    assert d.kind is DecisionKind.ACCEPTED
    rt = h.gate.runtime_of("ac")
    assert rt is not None
    assert rt.base_setpoint_c == 22.0
    assert rt.switch_times == ()  # a set-point shift is not a switch


# --- assisted mode: proposals ---------------------------------------------------------


def test_assisted_proposes_then_confirm_writes_as_the_user() -> None:
    h = make_harness(global_mode=ASSISTED)
    proposed = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert proposed.kind is DecisionKind.PROPOSED
    assert proposed.expires_at == T0 + timedelta(seconds=120)
    assert proposed.reasons[0].key == "safety.proposed"
    assert [p.intent.id for p in h.gate.proposals()] == ["i-1"]
    assert h.apply_calls == 0
    h.clock.advance(30)
    confirmed = asyncio.run(h.gate.confirm("i-1"))
    assert confirmed is not None
    assert confirmed.kind is DecisionKind.ACCEPTED
    assert confirmed.intent.origin is Origin.USER
    assert confirmed.intent.reasons[-1].key == "safety.confirmed"
    assert h.apply_calls == 1
    assert h.gate.proposals() == ()
    assert asyncio.run(h.gate.confirm("i-1")) is None


def test_expired_proposal_is_rejected_on_confirm_and_hidden_from_the_list() -> None:
    h = make_harness(global_mode=ASSISTED)
    asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    h.clock.advance(121)
    assert h.gate.proposals() == ()
    d = asyncio.run(h.gate.confirm("i-1"))
    assert d is not None
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.proposal_expired"
    assert h.apply_calls == 0


def test_dismiss_drops_a_proposal() -> None:
    h = make_harness(global_mode=ASSISTED)
    asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert h.gate.dismiss("i-1")
    assert not h.gate.dismiss("i-1")
    assert h.gate.proposals() == ()


def test_user_intent_in_assisted_mode_writes_directly() -> None:
    h = make_harness(global_mode=ASSISTED)
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True), origin=Origin.USER)))
    assert d.kind is DecisionKind.ACCEPTED


# --- overrides -------------------------------------------------------------------------


def _take_over(h: Harness, device: str = "pool") -> None:
    d = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), asset_id=device, ts=h.clock.now))
    )
    assert d.kind is DecisionKind.ACCEPTED
    h.states[device] = DeviceState(on=True)


def test_remote_switch_off_holds_the_device_until_next_day() -> None:
    h = make_harness()
    _take_over(h)
    h.clock.advance(60)
    verdict = asyncio.run(h.gate.observe("pool", SwitchAction(on=False)))
    assert verdict.override
    assert verdict.reason is not None
    assert verdict.reason.key == "safety.override_off"
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert rt.override
    assert rt.override_until == datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    h.states["pool"] = DeviceState(on=False)
    held = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-2", ts=h.clock.now))
    )
    assert held.kind is DecisionKind.HELD
    assert held.held_until == rt.override_until
    assert held.reasons[0].key == "safety.override_held"
    assert h.apply_calls == 1
    # The user's own intent passes the hold; resume now clears it.
    asyncio.run(h.gate.resume("pool"))
    h.clock.advance(5)
    again = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-3", ts=h.clock.now))
    )
    assert again.kind is DecisionKind.ACCEPTED


def test_hold_expires_by_itself() -> None:
    h = make_harness(
        devices=(
            switch_device().model_copy(
                update={"override_policy": OverridePolicy(kind="hours", hours=2)}
            ),
        )
    )
    _take_over(h)
    h.clock.advance(60)
    asyncio.run(h.gate.observe("pool", SwitchAction(on=False)))
    h.states["pool"] = DeviceState(on=False)
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert rt.override_until == h.clock.now + timedelta(hours=2)
    h.clock.advance(2 * 3600 + 1)
    d = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-2", ts=h.clock.now))
    )
    assert d.kind is DecisionKind.ACCEPTED
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert not rt.override


def test_our_context_id_is_never_an_override() -> None:
    h = make_harness()
    _take_over(h)
    h.clock.advance(60)
    verdict = asyncio.run(h.gate.observe("pool", SwitchAction(on=False), context_id="ctx-1"))
    assert not verdict.override


def test_observation_inside_settle_time_is_not_judged() -> None:
    async def no_wait(_: float) -> None:
        return None

    h = make_harness(sleep=no_wait)  # the clock does not move during the settle
    _take_over(h)
    h.clock.advance(1)  # settle is 2 s
    assert not asyncio.run(h.gate.observe("pool", SwitchAction(on=False))).override
    h.clock.advance(2)
    assert asyncio.run(h.gate.observe("pool", SwitchAction(on=False))).override


def test_changed_temperature_becomes_the_new_base() -> None:
    h = make_harness(observed={"ac": SetpointAction(setpoint_c=22.0, base_c=22.0)})
    h.states["ac"] = DeviceState(on=True, mode="cool", setpoint_c=22.0)
    asyncio.run(
        h.gate.submit(make_intent(SetpointAction(setpoint_c=23.0, base_c=22.0), asset_id="ac"))
    )
    h.clock.advance(60)
    verdict = asyncio.run(h.gate.observe("ac", SetpointAction(setpoint_c=25.0, base_c=22.0)))
    assert verdict.override
    assert verdict.new_base_setpoint_c == 25.0
    rt = h.gate.runtime_of("ac")
    assert rt is not None
    assert rt.base_setpoint_c == 25.0


def test_explicit_user_hold_and_resume() -> None:
    h = make_harness()
    until = T0 + timedelta(minutes=30)
    rt = asyncio.run(h.gate.hold("pool", until))
    assert rt.override
    assert rt.override_until == until
    d = asyncio.run(h.gate.submit(make_intent(SwitchAction(on=True))))
    assert d.kind is DecisionKind.HELD
    assert asyncio.run(h.gate.resume("pool")).override is False
    assert asyncio.run(h.gate.hold("pool", None)).is_held(T0 + timedelta(days=30))


# --- release paths ------------------------------------------------------------------------


def test_emergency_stop_releases_persists_and_rejects_until_cleared() -> None:
    h = make_harness()
    _take_over(h)
    asyncio.run(h.gate.emergency_stop())
    assert h.gate.emergency_stopped
    assert asyncio.run(h.store.load_emergency_stop()) is True
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    assert h.actuators["ac"].released == []  # never under control
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert not rt.under_control
    assert rt.last_written is None
    assert rt.last_off_ts == h.clock.now
    assert len(rt.switch_times) == 2
    released = h.emitted[-1]
    assert released.kind is DecisionKind.ACCEPTED
    assert isinstance(released.intent.action, ReleaseAction)
    assert released.intent.origin is Origin.USER
    assert [r.key for r in released.reasons] == ["safety.released", "safety.emergency_stop"]
    rejected = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-2", ts=h.clock.now))
    )
    assert rejected.kind is DecisionKind.REJECTED
    assert rejected.reasons == (REASON_EMERGENCY_STOP,)
    asyncio.run(h.gate.emergency_stop())  # idempotent
    asyncio.run(h.gate.clear_emergency_stop())
    assert not h.gate.emergency_stopped
    assert asyncio.run(h.store.load_emergency_stop()) is False


def test_emergency_stop_survives_a_restart_through_the_store() -> None:
    h = make_harness()
    _take_over(h)
    asyncio.run(h.gate.emergency_stop())
    fresh = make_harness(store=h.store)
    asyncio.run(fresh.gate.recover())
    assert fresh.gate.emergency_stopped


def test_crash_recovery_releases_devices_left_under_control() -> None:
    store = MemoryRuntimeStore()
    asyncio.run(
        store.save_runtime(
            DeviceRuntime(
                device_id="pool",
                baseline=SwitchAction(on=False),
                baseline_ts=T0 - timedelta(minutes=5),
                last_written=SwitchAction(on=True),
                last_write_ts=T0 - timedelta(minutes=5),
            )
        )
    )
    h = make_harness(store=store)
    asyncio.run(h.gate.recover())
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert not rt.under_control
    assert h.emitted[-1].intent.origin is Origin.SAFETY
    assert h.emitted[-1].reasons[1].key == "safety.crash_recovery"


def test_shutdown_releases_within_the_deadline_and_reports_a_hung_driver() -> None:
    h = make_harness(
        settings=ControlSettings(
            release_deadline_s=1.0, min_write_interval_s=1.0, default_settle_s=0
        )
    )
    _take_over(h)
    h.actuators["pool"].hang_release = True
    asyncio.run(h.gate.shutdown())
    assert h.actuators["pool"].released == []
    failed = h.emitted[-1]
    assert failed.kind is DecisionKind.FAILED
    assert [r.key for r in failed.reasons] == ["safety.release_timeout", "safety.shutdown"]


def test_release_failure_is_reported_and_keeps_the_device_under_control() -> None:
    h = make_harness()
    _take_over(h)
    h.actuators["pool"].fail_release = RuntimeError("no")
    asyncio.run(h.gate.shutdown())
    failed = h.emitted[-1]
    assert failed.kind is DecisionKind.FAILED
    assert failed.reasons[0].key == "safety.release_failed"
    rt = h.gate.runtime_of("pool")
    assert rt is not None
    assert rt.under_control


def test_release_intent_in_automatic_mode_restores_the_baseline() -> None:
    h = make_harness()
    _take_over(h)
    d = asyncio.run(h.gate.submit(make_intent(ReleaseAction(), intent_id="i-2", ts=h.clock.now)))
    assert d.kind is DecisionKind.ACCEPTED
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    again = asyncio.run(
        h.gate.submit(make_intent(ReleaseAction(), intent_id="i-3", ts=h.clock.now))
    )
    assert again.kind is DecisionKind.REJECTED
    assert again.reasons[0].key == "safety.not_under_control"


def test_release_of_a_device_without_actuator_is_rejected() -> None:
    store = MemoryRuntimeStore()
    asyncio.run(
        store.save_runtime(DeviceRuntime(device_id="pool", baseline=SwitchAction(on=False)))
    )
    gate = SafetyGate(clock=FakeClock(), sink=_drop, store=store)
    d = asyncio.run(gate.release_device("pool", REASON_SHADOW_MODE))
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.no_actuator"


def test_mode_change_to_shadow_releases_the_device() -> None:
    h = make_harness()
    _take_over(h)
    h.modes.set_device("pool", SHADOW)
    asyncio.run(h.gate.sync_modes())
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    assert h.emitted[-1].reasons[1].key == "safety.mode_shadow"
    h.modes.set_global(SHADOW)
    asyncio.run(h.gate.sync_modes())  # nothing left under control: no second release
    assert len(h.actuators["pool"].released) == 1


# --- watchdog integration ----------------------------------------------------------------


def _site() -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
        pv=[PvArray(name="PV", power=EntityRef(entity_id="sensor.pv"))],
    )


def _samples(now: datetime, grid: float | None = -1000.0, pv: float = 3000.0) -> dict[str, Sample]:
    out = {"sensor.pv": Sample(pv, Unit.W, now)}
    if grid is not None:
        out["sensor.grid"] = Sample(grid, Unit.W, now)
    return out


def test_site_fault_releases_everything_and_pauses_until_the_cooldown_ends() -> None:
    h = make_harness(
        settings=ControlSettings(
            min_write_interval_s=1.0, default_settle_s=0, safe_state_cooldown_s=60
        )
    )
    _take_over(h)
    faults = asyncio.run(h.gate.check_site(_site(), _samples(h.clock.now, grid=None)))
    assert [f.kind.value for f in faults] == ["unavailable"]
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    h.states["pool"] = DeviceState(on=False)
    d = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-2", ts=h.clock.now))
    )
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.watchdog_unavailable"
    h.clock.advance(5)
    assert asyncio.run(h.gate.check_site(_site(), _samples(h.clock.now))) == ()
    assert h.gate.watchdog.state(SITE_SCOPE, h.clock.now) is ScopeState.COOLDOWN
    d = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-3", ts=h.clock.now))
    )
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.watchdog_cooldown"
    h.clock.advance(61)
    d = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=True), intent_id="i-4", ts=h.clock.now))
    )
    assert d.kind is DecisionKind.ACCEPTED
    assert len(h.actuators["pool"].released) == 1  # a trip releases once, not every tick


def test_device_fault_pauses_and_releases_only_that_device() -> None:
    h = make_harness(settings=ControlSettings(min_write_interval_s=1.0, default_settle_s=0))
    _take_over(h)
    h.clock.advance(5)
    faults = asyncio.run(h.gate.check_device("pool", {}, DeviceState(quality=Quality.UNAVAILABLE)))
    assert faults
    assert faults[0].kind.value == "unavailable"
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    assert h.gate.watchdog.is_paused("pool", h.clock.now)
    assert not h.gate.watchdog.is_paused("ac", h.clock.now)
    assert asyncio.run(h.gate.check_device("ac", {}, DeviceState(on=False))) == ()


def test_connection_loss_pauses_and_restore_releases() -> None:
    h = make_harness()
    _take_over(h)
    asyncio.run(h.gate.connection_lost())
    d = asyncio.run(
        h.gate.submit(make_intent(SwitchAction(on=False), intent_id="i-2", ts=h.clock.now))
    )
    assert d.kind is DecisionKind.REJECTED
    assert d.reasons[0].key == "safety.connection_lost"
    assert h.actuators["pool"].released == []  # nothing can be written while offline
    asyncio.run(h.gate.connection_restored())
    assert h.actuators["pool"].released == [SwitchAction(on=False)]
    assert h.gate.watchdog.state(SITE_SCOPE, h.clock.now) is ScopeState.COOLDOWN


# --- property: shadow/assisted never write, the emergency stop always rejects ------------

ASSET_IDS = ("pool", "ac", "ghost")
intents = st.builds(
    Intent,
    id=st.text(min_size=1, max_size=6),
    ts=st.just(T0),
    asset_id=st.sampled_from(ASSET_IDS),
    action=st.one_of(
        st.builds(SwitchAction, on=st.booleans()),
        st.builds(SetpointAction, setpoint_c=st.floats(15, 30), base_c=st.floats(15, 30)),
        st.just(ReleaseAction()),
    ),
    reasons=st.just(()),
    origin=st.sampled_from(Origin),
)
Op = tuple[Literal["submit"], Intent] | tuple[Literal["stop", "clear"], None]
ops = st.one_of(
    st.tuples(st.just("submit"), intents),
    st.tuples(st.just("stop"), st.none()),
    st.tuples(st.just("clear"), st.none()),
)


async def _run(h: Harness, sequence: list[Op]) -> list[tuple[bool, Decision]]:
    results: list[tuple[bool, Decision]] = []
    for op, intent in sequence:
        if op == "stop":
            await h.gate.emergency_stop()
        elif op == "clear":
            await h.gate.clear_emergency_stop()
        else:
            assert intent is not None
            results.append((h.gate.emergency_stopped, await h.gate.submit(intent)))
    return results


@settings(max_examples=150, deadline=None)
@given(st.lists(ops, max_size=30), st.sampled_from([SHADOW, ASSISTED]))
def test_shadow_and_assisted_never_reach_an_actuator(
    sequence: list[Op], mode: OperatingMode
) -> None:
    """Shadow never writes; assisted writes only what the user explicitly submitted."""
    h = make_harness(global_mode=mode)
    results = asyncio.run(_run(h, sequence))
    assert all(d in h.emitted for _, d in results)  # releases are emitted as well
    applied = [i for a in h.actuators.values() for i in a.applied]
    if mode is SHADOW:
        assert h.apply_calls == 0
        assert h.release_calls == 0
    else:
        assert all(i.origin is Origin.USER for i in applied)
    for stopped, decision in results:
        if stopped:
            assert decision.kind is DecisionKind.REJECTED
            assert decision.reasons == (REASON_EMERGENCY_STOP,)
            assert not decision.wrote_hardware
        elif h.gate.modes_of(decision.intent.asset_id).effective is SHADOW:
            assert decision.kind is DecisionKind.SHADOW
            assert decision.reasons[0] == REASON_SHADOW_MODE
            assert not decision.wrote_hardware
        elif decision.intent.origin is not Origin.USER and not isinstance(
            decision.intent.action, ReleaseAction
        ):
            assert decision.kind in (DecisionKind.PROPOSED, DecisionKind.REJECTED)
            assert not decision.wrote_hardware
    toggles = [op for op, _ in sequence if op != "submit"]
    assert h.gate.emergency_stopped is (bool(toggles) and toggles[-1] == "stop")


@settings(max_examples=100, deadline=None)
@given(st.lists(ops, max_size=30))
def test_automatic_never_writes_under_emergency_stop_or_a_violation(sequence: list[Op]) -> None:
    h = make_harness()
    results = asyncio.run(_run(h, sequence))
    for stopped, decision in results:
        if stopped:
            assert decision.kind is DecisionKind.REJECTED
            assert not decision.wrote_hardware
        if decision.kind is DecisionKind.ACCEPTED and not isinstance(
            decision.intent.action, ReleaseAction
        ):
            # Accepted writes happened only in automatic mode with a known device.
            assert decision.mode is AUTO
            assert decision.intent.asset_id in ("pool", "ac")
    applied = [i for a in h.actuators.values() for i in a.applied]
    accepted_or_failed = [
        d.intent
        for d in h.emitted
        if d.wrote_hardware and not isinstance(d.intent.action, ReleaseAction)
    ]
    assert applied == accepted_or_failed


def test_flow_helper_shape() -> None:
    f = flows()
    assert isinstance(f, FlowSnapshot)
    assert isinstance(f.grid_w, Flow)
