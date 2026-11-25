# SPDX-License-Identifier: Apache-2.0
"""EV mode and target, program arm and cancel, grid status and events (PR 6-M)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from suncore.api import create_app
from suncore.core.flex import EvMode
from tests.api.conftest import NOW, FakeServices, client_for

LATER = (NOW + timedelta(hours=6)).isoformat()


async def test_flex_of_an_unknown_device_is_404_with_a_key(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/devices/nope/flex")
    assert r.status_code == 404
    assert r.json()["detail"] == "api.error.unknown_device"


async def test_ev_mode_is_set_and_read_back(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.put("/api/v1/devices/wallbox/ev", json={"mode": "now"})
        assert r.status_code == 200
        assert r.json()["mode"] == "now"
        flex = (await client.get("/api/v1/devices/wallbox/flex")).json()
    assert flex["ev"]["mode"] == "now"
    assert flex["job"] is None
    assert services.calls[-1] == ("set_ev_state", ("wallbox", EvMode.NOW, None))


async def test_ev_target_mode_needs_a_target(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.put("/api/v1/devices/wallbox/ev", json={"mode": "target"})
        assert r.status_code == 422
        ok = await client.put(
            "/api/v1/devices/wallbox/ev",
            json={"mode": "target", "target": {"soc_pct": 80, "by": LATER}},
        )
    assert ok.status_code == 200
    assert ok.json()["target"]["soc_pct"] == 80


async def test_ev_target_needs_exactly_one_quantity(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.put(
            "/api/v1/devices/wallbox/ev",
            json={"mode": "target", "target": {"soc_pct": 80, "energy_wh": 5000, "by": LATER}},
        )
    assert r.status_code == 422


async def test_ev_on_a_device_that_is_no_charger_is_422(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.put("/api/v1/devices/pool/ev", json={"mode": "off"})
    assert r.status_code == 422
    assert r.json()["detail"] == "api.error.ev_not_an_ev"


async def test_program_arm_cancel_roundtrip(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        armed = await client.post("/api/v1/devices/washer/program/arm", json={"deadline": LATER})
        assert armed.status_code == 200
        assert armed.json()["state"] == "armed"
        flex = (await client.get("/api/v1/devices/washer/flex")).json()
        assert flex["job"]["id"] == "washer-1"
        again = await client.post("/api/v1/devices/washer/program/arm", json={"deadline": LATER})
        assert again.status_code == 409
        assert again.json()["detail"] == "api.error.program_job_open"
        cancelled = await client.post("/api/v1/devices/washer/program/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        none = await client.post("/api/v1/devices/washer/program/cancel")
    assert none.status_code == 409
    assert none.json()["detail"] == "api.error.program_no_job"


@pytest.mark.parametrize(
    ("device", "deadline", "status", "key"),
    [
        ("pool", LATER, 422, "api.error.program_not_a_program"),
        ("washer", (NOW - timedelta(hours=1)).isoformat(), 422, "api.error.program_deadline_past"),
        ("nope", LATER, 404, "api.error.unknown_device"),
    ],
)
async def test_program_arm_errors_are_keys(
    services: FakeServices, device: str, deadline: str, status: int, key: str
) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.post(f"/api/v1/devices/{device}/program/arm", json={"deadline": deadline})
    assert r.status_code == status
    assert r.json()["detail"] == key


async def test_arm_needs_an_aware_deadline(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.post(
            "/api/v1/devices/washer/program/arm", json={"deadline": "2030-01-01T10:00:00"}
        )
    assert r.status_code == 422


async def test_grid_status_and_events(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        status = (await client.get("/api/v1/grid/status")).json()
        events = (await client.get("/api/v1/grid/events?days=7")).json()
    assert status == {
        "state": "connected",
        "para14a": "inactive",
        "month": "2026-01",
        "month_peak_w": 3100.0,
        "peak_limit_w": 3100.0,
    }
    assert events["events"][0]["kind"] == "para14a_start"
    assert events["peaks"][0]["peak_w"] == 3100.0
    assert ("grid_events", 7) in services.calls
