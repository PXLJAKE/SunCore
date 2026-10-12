# SPDX-License-Identifier: Apache-2.0
"""ConfigStore: round trip, document migrations, export/import."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from suncore.storage import (
    ConfigCorruptError,
    ConfigStore,
    Database,
    ImportRejectedError,
    SectionSpec,
    UnknownSectionError,
)
from suncore.storage.migrations import latest_version

from .conftest import make_site


async def test_put_get_round_trip(db: Database) -> None:
    store = ConfigStore(db)
    assert await store.get_site() is None
    site = make_site()
    await store.put_site(site)
    assert await store.get_site() == site
    await store.put_site(make_site("sensor.other"))
    loaded = await store.get_site()
    assert loaded is not None
    assert loaded.grid.power is not None
    assert loaded.grid.power.entity_id == "sensor.other"


async def test_unknown_section_errors(db: Database) -> None:
    store = ConfigStore(db)
    with pytest.raises(UnknownSectionError):
        await store.get("nope")
    with pytest.raises(UnknownSectionError):
        await store.put("nope", make_site())


async def test_wrong_model_type_rejected(db: Database) -> None:
    class Other(BaseModel):
        schema_version: int = 1

    with pytest.raises(TypeError):
        await ConfigStore(db).put("site", Other())


class DemoV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 2
    name: str


def _v1_to_v2(body: dict[str, Any]) -> dict[str, Any]:
    body["name"] = body.pop("title")
    return body


DEMO = {"demo": SectionSpec(DemoV2, (_v1_to_v2,))}


async def test_section_document_migration_on_get(db: Database) -> None:
    store = ConfigStore(db, DEMO)
    await db.execute(
        "INSERT INTO config_doc VALUES ('demo', 1, ?, 't')", (json.dumps({"title": "x"}),)
    )
    loaded = await store.get("demo")
    assert loaded == DemoV2(name="x")
    await store.put("demo", DemoV2(name="y"))
    row = await db.fetchone("SELECT schema_version FROM config_doc WHERE section='demo'")
    assert row == (2,)


async def test_stored_newer_or_broken_document_is_corrupt(db: Database) -> None:
    store = ConfigStore(db, DEMO)
    await db.execute("INSERT INTO config_doc VALUES ('demo', 3, '{}', 't')")
    with pytest.raises(ConfigCorruptError):
        await store.get("demo")
    await db.execute("UPDATE config_doc SET schema_version = 2, body = '{\"bad\": 1}'")
    with pytest.raises(ConfigCorruptError):
        await store.get("demo")


async def test_put_requires_current_schema_version(db: Database) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        await ConfigStore(db, DEMO).put("demo", DemoV2(schema_version=1, name="x"))


async def test_export_import_round_trip(db: Database, tmp_path: Any) -> None:
    store = ConfigStore(db)
    await store.put_site(make_site())
    exported = await store.export_json()
    assert exported["suncore_export"] == 1
    assert exported["db_schema_version"] == latest_version()
    assert exported["app_version"]
    assert exported["sections"]["site"]["schema_version"] == 1
    exported = json.loads(json.dumps(exported))  # plain JSON

    async with await Database.open(tmp_path / "other.db") as other_db:
        other = ConfigStore(other_db)
        assert await other.import_json(exported) == ["site"]
        assert await other.get_site() == make_site()
        assert await other.export_json() == exported


async def test_import_migrates_older_sections(db: Database) -> None:
    store = ConfigStore(db, DEMO)
    data = {
        "suncore_export": 1,
        "app_version": "0.0.1",
        "db_schema_version": 1,
        "sections": {"demo": {"schema_version": 1, "body": {"title": "old"}}},
    }
    await store.import_json(data)
    assert await store.get("demo") == DemoV2(name="old")


async def test_import_replaces_existing_sections(db: Database) -> None:
    store = ConfigStore(db, {**DEMO, "site": SectionSpec(type(make_site()))})
    await store.put_site(make_site())
    await store.import_json({"suncore_export": 1, "db_schema_version": 1, "sections": {}})
    assert await store.get_site() is None


def _valid_export() -> dict[str, Any]:
    return {
        "suncore_export": 1,
        "app_version": "1",
        "db_schema_version": latest_version(),
        "sections": {
            "site": {"schema_version": 1, "body": make_site("sensor.new").model_dump(mode="json")}
        },
    }


def _mutations() -> list[tuple[str, Any]]:
    def mut(f: Any) -> Any:
        d = deepcopy(_valid_export())
        f(d)
        return d

    return [
        ("not-object", ["x"]),
        ("no-header", mut(lambda d: d.pop("suncore_export"))),
        ("future-format", mut(lambda d: d.update(suncore_export=2))),
        ("newer-db", mut(lambda d: d.update(db_schema_version=latest_version() + 1))),
        ("no-db-version", mut(lambda d: d.pop("db_schema_version"))),
        ("sections-list", mut(lambda d: d.update(sections=[]))),
        (
            "unknown-section",
            mut(lambda d: d["sections"].update(zzz={"schema_version": 1, "body": {}})),
        ),
        ("newer-section", mut(lambda d: d["sections"]["site"].update(schema_version=9))),
        ("bad-version-type", mut(lambda d: d["sections"]["site"].update(schema_version="1"))),
        ("invalid-body", mut(lambda d: d["sections"]["site"].update(body={"grid": {}}))),
        ("body-not-object", mut(lambda d: d["sections"]["site"].update(body=[]))),
    ]


@pytest.mark.parametrize(("name", "data"), _mutations(), ids=[n for n, _ in _mutations()])
async def test_bad_import_rejected_without_data_loss(db: Database, name: str, data: Any) -> None:
    store = ConfigStore(db)
    original = make_site()
    await store.put_site(original)
    before = await store.export_json()
    with pytest.raises(ImportRejectedError):
        await store.import_json(data)
    assert await store.export_json() == before
    assert await store.get_site() == original


async def test_failure_during_write_rolls_back(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfigStore(db)
    await store.put_site(make_site())
    before = await store.export_json()

    def boom(_: BaseModel) -> str:
        raise OSError("disk")

    monkeypatch.setattr(ConfigStore, "_dump", staticmethod(boom))
    with pytest.raises(OSError, match="disk"):
        await store.import_json(_valid_export())
    monkeypatch.undo()
    assert await store.export_json() == before
