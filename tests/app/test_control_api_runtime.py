# SPDX-License-Identifier: Apache-2.0
"""The control endpoints on the real runtime (demo wiring, fake Home Assistant): HTTP -> stack."""

from __future__ import annotations

from suncore.api import create_app
from suncore.app.runtime import Runtime
from suncore.core.decisions import DecisionKind, Intent, OperatingMode, Origin, SwitchAction
from tests.api.conftest import client_for

POOL = "pool_pump"


async def test_control_state_and_the_mode_round_trip(demo_runtime: Runtime) -> None:
    async with client_for(create_app(demo_runtime)) as client:
        state = (await client.get("/api/v1/control/state")).json()
        pool = next(d for d in state["devices"] if d["device_id"] == POOL)
        assert pool["effective_mode"] == "shadow"
        assert {d["device_id"] for d in state["devices"]} == {POOL, "living_room_ac", "heating_rod"}

        # raising needs the identity token of the control state; a stale one is refused
        stale = await client.put(
            f"/api/v1/operation/devices/{POOL}", json={"mode": "assisted", "identity": "stale"}
        )
        assert stale.status_code == 409
        assert stale.json() == {"detail": "api.error.device_identity_changed"}
        ok = await client.put(
            f"/api/v1/operation/devices/{POOL}",
            json={"mode": "assisted", "identity": pool["identity"]},
        )
        assert ok.status_code == 200
        assert ok.json()["device_modes"] == {POOL: "assisted"}
        await client.put("/api/v1/operation/mode", json={"mode": "assisted"})
        pool = next(
            d
            for d in (await client.get("/api/v1/control/state")).json()["devices"]
            if d["device_id"] == POOL
        )
        assert (pool["global_mode"], pool["device_mode"], pool["effective_mode"]) == (
            "assisted",
        ) * 3

        # the stored operation document follows, and the full document cannot raise a mode
        stored = (await client.get("/api/v1/config/operation")).json()
        assert stored["mode"] == "assisted"
        raised = await client.put(
            "/api/v1/config/operation",
            json={"schema_version": 1, "mode": "automatic", "device_modes": {}},
        )
        assert raised.status_code == 409
        assert raised.json() == {"detail": "api.error.mode_raise_rejected"}
        lowered = await client.put(
            "/api/v1/config/operation",
            json={"schema_version": 1, "mode": "shadow", "device_modes": {}},
        )
        assert lowered.status_code == 200
        assert demo_runtime.modes.global_mode() is OperatingMode.SHADOW

        unknown = await client.put("/api/v1/operation/devices/nope", json={"mode": "shadow"})
        assert unknown.status_code == 404


async def test_emergency_stop_is_persisted_and_visible(demo_runtime: Runtime) -> None:
    async with client_for(create_app(demo_runtime)) as client:
        assert (await client.post("/api/v1/control/emergency-stop")).json() == {"active": True}
        assert (await client.get("/api/v1/control/state")).json()["emergency_stop"] is True
        assert await demo_runtime.runtime_store.load_emergency_stop() is True
        # nothing is accepted any more: the gate rejects with the emergency-stop reason
        decision = await demo_runtime.gate.submit(
            Intent(
                id="x",
                ts=demo_runtime.clock(),
                asset_id=POOL,
                action=SwitchAction(on=True),
                origin=Origin.USER,
            )
        )
        assert decision.kind is DecisionKind.REJECTED
        assert (await client.post("/api/v1/control/emergency-stop/clear")).json() == {
            "active": False
        }
        assert await demo_runtime.runtime_store.load_emergency_stop() is False


async def test_override_endpoint_shows_the_hold(demo_runtime: Runtime) -> None:
    async with client_for(create_app(demo_runtime)) as client:
        boost = await client.post(
            f"/api/v1/devices/{POOL}/override", json={"action": "boost", "minutes": 45}
        )
        assert boost.status_code == 200
        assert boost.json()["held"] is True
        assert boost.json()["held_until"] is not None
        state = (await client.get("/api/v1/control/state")).json()
        assert next(d for d in state["devices"] if d["device_id"] == POOL)["held"] is True
        resume = await client.post(f"/api/v1/devices/{POOL}/override", json={"action": "resume"})
        assert resume.json()["held"] is False
        unknown = await client.post("/api/v1/devices/nope/override", json={"action": "resume"})
        assert unknown.status_code == 404


async def test_decisions_are_filtered_in_the_database(demo_runtime: Runtime) -> None:
    for n, (device_id, action) in enumerate(
        [(POOL, SwitchAction(on=True)), ("living_room_ac", SwitchAction(on=False))]
    ):
        await demo_runtime.gate.submit(  # shadow mode: logged as "would have"
            Intent(
                id=f"i{n}",
                ts=demo_runtime.clock(),
                asset_id=device_id,
                action=action,
                origin=Origin.CONTROLLER,
            )
        )
    async with client_for(create_app(demo_runtime)) as client:
        everything = (await client.get("/api/v1/decisions")).json()
        pool = (await client.get("/api/v1/decisions", params={"device_id": POOL})).json()
        shadow = (await client.get("/api/v1/decisions", params={"kind": "shadow"})).json()
        none = (
            await client.get("/api/v1/decisions", params={"kind": ["accepted", "failed"]})
        ).json()
    assert {d["intent"]["asset_id"] for d in everything} >= {POOL, "living_room_ac"}
    assert {d["intent"]["asset_id"] for d in pool} == {POOL}
    assert shadow
    assert {d["kind"] for d in shadow} == {"shadow"}
    assert none == []


async def test_proposals_endpoints_on_the_runtime(demo_runtime: Runtime) -> None:
    async with client_for(create_app(demo_runtime)) as client:
        assert (await client.get("/api/v1/proposals")).json() == []
        gone = await client.post("/api/v1/proposals/never/confirm")
        assert gone.status_code == 404
        assert gone.json() == {"detail": "api.error.unknown_proposal"}
        assert (await client.post("/api/v1/proposals/never/dismiss")).status_code == 404
