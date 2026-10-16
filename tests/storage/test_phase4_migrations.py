# SPDX-License-Identifier: Apache-2.0
"""Phase 4 document migrations from stored Phase 3 documents (tariff v2, forecast v1, goals v1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from suncore.core.battery import BatterySettings, BatteryStrategy
from suncore.core.config import ForecastConfig, GoalsConfig, TariffConfig
from suncore.core.tariff import NIGHT_WINDOW
from suncore.storage import ConfigStore, Database
from suncore.storage.config_store import DEFAULT_SECTIONS

EXPORT = json.loads((Path(__file__).parent / "fixtures" / "export_phase3.json").read_text("utf-8"))


async def _store_raw(db: Database, section: str, version: int, body: dict[str, Any]) -> None:
    await db.execute(
        "INSERT INTO config_doc (section, schema_version, body, updated_at) VALUES (?, ?, ?, 't')",
        (section, version, json.dumps(body)),
    )


def test_current_versions() -> None:
    versions = {name: spec.current_version for name, spec in DEFAULT_SECTIONS.items()}
    assert versions["tariff"] == 3
    assert versions["forecast"] == 2
    assert versions["goals"] == 2
    assert versions["battery"] == 1


@pytest.mark.parametrize("section", ["tariff", "forecast", "goals"])
async def test_stored_phase3_documents_load(db: Database, section: str) -> None:
    doc = EXPORT["sections"][section]
    await _store_raw(db, section, doc["schema_version"], doc["body"])
    loaded = await ConfigStore(db).get(section)
    assert loaded is not None
    assert loaded.schema_version == DEFAULT_SECTIONS[section].current_version  # type: ignore[attr-defined]
    if isinstance(loaded, ForecastConfig):
        assert loaded.template_ids == ["solcast.pv"]
        assert set(loaded.sources[0].entities) == {"forecast_today", "forecast_tomorrow"}
    if isinstance(loaded, GoalsConfig):
        assert loaded.grid_charge_schedule == NIGHT_WINDOW
        assert loaded.battery_target is not None
    if isinstance(loaded, TariffConfig):
        assert loaded.import_.template_id == "tibber.price"
        assert loaded.import_.formula is None


async def test_phase3_export_imports_and_migration_never_enables_grid_charging(
    db: Database,
) -> None:
    store = ConfigStore(db)
    assert await store.import_json(EXPORT) == ["forecast", "goals", "tariff"]
    assert await store.get("battery") is None  # no battery section until the user makes one
    goals = await store.get("goals")
    assert isinstance(goals, GoalsConfig)
    assert goals.grid_charge_schedule == NIGHT_WINDOW
    # Re-export is at the current versions and imports again unchanged.
    exported = await store.export_json()
    assert exported["sections"]["goals"]["schema_version"] == 2
    assert "grid_charge_night_only" not in exported["sections"]["goals"]["body"]
    assert await ConfigStore(db).import_json(exported) == ["forecast", "goals", "tariff"]


async def test_battery_section_round_trips(db: Database) -> None:
    store = ConfigStore(db)
    settings = BatterySettings(strategy=BatteryStrategy.PREFER_FULL)
    await store.put("battery", settings)
    assert await store.get("battery") == settings
    assert not settings.grid_charge.enabled
