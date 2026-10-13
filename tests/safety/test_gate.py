# SPDX-License-Identifier: Apache-2.0
"""Property tests for the Phase 1 safety gate.

The gate is hard-wired to shadow mode: no sequence of intents, emergency
stops and releases may ever reach an actuator, and every decision is
either ``shadow`` or ``rejected``. Coroutines are driven with
``asyncio.run`` so that hypothesis controls every example's event loop.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    PowerSetpointAction,
    Reason,
    SwitchAction,
)
from suncore.safety.actuator import Actuator
from suncore.safety.gate import REASON_EMERGENCY_STOP, REASON_SHADOW_MODE, SafetyGate

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class SpyActuator:
    """Records every apply call; the tests assert it stays empty."""

    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self.applied: list[Intent] = []
        self.read_backs = 0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    async def apply(self, intent: Intent) -> None:
        self.applied.append(intent)

    async def read_back(self) -> Action | None:
        self.read_backs += 1
        return None


@dataclass
class FakeClock:
    now: datetime = T0
    step: timedelta = timedelta(seconds=5)

    def __call__(self) -> datetime:
        current = self.now
        self.now = current + self.step
        return current


@dataclass
class Harness:
    gate: SafetyGate
    clock: FakeClock
    actuators: dict[str, SpyActuator]
    emitted: list[Decision] = field(default_factory=list)

    @property
    def apply_calls(self) -> int:
        return sum(len(a.applied) for a in self.actuators.values())

    @property
    def read_back_calls(self) -> int:
        return sum(a.read_backs for a in self.actuators.values())


def make_harness(asset_ids: tuple[str, ...] = ("device.a", "battery.main")) -> Harness:
    clock = FakeClock()
    actuators = {aid: SpyActuator(aid) for aid in asset_ids}
    emitted: list[Decision] = []

    async def sink(decision: Decision) -> None:
        emitted.append(decision)

    gate = SafetyGate(clock=clock, sink=sink, actuators=actuators)
    return Harness(gate=gate, clock=clock, actuators=actuators, emitted=emitted)


def make_intent(asset_id: str = "device.a", action: Action | None = None) -> Intent:
    return Intent(
        id="i-1",
        ts=T0,
        asset_id=asset_id,
        action=action or SwitchAction(on=True),
        reasons=(Reason(key="controller.surplus"),),
        origin=Origin.CONTROLLER,
    )


# --- Structural guarantees ----------------------------------------------------


def test_spy_satisfies_the_actuator_protocol() -> None:
    assert isinstance(SpyActuator("x"), Actuator)


def test_mode_is_shadow_and_cannot_be_changed() -> None:
    h = make_harness()
    assert h.gate.mode is OperatingMode.SHADOW
    assert SafetyGate.MODE is OperatingMode.SHADOW
    # No setter, no "mode" constructor parameter, no mutable mode attribute.
    assert "mode" not in inspect.signature(SafetyGate).parameters
    assert not any(name.startswith("set_mode") for name in dir(SafetyGate))
    with pytest.raises(AttributeError):
        h.gate.mode = OperatingMode.AUTOMATIC  # type: ignore[misc]
    assert not any("mode" in name for name in vars(h.gate))


def test_gate_holds_actuators_but_does_not_expose_them() -> None:
    h = make_harness()
    assert h.gate.asset_ids == frozenset({"device.a", "battery.main"})
    public = {n for n in dir(h.gate) if not n.startswith("_")}
    for name in public:
        value = getattr(h.gate, name)
        assert not isinstance(value, Actuator), name
        if isinstance(value, dict | list | tuple | set | frozenset):
            assert not any(isinstance(v, Actuator) for v in value), name


def test_actuator_mapping_is_copied_and_read_only() -> None:
    source: dict[str, Actuator] = {"device.a": SpyActuator("device.a")}
    gate = SafetyGate(clock=FakeClock(), sink=_drop, actuators=source)
    source["device.b"] = SpyActuator("device.b")
    assert gate.asset_ids == frozenset({"device.a"})


async def _drop(_: Decision) -> None:
    return None


# --- Decision semantics -------------------------------------------------------


def test_intent_in_shadow_mode_yields_shadow_decision() -> None:
    h = make_harness()
    intent = make_intent()
    decision = asyncio.run(h.gate.submit(intent))
    assert decision.kind is DecisionKind.SHADOW
    assert decision.reasons == (REASON_SHADOW_MODE,)
    assert decision.reasons[0].key == "safety.shadow_mode"
    assert decision.mode is OperatingMode.SHADOW
    assert decision.intent == intent
    assert decision.ts == T0  # from the injected clock, not the wall clock
    assert decision.wrote_hardware is False
    assert h.emitted == [decision]
    assert h.apply_calls == 0
    assert h.read_back_calls == 0


def test_emergency_stop_rejects_until_released() -> None:
    h = make_harness()
    h.gate.emergency_stop()
    assert h.gate.emergency_stopped
    rejected = asyncio.run(h.gate.submit(make_intent()))
    assert rejected.kind is DecisionKind.REJECTED
    assert rejected.reasons == (REASON_EMERGENCY_STOP,)
    assert rejected.reasons[0].key == "safety.emergency_stop"
    assert rejected.mode is OperatingMode.SHADOW
    h.gate.release()
    assert not h.gate.emergency_stopped
    shadow = asyncio.run(h.gate.submit(make_intent()))
    assert shadow.kind is DecisionKind.SHADOW
    assert h.apply_calls == 0


def test_emergency_stop_and_release_are_idempotent_from_any_state() -> None:
    h = make_harness()
    h.gate.release()  # releasing without a stop is a no-op
    assert not h.gate.emergency_stopped
    h.gate.emergency_stop()
    h.gate.emergency_stop()
    assert h.gate.emergency_stopped
    h.gate.release()
    h.gate.release()
    assert not h.gate.emergency_stopped


def test_unknown_asset_is_still_only_shadowed_or_rejected() -> None:
    h = make_harness()
    decision = asyncio.run(h.gate.submit(make_intent(asset_id="device.unknown")))
    assert decision.kind is DecisionKind.SHADOW
    assert h.apply_calls == 0


def test_decisions_use_the_injected_clock() -> None:
    h = make_harness()
    first = asyncio.run(h.gate.submit(make_intent()))
    second = asyncio.run(h.gate.submit(make_intent()))
    assert first.ts == T0
    assert second.ts == T0 + timedelta(seconds=5)


def test_naive_clock_is_refused() -> None:
    gate = SafetyGate(clock=lambda: datetime(2026, 1, 1), sink=_drop)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(gate.submit(make_intent()))


def test_sink_failure_propagates_and_writes_nothing() -> None:
    h = make_harness()

    async def failing_sink(_: Decision) -> None:
        msg = "decision log unavailable"
        raise RuntimeError(msg)

    gate = SafetyGate(clock=h.clock, sink=failing_sink, actuators=h.actuators)
    with pytest.raises(RuntimeError, match="decision log unavailable"):
        asyncio.run(gate.submit(make_intent()))
    assert h.apply_calls == 0


def test_decision_round_trips_through_json() -> None:
    h = make_harness()
    h.gate.emergency_stop()
    decision = asyncio.run(h.gate.submit(make_intent(action=PowerSetpointAction(power_w=-500.0))))
    payload = decision.model_dump_json()
    assert '"safety.emergency_stop"' in payload
    assert Decision.model_validate_json(payload) == decision


# --- Property: no sequence of operations reaches an actuator -----------------

ASSET_IDS = ("device.a", "battery.main", "device.not_registered")

actions = st.one_of(
    st.builds(SwitchAction, on=st.booleans()),
    st.builds(
        PowerSetpointAction,
        power_w=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    ),
)
intents = st.builds(
    Intent,
    id=st.text(min_size=1, max_size=8),
    ts=st.just(T0),
    asset_id=st.sampled_from(ASSET_IDS),
    action=actions,
    reasons=st.just(()),
    origin=st.sampled_from(Origin),
)

Op = tuple[Literal["submit"], Intent] | tuple[Literal["stop", "release"], None]
ops = st.one_of(
    st.tuples(st.just("submit"), intents),
    st.tuples(st.just("stop"), st.none()),
    st.tuples(st.just("release"), st.none()),
)


async def _run(h: Harness, sequence: list[Op]) -> list[tuple[bool, Decision]]:
    """Apply the sequence; return (stop_active_at_submit, decision) pairs."""
    results: list[tuple[bool, Decision]] = []
    for op, intent in sequence:
        if op == "stop":
            h.gate.emergency_stop()
        elif op == "release":
            h.gate.release()
        else:
            assert intent is not None
            stopped = h.gate.emergency_stopped
            results.append((stopped, await h.gate.submit(intent)))
    return results


@settings(max_examples=200, deadline=None)
@given(st.lists(ops, max_size=40))
def test_no_sequence_ever_reaches_an_actuator(sequence: list[Op]) -> None:
    h = make_harness(ASSET_IDS[:2])
    results = asyncio.run(_run(h, sequence))

    assert h.apply_calls == 0
    assert h.read_back_calls == 0

    submitted = [intent for op, intent in sequence if op == "submit"]
    assert len(results) == len(submitted)
    assert [d for _, d in results] == h.emitted

    for (stopped, decision), intent in zip(results, submitted, strict=True):
        assert decision.intent == intent
        assert decision.mode is OperatingMode.SHADOW
        assert decision.kind in {DecisionKind.SHADOW, DecisionKind.REJECTED}
        assert decision.kind is not DecisionKind.ACCEPTED
        assert not decision.wrote_hardware
        if stopped:
            assert decision.kind is DecisionKind.REJECTED
            assert decision.reasons == (REASON_EMERGENCY_STOP,)
        else:
            assert decision.kind is DecisionKind.SHADOW
            assert decision.reasons == (REASON_SHADOW_MODE,)

    # Final stop state equals the last stop/release operation in the sequence.
    toggles = [op for op, _ in sequence if op != "submit"]
    assert h.gate.emergency_stopped is (bool(toggles) and toggles[-1] == "stop")
