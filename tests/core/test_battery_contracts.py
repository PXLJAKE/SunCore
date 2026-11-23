# SPDX-License-Identifier: Apache-2.0
"""ADR-0013 contracts (WP10 step 1): ``None`` = baseline, battery identity, modbus link."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.battery import (
    BATTERY_ACTION_FIELDS,
    FIELD_CAPABILITY,
    BatteryAction,
    BatteryCapability,
    BatterySettings,
    FieldRange,
    ModbusByteOrder,
    ModbusLink,
    action_fields,
    resolve_battery_action,
    round_up_to_step,
)
from suncore.core.config import OperationConfig
from suncore.core.decisions import OperatingMode
from suncore.core.identity import battery_identity, battery_identity_token
from suncore.core.site import BatteryConfig, EntityRef, GridConfig, SiteConfig

limit = st.one_of(st.none(), st.floats(min_value=0, max_value=20_000, allow_nan=False))
soc = st.one_of(st.none(), st.floats(min_value=0, max_value=100, allow_nan=False))


@st.composite
def actions(draw: st.DrawFn) -> BatteryAction:
    values = {
        "max_charge_w": draw(limit),
        "max_discharge_w": draw(limit),
        "min_soc_pct": draw(soc),
        "grid_charge_w": draw(limit),
    }
    if all(v is None for v in values.values()):
        values["min_soc_pct"] = 20.0
    grid, cap = values["grid_charge_w"], values["max_charge_w"]
    if grid is not None and cap is not None and grid > cap:
        values["grid_charge_w"] = cap
    return BatteryAction(**values)


def test_every_field_has_a_capability() -> None:
    assert set(FIELD_CAPABILITY) == set(BATTERY_ACTION_FIELDS)
    assert BatteryCapability.HARDWARE_FALLBACK not in FIELD_CAPABILITY.values()


def test_an_empty_action_is_rejected_release_means_baseline() -> None:
    with pytest.raises(ValidationError):
        BatteryAction()


@given(actions(), actions())
def test_none_is_the_baseline_and_set_fields_win(
    action: BatteryAction, base: BatteryAction
) -> None:
    resolved = resolve_battery_action(action, base)
    for name, value in action_fields(action).items():
        if name in ("max_charge_w", "grid_charge_w") and value is None:
            continue  # the cap/forced-power pair keeps the validator (see the test below)
        expected = value if value is not None else getattr(base, name)
        assert getattr(resolved, name) == expected


@given(actions())
def test_resolving_without_a_baseline_changes_nothing(action: BatteryAction) -> None:
    assert resolve_battery_action(action, None) == action


def test_an_inherited_cap_below_the_forced_power_is_dropped() -> None:
    resolved = resolve_battery_action(
        BatteryAction(grid_charge_w=3000), BatteryAction(max_charge_w=1000, min_soc_pct=10)
    )
    assert resolved == BatteryAction(grid_charge_w=3000, min_soc_pct=10)


def test_an_inherited_forced_power_above_the_requested_cap_is_lowered() -> None:
    resolved = resolve_battery_action(BatteryAction(max_charge_w=0), BatteryAction(grid_charge_w=1))
    assert resolved == BatteryAction(max_charge_w=0, grid_charge_w=0)


@given(actions(), actions())
def test_the_inherited_values_never_exceed_the_set_ones(
    action: BatteryAction, base: BatteryAction
) -> None:
    resolved = resolve_battery_action(action, base)
    if action.max_charge_w is not None:
        assert resolved.max_charge_w == action.max_charge_w
    if action.grid_charge_w is not None:
        assert resolved.grid_charge_w == action.grid_charge_w


@given(st.floats(0, 100), st.sampled_from([None, 1.0, 5.0, 10.0]))
def test_rounding_never_lowers_a_floor(value: float, step: float | None) -> None:
    rounded = round_up_to_step(value, step)
    assert rounded >= value - 1e-6
    if step is not None:
        assert rounded - value < step + 1e-6


def test_field_range_contains() -> None:
    r = FieldRange(5, 100, 5)
    assert r.contains(5)
    assert r.contains(100)
    assert not r.contains(4.9)


def _site(soc: str = "sensor.soc", power: str = "sensor.bat") -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
        battery=BatteryConfig(power=EntityRef(entity_id=power), soc=EntityRef(entity_id=soc)),
    )


def _battery(**kw: object) -> BatterySettings:
    base: dict[str, object] = {
        "adapter_template_id": "kostal.plenticore_rest",
        "entities": {"min_soc_number": EntityRef(entity_id="number.min_soc")},
    }
    return BatterySettings.model_validate(base | kw)


def test_observe_only_has_no_identity() -> None:
    assert battery_identity(BatterySettings(), _site()) is None
    assert battery_identity_token(None, _site()) is None


def test_identity_changes_with_template_entities_site_entities_and_modbus_unit() -> None:
    base = battery_identity_token(_battery(), _site())
    assert base is not None
    assert battery_identity_token(_battery(), _site()) == base
    assert battery_identity_token(_battery(adapter_template_id="generic.battery"), _site()) != base
    other_entity = {"min_soc_number": EntityRef(entity_id="number.other")}
    assert battery_identity_token(_battery(entities=other_entity), _site()) != base
    assert battery_identity_token(_battery(), _site(soc="sensor.soc2")) != base
    assert battery_identity_token(_battery(), _site(power="sensor.bat2")) != base
    link = ModbusLink(slave=71, byte_order=ModbusByteOrder.LITTLE)
    with_link = battery_identity_token(_battery(modbus=link), _site())
    assert with_link != base
    other_unit = battery_identity_token(
        _battery(modbus=link.model_copy(update={"slave": 1})), _site()
    )
    assert other_unit != with_link


def test_settings_are_not_identity() -> None:
    base = battery_identity_token(_battery(), _site())
    link = ModbusLink(slave=71, byte_order=ModbusByteOrder.LITTLE)
    a = battery_identity_token(_battery(modbus=link, fallback_measured=False), _site())
    b = battery_identity_token(
        _battery(modbus=link.model_copy(update={"byte_order": ModbusByteOrder.BIG})), _site()
    )
    assert a == b
    measured = battery_identity_token(_battery(modbus=link, fallback_measured=True), _site())
    assert measured != a  # lifts the assisted cap: identity (review minor 5)
    assert base != a
    inverted = _site().model_copy(
        update={
            "battery": BatteryConfig(
                power=EntityRef(entity_id="sensor.bat", invert_sign=True),
                soc=EntityRef(entity_id="sensor.soc"),
                min_soc_pct=20,
            )
        }
    )
    assert battery_identity_token(_battery(), inverted) == base


def test_modbus_link_bounds() -> None:
    with pytest.raises(ValidationError):
        ModbusLink(slave=71, byte_order=ModbusByteOrder.LITTLE, heartbeat_s=30)  # > timeout / 3
    with pytest.raises(ValidationError):
        ModbusLink(slave=0, byte_order=ModbusByteOrder.LITTLE)
    with pytest.raises(ValidationError):
        ModbusLink.model_validate({"slave": 71})  # the byte order is never guessed
    with pytest.raises(ValidationError):
        BatterySettings(modbus=ModbusLink(slave=71, byte_order=ModbusByteOrder.BIG))


def test_operation_battery_mode_defaults_to_shadow_and_old_documents_load() -> None:
    assert OperationConfig().battery_mode is OperatingMode.SHADOW
    old = OperationConfig.model_validate({"schema_version": 1, "mode": "automatic"})
    assert old.battery_mode is OperatingMode.SHADOW
    raised = old.model_copy(update={"battery_mode": OperatingMode.ASSISTED})
    assert raised.without_battery_mode().battery_mode is OperatingMode.SHADOW
    assert old.without_battery_mode() is old
