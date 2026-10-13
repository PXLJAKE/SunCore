# SPDX-License-Identifier: Apache-2.0
"""Models of the non-site config sections and the v2 site fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from suncore.core.config import (
    BatteryTarget,
    DevicesConfig,
    ExportTariff,
    GoalsConfig,
    ImportTariff,
    OperationConfig,
    RegionConfig,
    TariffConfig,
    WizardState,
)
from suncore.core.decisions import OperatingMode
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    OnOffControl,
    PowerModel,
    PowerModelKind,
)
from suncore.core.site import BatteryConfig, EntityRef, GridConfig, PvArray, SiteConfig

G = {"power": {"entity_id": "sensor.g"}}


def site(**kw: Any) -> SiteConfig:
    return SiteConfig.model_validate({"grid": G, **kw})


# --- site v2 -------------------------------------------------------------------------


def test_pv_array_ranges() -> None:
    PvArray(name="a", kwp=9.9, azimuth_deg=360, tilt_deg=90)
    for bad in ({"kwp": 0}, {"azimuth_deg": 361}, {"tilt_deg": 91}, {"tilt_deg": -1}, {"name": ""}):
        with pytest.raises(ValidationError):
            PvArray.model_validate({"name": "a", **bad})


def test_pv_names_unique_and_flow_uses_array_powers() -> None:
    with pytest.raises(ValidationError, match="unique"):
        site(pv=[{"name": "a"}, {"name": "a"}])
    s = site(
        pv=[
            {"name": "a", "power": {"entity_id": "sensor.a"}, "kwp": 4},
            {"name": "b", "kwp": 6},
        ]
    )
    assert [r.entity_id for r in s.pv_power_refs] == ["sensor.a"]
    assert s.total_kwp == 10
    assert site().total_kwp is None
    assert site(pv=[{"name": "a"}]).total_kwp is None


def test_export_limits_exclusive_and_pct_needs_kwp() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        GridConfig(
            power=EntityRef(entity_id="sensor.g"), export_limit_w=100, export_limit_pct_kwp=70
        )
    with pytest.raises(ValidationError, match="kwp"):
        SiteConfig(grid=GridConfig(power=EntityRef(entity_id="sensor.g"), export_limit_pct_kwp=70))
    ok = site(
        grid={**G, "export_limit_pct_kwp": 70},
        pv=[{"name": "a", "kwp": 8}],
    )
    assert ok.grid.export_limit_pct_kwp == 70
    with pytest.raises(ValidationError):
        GridConfig.model_validate({**G, "export_limit_pct_kwp": 101})
    with pytest.raises(ValidationError):
        GridConfig.model_validate({**G, "import_limit_w": -1})


def test_grid_para14a_and_counters() -> None:
    with pytest.raises(ValidationError, match="para14a"):
        GridConfig.model_validate({**G, "para14a_signal": {"entity_id": "binary_sensor.s"}})
    with pytest.raises(ValidationError, match="invert_sign"):
        GridConfig.model_validate(
            {**G, "energy_import": {"entity_id": "sensor.e", "invert_sign": True}}
        )
    s = site(
        grid={
            **G,
            "para14a": True,
            "para14a_signal": {"entity_id": "binary_sensor.s"},
            "energy_import": {"entity_id": "sensor.ei"},
            "energy_export": {"entity_id": "sensor.ee"},
        }
    )
    assert [r.entity_id for r in s.entity_refs()] == [
        "sensor.g",
        "binary_sensor.s",
        "sensor.ei",
        "sensor.ee",
    ]


def test_battery_limits() -> None:
    p, c = EntityRef(entity_id="sensor.p"), EntityRef(entity_id="sensor.c")
    b = BatteryConfig(power=p, soc=c, capacity_wh=10_000, min_soc_pct=10, max_soc_pct=95)
    assert b.control == "observe"
    for bad in (
        {"min_soc_pct": 50, "max_soc_pct": 50},
        {"min_soc_pct": 90, "max_soc_pct": 10},
        {"max_soc_pct": 101},
        {"min_soc_pct": -1},
        {"capacity_wh": 0},
        {"max_charge_w": 0},
        {"control": "write"},
    ):
        with pytest.raises(ValidationError):
            BatteryConfig(power=p, soc=c, **bad)  # type: ignore[arg-type]


# --- region ----------------------------------------------------------------------------


def test_region() -> None:
    r = RegionConfig(region_id="de", currency="EUR", timezone="Europe/Berlin")
    assert r.language == "auto"
    for bad in (
        {"timezone": "Mars/Base"},
        {"timezone": ""},
        {"currency": "eur"},
        {"currency": "EURO"},
        {"language": "fr"},
        {"region_id": "DE"},
    ):
        with pytest.raises(ValidationError):
            RegionConfig.model_validate(
                {"region_id": "de", "currency": "EUR", "timezone": "UTC", **bad}
            )


# --- tariff ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "fixed"},
        {"kind": "fixed", "fixed_price": 0.3, "template_id": "tibber.prices"},
        {"kind": "dynamic"},
        {"kind": "dynamic", "template_id": "tibber.prices", "fixed_price": 0.3},
        {"kind": "fixed", "fixed_price": -1},
    ],
)
def test_import_tariff_invalid(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ImportTariff.model_validate(kwargs)


def test_import_tariff_valid() -> None:
    ImportTariff(kind="fixed", fixed_price=0.31)
    ImportTariff(kind="dynamic", price_entity=EntityRef(entity_id="sensor.price"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "fixed"},
        {"kind": "fixed", "fixed_price": 0.08, "tiers": [{"price": 0.07}]},
        {"kind": "tiered"},
        {"kind": "tiered", "tiers": [{"price": 0.07}, {"price": 0.06}]},
        {"kind": "tiered", "tiers": [{"up_to_kwp": 40, "price": 1}, {"up_to_kwp": 10, "price": 1}]},
        {"kind": "tiered", "tiers": [{"up_to_kwp": 10, "price": 1}, {"up_to_kwp": 10, "price": 1}]},
        {"kind": "tiered", "fixed_price": 0.1, "tiers": [{"price": 0.07}]},
        {"kind": "none", "fixed_price": 0.1},
        {"kind": "none", "tiers": [{"price": 0.1}]},
    ],
)
def test_export_tariff_invalid(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExportTariff.model_validate(kwargs)


def test_tariff_section() -> None:
    t = TariffConfig(
        import_=ImportTariff(kind="dynamic", price_entity=EntityRef(entity_id="sensor.p")),
        export=ExportTariff(
            kind="tiered",
            tiers=[{"up_to_kwp": 10, "price": 0.0786}, {"price": 0.0680}],  # type: ignore[list-item]
        ),
        forecast_template_ids=["forecast.solcast"],
        forecast_entities={"today": EntityRef(entity_id="sensor.fc_today")},
    )
    assert [r.entity_id for r in t.entity_refs()] == ["sensor.fc_today", "sensor.p"]
    assert TariffConfig.model_validate_json(t.model_dump_json()) == t
    with pytest.raises(ValidationError, match="unique"):
        TariffConfig(
            import_=ImportTariff(kind="fixed", fixed_price=0.3),
            export=ExportTariff(kind="none"),
            forecast_template_ids=["a.b", "a.b"],
        )


# --- devices -----------------------------------------------------------------------------


def dev(dev_id: str, priority: int = 0) -> DeviceConfig:
    return DeviceConfig(
        id=dev_id,
        template_id="generic.switch",
        name=dev_id,
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id=f"switch.{dev_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=100),
        control=OnOffControl(kind="on_off", role="switch"),
        priority=priority,
    )


def test_devices_unique_ids_and_priority_order() -> None:
    with pytest.raises(ValidationError, match="unique"):
        DevicesConfig(devices=[dev("a"), dev("a")])
    cfg = DevicesConfig(devices=[dev("c", 5), dev("b", 1), dev("a", 5), dev("d", 1)])
    assert [d.id for d in cfg.by_priority()] == ["b", "d", "a", "c"]
    assert [d.id for d in cfg.devices] == ["c", "b", "a", "d"]  # stored order untouched


def test_operation_without_modes_of() -> None:
    op = OperationConfig(device_modes={"a": OperatingMode.AUTOMATIC, "b": OperatingMode.ASSISTED})
    assert op.without_modes_of(frozenset({"z"})) is op
    assert op.without_modes_of(frozenset({"a"})).device_modes == {"b": OperatingMode.ASSISTED}


# --- goals / operation / wizard ------------------------------------------------------------


def test_goals() -> None:
    g = GoalsConfig()
    assert g.battery_target is None
    assert g.grid_charge_night_only is True
    assert BatteryTarget(soc_pct=80, time="06:30").time == "06:30"
    for bad in ("6:30", "24:00", "06:60", "06:30:00", ""):
        with pytest.raises(ValidationError):
            BatteryTarget(soc_pct=80, time=bad)
    with pytest.raises(ValidationError):
        BatteryTarget(soc_pct=101, time="06:30")


def test_operation_defaults_to_shadow() -> None:
    o = OperationConfig()
    assert o.mode is OperatingMode.SHADOW
    assert o.device_modes == {}
    with pytest.raises(ValidationError):
        OperationConfig.model_validate({"device_modes": {"Bad Id": "shadow"}})
    with pytest.raises(ValidationError):
        OperationConfig.model_validate({"mode": "yolo"})


def test_wizard() -> None:
    w = WizardState()
    assert (w.completed, w.completed_steps, w.version) == (False, [], 1)
    for bad in ({"version": 0}, {"completed_steps": ["Bad Step"]}):
        with pytest.raises(ValidationError):
            WizardState.model_validate(bad)


def test_all_sections_frozen_and_forbid_extra() -> None:
    for model in (GoalsConfig, OperationConfig, WizardState, DevicesConfig):
        with pytest.raises(ValidationError):
            model.model_validate({"bogus": 1})
    with pytest.raises(ValidationError):
        WizardState().completed = True  # type: ignore[misc]


# --- the safety gate does not read the stored modes -----------------------------------------


def test_safety_package_does_not_import_config_sections() -> None:
    root = Path(__file__).parents[2] / "suncore" / "safety"
    for path in root.rglob("*.py"):
        text = path.read_text("utf-8")
        assert "core.config" not in text, path
        assert "OperationConfig" not in text, path
        assert "suncore.storage" not in text, path


def test_tariff_import_is_serialised_as_import_and_both_keys_load() -> None:
    tariff = TariffConfig(
        import_=ImportTariff(kind="fixed", fixed_price=0.3),
        export=ExportTariff(kind="none"),
    )
    dumped = tariff.model_dump(mode="json", by_alias=True)
    assert "import" in dumped
    assert "import_" not in dumped
    assert '"import":' in tariff.model_dump_json(by_alias=True)
    current = dict(dumped)
    legacy = {k: v for k, v in dumped.items() if k != "import"} | {"import_": dumped["import"]}
    assert TariffConfig.model_validate(legacy) == tariff  # documents stored before the alias
    assert TariffConfig.model_validate(current) == tariff
