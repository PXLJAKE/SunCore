# SPDX-License-Identifier: Apache-2.0
"""Energy dashboard import: verified semantics, sign inversion, old formats, external stats."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from suncore.core.site import SiteConfig
from suncore.ha.energy_import import suggest_from_prefs
from suncore.sim import demo_devices as d
from suncore.sim import registry


def by_path(result: Any) -> dict[str, Any]:
    return {s.path: s for s in result.suggestions}


def test_not_configured_has_no_suggestions() -> None:
    result = suggest_from_prefs(None)
    assert result.configured is False
    assert result.suggestions == ()
    assert result.notes == ()
    assert result.site == {}


def test_demo_prefs_map_grid_pv_battery() -> None:
    result = suggest_from_prefs(registry.energy_prefs(10.0))
    sug = by_path(result)
    assert sug["grid.power"].value == {"entity_id": "sensor.sim_grid_power", "invert_sign": False}
    assert sug["grid.power"].reason.key == "wizard.energy.reason.grid_rate"
    assert sug["grid.energy_import"].value["entity_id"] == d.GRID_IMPORT_KWH_ID
    assert sug["grid.energy_export"].value["entity_id"] == d.GRID_EXPORT_KWH_ID
    assert sug["pv.0.power"].value == {"entity_id": "sensor.sim_pv_power", "invert_sign": False}
    assert sug["pv.0.name"].value == "PV 1"
    # The demo battery uses stat_rate_inverted: the top-level stat_rate is the id HA
    # generates, always in HA's convention (+ = discharge) -> SunCore needs a flip.
    assert sug["battery.power"].value == {
        "entity_id": "sensor.sim_battery_power_inverted",
        "invert_sign": True,
    }
    assert sug["battery.power"].reason.key == "wizard.energy.reason.battery_rate"
    assert sug["battery.soc"].value["entity_id"] == "sensor.sim_battery_soc"
    assert sug["battery.capacity_wh"].value == 10_000.0
    assert sug["battery.capacity_wh"].reason.args == {"kwh": 10.0}
    assert result.notes == ()


def test_fragment_is_a_valid_site_when_completed() -> None:
    result = suggest_from_prefs(registry.energy_prefs(10.0))
    site = result.site
    assert site["grid"]["power"]["entity_id"] == "sensor.sim_grid_power"
    assert site["pv"] == [
        {"name": "PV 1", "power": {"entity_id": "sensor.sim_pv_power", "invert_sign": False}}
    ]
    assert site["battery"]["capacity_wh"] == 10_000.0
    # Every key of the fragment is a SiteConfig field (extra=forbid would reject typos).
    SiteConfig.model_validate({"schema_version": 2, **site})


def test_battery_sign_is_inverted_for_the_top_level_rate() -> None:
    prefs = {
        "energy_sources": [
            {"type": "battery", "stat_rate": "sensor.bat", "stat_soc": "sensor.soc"},
        ]
    }
    sug = by_path(suggest_from_prefs(prefs))
    assert sug["battery.power"].value == {"entity_id": "sensor.bat", "invert_sign": True}


def test_battery_raw_inverted_sensor_without_top_level_needs_no_flip() -> None:
    """``stat_rate_inverted`` is opposite to HA (+ = charge) = SunCore's convention."""
    prefs = {
        "energy_sources": [
            {"type": "battery", "power_config": {"stat_rate_inverted": "sensor.raw"}},
        ]
    }
    sug = by_path(suggest_from_prefs(prefs))
    assert sug["battery.power"].value == {"entity_id": "sensor.raw", "invert_sign": False}
    assert sug["battery.power"].reason.key == "wizard.energy.reason.battery_rate_inverted"


def test_battery_standard_power_config_without_top_level_is_inverted() -> None:
    prefs = {"energy_sources": [{"type": "battery", "power_config": {"stat_rate": "sensor.r"}}]}
    assert by_path(suggest_from_prefs(prefs))["battery.power"].value["invert_sign"] is True


def test_battery_two_sensors_without_generated_sensor_gets_a_note() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "battery",
                "power_config": {"stat_rate_from": "sensor.out", "stat_rate_to": "sensor.in"},
                "stat_soc": "sensor.soc",
            }
        ]
    }
    result = suggest_from_prefs(prefs)
    assert "battery.power" not in by_path(result)
    assert [(n.key, dict(n.args)) for n in result.notes] == [
        ("wizard.energy.note.no_power_sensor", {"source": "battery"})
    ]
    assert "battery.soc" in by_path(result)


def test_grid_raw_inverted_sensor_without_top_level_flips_the_sign() -> None:
    """The raw sensor has the opposite polarity of HA (+ = to grid): SunCore must invert."""
    prefs = {
        "energy_sources": [{"type": "grid", "power_config": {"stat_rate_inverted": "sensor.g"}}]
    }
    sug = by_path(suggest_from_prefs(prefs))
    assert sug["grid.power"].value == {"entity_id": "sensor.g", "invert_sign": True}
    assert sug["grid.power"].reason.key == "wizard.energy.reason.grid_rate_inverted"


def test_grid_two_sensor_form_becomes_an_import_export_pair() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "power_config": {"stat_rate_from": "sensor.imp", "stat_rate_to": "sensor.exp"},
            }
        ]
    }
    result = suggest_from_prefs(prefs)
    sug = by_path(result)
    assert sug["grid.import_power"].value["entity_id"] == "sensor.imp"
    assert sug["grid.export_power"].value["entity_id"] == "sensor.exp"
    assert "grid.power" not in sug
    SiteConfig.model_validate(
        {"schema_version": 2, "grid": result.site["grid"]},
    )


