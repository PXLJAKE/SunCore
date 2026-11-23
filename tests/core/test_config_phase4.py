# SPDX-License-Identifier: Apache-2.0
"""Phase 4 config documents: tariff v3, forecast v2, goals v2, battery section, device value."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.battery import BatterySettings, GridChargePolicy
from suncore.core.config import (
    SECTION_MODELS,
    ForecastConfig,
    GoalsConfig,
    ImportTariff,
    TariffConfig,
    effective_grid_charge_policy,
    migrate_forecast_v1_to_v2,
    migrate_goals_v1_to_v2,
    migrate_tariff_v2_to_v3,
)
from suncore.core.devices import DeviceConfig
from suncore.core.tariff import (
    NIGHT_WINDOW,
    PriceFormula,
    TouPeriod,
    TouTariff,
    TouWindow,
    WeeklySchedule,
)

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
    with pytest.raises(ValidationError):  # the v1 field is gone in v2
        GoalsConfig.model_validate({"grid_charge_night_only": True})
    with pytest.raises(ValidationError):  # P10 planning is the battery risk level (plan §0)
        GoalsConfig.model_validate({"conservative_planning": True})


DAY = WeeklySchedule.daily("10:00", "16:00")


@pytest.mark.parametrize(
    ("policy", "goals_schedule", "expected"),
    [
        # 3. never enables charging: a disabled policy stays as it is
        (GridChargePolicy(), NIGHT_WINDOW, GridChargePolicy()),
        # 2. the goals schedule fills in a missing schedule of an enabled policy
        (
            GridChargePolicy(enabled=True, max_price=0.1),
            NIGHT_WINDOW,
            GridChargePolicy(enabled=True, max_price=0.1, schedule=NIGHT_WINDOW),
        ),
        # 1. the battery policy's own schedule wins
        (
            GridChargePolicy(enabled=True, schedule=DAY),
            NIGHT_WINDOW,
            GridChargePolicy(enabled=True, schedule=DAY),
        ),
        # no goals schedule: the policy unchanged
        (
            GridChargePolicy(enabled=True, max_price=0.1),
            None,
            GridChargePolicy(enabled=True, max_price=0.1),
        ),
    ],
)
def test_effective_grid_charge_policy(
    policy: GridChargePolicy, goals_schedule: WeeklySchedule | None, expected: GridChargePolicy
) -> None:
    goals = GoalsConfig(grid_charge_schedule=goals_schedule)
    battery = BatterySettings(grid_charge=policy)
    effective = effective_grid_charge_policy(goals, battery)
    assert effective == expected
    assert effective.enabled is policy.enabled  # the goals never switch charging on


@settings(max_examples=60, deadline=None)
@given(
    enabled=st.booleans(),
    max_price=st.none() | st.floats(-1, 1, allow_nan=False),
    own=st.sampled_from([None, DAY]),
    goals_schedule=st.sampled_from([None, NIGHT_WINDOW]),
    hour=st.integers(0, 23),
    price=st.none() | st.floats(-1, 1, allow_nan=False),
)
def test_effective_policy_only_narrows(  # noqa: PLR0917 - hypothesis arguments
    enabled: bool,
    max_price: float | None,
    own: WeeklySchedule | None,
    goals_schedule: WeeklySchedule | None,
    hour: int,
    price: float | None,
) -> None:
    """Whatever the goals say, the effective policy never allows more than the battery's."""
    if enabled and max_price is None and own is None:
        return  # invalid policy (validator), covered elsewhere
    policy = GridChargePolicy(enabled=enabled, max_price=max_price, schedule=own)
    effective = effective_grid_charge_policy(
        GoalsConfig(grid_charge_schedule=goals_schedule), BatterySettings(grid_charge=policy)
    )
    at = datetime(2026, 1, 5, hour, tzinfo=UTC)
    if effective.allows(at, price, UTC):
        assert policy.allows(at, price, UTC)


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


# --- forecast.load (WP6, additive in v2) ---------------------------------------------------


