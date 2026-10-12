# SPDX-License-Identifier: Apache-2.0
"""Demo mode end to end, without Home Assistant: runtime + API + live feed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from websockets.asyncio.client import ClientConnection, connect

from suncore.api import create_app
from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.core.site import EntityRef
from suncore.ha.connection import CommandTimeoutError
from suncore.sim.fake_ha import SIM_ENTITY_IDS, demo_site_config
from tests.api.conftest import client_for, serving
from tests.app.conftest import RecordingConnection, demo_settings


async def _recv(ws: ClientConnection, wait_s: float = 5.0) -> dict[str, Any]:
    return json.loads(await asyncio.wait_for(ws.recv(), wait_s))  # type: ignore[no-any-return]


async def _wait_for_flows(runtime: Runtime) -> None:
    for _ in range(300):
        flows = runtime.flows()
        if flows is not None and flows.pv_w.value is not None and flows.grid_w.value is not None:
            return
        await asyncio.sleep(0.01)
    pytest.fail("no flows arrived")


async def test_demo_health_and_info(demo_runtime: Runtime) -> None:
    await _wait_for_flows(demo_runtime)
    async with client_for(create_app(demo_runtime)) as client:
        health = await client.get("/health")
        info = await client.get("/api/v1/info")
        site = await client.get("/api/v1/config/site")
        entities = await client.get("/api/v1/entities")
    assert health.status_code == 200
    body = health.json()
    assert body["ha_state"] == "connected"
    assert body["db_ok"] is True
    assert body["ha_last_update_age_s"] is not None
    assert info.json()["demo"] is True
    assert info.json()["mode"] == "shadow"
    assert site.json() == demo_site_config().model_dump(mode="json")
    assert {c["entity_id"] for c in entities.json()} == set(SIM_ENTITY_IDS)


async def test_demo_live_feed_balance_closes(demo_runtime: Runtime) -> None:
    app = create_app(demo_runtime, live_min_interval_s=0.02, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        messages = [await _recv(ws)]
        while len(messages) < 8:
            messages.append(await _recv(ws))
    assert messages[0]["type"] == "snapshot"
    assert messages[0]["health"]["ok"] is True
    flow_msgs = [m for m in messages if m["type"] == "flows" and m["flows"] is not None]
    assert flow_msgs, messages
    seen_values = set()
    for m in flow_msgs:
        f = m["flows"]
        for key in ("grid_w", "pv_w", "battery_w", "house_w"):
            assert f[key]["quality"] == "ok", (key, f)
        # Core convention: pv + grid == house + battery (values are rounded to 0.1 W).
        residual = f["pv_w"]["value"] + f["grid_w"]["value"] - f["battery_w"]["value"]
        assert residual == pytest.approx(f["house_w"]["value"], abs=0.5)
        assert 0.0 <= f["battery_soc_pct"]["value"] <= 100.0
        assert f["pv_w"]["value"] >= 0.0
        seen_values.add(f["house_w"]["value"])
    assert len(seen_values) > 1  # the simulation advances


async def test_put_site_resubscribes(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    assert conn.subscriptions[-1] == frozenset(SIM_ENTITY_IDS)
    reduced = demo_site_config().model_copy(update={"house": None})
    async with client_for(create_app(demo_runtime)) as client:
        r = await client.put("/api/v1/config/site", json=reduced.model_dump(mode="json"))
        stored = await client.get("/api/v1/config/site")
    assert r.status_code == 200
    assert len(conn.subscriptions) >= 2
    assert conn.subscriptions[-1] == frozenset(SIM_ENTITY_IDS) - {"sensor.sim_house_power"}
    assert stored.json()["house"] is None
    assert (await demo_runtime.config.get_site()) == reduced


@pytest.mark.parametrize(
    "error", [CommandTimeoutError("no initial event"), TimeoutError()], ids=["wrapped", "bare"]
)
async def test_put_site_survives_subscribe_timeout(
    demo_runtime: Runtime, error: BaseException
) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    conn.subscribe_error = error
    reduced = demo_site_config().model_copy(update={"house": None})
    with structlog.testing.capture_logs() as logs:
        async with client_for(create_app(demo_runtime)) as client:
            r = await client.put("/api/v1/config/site", json=reduced.model_dump(mode="json"))
    assert r.status_code == 200
    assert (await demo_runtime.config.get_site()) == reduced  # persisted
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["ha.subscribe_failed"]


async def test_put_site_notifies_live_listeners(demo_runtime: Runtime) -> None:
    hits: list[int] = []
    remove = demo_runtime.add_change_listener(lambda: hits.append(1))
    await demo_runtime.put_site(demo_site_config())
    remove()
    assert hits


async def test_export_import_roundtrip_resubscribes(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    async with client_for(create_app(demo_runtime)) as client:
        exported = (await client.get("/api/v1/config/export")).json()
        assert exported["sections"]["site"]["body"]["pv"][0]["entity_id"] == "sensor.sim_pv_power"
        exported["sections"]["site"]["body"]["pv"] = [
            EntityRef(entity_id="sensor.sim_pv_power", invert_sign=False).model_dump()
        ]
        before = len(conn.subscriptions)
        r = await client.post("/api/v1/config/import", json=exported)
        bad = await client.post("/api/v1/config/import", json={"nope": 1})
    assert r.status_code == 200
    assert r.json() == {"imported_sections": ["site"]}
    assert len(conn.subscriptions) == before + 1
    assert bad.status_code == 422


async def test_import_over_real_limit_is_413(demo_runtime: Runtime) -> None:
    limit = demo_runtime.max_import_bytes
    async with client_for(create_app(demo_runtime)) as client:
        r = await client.post("/api/v1/config/import", content=b" " * (limit + 1))
    assert r.status_code == 413


async def test_decisions_endpoint_reads_log(demo_runtime: Runtime) -> None:
    from suncore.core.decisions import Intent, Origin, SwitchAction  # noqa: PLC0415

    gate = demo_runtime.gate
    decision = await gate.submit(
        Intent(
            id="x1",
            ts=demo_runtime.clock(),
            asset_id="asset.test",
            action=SwitchAction(on=True),
            origin=Origin.USER,
        )
    )
    async with client_for(create_app(demo_runtime)) as client:
        r = await client.get("/api/v1/decisions?limit=10")
    body = r.json()
    assert [d["intent"]["id"] for d in body] == ["x1"]
    assert body[0]["kind"] == decision.kind.value == "shadow"


async def test_health_stays_200_while_connection_is_down(tmp_path: Path) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    try:
        # Not started: the fake connection is still DISCONNECTED.
        async with client_for(create_app(runtime)) as client:
            r = await client.get("/health")
        assert r.status_code == 200  # liveness: HA being away does not fail the watchdog
        assert r.json()["ha_state"] == "disconnected"
        assert r.json()["ok"] is True
    finally:
        await runtime.stop()


async def test_demo_does_not_touch_data_dir(tmp_path: Path) -> None:
    settings = demo_settings(tmp_path)
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    await runtime.stop()
    assert not settings.data_dir.exists()


async def test_peer_policy_follows_environment(tmp_path: Path) -> None:
    conn = RecordingConnection(6000.0)
    supervised = await build_runtime(
        demo_settings(tmp_path), env={"SUPERVISOR_TOKEN": "secret-token-1"}, connection=conn
    )
    try:
        assert supervised.peer_policy.enforce is True
        assert supervised.peer_policy.allowed_peers == ("172.30.32.2",)
    finally:
        await supervised.stop()
    override = await build_runtime(
        demo_settings(tmp_path, allow_any_peer=True),
        env={"SUPERVISOR_TOKEN": "secret-token-1"},
        connection=RecordingConnection(6000.0),
    )
    try:
        assert override.peer_policy.enforce is False
    finally:
        await override.stop()
    plain = await build_runtime(
        demo_settings(tmp_path), env={}, connection=RecordingConnection(6000.0)
    )
    try:
        assert plain.peer_policy.enforce is True
        assert plain.peer_policy.allowed_peers == ("127.0.0.1", "::1")
    finally:
        await plain.stop()
