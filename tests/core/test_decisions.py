# SPDX-License-Identifier: Apache-2.0
"""Contract tests for suncore.core.decisions: immutability, validation, JSON."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from suncore.core.decisions import (
    Action,
    ActionKind,
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    PowerSetpointAction,
    Reason,
    SwitchAction,
    is_reason_key,
)

TS = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def make_intent(**overrides: object) -> Intent:
    fields: dict[str, object] = {
        "id": "intent-1",
        "ts": TS,
        "asset_id": "device.heat_pump",
        "action": SwitchAction(on=True),
        "reasons": (Reason(key="controller.surplus", args={"surplus_w": 1500}),),
        "origin": Origin.CONTROLLER,
    }
    fields.update(overrides)
    return Intent.model_validate(fields)


# --- Reason ---------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["safety.shadow_mode", "controller.surplus.available", "a.b_c2", "x1.y"],
)
def test_reason_accepts_dotted_lowercase_keys(key: str) -> None:
    assert Reason(key=key).key == key
    assert is_reason_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "shadow",
        "Safety.Shadow",
        "safety.",
        ".safety",
        "safety..x",
        "safety.Mode",
        "a b.c",
        "1a.b",
    ],
)
def test_reason_rejects_free_text_and_malformed_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        Reason(key=key)
    assert not is_reason_key(key)


def test_reason_is_frozen_and_forbids_extra_fields() -> None:
    reason = Reason(key="safety.shadow_mode")
    with pytest.raises(ValidationError):
        reason.key = "other.key"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Reason.model_validate({"key": "safety.shadow_mode", "text": "free text"})


def test_reason_args_accept_scalars_only() -> None:
    reason = Reason(key="a.b", args={"s": "x", "i": 1, "f": 1.5, "b": True})
    assert dict(reason.args) == {"s": "x", "i": 1, "f": 1.5, "b": True}
    with pytest.raises(ValidationError):
        Reason.model_validate({"key": "a.b", "args": {"nested": {"x": 1}}})
    with pytest.raises(ValidationError):
        Reason.model_validate({"key": "a.b", "args": {"items": [1, 2]}})


# --- Enums ------------------------------------------------------------------


def test_enums_are_stable_strings() -> None:
    assert [m.value for m in OperatingMode] == ["shadow", "assisted", "automatic"]
    assert [m.value for m in DecisionKind] == ["accepted", "rejected", "shadow"]
    assert [m.value for m in Origin] == ["planner", "controller", "user"]
    assert [m.value for m in ActionKind] == ["switch", "power_setpoint"]


# --- Actions ----------------------------------------------------------------


def test_action_union_discriminates_on_kind() -> None:
    adapter: TypeAdapter[Action] = TypeAdapter(Action)
    switch = adapter.validate_python({"kind": "switch", "on": False})
    assert isinstance(switch, SwitchAction)
    power = adapter.validate_python({"kind": "power_setpoint", "power_w": 2500.0})
    assert isinstance(power, PowerSetpointAction)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "teleport", "on": True})
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "switch", "power_w": 1.0})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_power_setpoint_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValidationError):
        PowerSetpointAction(power_w=bad)


# --- Intent -----------------------------------------------------------------


def test_intent_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        make_intent(ts=datetime(2026, 10, 12, 12, 0))  # noqa: DTZ001 - naive on purpose


@pytest.mark.parametrize("field", ["id", "asset_id"])
def test_intent_rejects_empty_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        make_intent(**{field: ""})


def test_intent_is_frozen() -> None:
    intent = make_intent()
    with pytest.raises(ValidationError):
        intent.asset_id = "other"  # type: ignore[misc]


def test_intent_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_intent(note="free text")


# --- Decision ---------------------------------------------------------------


def test_decision_wrote_hardware_only_when_accepted() -> None:
    intent = make_intent()
    for kind in DecisionKind:
        decision = Decision(intent=intent, kind=kind, ts=TS, mode=OperatingMode.SHADOW)
        assert decision.wrote_hardware is (kind is DecisionKind.ACCEPTED)


def test_decision_json_round_trip_is_lossless() -> None:
    intent = make_intent(action=PowerSetpointAction(power_w=-1200.5))
    decision = Decision(
        intent=intent,
        kind=DecisionKind.SHADOW,
        ts=TS,
        reasons=(Reason(key="safety.shadow_mode"),),
        mode=OperatingMode.SHADOW,
    )
    payload = decision.model_dump_json()
    parsed = json.loads(payload)
    assert parsed["kind"] == "shadow"
    assert parsed["mode"] == "shadow"
    assert parsed["intent"]["action"] == {"kind": "power_setpoint", "power_w": -1200.5}
    assert Decision.model_validate_json(payload) == decision


# --- Property: any valid decision survives JSON -----------------------------

reason_keys = st.from_regex(r"[a-z][a-z0-9_]{0,8}(\.[a-z][a-z0-9_]{0,8}){1,3}", fullmatch=True)
scalars = st.one_of(
    st.text(max_size=20),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.booleans(),
)
reasons = st.builds(
    Reason,
    key=reason_keys,
    args=st.dictionaries(st.text(min_size=1, max_size=10), scalars, max_size=4),
)
aware_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 - hypothesis adds the zone
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)
actions = st.one_of(
    st.builds(SwitchAction, on=st.booleans()),
    st.builds(
        PowerSetpointAction,
        power_w=st.floats(allow_nan=False, allow_infinity=False, width=32),
    ),
)
intents = st.builds(
    Intent,
    id=st.text(min_size=1, max_size=16),
    ts=aware_datetimes,
    asset_id=st.text(min_size=1, max_size=16),
    action=actions,
    reasons=st.tuples(reasons) | st.just(()),
    origin=st.sampled_from(Origin),
)
decisions = st.builds(
    Decision,
    intent=intents,
    kind=st.sampled_from(DecisionKind),
    ts=aware_datetimes,
    reasons=st.lists(reasons, max_size=3).map(tuple),
    mode=st.sampled_from(OperatingMode),
)


@given(decisions)
def test_any_decision_round_trips_through_json(decision: Decision) -> None:
    restored = Decision.model_validate_json(decision.model_dump_json())
    assert restored == decision
    assert restored.model_dump() == decision.model_dump()
