# SPDX-License-Identifier: Apache-2.0
"""Config v2: site migration, all sections, history, new-device shadow rule."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from suncore.core.battery import (
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    GridChargePolicy,
    RiskLevel,
)
from suncore.core.config import (
    SECTION_MODELS,
    BatteryTarget,
    DevicesConfig,
    ExportTariff,
    ExportTier,
    ForecastConfig,
    ForecastSourceConfig,
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
from suncore.core.site import EntityRef, GridConfig, PvArray, SiteConfig
from suncore.core.tariff import NIGHT_WINDOW
from suncore.storage import (
    ConfigCorruptError,
    ConfigStore,
    Database,
    HistoryEntryNotFoundError,
    ImportRejectedError,
    UnknownSectionError,
)
from suncore.storage.config_store import DEFAULT_SECTIONS, HISTORY_LIMIT
from suncore.storage.migrations import latest_version

from .conftest import make_site

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text("utf-8"))


# --- site v1 -> v2 -----------------------------------------------------------


async def test_v1_site_document_migrates_on_get(db: Database) -> None:
    body = _fixture("site_v1_doc.json")
    await db.execute("INSERT INTO config_doc VALUES ('site', 1, ?, 't')", (json.dumps(body),))
    site = await ConfigStore(db).get_site()
    assert site is not None
    assert site.schema_version == 2
    assert [(a.name, a.power) for a in site.pv] == [
        ("PV 1", EntityRef(entity_id="sensor.pv_east")),
        ("PV 2", EntityRef(entity_id="sensor.pv_west", invert_sign=True)),
    ]
    assert all(a.kwp is None and a.tilt_deg is None for a in site.pv)
    assert site.grid.power == EntityRef(entity_id="sensor.grid_power", invert_sign=True)
    assert site.grid.para14a is False
    assert site.battery is not None
    assert site.battery.control == "observe"
    assert [r.entity_id for r in site.entity_refs()] == [
        "sensor.grid_power",
        "sensor.pv_east",
        "sensor.pv_west",
        "sensor.bat_power",
        "sensor.bat_soc",
        "sensor.house_power",
    ]


async def test_v1_site_without_pv_migrates(db: Database) -> None:
    body = {"schema_version": 1, "grid": {"power": {"entity_id": "sensor.g"}}}
    await db.execute("INSERT INTO config_doc VALUES ('site', 1, ?, 't')", (json.dumps(body),))
    site = await ConfigStore(db).get_site()
    assert site is not None
    assert site.pv == []


async def test_v1_export_file_imports(db: Database) -> None:
    store = ConfigStore(db)
    assert await store.import_json(_fixture("export_v1.json")) == ["site"]
    site = await store.get_site()
    assert site is not None
    assert site.grid.is_pair
    assert [a.power for a in site.pv] == [EntityRef(entity_id="sensor.pv_total")]
    exported = await store.export_json()
    assert exported["db_schema_version"] == latest_version() == 3
    assert exported["sections"]["site"]["schema_version"] == 2


async def test_corrupt_v1_pv_is_reported(db: Database) -> None:
    body = {"schema_version": 1, "grid": {"power": {"entity_id": "sensor.g"}}, "pv": "x"}
    await db.execute("INSERT INTO config_doc VALUES ('site', 1, ?, 't')", (json.dumps(body),))
    with pytest.raises(ConfigCorruptError):
        await ConfigStore(db).get_site()


# --- newer versions are refused -------------------------------------------------


@pytest.mark.parametrize("section", sorted(DEFAULT_SECTIONS))
async def test_newer_section_version_refused(db: Database, section: str) -> None:
    store = ConfigStore(db)
    newer = DEFAULT_SECTIONS[section].current_version + 1
    data = {
        "suncore_export": 1,
        "db_schema_version": latest_version(),
        "sections": {section: {"schema_version": newer, "body": {}}},
    }
    with pytest.raises(ImportRejectedError):
        await store.import_json(data)
    await db.execute("INSERT INTO config_doc VALUES (?, ?, '{}', 't')", (section, newer))
    with pytest.raises(ConfigCorruptError):
        await store.get(section)


async def test_newer_db_export_refused(db: Database) -> None:
    data = {"suncore_export": 1, "db_schema_version": latest_version() + 1, "sections": {}}
    with pytest.raises(ImportRejectedError):
        await ConfigStore(db).import_json(data)


# --- hypothesis round trip of every section ---------------------------------------

_ids = st.from_regex(r"[a-z][a-z0-9]{0,6}(_[a-z0-9]{1,4}){0,2}", fullmatch=True).filter(
    lambda i: not i.startswith("ghost")
)
_eid = st.builds(lambda d, o: f"{d}.{o}", _ids, _ids)
_ref = st.builds(EntityRef, entity_id=_eid, invert_sign=st.booleans())
_pos = st.floats(min_value=0.1, max_value=1e5, allow_nan=False)
_pct = st.floats(min_value=0, max_value=100, allow_nan=False)


@st.composite
def _sites(draw: st.DrawFn) -> SiteConfig:
    eids = draw(st.lists(_eid, min_size=8, max_size=8, unique=True))
    n_pv = draw(st.integers(0, 3))
    pv = [
        PvArray(
            name=f"A{i}",
            power=EntityRef(entity_id=eids[i]),
            kwp=draw(st.none() | st.floats(0.1, 10_000, allow_nan=False)),
            azimuth_deg=draw(st.none() | st.floats(0, 360, allow_nan=False)),
            tilt_deg=draw(st.none() | st.floats(0, 90, allow_nan=False)),
            inverter=draw(st.none() | _ids),
        )
        for i in range(n_pv)
    ]
    grid = GridConfig(
        power=EntityRef(entity_id=eids[4]),
        import_limit_w=draw(st.none() | _pos),
        export_limit_w=draw(st.none() | _pos),
        para14a=draw(st.booleans()),
    )
    return SiteConfig(grid=grid, pv=pv)


@st.composite
def _devices(draw: st.DrawFn) -> DevicesConfig:
    ids = draw(st.lists(_ids, max_size=4, unique=True))
    return DevicesConfig(
        devices=[
            DeviceConfig(
                id=i,
                template_id="generic.switch",
                name=f"Dev {i}",
                kind=DeviceKind.SWITCH,
                entities={"switch": EntityRef(entity_id=f"switch.{i}")},
                power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=draw(_pos)),
                control=OnOffControl(kind="on_off", role="switch"),
                priority=draw(st.integers(0, 100)),
            )
            for i in ids
        ]
    )


_modes = st.sampled_from(list(OperatingMode))
_sections: dict[str, st.SearchStrategy[BaseModel]] = {
    "site": _sites(),
    "region": st.builds(
        RegionConfig,
        region_id=st.sampled_from(["de", "generic"]),
        currency=st.sampled_from(["EUR", "CHF", "USD"]),
        timezone=st.sampled_from(["Europe/Berlin", "UTC", "America/New_York"]),
        language=st.sampled_from(["auto", "en", "de"]),
    ),
    "tariff": st.builds(
        TariffConfig,
        import_=st.one_of(
            st.builds(ImportTariff, kind=st.just("fixed"), fixed_price=st.floats(0, 2)),
            st.builds(ImportTariff, kind=st.just("dynamic"), template_id=st.just("tibber.price")),
        ),
        export=st.one_of(
            st.builds(ExportTariff, kind=st.just("none")),
            st.builds(ExportTariff, kind=st.just("fixed"), fixed_price=st.floats(0, 1)),
            st.just(
                ExportTariff(
                    kind="tiered",
                    tiers=(ExportTier(up_to_kwp=10, price=0.08), ExportTier(price=0.07)),
                )
            ),
        ),
    ),
    "forecast": st.builds(
        ForecastConfig,
        sources=st.just(
            [
                ForecastSourceConfig(
                    template_id="solcast.pv",
                    entities={"today": EntityRef(entity_id="sensor.fc_today")},
                )
            ]
        ),
    ),
    "devices": _devices(),
    "goals": st.builds(
        GoalsConfig,
        battery_target=st.none()
        | st.builds(BatteryTarget, soc_pct=_pct, time=st.sampled_from(["06:30", "23:59", "00:00"])),
        climate_before_export=st.booleans(),
        grid_charge_schedule=st.sampled_from([None, NIGHT_WINDOW]),
        conservative_planning=st.booleans(),
    ),
    "operation": st.builds(
        OperationConfig,
        mode=_modes,
        device_modes=st.dictionaries(_ids.map(lambda i: f"ghost_{i}"), _modes, max_size=3),
    ),
    "battery": st.builds(
        BatterySettings,
        model=st.none()
        | st.builds(
            BatteryModel,
            capacity_wh=st.floats(1000, 50_000),
            max_charge_w=st.floats(100, 20_000),
            max_discharge_w=st.floats(100, 20_000),
        ),
        strategy=st.sampled_from(list(BatteryStrategy)),
        risk=st.sampled_from(list(RiskLevel)),
        grid_charge=st.sampled_from(
            [GridChargePolicy(), GridChargePolicy(enabled=True, max_price=0.12)]
        ),
    ),
    "wizard": st.builds(
        WizardState,
        completed=st.booleans(),
        completed_steps=st.lists(st.sampled_from(["grid", "pv", "battery"]), max_size=3),
        version=st.integers(1, 5),
    ),
}


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(data=st.data())
async def test_all_sections_round_trip_through_export_import(
    data: st.DataObject, tmp_path_factory: pytest.TempPathFactory
) -> None:
    models = {name: data.draw(strat) for name, strat in _sections.items()}
    tmp = tmp_path_factory.mktemp("rt")
    async with await Database.open(tmp / "a.db") as a, await Database.open(tmp / "b.db") as b:
        sa, sb = ConfigStore(a), ConfigStore(b)
        for name, model in models.items():
            await sa.put(name, model)
        exported = json.loads(json.dumps(await sa.export_json()))
        assert sorted(exported["sections"]) == sorted(DEFAULT_SECTIONS)
        assert await sb.import_json(exported) == sorted(DEFAULT_SECTIONS)
        for name, model in models.items():
            loaded = await sb.get(name)
            assert loaded is not None
            if name == "devices":  # new devices are normalised to shadow, ids unchanged
                assert isinstance(loaded, DevicesConfig)
                assert [d.id for d in loaded.devices] == [d.id for d in model.devices]  # type: ignore[attr-defined]
            if name == "operation":  # an import always resets the global mode to shadow
                assert isinstance(loaded, OperationConfig)
                assert loaded.mode is OperatingMode.SHADOW
                assert loaded.device_modes == model.device_modes  # type: ignore[attr-defined]
                continue
            assert loaded == model
        exported_b = await sb.export_json()
        exported_b["sections"]["operation"] = exported["sections"]["operation"]
        assert exported_b == exported


# --- history ------------------------------------------------------------------


async def test_put_records_previous_body_and_prunes(db: Database) -> None:
    store = ConfigStore(db)
    assert await store.history("site") == []
    for i in range(HISTORY_LIMIT + 5):
        await store.put_site(make_site(f"sensor.g{i}"))
    hist = await store.history("site")
    assert len(hist) == HISTORY_LIMIT
    assert hist[0].body["grid"]["power"]["entity_id"] == f"sensor.g{HISTORY_LIMIT + 3}"
    assert hist[-1].body["grid"]["power"]["entity_id"] == "sensor.g4"
    assert [h.id for h in hist] == sorted((h.id for h in hist), reverse=True)
    assert await store.history("region") == []


async def test_identical_put_adds_no_history(db: Database) -> None:
    store = ConfigStore(db)
    await store.put_site(make_site())
    await store.put_site(make_site())
    assert await store.history("site") == []


async def test_restore_swaps_and_records(db: Database) -> None:
    store = ConfigStore(db)
    await store.put_site(make_site("sensor.a"))
    await store.put_site(make_site("sensor.b"))
    (entry,) = await store.history("site")
    restored = await store.restore("site", entry.id)
    assert restored == make_site("sensor.a")
    assert await store.get_site() == make_site("sensor.a")
    assert [h.body["grid"]["power"]["entity_id"] for h in await store.history("site")] == [
        "sensor.b",
        "sensor.a",
    ]
    with pytest.raises(HistoryEntryNotFoundError):
        await store.restore("site", 9999)
    with pytest.raises(HistoryEntryNotFoundError):  # other section's id
        await store.restore("region", entry.id)
    with pytest.raises(UnknownSectionError):
        await store.history("nope")


async def test_restore_migrates_old_history_body(db: Database) -> None:
    store = ConfigStore(db)
    await db.execute(
        "INSERT INTO config_history (section, schema_version, body, replaced_at) "
        "VALUES ('site', 1, ?, 't')",
        (json.dumps(_fixture("site_v1_doc.json")),),
    )
    (entry,) = await store.history("site")
    site = await store.restore("site", entry.id)
    assert isinstance(site, SiteConfig)
    assert len(site.pv) == 2


async def test_import_records_history_for_replaced_and_dropped_sections(db: Database) -> None:
    store = ConfigStore(db)
    await store.put_site(make_site("sensor.old"))
    await store.put("goals", GoalsConfig(climate_before_export=True))
    data = _fixture("export_v1.json")
    await store.import_json(data)
    assert len(await store.history("site")) == 1
    (dropped,) = await store.history("goals")
    assert dropped.body["climate_before_export"] is True
    assert await store.get("goals") is None
    await store.restore("goals", dropped.id)
    assert await store.get("goals") == GoalsConfig(climate_before_export=True)


async def test_corrupt_current_body_can_be_overwritten(db: Database) -> None:
    store = ConfigStore(db)
    await db.execute("INSERT INTO config_doc VALUES ('site', 1, '[]', 't')")
    await store.put_site(make_site())
    assert await store.get_site() == make_site()
    assert len(await store.history("site")) == 1


# --- new devices start in shadow (operation.device_modes) ---------------------------------


def _device(dev_id: str) -> DeviceConfig:
    return DeviceConfig(
        id=dev_id,
        template_id="generic.switch",
        name=dev_id,
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id=f"switch.{dev_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=100),
        control=OnOffControl(kind="on_off", role="switch"),
    )


AUTO = OperatingMode.AUTOMATIC


async def test_new_devices_lose_stored_mode_existing_keep_it(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("operation", OperationConfig(device_modes={"a": AUTO, "ghost": AUTO}))
    await store.put("devices", DevicesConfig(devices=[_device("a")]))
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"ghost": AUTO}  # "a" was new
    await store.put("operation", OperationConfig(device_modes={"a": AUTO}))
    await store.put("devices", DevicesConfig(devices=[_device("a"), _device("b")]))
    await store.put(
        "operation", OperationConfig(device_modes={"a": AUTO, "b": AUTO})
    )  # operation puts are stored as given
    await store.put("devices", DevicesConfig(devices=[_device("a"), _device("b")]))
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"a": AUTO, "b": AUTO}  # nothing new this time
    await store.put("devices", DevicesConfig(devices=[_device("a"), _device("b"), _device("c")]))
    await store.put("operation", OperationConfig(device_modes={"a": AUTO, "c": AUTO}))
    await store.put("devices", DevicesConfig(devices=[_device("a"), _device("c")]))  # c known
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"a": AUTO, "c": AUTO}


async def test_import_never_inherits_modes_for_new_devices(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("devices", DevicesConfig(devices=[_device("a")]))
    data = {
        "suncore_export": 1,
        "db_schema_version": latest_version(),
        "sections": {
            "devices": {
                "schema_version": 1,
                "body": DevicesConfig(devices=[_device("a"), _device("c")]).model_dump(mode="json"),
            },
            "operation": {
                "schema_version": 1,
                "body": OperationConfig(
                    mode=AUTO, device_modes={"a": AUTO, "c": AUTO, "zzz": AUTO}
                ).model_dump(mode="json"),
            },
        },
    }
    await store.import_json(data)
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"a": AUTO, "zzz": AUTO}
    assert op.mode is OperatingMode.SHADOW  # an import always resets the global mode


async def test_import_without_devices_drops_all_device_modes(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("devices", DevicesConfig(devices=[_device("a")]))
    data = {
        "suncore_export": 1,
        "db_schema_version": latest_version(),
        "sections": {
            "operation": {
                "schema_version": 1,
                "body": OperationConfig(device_modes={"a": AUTO}).model_dump(mode="json"),
            }
        },
    }
    await store.import_json(data)
    assert await store.get("operation") == OperationConfig()


async def test_tariff_stored_with_import_key_and_legacy_key_still_loads(db: Database) -> None:
    store = ConfigStore(db)
    tariff = TariffConfig(
        import_=ImportTariff(kind="fixed", fixed_price=0.3), export=ExportTariff(kind="none")
    )
    await store.put("tariff", tariff)
    row = await db.fetchone("SELECT body FROM config_doc WHERE section = 'tariff'")
    assert row is not None
    body = json.loads(str(row[0]))
    assert "import" in body
    assert "import_" not in body
    exported = await store.export_json()
    assert "import" in exported["sections"]["tariff"]["body"]
    legacy = {k: v for k, v in body.items() if k != "import"} | {"import_": body["import"]}
    await db.execute(
        "UPDATE config_doc SET body = ? WHERE section = 'tariff'", (json.dumps(legacy),)
    )
    assert await store.get("tariff") == tariff


V1_TARIFF = {
    "schema_version": 1,
    "import": {"kind": "fixed", "fixed_price": 0.3},
    "export": {"kind": "none"},
    "forecast_template_ids": ["solcast.pv"],
    "forecast_entities": {"today": {"entity_id": "sensor.fc_today"}},
}


async def _store_v1_tariff(db: Database, body: dict[str, Any]) -> None:
    await db.execute(
        "INSERT INTO config_doc (section, schema_version, body, updated_at) "
        "VALUES ('tariff', 1, ?, '2026-01-01T00:00:00+00:00')",
        (json.dumps(body),),
    )


async def test_tariff_v1_forecast_fields_move_to_the_forecast_section(db: Database) -> None:
    store = ConfigStore(db)
    await _store_v1_tariff(db, V1_TARIFF)
    # A plain read already drops the moved fields (document migration)...
    tariff = await store.get("tariff")
    assert isinstance(tariff, TariffConfig)
    assert tariff.schema_version == 3
    # ...and the start-up migration keeps them in their new section (forecast v2).
    assert await store.migrate_legacy_forecast() is True
    forecast = await store.get("forecast")
    assert forecast == ForecastConfig(
        sources=[
            ForecastSourceConfig(
                template_id="solcast.pv",
                entities={"today": EntityRef(entity_id="sensor.fc_today")},
            )
        ]
    )
    row = await db.fetchone("SELECT schema_version, body FROM config_doc WHERE section='tariff'")
    assert row is not None
    assert int(str(row[0])) == 3
    assert "forecast_entities" not in json.loads(str(row[1]))
    assert [h.schema_version for h in await store.history("tariff")] == [1]
    assert await store.migrate_legacy_forecast() is False  # idempotent


async def test_legacy_forecast_migration_keeps_an_existing_forecast_section(db: Database) -> None:
    store = ConfigStore(db)
    mine = ForecastConfig(sources=[ForecastSourceConfig(template_id="other.pv")])
    await store.put("forecast", mine)
    await _store_v1_tariff(db, V1_TARIFF)
    assert await store.migrate_legacy_forecast() is True
    assert await store.get("forecast") == mine


async def test_tariff_v1_without_forecast_fields_creates_no_forecast_section(
    db: Database,
) -> None:
    store = ConfigStore(db)
    await _store_v1_tariff(db, {k: v for k, v in V1_TARIFF.items() if not k.startswith("forecast")})
    await store.migrate_legacy_forecast()
    assert await store.get("forecast") is None
    assert isinstance(await store.get("tariff"), TariffConfig)


async def test_import_of_a_v1_tariff_export_carries_the_forecast_over(db: Database) -> None:
    store = ConfigStore(db)
    data = {
        "suncore_export": 1,
        "app_version": "0.2.0",
        "db_schema_version": latest_version(),
        "sections": {"tariff": {"schema_version": 1, "body": V1_TARIFF}},
    }
    assert await store.import_json(data) == ["forecast", "tariff"]
    forecast = await store.get("forecast")
    assert isinstance(forecast, ForecastConfig)
    assert forecast.template_ids == ["solcast.pv"]


def test_section_models_match_the_api_registry() -> None:
    assert {name: spec.model for name, spec in DEFAULT_SECTIONS.items()} == dict(SECTION_MODELS)


async def test_stored_sections_lists_only_written_ones(db: Database) -> None:
    store = ConfigStore(db)
    assert await store.stored_sections() == frozenset()
    await store.put("wizard", WizardState())
    assert await store.stored_sections() == frozenset({"wizard"})


# --- device identity rule (id + template + entities), STATUS F6/A2 ------------------------


def _device_with(
    dev_id: str, *, entity: str | None = None, template: str | None = None
) -> DeviceConfig:
    base = _device(dev_id)
    update: dict[str, object] = {}
    if entity is not None:
        update["entities"] = {"switch": EntityRef(entity_id=entity)}
    if template is not None:
        update["template_id"] = template
    return base.model_copy(update=update)


async def _setup_auto(store: ConfigStore, *devices: DeviceConfig) -> None:
    await store.put("devices", DevicesConfig(devices=list(devices)))
    await store.put("operation", OperationConfig(device_modes={d.id: AUTO for d in devices}))


@pytest.mark.parametrize(
    "changed",
    [
        _device_with("a", entity="switch.other"),
        _device_with("a", template="generic.other"),
    ],
    ids=["entities", "template"],
)
async def test_put_devices_changed_identity_falls_back_to_shadow(
    db: Database, changed: DeviceConfig
) -> None:
    store = ConfigStore(db)
    await _setup_auto(store, _device("a"), _device("b"))
    await store.put("devices", DevicesConfig(devices=[changed, _device("b")]))
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"b": AUTO}


async def test_put_devices_setting_change_keeps_mode(db: Database) -> None:
    store = ConfigStore(db)
    await _setup_auto(store, _device("a"))
    renamed = _device("a").model_copy(update={"name": "Boiler", "priority": 3})
    await store.put("devices", DevicesConfig(devices=[renamed]))
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"a": AUTO}


async def test_import_changed_identity_drops_mode_and_resets_global(db: Database) -> None:
    store = ConfigStore(db)
    await _setup_auto(store, _device("a"), _device("b"))
    data = {
        "suncore_export": 1,
        "db_schema_version": latest_version(),
        "sections": {
            "devices": {
                "schema_version": 1,
                "body": DevicesConfig(
                    devices=[_device_with("a", entity="switch.moved"), _device("b")]
                ).model_dump(mode="json"),
            },
            "operation": {
                "schema_version": 1,
                "body": OperationConfig(mode=AUTO, device_modes={"a": AUTO, "b": AUTO}).model_dump(
                    mode="json"
                ),
            },
        },
    }
    await store.import_json(data)
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {"b": AUTO}
    assert op.mode is OperatingMode.SHADOW


async def test_restore_devices_changed_identity_falls_back_to_shadow(db: Database) -> None:
    store = ConfigStore(db)
    await _setup_auto(store, _device("a"))
    await store.put("devices", DevicesConfig(devices=[_device_with("a", entity="switch.x")]))
    await store.put("operation", OperationConfig(device_modes={"a": AUTO}))
    (entry,) = await store.history("devices")
    await store.restore("devices", entry.id)  # back to switch.a: identity changed again
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {}


async def test_restore_operation_drops_modes_of_changed_devices(db: Database) -> None:
    store = ConfigStore(db)
    await _setup_auto(store, _device("a"), _device("b"))
    await store.put("operation", OperationConfig())  # history: both automatic
    # "a" is re-mapped; the devices put already drops nothing (operation is shadow now)
    await store.put(
        "devices", DevicesConfig(devices=[_device_with("a", entity="switch.x"), _device("b")])
    )
    (entry,) = [h for h in await store.history("operation") if h.body["device_modes"]]
    restored = await store.restore("operation", entry.id)
    assert isinstance(restored, OperationConfig)
    assert restored.device_modes == {"b": AUTO}
    stored = await store.get("operation")
    assert stored == restored


async def test_restore_operation_without_identity_record_is_conservative(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("devices", DevicesConfig(devices=[_device("a")]))
    await db.execute(  # an operation body from before the record existed
        "INSERT INTO config_history (section, schema_version, body, replaced_at) "
        "VALUES ('operation', 1, ?, 't')",
        (OperationConfig(device_modes={"a": AUTO}).model_dump_json(),),
    )
    (entry,) = await store.history("operation")
    restored = await store.restore("operation", entry.id)
    assert isinstance(restored, OperationConfig)
    assert restored.device_modes == {}


async def test_put_operation_is_stored_as_given(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("devices", DevicesConfig(devices=[_device("a")]))
    await store.put("operation", OperationConfig(mode=AUTO, device_modes={"a": AUTO}))
    op = await store.get("operation")
    assert isinstance(op, OperationConfig)
    assert op.mode is AUTO
    assert op.device_modes == {"a": AUTO}
