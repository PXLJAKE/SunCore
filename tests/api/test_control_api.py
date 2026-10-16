# SPDX-License-Identifier: Apache-2.0
"""Control endpoints (Phase 3 WP9) against the in-memory services: shapes and error keys."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from suncore.api import create_app
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from tests.api.conftest import (
    NOW,
    FakeServices,
    client_for,
    make_control_state,
    make_decision,
    serving,
)


def proposal(n: int = 1) -> Decision:
    intent = Intent(
        id=f"p{n}",
        ts=NOW,
        asset_id="pool",
        action=SwitchAction(on=True),
        reasons=(Reason(key="controller.surplus_available", args={"watts": 900}),),
        origin=Origin.CONTROLLER,
    )
    return Decision(
        intent=intent,
        kind=DecisionKind.PROPOSED,
        ts=NOW,
        reasons=(Reason(key="safety.proposed"),),
        mode=OperatingMode.ASSISTED,
        expires_at=NOW.replace(minute=2),
    )


async def test_control_state_shape(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/control/state")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "ts",
        "active",
        "emergency_stop",
        "global_mode",
        "site",
        "devices",
        "proposals",
    }
    device = body["devices"][0]
    assert device["device_id"] == "pool"
    assert device["identity"] == "current"
    assert device["effective_mode"] == "shadow"
    assert device["estimate"] == {
        "value_w": 0.0,
        "uncertainty_w": 10.0,
        "quality": "ok",
        "age_s": 0.0,
    }
    assert {"held", "held_until", "release_pending", "paused", "reasons", "last_decision"} <= set(
        device
    )


async def test_emergency_stop_and_clear(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        stop = await client.post("/api/v1/control/emergency-stop")
        state = await client.get("/api/v1/control/state")
        clear = await client.post("/api/v1/control/emergency-stop/clear")
        after = await client.get("/api/v1/control/state")
    assert stop.json() == {"active": True}
    assert state.json()["emergency_stop"] is True
    assert clear.json() == {"active": False}
    assert after.json()["emergency_stop"] is False
    assert [c[0] for c in services.calls] == ["emergency_stop", "clear_emergency_stop"]


async def test_override_boost_pause_resume(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        boost = await client.post(
            "/api/v1/devices/pool/override", json={"action": "boost", "minutes": 30}
        )
        pause = await client.post(
            "/api/v1/devices/pool/override",
            json={"action": "pause", "until": "2026-06-21T18:00:00Z"},
        )
        resume = await client.post("/api/v1/devices/pool/override", json={"action": "resume"})
    assert boost.status_code == pause.status_code == resume.status_code == 200
    assert boost.json()["held"] is True
    assert resume.json()["held"] is False
    commands = [c[1] for c in services.calls if c[0] == "override"]
    assert [cmd[1].action for cmd in commands] == ["boost", "pause", "resume"]  # type: ignore[index]
    assert commands[0][1].minutes == 30  # type: ignore[index]


async def test_override_errors_are_catalogue_keys(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        unknown = await client.post("/api/v1/devices/nope/override", json={"action": "resume"})
        no_minutes = await client.post("/api/v1/devices/pool/override", json={"action": "boost"})
        stray = await client.post(
            "/api/v1/devices/pool/override", json={"action": "resume", "minutes": 5}
        )
        bad_until = await client.post(
            "/api/v1/devices/pool/override", json={"action": "boost", "minutes": 5, "until": "x"}
        )
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "api.error.unknown_device"}
    for r in (no_minutes, stray, bad_until):
        assert r.status_code == 422
        assert r.json()["detail"] == "api.error.validation"
    assert no_minutes.json()["issues"][0]["key"] == "api.validation.override_boost_minutes"
    assert stray.json()["issues"][0]["key"] == "api.validation.override_minutes_only_boost"


async def test_a_pause_until_the_past_is_a_validation_error(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        past = await client.post(
            "/api/v1/devices/pool/override",
            json={"action": "pause", "until": "2025-06-21T18:00:00Z"},
        )
    assert past.status_code == 422
    assert past.json() == {
        "detail": "api.error.validation",
        "issues": [
            {"loc": ["body", "until"], "key": "api.validation.override_until_past", "args": {}}
        ],
    }
    assert [c for c in services.calls if c[0] == "override"] == []


async def test_proposals_confirm_and_dismiss(services: FakeServices) -> None:
    services.proposal_list = [proposal(1), proposal(2)]
    async with client_for(create_app(services)) as client:
        listed = await client.get("/api/v1/proposals")
        confirmed = await client.post("/api/v1/proposals/p1/confirm")
        dismissed = await client.post("/api/v1/proposals/p2/dismiss")
        gone = await client.post("/api/v1/proposals/p1/confirm")
        gone_dismiss = await client.post("/api/v1/proposals/p2/dismiss")
    assert [d["intent"]["id"] for d in listed.json()] == ["p1", "p2"]
    assert listed.json()[0]["kind"] == "proposed"
    assert listed.json()[0]["expires_at"] is not None
    assert confirmed.status_code == 200
    assert confirmed.json()["kind"] == "accepted"
    assert dismissed.status_code == 204
    assert dismissed.content == b""
    for r in (gone, gone_dismiss):
        assert r.status_code == 404
        assert r.json() == {"detail": "api.error.unknown_proposal"}


async def test_decisions_filter_by_device_and_kind(services: FakeServices) -> None:
    other = make_decision(2).model_copy(update={"kind": DecisionKind.ACCEPTED})
    pool = make_decision(3)
    pool = pool.model_copy(
        update={"intent": pool.intent.model_copy(update={"asset_id": "pool"})},
    )
    services.decision_list = [make_decision(1), other, pool]
    async with client_for(create_app(services)) as client:
        everything = await client.get("/api/v1/decisions")
        by_device = await client.get("/api/v1/decisions", params={"device_id": "pool"})
        by_kind = await client.get("/api/v1/decisions", params={"kind": ["accepted", "failed"]})
        bad_kind = await client.get("/api/v1/decisions", params={"kind": "wat"})
    assert len(everything.json()) == 3
    assert [d["intent"]["id"] for d in by_device.json()] == ["i3"]
    assert [d["intent"]["id"] for d in by_kind.json()] == ["i2"]
    assert (None, None) in services.decision_filters
    assert ("pool", None) in services.decision_filters
    assert (None, (DecisionKind.ACCEPTED, DecisionKind.FAILED)) in services.decision_filters
    assert bad_kind.status_code == 422


async def test_operation_mode_endpoints(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        glob = await client.put("/api/v1/operation/mode", json={"mode": "assisted"})
        shadow = await client.put("/api/v1/operation/devices/pool", json={"mode": "shadow"})
        no_identity = await client.put("/api/v1/operation/devices/pool", json={"mode": "automatic"})
        stale = await client.put(
            "/api/v1/operation/devices/pool", json={"mode": "automatic", "identity": "old"}
        )
        ok = await client.put(
            "/api/v1/operation/devices/pool", json={"mode": "automatic", "identity": "current"}
        )
        unknown = await client.put("/api/v1/operation/devices/nope", json={"mode": "shadow"})
        bad = await client.put("/api/v1/operation/mode", json={"mode": "turbo"})
    assert glob.json()["mode"] == "assisted"
    assert shadow.status_code == 200
    assert no_identity.status_code == 422
    assert no_identity.json()["issues"][0]["key"] == "api.validation.mode_identity_required"
    assert stale.status_code == 409
    assert stale.json() == {"detail": "api.error.device_identity_changed"}
    assert ok.json()["device_modes"] == {"pool": "automatic"}
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "api.error.unknown_device"}
    assert bad.status_code == 422


async def test_put_operation_document_that_raises_a_mode_is_409(services: FakeServices) -> None:
    from suncore.core.controlstate import ModeRaiseRejectedError  # noqa: PLC0415

    async def reject(name: str, model: Any) -> Any:
        raise ModeRaiseRejectedError(name)

    services.put_section = reject  # type: ignore[method-assign]
    async with client_for(create_app(services)) as client:
        r = await client.put("/api/v1/config/operation", json={"mode": "automatic"})
    assert r.status_code == 409
    assert r.json() == {"detail": "api.error.mode_raise_rejected"}


async def recv(ws: ClientConnection, wait_s: float = 3.0) -> dict[str, Any]:
    msg = json.loads(await asyncio.wait_for(ws.recv(), wait_s))
    assert isinstance(msg, dict)
    return msg


async def test_live_feed_carries_the_control_state(services: FakeServices) -> None:
    app = create_app(services, live_min_interval_s=0.05, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        snap = await recv(ws)
        assert snap["type"] == "snapshot"
        assert snap["control"]["emergency_stop"] is False
        services.control = make_control_state(emergency_stop=True)
        services.trigger_control()
        msg = await recv(ws)
        while msg["type"] != "control":  # a decisions message may interleave
            msg = await recv(ws)
        assert msg["control"]["emergency_stop"] is True
        assert msg["control"]["devices"][0]["device_id"] == "pool"
        assert not services.health_calls > 2  # a control event does not run the health check