def test_forecast_load_inputs_are_optional_and_additive() -> None:
    stored_v2 = {"schema_version": 2, "sources": [], "learning": {"min_history_days": 30}}
    config = ForecastConfig.model_validate(stored_v2)
    assert config.load.workday is None
    assert config.load.weather is None
    assert config.entity_refs() == []


def test_forecast_load_entities_are_checked_and_listed() -> None:
    config = ForecastConfig.model_validate(
        {
            "load": {
                "workday": {"entity_id": "binary_sensor.workday_sensor"},
                "weather": {"entity_id": "weather.forecast_home"},
            }
        }
    )
    assert [r.entity_id for r in config.entity_refs()] == [
        "binary_sensor.workday_sensor",
        "weather.forecast_home",
    ]
    with pytest.raises(ValidationError, match="forecast_workday_domain"):
        ForecastConfig.model_validate({"load": {"workday": {"entity_id": "sensor.x"}}})
    with pytest.raises(ValidationError, match="forecast_weather_domain"):
        ForecastConfig.model_validate({"load": {"weather": {"entity_id": "sensor.x"}}})


# --- tariff source settings (WP4, additive) ----------------------------------------------


def test_dynamic_import_takes_source_settings_and_lists_their_entities() -> None:
    from suncore.core.site import EntityRef  # noqa: PLC0415

    body = {
        "import": {
            "kind": "dynamic",
            "template_id": "octopus_energy.rates",
            "price_entity": {"entity_id": "sensor.rate"},
            "role_entities": {
                "current_day_rates": {"entity_id": "event.today"},
                "next_day_rates": {"entity_id": "event.tomorrow"},
            },
            "action_params": {"config_entry": "01H", "areas": ["SE3", "SE4"]},
            "series_key": "SE3",
            "price_includes": "all_in",
        },
        "export": {"kind": "none"},
    }
    tariff = TariffConfig.model_validate(body)
    assert [r.entity_id for r in tariff.entity_refs()] == [
        "sensor.rate",
        "event.today",
        "event.tomorrow",
    ]
    assert tariff.import_.action_params["areas"] == ["SE3", "SE4"]
    assert EntityRef(entity_id="sensor.rate") in tariff.entity_refs()
    again = TariffConfig.model_validate(tariff.model_dump(mode="json", by_alias=True))
    assert again == tariff


def test_older_tariff_documents_load_without_the_new_fields() -> None:
    tariff = TariffConfig.model_validate(
        {"import": {"kind": "dynamic", "template_id": "tibber.price"}, "export": {"kind": "none"}}
    )
    assert tariff.import_.role_entities == {}
    assert tariff.import_.action_params == {}
    assert tariff.import_.series_key is None
    assert tariff.import_.price_includes is None


@pytest.mark.parametrize(
    "extra",
    [
        {"role_entities": {"x": {"entity_id": "sensor.x"}}},
        {"action_params": {"a": "b"}},
        {"series_key": "SE3"},
        {"price_includes": "all_in"},
    ],
)
def test_source_settings_need_a_dynamic_tariff(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as err:
        TariffConfig.model_validate(
            {"import": {"kind": "fixed", "fixed_price": 0.3, **extra}, "export": {"kind": "none"}}
        )
    assert "suncore.import_source_dynamic_only" in str(err.value)


def test_the_price_role_is_the_price_entity() -> None:
    with pytest.raises(ValidationError) as err:
        TariffConfig.model_validate(
            {
                "import": {
                    "kind": "dynamic",
                    "template_id": "tibber.price",
                    "role_entities": {"price": {"entity_id": "sensor.x"}},
                },
                "export": {"kind": "none"},
            }
        )
    assert "suncore.import_role_price" in str(err.value)


def test_action_params_reject_empty_and_oversized_values() -> None:
    base = {"kind": "dynamic", "template_id": "nordpool.price"}
    for params in ({"": "x"}, {"a": ""}, {"a": "x" * 201}, {"a": []}, {"a": [""]}, {"a": 5}):
        with pytest.raises(ValidationError):
            TariffConfig.model_validate(
                {"import": {**base, "action_params": params}, "export": {"kind": "none"}}
            )
