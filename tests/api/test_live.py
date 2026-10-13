# SPDX-License-Identifier: Apache-2.0
"""The live WebSocket against a real in-process uvicorn."""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect

from suncore.api import create_app
from tests.api.conftest import FakeServices, make_decision, make_flows, serving


async def recv(ws: ClientConnection, wait_s: float = 3.0) -> dict[str, Any]:
    msg = json.loads(await asyncio.wait_for(ws.recv(), wait_s))
    assert isinstance(msg, dict)
    return msg


async def test_snapshot_then_flows_update(services: FakeServices) -> None:
    app = create_app(services, live_min_interval_s=0.05, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        snap = await recv(ws)
        assert snap["type"] == "snapshot"
        assert snap["health"]["ok"] is True
        assert snap["flows"]["pv_w"] == {"value": 3000.0, "quality": "ok"}
        assert snap["flows"]["house_w"]["quality"] == "stale"
        assert snap["flows"]["battery_soc_pct"] == {"value": None, "quality": "unavailable"}
        # reasons: key and args only, no rendered text
        reason = snap["decisions"][0]["reasons"][0]
        assert reason == {"key": "safety.shadow_mode", "args": {}}
        assert set(snap["decisions"][0]["intent"]["reasons"][0]) == {"key", "args"}

        services.flow = make_flows(pv=4200.0)
        services.trigger()
        update = await recv(ws)
        assert update["type"] == "flows"
        assert update["flows"]["pv_w"]["value"] == 4200.0


async def test_snapshot_without_site_has_null_flows(services: FakeServices) -> None:
    services.flow = None
    async with (
        serving(create_app(services)) as port,
        connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws,
    ):
        assert (await recv(ws))["flows"] is None


async def test_updates_are_throttled_and_coalesced(services: FakeServices) -> None:
    interval = 0.3
    app = create_app(services, live_min_interval_s=interval, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        await recv(ws)
        received: list[tuple[float, dict[str, Any]]] = []

        async def reader() -> None:
            while True:
                msg = await recv(ws, wait_s=5)
                received.append((time.monotonic(), msg))

        task = asyncio.create_task(reader())
        started = time.monotonic()
        for i in range(30):  # a burst of 30 changes within ~0.3 s
            services.flow = make_flows(pv=float(i))
            services.trigger()
            await asyncio.sleep(0.01)
        await asyncio.sleep(interval * 2)
        task.cancel()
        duration = time.monotonic() - started
        # Count-based (not gap-based) so scheduler jitter on CI cannot flake it.
        assert 1 <= len(received) <= math.ceil(duration / interval) + 1, (duration, len(received))
        assert len(received) < 30  # coalesced, nowhere near one per change
        assert received[-1][1]["flows"]["pv_w"]["value"] == 29.0  # newest value wins


async def test_health_sent_on_change_and_periodically(services: FakeServices) -> None:
    app = create_app(services, live_min_interval_s=0.01, live_health_interval_s=0.2)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        await recv(ws)
        periodic = await recv(ws)
        assert periodic["type"] == "health"
        services.ha_state = "reconnecting"
        services.trigger_health()
        seen: list[dict[str, Any]] = []
        for _ in range(4):
            seen.append(await recv(ws))
            if any(
                m["type"] == "health" and m["health"]["ha_state"] == "reconnecting" for m in seen
            ):
                break
        else:
            pytest.fail(f"no health change seen: {seen}")


async def test_flow_events_do_not_trigger_health_checks(services: FakeServices) -> None:
    app = create_app(services, live_min_interval_s=0.01, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        await recv(ws)
        baseline = services.health_calls  # the snapshot
        for i in range(5):
            services.flow = make_flows(pv=float(i))
            services.trigger()
            assert (await recv(ws))["type"] == "flows"
        assert services.health_calls == baseline
        services.ha_state = "reconnecting"
        services.trigger_health()
        msg = await recv(ws)
        assert msg["type"] == "health"
        assert msg["health"]["ha_state"] == "reconnecting"
        assert services.health_calls == baseline + 1


async def test_drain_distinguishes_disconnect_from_runtime_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from starlette.websockets import WebSocketDisconnect, WebSocketState  # noqa: PLC0415

    from suncore.api.app import _drain  # noqa: PLC0415

    class StubWS:
        def __init__(self, error: BaseException, client_state: WebSocketState) -> None:
            self.error = error
            self.client_state = client_state

        async def receive_text(self) -> str:
            raise self.error

    await _drain(StubWS(WebSocketDisconnect(1001), WebSocketState.CONNECTED))  # type: ignore[arg-type]
    await _drain(StubWS(RuntimeError("gone"), WebSocketState.DISCONNECTED))  # type: ignore[arg-type]
    assert not caplog.records
    with caplog.at_level("ERROR"):
        await _drain(StubWS(RuntimeError("bug"), WebSocketState.CONNECTED))  # type: ignore[arg-type]
    assert any("live websocket failed" in r.getMessage() for r in caplog.records)


async def test_new_decisions_are_pushed(services: FakeServices) -> None:
    app = create_app(services, live_min_interval_s=0.01, live_health_interval_s=0.1)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        await recv(ws)
        services.decision_list = [make_decision(2), make_decision(1)]
        for _ in range(10):
            msg = await recv(ws)
            if msg["type"] == "decisions":
                assert [d["intent"]["id"] for d in msg["decisions"]] == ["i2", "i1"]
                break
        else:
            pytest.fail("decisions were not pushed")


async def test_listener_removed_after_disconnect(services: FakeServices) -> None:
    async with serving(create_app(services)) as port:
        async with connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
            await recv(ws)
            assert len(services.listeners) == 1
        for _ in range(100):
            if not services.listeners:
                break
            await asyncio.sleep(0.02)
    assert services.listeners == []
