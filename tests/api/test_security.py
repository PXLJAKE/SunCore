# SPDX-License-Identifier: Apache-2.0
"""Peer check: ingress proxy only, /health open to loopback, dev override."""

from __future__ import annotations

import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from suncore.api import PeerPolicy, create_app
from tests.api.conftest import FakeServices, client_for, serving

INGRESS = ("172.30.32.2", 40000)
OTHER = ("172.30.32.5", 40000)
LOOPBACK = ("127.0.0.1", 40000)


@pytest.fixture
def enforced(services: FakeServices) -> FakeServices:
    services.peer_policy = PeerPolicy(enforce=True)
    return services


async def test_ingress_peer_allowed(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), INGRESS) as client:
        assert (await client.get("/api/v1/info")).status_code == 200
        assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/info", "/api/v1/config/export", "/openapi.json", "/"])
async def test_other_peer_denied(enforced: FakeServices, path: str) -> None:
    async with client_for(create_app(enforced), OTHER) as client:
        r = await client.get(path)
    assert r.status_code == 403
    assert r.json() == {"detail": "api.error.forbidden"}


async def test_write_from_other_peer_denied(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), OTHER) as client:
        r = await client.put("/api/v1/config/site", json={})
    assert r.status_code == 403
    assert enforced.site is None


async def test_health_exempt_only_for_loopback(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), LOOPBACK) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/api/v1/info")).status_code == 403
    async with client_for(create_app(enforced), OTHER) as client:
        assert (await client.get("/health")).status_code == 403


async def test_health_loopback_ipv6(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), ("::1", 1)) as client:
        assert (await client.get("/health")).status_code == 200


async def test_missing_client_is_denied(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), None) as client:  # type: ignore[arg-type]
        assert (await client.get("/api/v1/info")).status_code == 403


async def test_dev_override_allows_everyone(services: FakeServices) -> None:
    services.peer_policy = PeerPolicy(enforce=False)
    async with client_for(create_app(services), OTHER) as client:
        assert (await client.get("/api/v1/info")).status_code == 200


async def test_websocket_denied_for_other_peer(services: FakeServices) -> None:
    services.peer_policy = PeerPolicy(enforce=True, allowed_peers=("10.9.9.9",))
    async with serving(create_app(services)) as port:
        with pytest.raises(InvalidStatus) as exc:
            async with connect(f"ws://127.0.0.1:{port}/api/v1/live"):
                pass
    assert exc.value.response.status_code == 403


async def test_websocket_allowed_for_ingress_peer(services: FakeServices) -> None:
    services.peer_policy = PeerPolicy(enforce=True, allowed_peers=("127.0.0.1",))
    async with (
        serving(create_app(services)) as port,
        connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws,
    ):
        assert json.loads(await ws.recv())["type"] == "snapshot"
