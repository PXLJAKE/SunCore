# SPDX-License-Identifier: Apache-2.0
"""The ``expert`` section (Phase 5 WP5): additive, own schema version, defaults change nothing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from suncore.core.config import TariffConfig
from suncore.core.control import SAFETY_LIMITS, ControlSettings
from suncore.core.expert import EXPERT_SCHEMA_VERSION, ExpertConfig
from suncore.core.params import apply_controller
from suncore.storage import ConfigStore, Database
from suncore.storage.config_store import DEFAULT_SECTIONS
from suncore.storage.errors import ConfigCorruptError, ImportRejectedError

EXPORT = json.loads((Path(__file__).parent / "fixtures" / "export_phase3.json").read_text("utf-8"))


async def _store_raw(db: Database, section: str, version: int, body: dict[str, Any]) -> None:
    await db.execute(
        "INSERT INTO config_doc (section, schema_version, body, updated_at) VALUES (?, ?, ?, 't')",
        (section, version, json.dumps(body)),
    )


def test_the_section_has_its_own_version_and_model() -> None:
    spec = DEFAULT_SECTIONS["expert"]
    assert spec.model is ExpertConfig
    assert spec.current_version == EXPERT_SCHEMA_VERSION == 1


async def test_a_database_without_the_section_means_all_defaults(db: Database) -> None:
    """Older installations have no ``expert`` document: ``None``, and today's settings."""
    store = ConfigStore(db)
    assert await store.get("expert") is None
    assert apply_controller(ControlSettings(), None) == ControlSettings()


async def test_phase3_and_phase4_exports_import_without_it(db: Database) -> None:
    store = ConfigStore(db)
    await store.import_json(EXPORT)
    assert await store.get("expert") is None
    assert "expert" not in (await store.export_json())["sections"]


async def test_stored_tariff_without_the_new_fields_loads_with_defaults(db: Database) -> None:
    """``remunerate_negative`` and ``negative_reference_entity`` are additive in tariff v3."""
    await _store_raw(
        db,
        "tariff",
        3,
        {
            "schema_version": 3,
            "import": {"kind": "fixed", "fixed_price": 0.3},
            "export": {"kind": "fixed", "fixed_price": 0.08},
        },
    )
    tariff = await ConfigStore(db).get("tariff")
    assert isinstance(tariff, TariffConfig)
    assert tariff.export.remunerate_negative is False
    assert tariff.export.negative_reference_entity is None


async def test_a_stored_section_roundtrips_through_export_and_import(db: Database) -> None:
    store = ConfigStore(db)
    await store.put(
        "expert", ExpertConfig(overrides={"controller.tick_s": 9, "planner.mip_gap": 0.1})
    )
    exported = await store.export_json()
    assert exported["sections"]["expert"]["schema_version"] == 1
    assert exported["sections"]["expert"]["body"]["overrides"] == {
        "controller.tick_s": 9.0,
        "planner.mip_gap": 0.1,
    }
    await store.put("expert", ExpertConfig())
    assert await store.import_json(exported) == ["expert"]
    restored = await store.get("expert")
    assert isinstance(restored, ExpertConfig)
    assert restored.overrides["controller.tick_s"] == 9.0


async def test_a_replaced_body_lands_in_the_configuration_history(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("expert", ExpertConfig(overrides={"controller.tick_s": 9}))
    await store.put("expert", ExpertConfig(overrides={"controller.tick_s": 12}))
    history = await store.history("expert")
    assert [entry.body["overrides"] for entry in history] == [{"controller.tick_s": 9.0}]
    restored = await store.restore("expert", history[0].id)
    assert isinstance(restored, ExpertConfig)
    assert restored.overrides == {"controller.tick_s": 9.0}


@pytest.mark.parametrize(
    ("overrides", "loaded"),
    [
        # below the hard limit (also: a limit tightened by a later release) -> nearest bound
        ({"controller.min_write_interval_s": 0.01}, {"controller.min_write_interval_s": 1.0}),
        ({"controller.default_settle_s": 600}, {"controller.default_settle_s": 60.0}),
        (
            {"controller.default_max_switches_per_hour": 3600},
            {"controller.default_max_switches_per_hour": 30},
        ),
        # a key the registry no longer knows, or a value of the wrong type -> default
        ({"controller.unknown": 1, "controller.tick_s": 9}, {"controller.tick_s": 9.0}),
        ({"controller.reserve_w": "lots"}, {}),
        ({"tariff.export.remunerate_negative": True}, {}),
        # the combined staleness window is reduced to 180 s
        (
            {"controller.tick_s": 60, "controller.inputs_max_age_ticks": 10},
            {"controller.tick_s": 60.0, "controller.inputs_max_age_ticks": 3},
        ),
    ],
)
async def test_a_stored_document_outside_the_hard_limits_loads_clamped_never_loosened(
    db: Database, overrides: dict[str, Any], loaded: dict[str, Any]
) -> None:
    """A row patched around the API, or written under older limits, cannot loosen the safety
    layer - and cannot stop the start-up either (no stack would mean no crash recovery that
    releases held devices): the load clamps to the hard range and drops what is unknown."""
    await _store_raw(db, "expert", 1, {"schema_version": 1, "overrides": overrides})
    expert = await ConfigStore(db).get("expert")
    assert isinstance(expert, ExpertConfig)
    assert expert.overrides == loaded
    settings = apply_controller(ControlSettings(), expert)
    for name, (lo, hi) in SAFETY_LIMITS.items():
        assert lo <= getattr(settings, name) <= hi, name


async def test_a_structurally_broken_document_is_still_corrupt(db: Database) -> None:
    await _store_raw(db, "expert", 1, {"schema_version": 1, "overrides": {}, "stray": 1})
    with pytest.raises(ConfigCorruptError):
        await ConfigStore(db).get("expert")


async def test_restore_and_import_stay_strict(db: Database) -> None:
    """Only the stored current document is repaired; an import or a history entry outside the
    hard range is refused as a whole (the user sees the error, nothing is applied)."""
    store = ConfigStore(db)
    bad = {"schema_version": 1, "body": {"overrides": {"controller.default_settle_s": 600}}}
    with pytest.raises(ImportRejectedError):
        await store.import_json(
            {
                "suncore_export": 1,
                "db_schema_version": 1,
                "sections": {"expert": bad},
            }
        )
    await db.execute(
        "INSERT INTO config_history (section, schema_version, body, replaced_at)"
        " VALUES ('expert', 1, ?, 't')",
        (json.dumps(bad["body"]),),
    )
    (entry,) = await store.history("expert")
    with pytest.raises(ConfigCorruptError):
        await store.restore("expert", entry.id)


async def test_a_newer_version_is_refused(db: Database) -> None:
    await _store_raw(db, "expert", 2, {"overrides": {}})
    with pytest.raises(ConfigCorruptError):
        await ConfigStore(db).get("expert")