def test_old_grid_format_flow_from_flow_to_power_lists() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "flow_from": [
                    {"stat_energy_from": "sensor.imp_kwh", "entity_energy_price": "sensor.price"}
                ],
                "flow_to": [{"stat_energy_to": "sensor.exp_kwh"}],
                "power": [{"stat_rate": "sensor.grid_w"}],
                "cost_adjustment_day": 0,
            }
        ],
        "device_consumption": [],
    }
    sug = by_path(suggest_from_prefs(prefs))
    assert sug["grid.power"].value["entity_id"] == "sensor.grid_w"
    assert sug["grid.energy_import"].value["entity_id"] == "sensor.imp_kwh"
    assert sug["grid.energy_export"].value["entity_id"] == "sensor.exp_kwh"


def test_old_grid_format_without_power_entry_still_yields_energy_counters() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "flow_from": [{"stat_energy_from": "sensor.imp_kwh"}],
                "flow_to": [],
            }
        ]
    }
    result = suggest_from_prefs(prefs)
    assert set(by_path(result)) == {"grid.energy_import"}
    assert [n.key for n in result.notes] == ["wizard.energy.note.no_power_sensor"]


def test_external_statistics_are_never_entities() -> None:
    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "stat_energy_from": "mydomain:imported_energy",
                "stat_energy_to": "sensor.exp",
                "stat_rate": "ext:rate",
            },
            {"type": "battery", "stat_soc": "ext:soc", "stat_rate": "sensor.bat"},
        ]
    }
    result = suggest_from_prefs(prefs)
    sug = by_path(result)
    assert "grid.energy_import" not in sug
    assert "grid.power" not in sug
    assert sug["grid.energy_export"].value["entity_id"] == "sensor.exp"
    assert "battery.soc" not in sug
    external = [n for n in result.notes if n.key == "wizard.energy.note.external_statistic"]
    assert {n.args["statistic_id"] for n in external} == {
        "mydomain:imported_energy",
        "ext:rate",
        "ext:soc",
    }


def test_several_pv_sources_become_arrays_with_unique_names() -> None:
    prefs = {
        "energy_sources": [
            {"type": "solar", "stat_rate": "sensor.s1", "name": "Roof"},
            {"type": "solar", "stat_energy_from": "sensor.energy_only"},
            {"type": "solar", "stat_rate": "sensor.s3", "name": "Roof"},
            {"type": "solar", "stat_rate": "sensor.s4"},
        ]
    }
    result = suggest_from_prefs(prefs)
    pv = result.site["pv"]
    assert [a["power"]["entity_id"] for a in pv] == ["sensor.s1", "sensor.s3", "sensor.s4"]
    names = [a["name"] for a in pv]
    assert len(set(names)) == 3
    assert names[0] == "Roof"
    assert [n.args for n in result.notes if n.key == "wizard.energy.note.no_power_sensor"] == [
        {"source": "solar", "name": "PV 2"}
    ]
    SiteConfig.model_validate(
        {"schema_version": 2, "grid": {"power": {"entity_id": "sensor.g"}}, "pv": pv}
    )


def test_multiple_grids_and_batteries_use_the_first_and_say_so() -> None:
    prefs = {
        "energy_sources": [
            {"type": "grid", "stat_rate": "sensor.g1"},
            {"type": "grid", "stat_rate": "sensor.g2"},
            {"type": "battery", "stat_rate": "sensor.b1"},
            {"type": "battery", "stat_rate": "sensor.b2"},
        ]
    }
    result = suggest_from_prefs(prefs)
    sug = by_path(result)
    assert sug["grid.power"].value["entity_id"] == "sensor.g1"
    assert sug["battery.power"].value["entity_id"] == "sensor.b1"
    assert {n.key: n.args["count"] for n in result.notes} == {
        "wizard.energy.note.multiple_grid": 2,
        "wizard.energy.note.multiple_battery": 2,
    }


@pytest.mark.parametrize("capacity", [0, -3, "10", None, float("inf"), True])
def test_unusable_capacity_is_ignored(capacity: Any) -> None:
    prefs = {"energy_sources": [{"type": "battery", "stat_rate": "sensor.b", "capacity": capacity}]}
    assert "battery.capacity_wh" not in by_path(suggest_from_prefs(prefs))


def test_malformed_prefs_do_not_raise() -> None:
    assert suggest_from_prefs({"energy_sources": "nope"}).suggestions == ()
    assert suggest_from_prefs({"energy_sources": [1, None, {"type": "gas"}]}).suggestions == ()
    assert suggest_from_prefs({}).configured is True
    weird = {"energy_sources": [{"type": "grid", "stat_rate": 5, "flow_from": "x", "power": 3}]}
    assert suggest_from_prefs(weird).suggestions == ()


def test_unknown_entities_are_flagged() -> None:
    prefs = {"energy_sources": [{"type": "grid", "stat_rate": "sensor.ghost"}]}
    result = suggest_from_prefs(prefs, known_entity_ids={"sensor.other"})
    assert [(n.key, n.args["entity_id"]) for n in result.notes] == [
        ("wizard.energy.note.entity_unknown", "sensor.ghost")
    ]
    assert suggest_from_prefs(prefs, known_entity_ids={"sensor.ghost"}).notes == ()


def test_invalid_fragment_would_be_rejected_by_the_model() -> None:
    """The fragment is a proposal: a half-filled site is not valid until the user finishes it."""
    result = suggest_from_prefs({"energy_sources": [{"type": "battery", "stat_rate": "sensor.b"}]})
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({"schema_version": 2, **result.site})
