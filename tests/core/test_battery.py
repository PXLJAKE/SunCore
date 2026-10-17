# SPDX-License-Identifier: Apache-2.0
"""Battery model, strategies, grid-charge policy, risk levels and the ``battery`` section."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from suncore.core import battery
from suncore.core.battery import (
    BatteryAction,
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    CalibrationCharge,
    GridChargePolicy,
    RiskLevel,
)
from suncore.core.decisions import Action, ActionKind
from suncore.core.decisions import BatteryAction as CoreBatteryAction
from suncore.core.site import EntityRef
from suncore.core.tariff import NIGHT_WINDOW

BERLIN = ZoneInfo("Europe/Berlin")


def model(**kw: float) -> BatteryModel:
    base: dict[str, float] = {
        "capacity_wh": 29_600,
        "max_charge_w": 10_000,
        "max_discharge_w": 10_000,
    }
    return BatteryModel.model_validate(base | kw)


def test_defaults_are_the_maintainers_answers() -> None:
    m = model()
    assert (m.charge_efficiency, m.discharge_efficiency) == (0.95, 0.95)
    assert m.degradation_cost_per_kwh == 0.04
    assert m.grid_charge_margin_per_kwh == 0.03
    assert m.bms_drift_margin_pct == 5.0
    assert m.reserve_pct == 10.0
    assert m.max_soc_pct == 100.0
    assert m.min_soc_pct == 15.0
    assert m.round_trip_efficiency == pytest.approx(0.9025)
    assert m.usable_wh == pytest.approx(29_600 * 0.85)
    assert m.soc_to_wh(50) == 14_800
    assert m.wh_to_soc(14_800) == 50


def test_corridor_must_stay_open() -> None:
    with pytest.raises(ValidationError, match="must be below max_soc_pct"):
        model(reserve_pct=60, bms_drift_margin_pct=5, max_soc_pct=65)
    for bad in ({"charge_efficiency": 0}, {"charge_efficiency": 1.1}, {"capacity_wh": 0}):
        with pytest.raises(ValidationError):
            model(**bad)


def test_spread_rule() -> None:
    m = model()
    threshold = (0.20 + 0.04 + 0.03) / 0.9025
    assert m.min_later_price(0.20) == pytest.approx(threshold)
    assert m.grid_charge_pays(0.20, threshold + 0.001)
    assert not m.grid_charge_pays(0.20, threshold)


@given(charge=st.floats(-0.5, 1.0), later=st.floats(-0.5, 2.0))
def test_grid_charging_never_pays_below_charge_price(charge: float, later: float) -> None:
    if model().grid_charge_pays(charge, later):
        assert later > charge


def test_strategies_and_risk_levels() -> None:
    assert [s.value for s in BatteryStrategy] == [
        "self_consumption",
        "prefer_full",
        "target_by_time",
    ]
    assert [(r.value, r.probability, r.quantile) for r in RiskLevel] == [
        ("cautious", 0.9, 0.1),
        ("balanced", 0.75, 0.25),
        ("bold", 0.5, 0.5),
    ]


def test_grid_charge_policy() -> None:
    off = GridChargePolicy()
    friday_night = datetime(2026, 10, 16, 23, tzinfo=BERLIN)
    noon = friday_night.replace(hour=12)
    assert not off.allows(friday_night, 0.0, BERLIN)
    with pytest.raises(ValidationError, match="price level or a schedule"):
        GridChargePolicy(enabled=True)
    level = GridChargePolicy(enabled=True, max_price=0.15)
    assert level.allows(noon, 0.15, BERLIN)
    assert not level.allows(noon, 0.16, BERLIN)
    assert not level.allows(noon, None, BERLIN)  # unknown price never satisfies a level
    sched = GridChargePolicy(enabled=True, schedule=NIGHT_WINDOW)
    assert sched.allows(friday_night, None, BERLIN)
    assert not sched.allows(noon, 0.0, BERLIN)
    both = GridChargePolicy(enabled=True, max_price=-0.01, schedule=NIGHT_WINDOW)
    assert both.allows(friday_night, -0.05, BERLIN)
    assert not both.allows(friday_night, 0.0, BERLIN)
    assert GridChargePolicy(max_price=0.1).enabled is False  # a level alone enables nothing


def test_calibration_is_off_by_default() -> None:
    c = CalibrationCharge()
    assert (c.enabled, c.interval_days, c.target_soc_pct) == (False, 30, 100.0)
    with pytest.raises(ValidationError):
        CalibrationCharge(interval_days=0)


def test_battery_settings_defaults_and_rules() -> None:
    s = BatterySettings()
    assert s.schema_version == 1
    assert s.observe_only
    assert s.strategy is BatteryStrategy.SELF_CONSUMPTION
    assert s.risk is RiskLevel.CAUTIOUS
    assert s.morning_buffer_pct is None
    assert s.bridge_next_day is True
    assert s.autarky_weight_per_kwh == 0.01
    assert not s.grid_charge.enabled
    assert not s.calibration.enabled
    assert s.entity_refs() == []
    with pytest.raises(ValidationError, match="adapter template"):
        BatterySettings(entities={"min_soc_number": EntityRef(entity_id="number.min_soc")})
    with pytest.raises(ValidationError, match="more than once"):
        BatterySettings(
            adapter_template_id="generic.battery",
            entities={
                "a": EntityRef(entity_id="number.x"),
                "b": EntityRef(entity_id="number.x"),
            },
        )
    for bad in ({"autarky_weight_per_kwh": 1.5}, {"morning_buffer_pct": 101}, {"risk": "yolo"}):
        with pytest.raises(ValidationError):
            BatterySettings.model_validate(bad)
    s2 = BatterySettings(
        adapter_template_id="generic.battery",
        entities={"min_soc_number": EntityRef(entity_id="number.min_soc")},
        model=model(),
        strategy=BatteryStrategy.PREFER_FULL,
    )
    assert not s2.observe_only
    assert [r.entity_id for r in s2.entity_refs()] == ["number.min_soc"]
    assert BatterySettings.model_validate_json(s2.model_dump_json()) == s2


def test_battery_action_contract() -> None:
    assert battery.BatteryAction is CoreBatteryAction
    a = BatteryAction(min_soc_pct=20)
    assert a.kind is ActionKind.BATTERY
    parsed = TypeAdapter(Action).validate_python({"kind": "battery", "grid_charge_w": 3000})
    assert isinstance(parsed, BatteryAction)
    assert parsed.grid_charge_w == 3000
    with pytest.raises(ValidationError, match="at least one limit"):
        BatteryAction()
    for bad in (
        {"max_charge_w": -1},
        {"max_discharge_w": float("inf")},
        {"min_soc_pct": 101},
        {"grid_charge_w": -5},
    ):
        with pytest.raises(ValidationError):
            BatteryAction.model_validate(bad)


def test_battery_action_grid_charge_never_above_the_charge_cap() -> None:
    """m7: forced grid charging is bounded by ``max_charge_w``; a cap of 0 forbids it."""
    assert BatteryAction(max_charge_w=3000, grid_charge_w=3000).grid_charge_w == 3000
    assert BatteryAction(max_charge_w=0, grid_charge_w=0).grid_charge_w == 0
    assert BatteryAction(grid_charge_w=5000).max_charge_w is None  # no cap given
    with pytest.raises(ValidationError, match="above max_charge_w"):
        BatteryAction(max_charge_w=2000, grid_charge_w=2500)
    with pytest.raises(ValidationError, match="above max_charge_w"):
        BatteryAction(max_charge_w=0, grid_charge_w=1)
