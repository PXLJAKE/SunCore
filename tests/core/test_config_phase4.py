# SPDX-License-Identifier: Apache-2.0
"""Phase 4 config documents: tariff v3, forecast v2, goals v2, battery section, device value."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from suncore.core.battery import BatterySettings
from suncore.core.config import (
    SECTION_MODELS,
    ForecastConfig,
    GoalsConfig,
    ImportTariff,
    TariffConfig,
    migrate_forecast_v1_to_v2,
    migrate_goals_v1_to_v2,
    migrate_tariff_v2_to_v3,
)
from suncore.core.devices import DeviceConfig
from suncore.core.tariff import NIGHT_WINDOW, PriceFormula, TouPeriod, TouTariff, TouWindow

TOU = TouTariff(
    base_price=0.32,
    periods=(TouPeriod(window=TouWindow(start="22:00", end="06:00"), price=0.24),),
)


def test_tariff_v3_formula_and_tou() -> None:
    dyn = ImportTariff(
        kind="dynamic", template_id="nordpool.spot", formula=PriceFormula(grid_fee=0.08, vat_pct=19)
    )
    assert dyn.formula is not None
    tou = ImportTariff(kind="tou", tou=TOU)
    t = TariffConfig.model_validate(
        {"import": tou.model_dump(mode="json"), "export": {"kind": "none"}}
    )
    assert t.schema_version == 3
    assert TariffConfig.model_validate_json(t.model_dump_json(by_alias=True)) == t


@pytest.mark.parametrize(
    ("doc", "key"),
    [
        ({"kind": "tou"}, "needs `tou`"),
        ({"kind": "tou", "tou": TOU, "fixed_price": 0.3}, "takes no fixed price"),
        ({"kind": "fixed", "fixed_price": 0.3, "formula": PriceFormula()}, "needs kind `dynamic`"),
        ({"kind": "fixed", "fixed_price": 0.3, "tou": TOU}, "needs kind `tou`"),
        ({"kind": "dynamic", "template_id": "a.b", "tou": TOU}, "needs kind `tou`"),
    ],
)
def test_tariff_v3_rejects(doc: dict[str, Any], key: str) -> None:
    with pytest.raises(ValidationError, match=key):
        ImportTariff.model_validate(doc)


def test_tariff_v2_body_is_a_valid_v3_body() -> None:
    v2 = {
        "schema_version": 2,
        "import": {"kind": "dynamic", "template_id": "tibber.price"},
        "export": {"kind": "fixed", "fixed_price": 0.08},
    }
    body = migrate_tariff_v2_to_v3(copy.deepcopy(v2))
    assert body == v2
    t = TariffConfig.model_validate({**body, "schema_version": 3})
    assert t.import_.formula is None


def test_forecast_v1_to_v2() -> None:
    body = migrate_forecast_v1_to_v2(
        {
            "schema_version": 1,
            "forecast_template_ids": ["solcast.pv", "other.pv"],
            "forecast_entities": {"forecast_today": {"entity_id": "sensor.today"}},
        }
    )
    f = ForecastConfig.model_validate({**body, "schema_version": 2})
    assert f.template_ids == ["solcast.pv", "other.pv"]
    assert all(s.weight_mode == "auto" for s in f.sources)
    assert [list(s.entities) for s in f.sources] == [["forecast_today"]] * 2
    assert migrate_forecast_v1_to_v2({"schema_version": 1}) == {
        "schema_version": 1,
        "sources": [],
    }
    with pytest.raises(TypeError):
        migrate_forecast_v1_to_v2({"forecast_template_ids": "solcast.pv"})


@pytest.mark.parametrize(
    ("v1", "schedule"),
    [
        ({"grid_charge_night_only": True}, NIGHT_WINDOW),
        ({}, NIGHT_WINDOW),  # v1 default was True
        ({"grid_charge_night_only": False}, None),
    ],
)
def test_goals_v1_to_v2(v1: dict[str, Any], schedule: object) -> None:
    body = migrate_goals_v1_to_v2({"schema_version": 1, "climate_before_export": True, **v1})
    g = GoalsConfig.model_validate({**body, "schema_version": 2})
    assert g.grid_charge_schedule == schedule
    assert g.climate_before_export is True
    assert g.conservative_planning is True
    with pytest.raises(ValidationError):  # the v1 field is gone in v2
        GoalsConfig.model_validate({"grid_charge_night_only": True})


def test_battery_is_a_section() -> None:
    assert SECTION_MODELS["battery"] is BatterySettings


def test_device_planner_value_is_optional() -> None:
    doc = {
        "id": "pool",
        "template_id": "generic.switch",
        "name": "Pool",
        "kind": "switch",
        "entities": {"switch": {"entity_id": "switch.pool"}},
        "power_model": {"kind": "nominal", "nominal_w": 800},
        "control": {"kind": "on_off", "role": "switch"},
    }
    assert DeviceConfig.model_validate(doc).planner_value_per_kwh is None
    assert (
        DeviceConfig.model_validate(doc | {"planner_value_per_kwh": 0.1}).planner_value_per_kwh
        == 0.1
    )
    with pytest.raises(ValidationError):
        DeviceConfig.model_validate(doc | {"planner_value_per_kwh": -0.1})
