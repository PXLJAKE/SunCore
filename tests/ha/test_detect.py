# SPDX-License-Identifier: Apache-2.0
"""Role detection: ranking, criteria, exclusions, template roles and the legacy picker."""

from __future__ import annotations

from pathlib import Path

import pytest

from suncore.core.entities import EntityMeta
from suncore.core.signals import Quality
from suncore.ha.cache import EntityCache
from suncore.ha.connection import EntityState
from suncore.ha.detect import (
    CRITERION_KEYS,
    HIDDEN_MALUS,
    NAME_AFFINITY_WEIGHT,
    SITE_ROLES,
    criteria_keys,
    describe,
    detect_site_role,
    detect_template_role,
    is_candidate,
    legacy_entities,
)
from suncore.ha.registry import EntityCatalogue
from suncore.templates import RoleSpec, load_templates
from suncore.templates.matcher import WEIGHTS

BUILTIN_DIR = Path(__file__).resolve().parents[2] / "templates"


def meta(entity_id: str, **kw: object) -> EntityMeta:
    return EntityMeta.of(entity_id, **kw)  # type: ignore[arg-type]


def power(entity_id: str, **kw: object) -> EntityMeta:
    return meta(entity_id, device_class="power", unit="W", **kw)


def test_every_criterion_the_matcher_can_report_has_a_catalogue_key() -> None:
    assert set(WEIGHTS) - {"domain", "same_device"} <= set(CRITERION_KEYS)
    assert set(CRITERION_KEYS) == set(WEIGHTS) | {"name", "name_affinity"}
    assert all(k == f"detect.criterion.{n}" for n, k in CRITERION_KEYS.items())
    assert criteria_keys(["domain", "bogus", "name"]) == (
        "detect.criterion.domain",
        "detect.criterion.name",
    )


def test_site_roles_cover_the_documented_set() -> None:
    assert set(SITE_ROLES) == {
        "grid_power",
        "grid_import_power",
        "grid_export_power",
        "grid_energy_import",
        "grid_energy_export",
        "pv_power",
        "battery_power",
        "battery_soc",
        "house_power",
        "price",
        "para14a_signal",
    }


def test_include_hidden_adds_hidden_kinds_with_a_malus_but_never_disabled() -> None:
    metas = [
        power("sensor.good"),
        power("sensor.hidden", hidden=True),
        power("sensor.diag", entity_category="diagnostic"),
        power("sensor.off", disabled=True),
    ]
    default = detect_site_role("grid_power", metas)
    assert [m.meta.entity_id for m in default] == ["sensor.good"]
    wide = detect_site_role("grid_power", metas, include_hidden=True)
    assert [m.meta.entity_id for m in wide] == ["sensor.good", "sensor.diag", "sensor.hidden"]
    assert wide[0].score - wide[1].score == HIDDEN_MALUS
    assert is_candidate(power("a", hidden=True), include_hidden=True)
    assert not is_candidate(power("a", disabled=True), include_hidden=True)


def test_include_hidden_for_template_roles() -> None:
    role = RoleSpec.model_validate(
        {"description": "role.test.x", "matcher": {"domain": "sensor", "device_class": "power"}}
    )
    metas = [power("sensor.good"), power("sensor.hidden", hidden=True)]
    assert [m.meta.entity_id for m in detect_template_role(role, metas)] == ["sensor.good"]
    wide = detect_template_role(role, metas, include_hidden=True)
    assert [m.meta.entity_id for m in wide] == ["sensor.good", "sensor.hidden"]
    assert wide[0].score - wide[1].score == HIDDEN_MALUS


def test_exclusions_hidden_disabled_diagnostic_config() -> None:
    assert is_candidate(power("sensor.a"))
    assert not is_candidate(power("sensor.a", hidden=True))
    assert not is_candidate(power("sensor.a", disabled=True))
    assert not is_candidate(power("sensor.a", entity_category="diagnostic"))
    assert not is_candidate(power("sensor.a", entity_category="config"))
    got = detect_site_role(
        "grid_power",
        [
            power("sensor.grid_a"),
            power("sensor.grid_hidden", hidden=True),
            power("sensor.grid_diag", entity_category="diagnostic"),
            power("sensor.grid_off", disabled=True),
        ],
    )
    assert [m.meta.entity_id for m in got] == ["sensor.grid_a"]


def test_ranking_prefers_device_class_unit_and_name() -> None:
    got = detect_site_role(
        "pv_power",
        [
            power("sensor.zzz_total"),
            power("sensor.solar_power", name="Solar power"),
            meta("sensor.pv_template", unit="W"),  # no device class: fallback matcher
            meta("sensor.humidity_w", device_class="humidity", unit="W"),
            meta("sensor.energy_kwh", device_class="energy", unit="kWh"),
            power("number.pv_limit"),
        ],
    )
    ids = [m.meta.entity_id for m in got]
    assert ids == ["sensor.solar_power", "sensor.zzz_total", "sensor.pv_template"]
    top = got[0]
    assert top.criteria == ("domain", "device_class", "units", "name")
    assert top.score == WEIGHTS["domain"] + WEIGHTS["device_class"] + WEIGHTS["units"] + 15
    assert got[1].criteria == ("domain", "device_class", "units")
    assert got[2].criteria == ("domain", "units", "name")  # fallback: unit only


