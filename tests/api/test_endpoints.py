# SPDX-License-Identifier: Apache-2.0
"""HTTP endpoints through the ASGI transport (no sockets)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from suncore.api import create_app
from tests.api.conftest import FakeServices, client_for, make_site


async def test_health_ok(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "ok": True,
        "ha_state": "connected",
        "ha_last_update_age_s": 1.5,
        "db_ok": True,
        "version": "9.9.9",
    }


@pytest.mark.parametrize(
    ("ha_state", "db_ok"),
    [("connected", False), ("auth_failed", True), ("closed", True), ("reconnecting", False)],
)
async def test_health_503(services: FakeServices, ha_state: str, db_ok: bool) -> None:
    services.ha_state, services.db_ok = ha_state, db_ok
    async with client_for(create_app(services)) as client:
        r = await client.get("/health")
    assert r.status_code == 503
    assert r.json()["ok"] is False


@pytest.mark.parametrize("ha_state", ["connecting", "reconnecting", "disconnected"])
async def test_health_stays_200_while_ha_is_away(services: FakeServices, ha_state: str) -> None:
    """Liveness for the Supervisor watchdog: an HA core restart must not restart the app."""
    services.ha_state = ha_state
    async with client_for(create_app(services)) as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["ha_state"] == ha_state


async def test_info(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/info")
    assert r.json() == {"version": "9.9.9", "demo": False, "mode": "shadow"}


async def test_site_roundtrip(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        assert (await client.get("/api/v1/config/site")).json() is None
        site = make_site()
        put = await client.put("/api/v1/config/site", json=site.model_dump(mode="json"))
        assert put.status_code == 200
        got = await client.get("/api/v1/config/site")
    assert services.site == site
    assert got.json() == site.model_dump(mode="json")


async def test_put_site_validates(services: FakeServices) -> None:
    bad = {"grid": {}, "pv": []}  # grid needs power or an import/export pair
    async with client_for(create_app(services)) as client:
        r = await client.put("/api/v1/config/site", json=bad)
        extra = await client.put(
            "/api/v1/config/site", json={**make_site().model_dump(mode="json"), "oops": 1}
        )
    assert r.status_code == 422
    assert extra.status_code == 422
    assert services.site is None


async def test_entities(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        ok = await client.get("/api/v1/entities")
        services.ha_down = True
        down = await client.get("/api/v1/entities")
    assert ok.json() == [
        {
            "entity_id": "sensor.pv",
            "name": "PV",
            "device_class": "power",
            "unit": "W",
            "state": "1200",
            "available": True,
        }
    ]
    assert down.status_code == 503
    assert down.json() == {"detail": "api.error.ha_unavailable"}


async def test_export(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/config/export")
    assert r.json()["suncore_export"] == 1


async def test_import_ok_and_rejected(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        ok = await client.post("/api/v1/config/import", json={"suncore_export": 1})
        bad = await client.post("/api/v1/config/import", json={"suncore_export": 7})
        garbage = await client.post("/api/v1/config/import", content=b"{not json")
    assert ok.status_code == 200
    assert ok.json() == {"imported_sections": ["site"]}
    assert bad.status_code == 422
    assert bad.json() == {"detail": "api.error.import_rejected"}
    assert garbage.status_code == 422
    assert garbage.json() == {"detail": "api.error.invalid_json"}


async def test_import_oversize_is_413_before_parsing(services: FakeServices) -> None:
    padding = "x" * 500  # limit of FakeServices is 200 bytes
    body = json.dumps({"suncore_export": 1, "pad": padding})
    async with client_for(create_app(services)) as client:
        r = await client.post("/api/v1/config/import", content=body)

        # No Content-Length header (chunked): the streaming guard must catch it too.
        async def chunks():  # type: ignore[no-untyped-def]
            for i in range(0, len(body), 100):
                yield body[i : i + 100].encode()

        r2 = await client.post("/api/v1/config/import", content=chunks())
    assert r.status_code == 413
    assert r.json() == {"detail": "api.error.import_too_large"}
    assert r2.status_code == 413
    assert services.imported == []


async def test_decisions_bounded(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/decisions?limit=5")
        too_big = await client.get("/api/v1/decisions?limit=100000")
        zero = await client.get("/api/v1/decisions?limit=0")
        default = await client.get("/api/v1/decisions")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["kind"] == "shadow"
    assert body[0]["reasons"] == [{"key": "safety.shadow_mode", "args": {}}]
    assert body[0]["intent"]["reasons"][0] == {"key": "controller.surplus", "args": {"watts": 1200}}
    assert too_big.status_code == 422
    assert zero.status_code == 422
    assert default.status_code == 200
    assert services.last_limit == 100


async def test_openapi_contains_live_messages(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        spec = (await client.get("/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    assert {"LiveMessage", "SnapshotMessage", "FlowsMessage", "SiteConfig"} <= schemas.keys()
    assert "/api/v1/config/site" in spec["paths"]


# --- static SPA -------------------------------------------------------------------


@pytest.fixture
def spa_dir(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("secret")
    return tmp_path


async def test_spa_served_when_present(services: FakeServices, spa_dir: Path) -> None:
    services.static_dir = spa_dir
    async with client_for(create_app(services)) as client:
        index = await client.get("/")
        asset = await client.get("/assets/app.js")
        route = await client.get("/settings/site")
        api_404 = await client.get("/api/v1/nope")
        health = await client.get("/health")
        traversal = await client.get("/..%2Fsecret.txt")
    assert index.text == "<html>spa</html>"
    assert asset.text == "console.log(1)"
    assert route.text == "<html>spa</html>"
    assert api_404.status_code == 404
    assert health.json()["ok"] is True
    assert "secret" not in traversal.text


async def test_no_spa_when_dist_missing(services: FakeServices, tmp_path: Path) -> None:
    services.static_dir = tmp_path / "missing"
    async with client_for(create_app(services)) as client:
        r = await client.get("/")
    assert r.status_code == 404


async def test_no_spa_without_index(services: FakeServices, tmp_path: Path) -> None:
    services.static_dir = tmp_path  # exists, but no index.html
    async with client_for(create_app(services)) as client:
        assert (await client.get("/anything")).status_code == 404
