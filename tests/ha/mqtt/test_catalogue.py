# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections import Counter

import pytest

from suncore.core.devices import DeviceKind
from suncore.ha.mqtt import ALL_ENTITIES, EVENT_TYPES, Platform
from suncore.ha.mqtt.catalogue import (
    BATTERY_ENTITIES,
    SITE_ENTITIES,
    Scope,
    spec_by_key,
    specs_for_device,
)
from suncore.i18n import load_catalogue

PLAN_SITE = {
    Platform.SENSOR: {
        "status",
        "mode",
        "next_action",
        "plan_cost_today",
        "savings_today",
        "savings_month",
        "autarky_today",
        "pv_forecast_error",
        "month_peak",
        "available_surplus",
        "grid_state",
    },
    Platform.BINARY_SENSOR: {
        "healthy",
        "shadow_mode",
        "emergency_stop",
        "para14a_active",
        "island",
    },
    Platform.SWITCH: {"auto"},
    Platform.BUTTON: {"replan", "emergency_stop_button", "emergency_stop_clear"},
    Platform.SELECT: {"battery_strategy"},
    Platform.NUMBER: {"battery_reserve", "battery_evening_target"},
    Platform.EVENT: {"events"},
}


def test_site_catalogue_matches_the_plan() -> None:
    found: dict[Platform, set[str]] = {}
    for spec in SITE_ENTITIES:
        found.setdefault(spec.platform, set()).add(spec.key)
    assert found == PLAN_SITE


def test_keys_are_unique_and_suffixes_do_not_collide_per_scope() -> None:
    keys = Counter(s.key for s in ALL_ENTITIES)
    assert [k for k, n in keys.items() if n > 1] == []
    site_objects = [s.object_id() for s in (*SITE_ENTITIES, *BATTERY_ENTITIES)]
    assert len(site_objects) == len(set(site_objects))
    for kind in DeviceKind:
        suffixes = [s.suffix for s in specs_for_device(kind)]
        assert len(suffixes) == len(set(suffixes))


def test_no_energy_class_entities_mirror_the_meters() -> None:
    assert all(s.device_class != "energy" for s in ALL_ENTITIES)
    assert all(s.state_class not in ("total", "total_increasing") for s in ALL_ENTITIES)


def test_monetary_sensors_have_no_state_class() -> None:
    # HA allows only `total` for monetary (sensor/const.py); the daily values reset, so none.
    for spec in ALL_ENTITIES:
        if spec.device_class == "monetary":
            assert spec.state_class is None


def test_object_id_of_device_entity_needs_the_device() -> None:
    spec = spec_by_key("device_mode")
    assert spec is not None
    assert spec.object_id("heater") == "heater_mode"
    with pytest.raises(ValueError, match="device id"):
        spec.object_id()


def test_device_kind_specific_entities() -> None:
    wallbox = {s.key for s in specs_for_device(DeviceKind.WALLBOX)}
    assert {"ev_mode", "target_soc", "target_energy", "target_time"} <= wallbox
    assert "arm" not in wallbox
    assert {"comfort_min", "comfort_max"} <= {s.key for s in specs_for_device(DeviceKind.CLIMATE)}
    assert {"arm", "cancel", "finish_by", "job_state", "planned_start"} <= {
        s.key for s in specs_for_device(DeviceKind.PROGRAM)
    }


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_name_and_state_has_a_catalogue_text(language: str) -> None:
    catalogue = load_catalogue(language)
    for spec in ALL_ENTITIES:
        assert catalogue.has(spec.name_key), spec.name_key
        for value in spec.enum_values:
            assert catalogue.has(f"mqtt.state.{spec.enum_group}.{value}"), (spec.key, value)
    for key in ("mqtt.device.site", "mqtt.device.battery"):
        assert catalogue.has(key)


def test_event_types_are_the_plans() -> None:
    assert len(EVENT_TYPES) == 13
    assert {"device_switched", "proposal", "error"} <= set(EVENT_TYPES)


def test_scopes() -> None:
    assert {s.scope for s in SITE_ENTITIES} == {Scope.SITE}
    assert {s.scope for s in BATTERY_ENTITIES} == {Scope.BATTERY}