def test_battery_soc_accepts_battery_class_and_unclassified_percent() -> None:
    got = detect_site_role(
        "battery_soc",
        [
            meta("sensor.soc_sunspec", unit="%"),
            meta("sensor.battery_level", device_class="battery", unit="%"),
            meta("sensor.humidity", device_class="humidity", unit="%"),
            meta("sensor.temp", unit="°C"),
        ],
    )
    assert [m.meta.entity_id for m in got] == ["sensor.battery_level", "sensor.soc_sunspec"]


def test_energy_roles_use_energy_units_and_direction_keywords() -> None:
    metas = [
        meta("sensor.grid_import_energy", device_class="energy", unit="kWh"),
        meta("sensor.grid_export_energy", device_class="energy", unit="kWh"),
        meta("sensor.generic_counter", device_class="energy", unit="Wh"),
        power("sensor.grid_import_power"),
    ]
    imp = detect_site_role("grid_energy_import", metas)
    exp = detect_site_role("grid_energy_export", metas)
    assert imp[0].meta.entity_id == "sensor.grid_import_energy"
    assert exp[0].meta.entity_id == "sensor.grid_export_energy"
    assert "sensor.grid_import_power" not in {m.meta.entity_id for m in imp}


def test_para14a_signal_requires_a_keyword() -> None:
    got = detect_site_role(
        "para14a_signal",
        [
            meta("binary_sensor.evu_sperre"),
            meta("input_boolean.para14a_active"),
            meta("binary_sensor.window_open"),
            meta("sensor.random"),
        ],
    )
    assert {m.meta.entity_id for m in got} == {
        "binary_sensor.evu_sperre",
        "input_boolean.para14a_active",
    }


def test_price_role_matches_currency_per_kwh_sensors() -> None:
    got = detect_site_role(
        "price",
        [
            meta("sensor.tibber_strompreis", unit="EUR/kWh"),
            meta("sensor.other_rate", unit="CHF/kWh"),
            meta("sensor.grid_power", unit="W", device_class="power"),
            meta("sensor.percent", unit="%"),
            meta("sensor.lowercase", unit="eur/kWh"),
        ],
    )
    assert [m.meta.entity_id for m in got] == ["sensor.tibber_strompreis", "sensor.other_rate"]
    assert "name" in got[0].criteria
    assert "unit_pattern" in got[0].criteria


def test_unknown_site_role_raises() -> None:
    with pytest.raises(KeyError):
        detect_site_role("nope", [])


# --- template roles ---------------------------------------------------------------


CATALOG = load_templates(BUILTIN_DIR)


def role(template_id: str, name: str) -> RoleSpec:
    template = CATALOG.get(template_id)
    assert template is not None
    return template.roles[name]  # type: ignore[union-attr]


def test_lg_roles_match_by_translation_key_and_same_device() -> None:
    metas = [
        meta(
            "climate.lg",
            platform="lg_thinq",
            translation_key="climate_air_conditioner",
            device_id="d1",
        ),
        meta(
            "sensor.lg_today",
            platform="lg_thinq",
            translation_key="energy_usage_today",
            device_class="energy",
            unit="Wh",
            device_id="d1",
        ),
        meta(
            "sensor.lg2_today",
            platform="lg_thinq",
            translation_key="energy_usage_today",
            device_class="energy",
            unit="Wh",
            device_id="d2",
        ),
        meta(
            "sensor.other",
            platform="x",
            translation_key="energy_usage_today",
            device_class="energy",
            unit="Wh",
            device_id="d1",
        ),
    ]
    climate = detect_template_role(role("lg.thinq_climate", "climate"), metas)
    assert [m.meta.entity_id for m in climate] == ["climate.lg"]
    spec = next(
        r
        for r in CATALOG.get("lg.thinq_climate").roles.values()  # type: ignore[union-attr]
        if r.same_device_as is not None
    )
    free = detect_template_role(spec, metas)
    scoped = detect_template_role(spec, metas, same_device_id="d1")
    assert {m.meta.entity_id for m in free} >= {"sensor.lg_today", "sensor.lg2_today"}
    assert [m.meta.entity_id for m in scoped] == ["sensor.lg_today"]
    assert "same_device" in scoped[0].criteria
    assert scoped[0].score > free[-1].score


