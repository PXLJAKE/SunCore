# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/ha-entities/*`` through the ASGI transport."""

from __future__ import annotations

from suncore.api import create_app
from suncore.core.haentities import BrokerState, HaEntitiesStatus
from tests.api.conftest import FakeServices, client_for


async def test_status_connected(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/ha-entities/status")
    assert r.status_code == 200
    assert r.json() == {
        "state": "connected",
        "source": "supervisor",
        "entity_count": 12,
        "reason_key": None,
        "hint_key": None,
    }


async def test_status_without_broker_carries_the_hint(services: FakeServices) -> None:
    services.ha_entities = HaEntitiesStatus(
        BrokerState.NO_BROKER, "supervisor", 0, "mqtt.reason.no_broker_service"
    )
    async with client_for(create_app(services)) as client:
        body = (await client.get("/api/v1/ha-entities/status")).json()
    assert body["state"] == "no_broker"
    assert body["reason_key"] == "mqtt.reason.no_broker_service"
    assert body["hint_key"] == "mqtt.hint.install_broker"
    assert body["entity_count"] == 0


async def test_error_state_has_a_reason_but_no_install_hint(services: FakeServices) -> None:
    services.ha_entities = HaEntitiesStatus(
        BrokerState.ERROR, "manual", 0, "mqtt.reason.auth_failed"
    )
    async with client_for(create_app(services)) as client:
        body = (await client.get("/api/v1/ha-entities/status")).json()
    assert (body["state"], body["source"], body["hint_key"]) == ("error", "manual", None)
    assert body["reason_key"] == "mqtt.reason.auth_failed"


async def test_republish(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        first = await client.post("/api/v1/ha-entities/republish")
        services.ha_entities = HaEntitiesStatus(BrokerState.NO_BROKER, None, 0, None)
        second = await client.post("/api/v1/ha-entities/republish")
    assert first.json() == {"republished": True}
    assert second.json() == {"republished": False}
