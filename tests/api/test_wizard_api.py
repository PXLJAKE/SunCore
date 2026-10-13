# SPDX-License-Identifier: Apache-2.0
"""Entity detection, Energy dashboard, system check and preview endpoints."""

from __future__ import annotations

from suncore.api import EntityQuery, create_app
from suncore.core.decisions import Reason
from suncore.core.detection import EnergySuggestions, Suggestion
from suncore.core.signals import Quality
from suncore.core.wizard import CheckItem, PreviewItem
from tests.api.conftest import FakeServices, client_for, make_entity, make_site


async def test_entities_by_site_role(services: FakeServices) -> None:
    services.entity_list = [
        make_entity(
            "sensor.grid",
            score=60,
            criteria=("detect.criterion.domain", "detect.criterion.device_class"),
            platform="kostal_plenticore",
            area_name="Technik",
        )
    ]
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/entities", params={"role": "grid_power"})
    assert r.status_code == 200
    item = r.json()[0]
    assert item["score"] == 60
    assert item["criteria"] == ["detect.criterion.domain", "detect.criterion.device_class"]
    assert item["platform"] == "kostal_plenticore"
    assert item["area_name"] == "Technik"
    assert services.queries == [EntityQuery(role="grid_power")]


async def test_entities_by_template_role_with_device(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get(
            "/api/v1/entities",
            params={"template": "lg.thinq_climate", "role": "energy_today", "device_id": "d1"},
        )
    assert r.status_code == 200
    assert services.queries == [
        EntityQuery(role="energy_today", template_id="lg.thinq_climate", device_id="d1")
    ]


async def test_entities_errors(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        no_role = await client.get("/api/v1/entities", params={"template": "lg.thinq_climate"})
        bad_role = await client.get("/api/v1/entities", params={"role": "nope"})
        bad_template = await client.get(
            "/api/v1/entities", params={"template": "nope.nope", "role": "x"}
        )
    assert (no_role.status_code, no_role.json()) == (422, {"detail": "api.error.role_required"})
    assert (bad_role.status_code, bad_role.json()) == (404, {"detail": "api.error.unknown_role"})
    assert bad_template.status_code == 404
    assert bad_template.json() == {"detail": "api.error.unknown_template"}


async def test_entities_quality_is_serialised(services: FakeServices) -> None:
    services.entity_list = [make_entity(quality=Quality.STALE, value=None, available=False)]
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/entities")
    assert r.json()[0]["quality"] == "stale"
    assert r.json()[0]["value"] is None


async def test_energy_prefs_not_configured_and_configured(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        none = await client.get("/api/v1/wizard/energy-prefs")
        services.energy = EnergySuggestions(
            configured=True,
            suggestions=(
                Suggestion(
                    "battery.power",
                    {"entity_id": "sensor.bat", "invert_sign": True},
                    Reason(key="wizard.energy.reason.battery_rate"),
                ),
                Suggestion(
                    "battery.capacity_wh",
                    10000.0,
                    Reason(key="wizard.energy.reason.battery_capacity", args={"kwh": 10.0}),
                ),
            ),
            notes=(Reason(key="wizard.energy.note.multiple_grid", args={"count": 2}),),
            site={"battery": {"power": {"entity_id": "sensor.bat", "invert_sign": True}}},
        )
        got = await client.get("/api/v1/wizard/energy-prefs")
        services.ha_down = True
        down = await client.get("/api/v1/wizard/energy-prefs")
    assert none.json() == {"configured": False, "suggestions": [], "notes": [], "site": {}}
    body = got.json()
    assert body["configured"] is True
    assert body["suggestions"][0] == {
        "path": "battery.power",
        "value": {"entity_id": "sensor.bat", "invert_sign": True},
        "reason": {"key": "wizard.energy.reason.battery_rate", "args": {}},
    }
    assert body["suggestions"][1]["value"] == 10000.0
    assert body["notes"] == [{"key": "wizard.energy.note.multiple_grid", "args": {"count": 2}}]
    assert down.status_code == 503
    assert down.json() == {"detail": "api.error.ha_unavailable"}


async def test_check_without_body_uses_the_stored_site(services: FakeServices) -> None:
    services.check_items = [
        CheckItem("ok", Reason(key="wizard.check.units_ok")),
        CheckItem(
            "warn",
            Reason(key="wizard.check.entity_stale", args={"entity_id": "sensor.a"}),
            "sensor.a",
        ),
    ]
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/wizard/check")
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "warn"
    assert body["items"][1] == {
        "severity": "warn",
        "reason": {"key": "wizard.check.entity_stale", "args": {"entity_id": "sensor.a"}},
        "subject": "sensor.a",
    }
    assert services.checked_sites == [None]


async def test_check_with_an_unsaved_site(services: FakeServices) -> None:
    site = make_site()
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/wizard/check", json={"site": site.model_dump(mode="json")})
        bad = await client.post("/api/v1/wizard/check", json={"site": {"grid": {}}})
    assert r.status_code == 200
    assert r.json() == {"severity": "ok", "items": []}
    assert services.checked_sites == [site]
    assert bad.status_code == 422


async def test_check_body_is_size_limited(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.post(
            "/api/v1/wizard/check",
            content=b"{}",
            headers={"content-length": "999999", "content-type": "application/json"},
        )
    assert r.status_code == 413
    assert r.json() == {"detail": "api.error.body_too_large"}


async def test_preview(services: FakeServices) -> None:
    services.preview_items = [
        PreviewItem(
            path="battery.power",
            entity_id="sensor.bat",
            invert_sign=True,
            raw_value=2.1,
            unit="kW",
            quality=Quality.OK,
            base_value=-2100.0,
            base_unit="W",
            expected="power",
            unit_ok=True,
        )
    ]
    fragment = {"battery": {"power": {"entity_id": "sensor.bat", "invert_sign": True}}}
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/preview", json=fragment)
        big = await client.post("/api/v1/preview", json={"too": "big"})
        not_object = await client.post("/api/v1/preview", json=[1, 2])
    assert r.status_code == 200
    assert r.json() == {
        "items": [
            {
                "path": "battery.power",
                "entity_id": "sensor.bat",
                "invert_sign": True,
                "raw_value": 2.1,
                "unit": "kW",
                "quality": "ok",
                "base_value": -2100.0,
                "base_unit": "W",
                "expected": "power",
                "unit_ok": True,
            }
        ]
    }
    assert services.fragments[0] == fragment
    assert big.status_code == 422
    assert big.json() == {"detail": "api.error.preview_too_large"}
    assert not_object.status_code == 422
