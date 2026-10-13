# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/config/<section>``, history and restore through the ASGI transport."""

from __future__ import annotations

from typing import Any

import pytest

from suncore.api import create_app
from suncore.core.config import SECTION_MODELS
from suncore.storage import HistoryEntry
from tests.api.conftest import FakeServices, client_for, make_site

VALID: dict[str, dict[str, Any]] = {
    "region": {"region_id": "de", "currency": "EUR", "timezone": "Europe/Berlin"},
    "tariff": {
        "import": {"kind": "fixed", "fixed_price": 0.3},
        "export": {"kind": "fixed", "fixed_price": 0.08},
    },
    "devices": {"devices": []},
    "goals": {"climate_before_export": True},
    "operation": {"mode": "shadow", "device_modes": {}},
    "wizard": {"completed": False, "completed_steps": ["region"]},
}


def test_every_section_is_covered_by_the_fixture() -> None:
    assert set(VALID) | {"site"} == set(SECTION_MODELS)


@pytest.mark.parametrize("section", sorted(VALID))
async def test_section_roundtrip(services: FakeServices, section: str) -> None:
    async with client_for(create_app(services)) as client:
        assert (await client.get(f"/api/v1/config/{section}")).json() is None
        put = await client.put(f"/api/v1/config/{section}", json=VALID[section])
        assert put.status_code == 200, put.text
        got = await client.get(f"/api/v1/config/{section}")
    assert got.status_code == 200
    assert got.json() == put.json()
    assert section in services.sections


async def test_tariff_uses_the_import_alias_on_the_wire(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        put = await client.put("/api/v1/config/tariff", json=VALID["tariff"])
        # The Python-side name is also accepted (documents stored before the alias existed).
        old = await client.put(
            "/api/v1/config/tariff",
            json={"import_": VALID["tariff"]["import"], "export": VALID["tariff"]["export"]},
        )
    assert "import" in put.json()
    assert "import_" not in put.json()
    assert old.status_code == 200


@pytest.mark.parametrize("section", sorted(VALID))
async def test_section_rejects_unknown_fields_and_wrong_types(
    services: FakeServices, section: str
) -> None:
    async with client_for(create_app(services)) as client:
        extra = await client.put(f"/api/v1/config/{section}", json={**VALID[section], "oops": 1})
        wrong = await client.put(f"/api/v1/config/{section}", json=["not", "an", "object"])
    assert extra.status_code == 422
    assert wrong.status_code == 422
    assert section not in services.sections


async def test_site_section_still_goes_through_the_site_methods(services: FakeServices) -> None:
    site = make_site()
    async with client_for(create_app(services)) as client:
        await client.put("/api/v1/config/site", json=site.model_dump(mode="json"))
    assert services.site == site
    assert "site" not in services.sections


async def test_unknown_section_is_not_routed(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        assert (await client.get("/api/v1/config/nope")).status_code == 404
        assert (await client.put("/api/v1/config/nope", json={})).status_code in (404, 405)


# --- history -------------------------------------------------------------------------------


def entry(entry_id: int, section: str = "goals", **body: Any) -> HistoryEntry:
    return HistoryEntry(entry_id, section, 1, "2026-10-13T12:00:00+00:00", body)


async def test_history_lists_newest_first_and_validates_the_section(
    services: FakeServices,
) -> None:
    services.history_entries["goals"] = [
        entry(2, climate_before_export=True),
        entry(1, climate_before_export=False),
    ]
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/config/goals/history")
        empty = await client.get("/api/v1/config/region/history")
        unknown = await client.get("/api/v1/config/nope/history")
    assert [e["id"] for e in r.json()] == [2, 1]
    assert r.json()[0] == {
        "id": 2,
        "section": "goals",
        "schema_version": 1,
        "replaced_at": "2026-10-13T12:00:00+00:00",
        "body": {"climate_before_export": True},
    }
    assert empty.json() == []
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "api.error.unknown_section"}


async def test_restore_makes_the_entry_current(services: FakeServices) -> None:
    services.history_entries["goals"] = [entry(7, climate_before_export=True)]
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/config/goals/history/7/restore")
        missing = await client.post("/api/v1/config/goals/history/8/restore")
        unknown = await client.post("/api/v1/config/nope/history/7/restore")
        bad_id = await client.post("/api/v1/config/goals/history/abc/restore")
    assert r.status_code == 200
    body = r.json()
    assert body["section"] == "goals"
    assert body["body"]["climate_before_export"] is True
    assert services.restored == [("goals", 7)]
    assert missing.status_code == 404
    assert missing.json() == {"detail": "api.error.history_not_found"}
    assert unknown.json() == {"detail": "api.error.unknown_section"}
    assert bad_id.status_code == 422


async def test_restore_of_a_body_that_no_longer_validates_is_a_409(
    services: FakeServices,
) -> None:
    from suncore.storage import ConfigCorruptError  # noqa: PLC0415

    async def boom(name: str, entry_id: int) -> Any:
        raise ConfigCorruptError("old body is invalid")

    services.restore_section = boom  # type: ignore[method-assign]
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/config/goals/history/1/restore")
    assert r.status_code == 409
    assert r.json() == {"detail": "api.error.history_invalid"}
