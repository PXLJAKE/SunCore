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
    "forecast": {
        "sources": [
            {"template_id": "solcast.pv", "entities": {"today": {"entity_id": "sensor.fc_today"}}}
        ],
    },
    "devices": {"devices": []},
    "goals": {"climate_before_export": True},
    "operation": {"mode": "shadow", "device_modes": {}},
    "wizard": {"completed": False, "completed_steps": ["region"]},
    "battery": {
        "model": {"capacity_wh": 10_000, "max_charge_w": 5000, "max_discharge_w": 5000},
        "strategy": "prefer_full",
    },
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


async def test_tariff_formula_on_an_all_in_template_is_rejected(services: FakeServices) -> None:
    """m6: Tibber's price is all-in; a formula on top would count fees and VAT twice."""
    dynamic = {"kind": "dynamic", "template_id": "tibber.price"}
    formula = {"grid_fee": 0.08, "vat_pct": 19}
    export = VALID["tariff"]["export"]
    async with client_for(create_app(services)) as client:
        bad = await client.put(
            "/api/v1/config/tariff",
            json={"import": {**dynamic, "formula": formula}, "export": export},
        )
        plain = await client.put(
            "/api/v1/config/tariff", json={"import": dynamic, "export": export}
        )
        entity = await client.put(  # no template: price_includes unknown, the user decides
            "/api/v1/config/tariff",
            json={
                "import": {
                    "kind": "dynamic",
                    "price_entity": {"entity_id": "sensor.spot"},
                    "formula": formula,
                },
                "export": export,
            },
        )
    assert bad.status_code == 422
    assert bad.json() == {
        "detail": "api.error.validation",
        "issues": [
            {
                "loc": ["body", "import", "formula"],
                "key": "api.validation.import_formula_all_in",
                "args": {"template_id": "tibber.price"},
            }
        ],
    }
    assert plain.status_code == 200
    assert entity.status_code == 200


async def test_the_users_price_statement_decides_whether_a_formula_is_allowed(
    services: FakeServices,
) -> None:
    """ENTSO-E is spot-only by default but may carry the user's modifier template and VAT."""
    formula = {"grid_fee": 0.08, "vat_pct": 19}
    export = VALID["tariff"]["export"]

    def body(template: str, includes: str | None) -> dict[str, Any]:
        imp: dict[str, Any] = {"kind": "dynamic", "template_id": template, "formula": formula}
        if includes is not None:
            imp["price_includes"] = includes
        return {"import": imp, "export": export}

    async with client_for(create_app(services)) as client:
        spot_default = await client.put("/api/v1/config/tariff", json=body("entsoe.price", None))
        declared_all_in = await client.put(
            "/api/v1/config/tariff", json=body("entsoe.price", "all_in")
        )
        declared_spot = await client.put(  # a contract that passes the spot price through
            "/api/v1/config/tariff", json=body("tibber.price", "spot_only")
        )
    assert spot_default.status_code == 200
    assert declared_all_in.status_code == 422
    assert declared_all_in.json()["issues"][0]["key"] == "api.validation.import_formula_all_in"
    assert declared_spot.status_code == 200


async def test_required_request_fields_of_the_template_are_checked(services: FakeServices) -> None:
    """m1: Nord Pool needs the `config_entry` id; a tariff without it is rejected with a key."""
    export = VALID["tariff"]["export"]
    base = {"kind": "dynamic", "template_id": "nordpool.price"}
    async with client_for(create_app(services)) as client:
        bad = await client.put("/api/v1/config/tariff", json={"import": base, "export": export})
        optional_only = await client.put(
            "/api/v1/config/tariff",
            json={"import": {**base, "action_params": {"areas": ["SE3"]}}, "export": export},
        )
        good = await client.put(
            "/api/v1/config/tariff",
            json={"import": {**base, "action_params": {"config_entry": "01H"}}, "export": export},
        )
    assert bad.status_code == 422
    assert bad.json()["issues"] == [
        {
            "loc": ["body", "import", "action_params", "config_entry"],
            "key": "api.validation.import_param_missing",
            "args": {"template_id": "nordpool.price", "field": "config_entry"},
        }
    ]
    assert optional_only.status_code == 422
    assert good.status_code == 200