def test_template_roles_exclude_hidden_and_diagnostic() -> None:
    metas = [
        meta("climate.a", platform="lg_thinq", translation_key="climate_air_conditioner"),
        meta(
            "climate.b",
            platform="lg_thinq",
            translation_key="climate_air_conditioner",
            hidden=True,
        ),
    ]
    got = detect_template_role(role("lg.thinq_climate", "climate"), metas)
    assert [m.meta.entity_id for m in got] == ["climate.a"]


# --- describe and legacy ------------------------------------------------------------


class _Conn:
    """Only what the catalogue constructor touches."""


def _catalogue_with(states: list[EntityState]) -> tuple[EntityCatalogue, EntityCache]:
    catalogue = EntityCatalogue(_Conn())  # type: ignore[arg-type]
    catalogue.browse.ingest_states(states)
    return catalogue, EntityCache()


def test_describe_prefers_live_values_and_reports_missing_ones() -> None:
    browse_state = EntityState("sensor.a", "10", {"unit_of_measurement": "W"})
    catalogue, live = _catalogue_with([browse_state])
    live.ingest_states([EntityState("sensor.a", "42", {"unit_of_measurement": "W"})])
    matches = detect_site_role("grid_power", [power("sensor.a", name="Grid"), power("sensor.gone")])
    out = {e.entity_id: e for e in describe(matches, live=live, catalogue=catalogue)}
    a = out["sensor.a"]
    assert (a.value, a.quality, a.state, a.available) == (42.0, Quality.OK, "42", True)
    assert a.unit == "W"
    assert a.device_class == "power"
    assert a.criteria[0] == "detect.criterion.domain"
    gone = out["sensor.gone"]
    assert (gone.value, gone.quality, gone.state, gone.available) == (
        None,
        Quality.UNAVAILABLE,
        "unavailable",
        False,
    )


def test_legacy_entities_keep_phase_1_rules() -> None:
    states = [
        EntityState("sensor.pv", "1200", {"device_class": "power", "unit_of_measurement": "W"}),
        EntityState("sensor.soc", "55", {"unit_of_measurement": "%"}),
        EntityState("sensor.hidden", "1", {"device_class": "power", "unit_of_measurement": "W"}),
        EntityState("sensor.temp", "5", {"unit_of_measurement": "°C"}),
    ]
    catalogue, live = _catalogue_with(states)
    metas = [
        power("sensor.pv"),
        meta("sensor.soc", unit="%"),
        power("sensor.hidden", hidden=True),
        meta("sensor.temp", unit="°C"),
    ]
    got = legacy_entities(metas, live=live, catalogue=catalogue)
    assert [(e.entity_id, e.device_class, e.unit) for e in got] == [
        ("sensor.pv", "power", "W"),
        ("sensor.soc", "battery", "%"),
    ]
    assert all(e.score == 0 and e.criteria == () for e in got)
    wide = legacy_entities(metas, live=live, catalogue=catalogue, include_hidden=True)
    assert [e.entity_id for e in wide] == ["sensor.hidden", "sensor.pv", "sensor.soc"]


def test_name_affinity_ranks_sensors_of_the_same_named_device_first() -> None:
    role = RoleSpec.model_validate(
        {
            "description": "role.test.power",
            "matcher": {
                "domain": "sensor",
                "device_class": "power",
                "units": ["W", "kW"],
                "state_class": "measurement",
            },
        }
    )
    metas = [
        power("sensor.aaa_grid", state_class="measurement", device_id="d_house"),
        power("sensor.bbb_other", state_class="measurement", device_id="d_other"),
        power("sensor.ccc_current", state_class="measurement", device_id="d_b"),
        power(
            "sensor.ddd_plug",
            state_class="measurement",
            device_id="d_plug",
            name="Schlafen   Jake Klimaanlage Power",
        ),
    ]
    names = {"d_b": "  schlafen jake KLIMAANLAGE ", "d_house": "House", "d_other": "Other"}
    plain = detect_template_role(role, metas)
    assert [m.meta.entity_id for m in plain] == [
        "sensor.aaa_grid",
        "sensor.bbb_other",
        "sensor.ccc_current",
        "sensor.ddd_plug",
    ]  # no hint: ties by entity id, no affinity criterion
    assert all("name_affinity" not in m.criteria for m in plain)
    hinted = detect_template_role(
        role,
        metas,
        name_hint="Schlafen Jake Klimaanlage",
        device_name_of=lambda device_id: names.get(device_id or ""),
    )
    assert [m.meta.entity_id for m in hinted] == [
        "sensor.ccc_current",  # device name equal (case and whitespace insensitive)
        "sensor.ddd_plug",  # friendly name contains the device name
        "sensor.aaa_grid",
        "sensor.bbb_other",
    ]
    assert hinted[0].criteria[-1] == "name_affinity"
    assert hinted[0].score - hinted[2].score == NAME_AFFINITY_WEIGHT
    assert criteria_keys(hinted[0].criteria)[-1] == "detect.criterion.name_affinity"
    # A hint never drops a candidate and never adds one.
    assert len(hinted) == len(plain)
