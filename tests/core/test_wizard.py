# SPDX-License-Identifier: Apache-2.0
"""System check and live preview (pure logic)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from suncore.core.config import DevicesConfig
from suncore.core.devices import DeviceConfig
from suncore.core.signals import Quality, Sample
from suncore.core.site import (
    BatteryConfig,
    EntityRef,
    GridConfig,
    PvArray,
    SiteConfig,
)
from suncore.core.units import Unit
from suncore.core.wizard import (
    PREVIEW_MAX_REFS,
    CheckItem,
    PreviewTooLargeError,
    collect_refs,
    expected_dimension,
    preview,
    run_system_check,
    worst_severity,
)

TS = datetime(2026, 6, 21, 12, tzinfo=UTC)


def sample(value: float | None, unit: Unit | None = Unit.W, q: Quality = Quality.OK) -> Sample:
    return Sample(value, unit, TS, q)


def site(*, house: bool = True, battery: bool = True, invert_battery: bool = False) -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
        pv=[PvArray(name="PV", power=EntityRef(entity_id="sensor.pv"))],
        battery=BatteryConfig(
            power=EntityRef(entity_id="sensor.bat", invert_sign=invert_battery),
            soc=EntityRef(entity_id="sensor.soc"),
        )
        if battery
        else None,
        house=EntityRef(entity_id="sensor.house") if house else None,
    )


def samples(
    grid: float = 500.0,
    pv: float = 3000.0,
    bat: float = 1000.0,
    house: float = 2500.0,
    soc: float = 50.0,
) -> dict[str, Sample]:
    return {
        "sensor.grid": sample(grid),
        "sensor.pv": sample(pv),
        "sensor.bat": sample(bat),
        "sensor.house": sample(house),
        "sensor.soc": sample(soc, Unit.PERCENT),
    }


def keys(items: list) -> list[str]:  # type: ignore[type-arg]
    return [i.reason.key for i in items]


PRESENT = {"site", "region", "tariff", "forecast"}


def test_everything_fine() -> None:
    items = run_system_check(site(), samples(), PRESENT)
    assert keys(items) == [
        "wizard.check.entities_fresh",
        "wizard.check.units_ok",
        "wizard.check.balance_ok",
        "wizard.check.battery_sign_plausible",
    ]
    assert worst_severity(items) == "ok"
    assert items[0].reason.args == {"count": 5}


def test_missing_site_and_sections() -> None:
    items = run_system_check(None, {}, set())
    assert [(i.severity, i.reason.key, i.subject) for i in items] == [
        ("error", "wizard.check.site_missing", "site"),
        ("warn", "wizard.check.section_missing", "region"),
        ("warn", "wizard.check.section_missing", "tariff"),
        ("ok", "wizard.check.forecast_missing", "forecast"),
    ]
    assert worst_severity(items) == "error"
    assert worst_severity([]) == "ok"
    no_forecast = run_system_check(site(), samples(), {"site", "region", "tariff"})
    missing = [i for i in no_forecast if i.reason.key == "wizard.check.forecast_missing"]
    assert [i.severity for i in missing] == ["ok"]  # an optional section is only a note
    assert worst_severity(no_forecast) == "ok"
    only_tariff = run_system_check(site(), samples(), {"site", "region"})
    assert [i.subject for i in only_tariff if i.reason.key == "wizard.check.section_missing"] == [
        "tariff"
    ]


def test_entity_problems_by_quality() -> None:
    s = samples()
    del s["sensor.grid"]
    s["sensor.pv"] = sample(None, Unit.W, Quality.UNAVAILABLE)
    s["sensor.bat"] = sample(1000.0, Unit.W, Quality.STALE)
    s["sensor.house"] = sample(None, None, Quality.INVALID)
    items = run_system_check(site(), s, PRESENT)
    found = {
        i.reason.key: i.severity for i in items if i.reason.key.startswith("wizard.check.entity")
    }
    assert found == {
        "wizard.check.entity_missing": "error",
        "wizard.check.entity_unavailable": "warn",
        "wizard.check.entity_stale": "warn",
        "wizard.check.entity_invalid": "error",
    }
    assert "wizard.check.entities_fresh" not in keys(items)
    assert "wizard.check.balance_unknown" in keys(items)


def test_unit_problems_stop_the_balance_check() -> None:
    s = samples()
    s["sensor.grid"] = sample(5.0, Unit.KWH)  # energy where power is expected
    s["sensor.soc"] = sample(120.0, Unit.PERCENT)
    s["sensor.pv"] = sample(3000.0, None)  # unknown unit
    items = run_system_check(site(), s, PRESENT)
    ks = keys(items)
    assert "wizard.check.unit_mismatch" in ks
    assert "wizard.check.soc_range" in ks
    assert "wizard.check.unit_unknown" in ks
    assert not any("balance" in k for k in ks)
    mismatch = next(i for i in items if i.reason.key == "wizard.check.unit_mismatch")
    assert dict(mismatch.reason.args) == {
        "entity_id": "sensor.grid",
        "expected": "power",
        "unit": "kWh",
    }


def test_kw_units_are_fine() -> None:
    s = samples()
    s["sensor.grid"] = sample(0.5, Unit.KW)
    assert "wizard.check.balance_ok" in keys(run_system_check(site(), s, PRESENT))


def test_balance_off_with_no_single_explanation() -> None:
    items = run_system_check(site(), samples(house=9000.0), PRESENT)
    off = next(i for i in items if i.reason.key == "wizard.check.balance_off")
    assert off.severity == "warn"
    assert off.reason.args["residual_w"] == -6500.0
    assert "wizard.check.sign_suspect" not in keys(items)


def test_wrong_battery_sign_is_detected_from_the_balance() -> None:
    # true flows: pv 3000 + grid 500 = house 2500 + battery 1000 (charging); reported inverted
    items = run_system_check(site(), samples(bat=-1000.0), PRESENT)
    suspect = next(i for i in items if i.reason.key == "wizard.check.sign_suspect")
    assert dict(suspect.reason.args) == {"part": "battery"}
    assert "wizard.check.battery_sign_plausible" not in keys(items)


def test_wrong_grid_sign_is_detected_from_the_balance() -> None:
    # pv 3000 + grid(-1000) = house 2000 + battery 0; the grid sensor reports +1000
    items = run_system_check(
        site(), samples(grid=1000.0, pv=3000.0, bat=0.0, house=2000.0 + 4000.0 - 4000.0), PRESENT
    )
    # residual = 3000 + 1000 - 0 - 2000 = 2000 -> off; flipping the grid gives 0 -> suspect
    suspect = [i for i in items if i.reason.key == "wizard.check.sign_suspect"]
    assert [dict(i.reason.args) for i in suspect] == [{"part": "grid"}]


def test_wrong_pv_sign_is_detected() -> None:
    items = run_system_check(
        site(), samples(grid=-2500.0, pv=-3000.0, bat=0.0, house=500.0), PRESENT
    )
    assert "wizard.check.pv_negative" in keys(items)


def test_derived_house_and_negative_house() -> None:
    ok = run_system_check(site(house=False), samples(), PRESENT)
    assert "wizard.check.balance_derived" in keys(ok)
    neg = run_system_check(site(house=False), samples(grid=-4000.0, pv=0.0, bat=0.0), PRESENT)
    assert "wizard.check.house_negative" in keys(neg)


def test_battery_sign_vs_soc() -> None:
    full = run_system_check(site(house=False), samples(soc=100.0, bat=2000.0), PRESENT)
    assert "wizard.check.battery_sign_soc" in keys(full)
    empty = run_system_check(site(house=False), samples(soc=0.0, bat=-2000.0), PRESENT)
    assert "wizard.check.battery_sign_soc" in keys(empty)
    fine = run_system_check(site(house=False), samples(soc=100.0, bat=-2000.0), PRESENT)
    assert "wizard.check.battery_sign_soc" not in keys(fine)
    assert "wizard.check.battery_sign_plausible" in keys(fine)


def test_no_battery_no_battery_checks() -> None:
    items = run_system_check(site(battery=False), samples(bat=0.0, house=3500.0), PRESENT)
    assert not any("battery" in k for k in keys(items))


# --- preview ----------------------------------------------------------------------------


def test_expected_dimension_from_paths() -> None:
    assert expected_dimension("battery.soc").value == "ratio"  # type: ignore[union-attr]
    assert expected_dimension("grid.energy_import").value == "energy"  # type: ignore[union-attr]
    assert expected_dimension("grid.import_power").value == "power"  # type: ignore[union-attr]
    assert expected_dimension("house").value == "power"  # type: ignore[union-attr]
    assert expected_dimension("entities.climate") is None
    assert expected_dimension("grid.para14a_signal") is None


def test_collect_refs_walks_dicts_and_lists() -> None:
    fragment = {
        "grid": {
            "power": {"entity_id": "sensor.grid", "invert_sign": True},
            "export_limit_w": 100,
        },
        "pv": [
            {"name": "A", "power": {"entity_id": "sensor.pv1"}},
            {"name": "B", "power": {"entity_id": "not valid"}},
        ],
        "other": {"entity_id": "sensor.x", "nested": {"entity_id": "sensor.never"}},
    }
    assert collect_refs(fragment) == [
        ("grid.power", "sensor.grid", True),
        ("pv.0.power", "sensor.pv1", False),
        ("other", "sensor.x", False),
    ]
    assert collect_refs({"entity_id": "sensor.root"}) == [("", "sensor.root", False)]
    assert collect_refs("string") == []


def test_collect_refs_limits() -> None:
    many = {"pv": [{"power": {"entity_id": f"sensor.s{i}"}} for i in range(PREVIEW_MAX_REFS + 1)]}
    with pytest.raises(PreviewTooLargeError):
        collect_refs(many)
    deep: dict[str, object] = {"v": 1}
    for _ in range(20):
        deep = {"n": deep}
    with pytest.raises(PreviewTooLargeError):
        collect_refs(deep)


def test_preview_converts_and_inverts() -> None:
    fragment = {
        "battery": {
            "power": {"entity_id": "sensor.bat", "invert_sign": True},
            "soc": {"entity_id": "sensor.soc"},
        },
        "grid": {"power": {"entity_id": "sensor.grid"}, "energy_import": {"entity_id": "sensor.e"}},
        "house": {"entity_id": "sensor.gone"},
    }
    s = {
        "sensor.bat": sample(2.1, Unit.KW),
        "sensor.soc": sample(55.0, Unit.PERCENT, Quality.STALE),
        "sensor.grid": sample(100.0, Unit.KWH),  # wrong dimension for power
        "sensor.e": sample(1.5, Unit.KWH),
    }
    items = {i.path: i for i in preview(fragment, s)}
    bat = items["battery.power"]
    assert (bat.raw_value, bat.unit, bat.base_value, bat.base_unit) == (2.1, "kW", -2100.0, "W")
    assert bat.invert_sign is True
    assert bat.unit_ok is True
    soc = items["battery.soc"]
    assert (soc.base_value, soc.base_unit, soc.quality) == (55.0, "%", Quality.STALE)
    assert items["grid.power"].unit_ok is False
    assert items["grid.energy_import"].base_value == 1500.0
    assert items["grid.energy_import"].base_unit == "Wh"
    gone = items["house"]
    assert (gone.quality, gone.base_value, gone.unit, gone.unit_ok) == (
        Quality.UNAVAILABLE,
        None,
        None,
        None,
    )
    assert gone.expected == "power"


def test_preview_without_usable_value() -> None:
    items = preview(
        {"a": {"entity_id": "sensor.u"}, "b": {"entity_id": "sensor.v"}},
        {
            "sensor.u": sample(None, Unit.W, Quality.UNAVAILABLE),
            "sensor.v": sample(float("inf"), Unit.W),
        },
    )
    assert [i.base_value for i in items] == [None, None]


# --- duplicate unit --------------------------------------------------------------------


def _ac(device_id: str, name: str, entity_id: str, kind: str = "climate") -> DeviceConfig:
    climate = kind == "climate"
    return DeviceConfig.model_validate(
        {
            "id": device_id,
            "template_id": "generic.climate",
            "name": name,
            "kind": kind,
            "entities": {"climate": {"entity_id": entity_id}},
            "power_model": {"kind": "nominal", "nominal_w": 900},
            "control": (
                {"kind": "setpoint", "role": "climate", "shift_max_c": 2}
                if climate
                else {"kind": "on_off", "role": "climate"}
            ),
            "comfort": {"min_c": 20, "max_c": 26} if climate else None,
        }
    )


REGISTRY = {
    "climate.a": ("dev_a", "Schlafen Jake Klimaanlage", "lg_thinq"),
    "climate.b": ("dev_b", "  schlafen   jake klimaanlage ", "smartthinq_sensors"),
    "climate.c": ("dev_c", "Wohnzimmer", "lg_thinq"),
    "climate.d": ("dev_a", "Schlafen Jake Klimaanlage", "lg_thinq"),  # same registry device as `a`
    "climate.e": ("dev_e", "Schlafen Jake Klimaanlage", "lg_thinq"),  # same name, same platform
}


def duplicates(*devices: DeviceConfig) -> list[CheckItem]:
    items = run_system_check(None, {}, PRESENT, DevicesConfig(devices=list(devices)), REGISTRY.get)
    return [i for i in items if i.reason.key == "wizard.check.duplicate_unit"]


def test_two_integrations_for_one_unit_are_a_warning() -> None:
    found = duplicates(_ac("ac1", "AC via LG", "climate.a"), _ac("ac2", "AC via ST", "climate.b"))
    assert [(i.severity, i.subject) for i in found] == [("warn", "ac2")]
    assert found[0].reason.args == {
        "device_a": "AC via LG",
        "device_b": "AC via ST",
        "unit_name": "Schlafen Jake Klimaanlage",
    }
    assert worst_severity(found) == "warn"  # a warning, never an error


def test_the_same_entity_twice_is_a_duplicate_for_every_kind() -> None:
    found = duplicates(
        _ac("ac1", "One", "climate.c"), _ac("ac2", "Two", "climate.c", kind="generic")
    )
    assert [i.subject for i in found] == ["ac2"]
    assert found[0].reason.args["unit_name"] == "climate.c"


def test_no_duplicate_for_different_names_one_registry_device_or_unknown_registry() -> None:
    assert duplicates(_ac("ac1", "One", "climate.a"), _ac("ac2", "Two", "climate.c")) == []
    # two climate entities of ONE registry device are not two integrations
    assert duplicates(_ac("ac1", "One", "climate.a"), _ac("ac2", "Two", "climate.d")) == []
    # equal names on the SAME platform (two rooms called "AC") are not one unit
    assert duplicates(_ac("ac1", "One", "climate.a"), _ac("ac2", "Two", "climate.e")) == []
    # without registry data only the same-entity check can run
    items = run_system_check(
        None,
        {},
        PRESENT,
        DevicesConfig(devices=[_ac("ac1", "One", "climate.a"), _ac("ac2", "Two", "climate.b")]),
    )
    assert not any(i.reason.key == "wizard.check.duplicate_unit" for i in items)
    # non-climate devices are not compared by name (default names such as "Smart plug" repeat)
    registry = {
        "climate.a": ("x", "Smart plug", "p1"),
        "climate.b": ("y", "Smart plug", "p2"),
    }
    plugs = run_system_check(
        None,
        {},
        PRESENT,
        DevicesConfig(
            devices=[
                _ac("p1", "P1", "climate.a", kind="generic"),
                _ac("p2", "P2", "climate.b", kind="generic"),
            ]
        ),
        registry.get,
    )
    assert not any(i.reason.key == "wizard.check.duplicate_unit" for i in plugs)
