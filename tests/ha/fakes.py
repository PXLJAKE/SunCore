# SPDX-License-Identifier: Apache-2.0
"""Test doubles: a fake Home Assistant WebSocket server and a fake clock.

The server follows the message shapes verified against ``home-assistant/core``
(``websocket_api/auth.py``, ``commands.py``, ``messages.py``; see
``suncore/ha/websocket.py``). It is a deliberately small re-implementation, not HA.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

TOKEN = "test-token-0123456789abcdef"


class FakeClock:
    """Manual clock; ``sleep`` blocks until ``advance`` moves time past the deadline."""

    def __init__(self) -> None:
        self.now = 0.0
        self.requested: list[float] = []
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.requested.append(delay)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (self.now + delay, future)
        self._sleepers.append(entry)
        try:
            await future
        finally:
            with suppress(ValueError):
                self._sleepers.remove(entry)

    async def settle(self, rounds: int = 5) -> None:
        for _ in range(rounds):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        target = self.now + seconds
        while True:
            due = sorted(
                (s for s in self._sleepers if s[0] <= target and not s[1].done()),
                key=lambda s: s[0],
            )
            if not due:
                break
            deadline, future = due[0]
            self.now = max(self.now, deadline)
            future.set_result(None)
            await self.settle()
        self.now = target
        await self.settle()

    def wall(self) -> datetime:
        return datetime.fromtimestamp(1_700_000_000 + self.now, UTC)


async def wait_until(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    """Poll with the real clock until ``predicate`` holds (I/O needs real loop turns)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() > deadline:
            msg = "condition not reached"
            raise AssertionError(msg)
        await asyncio.sleep(0.005)


def compress(state: dict[str, Any]) -> dict[str, Any]:
    """``State.as_compressed_state``: context as plain id, ``lu`` only if different."""
    out: dict[str, Any] = {
        "s": state["state"],
        "a": state.get("attributes", {}),
        "c": "ctx",
        "lc": state.get("lc", 1000.0),
    }
    if state.get("lu", out["lc"]) != out["lc"]:
        out["lu"] = state["lu"]
    return out


class FakeHA:
    """Fake HA server. Knobs are plain attributes; ``connections`` are live sockets."""

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.states: dict[str, dict[str, Any]] = {}
        self.respond_pong = True
        self.coalesce_replies = False
        self.withhold_initial_event = False
        self.fail_commands: dict[str, tuple[str, str]] = {}
        self.received: list[dict[str, Any]] = []
        self.auth_attempts = 0
        self.connection_count = 0
        self.connections: list[web.WebSocketResponse] = []
        self.subscriptions: dict[web.WebSocketResponse, tuple[int, set[str]]] = {}
        self.config: dict[str, Any] = {"unit_system": {"temperature": "°C"}, "version": "2026.11.0"}
        # Registries and Energy dashboard (shapes: docs/research/ha-websocket-api.md).
        self.entity_registry: dict[str, Any] = {"entity_categories": {}, "entities": []}
        self.device_registry: list[dict[str, Any]] = []
        self.area_registry: list[dict[str, Any]] = []
        self.energy_prefs: dict[str, Any] | None = None  # None = "No prefs" (not_found)
        self.service_response: Callable[[dict[str, Any]], Any] = lambda msg: {"echo": msg}
        self.event_subscriptions: dict[web.WebSocketResponse, dict[int, str]] = {}
        self._server: TestServer | None = None

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/websocket", self._handler)
        self._server = TestServer(app)
        await self._server.start_server()

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.close()
            self._server = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://{self._server.host}:{self._server.port}"

    # --- scenario helpers -------------------------------------------------------

    def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None, **extra: Any
    ) -> None:
        self.states[entity_id] = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes or {},
            **extra,
        }

    async def drop_connections(self) -> None:
        for ws in list(self.connections):
            await ws.close()

    async def send_raw(self, payload: Any) -> None:
        for ws in list(self.subscriptions):
            await ws.send_str(json.dumps(payload))

    async def push_registry_event(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        """Send a registry update event to every subscriber of ``event_type``."""
        for ws, subs in list(self.event_subscriptions.items()):
            for sub_id, subscribed in subs.items():
                if subscribed == event_type:
                    event = {"event_type": event_type, "data": data or {}, "origin": "LOCAL"}
                    await ws.send_str(json.dumps({"id": sub_id, "type": "event", "event": event}))

    async def push_event(self, event: dict[str, Any]) -> None:
        for ws, (sub_id, _) in list(self.subscriptions.items()):
            await ws.send_str(json.dumps({"id": sub_id, "type": "event", "event": event}))

    async def change(
        self,
        entity_id: str,
        state: str | None = None,
        attributes: dict[str, Any] | None = None,
        remove_attributes: list[str] | None = None,
        **times: float,
    ) -> None:
        """Mutate a state and push the compressed diff HA would send."""
        current = self.states[entity_id]
        additions: dict[str, Any] = {}
        diff: dict[str, Any] = {"+": additions}
        if state is not None and state != current["state"]:
            current["state"] = state
            additions["s"] = state
        if attributes:
            current["attributes"] = {**current["attributes"], **attributes}
            additions["a"] = attributes
        if remove_attributes:
            for key in remove_attributes:
                current["attributes"].pop(key, None)
            diff["-"] = {"a": remove_attributes}
        additions.update(times)
        await self.push_event({"c": {entity_id: diff}})

    # --- protocol -----------------------------------------------------------------

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connection_count += 1
        await ws.send_str(json.dumps({"type": "auth_required", "ha_version": "2026.11.0"}))
        auth = await ws.receive_json()
        self.auth_attempts += 1
        if auth.get("type") != "auth" or auth.get("access_token") != self.token:
            await ws.send_str(
                json.dumps({"type": "auth_invalid", "message": "Invalid access token or password"})
            )
            await ws.close()
            return ws
        await ws.send_str(json.dumps({"type": "auth_ok", "ha_version": "2026.11.0"}))
        self.connections.append(ws)
        try:
            async for msg in ws:
                if msg.type is WSMsgType.TEXT:
                    await self._on_message(ws, json.loads(msg.data))
        finally:
            self.subscriptions.pop(ws, None)
            self.event_subscriptions.pop(ws, None)
            with suppress(ValueError):
                self.connections.remove(ws)
        return ws

    async def _reply(self, ws: web.WebSocketResponse, *messages: dict[str, Any]) -> None:
        if self.coalesce_replies and len(messages) > 1:
            await ws.send_str(json.dumps(list(messages)))
        else:
            for message in messages:
                await ws.send_str(json.dumps(message))

    async def _on_message(  # noqa: PLR0912
        self, ws: web.WebSocketResponse, msg: dict[str, Any]
    ) -> None:
        self.received.append(msg)
        mid, kind = msg["id"], msg["type"]

        def ok(result: Any = None) -> dict[str, Any]:
            return {"id": mid, "type": "result", "success": True, "result": result}

        if kind in self.fail_commands:
            code, text = self.fail_commands[kind]
            error = {"code": code, "message": text}
            await self._reply(ws, {"id": mid, "type": "result", "success": False, "error": error})
        elif kind == "ping":
            if self.respond_pong:
                await self._reply(ws, {"id": mid, "type": "pong"})
        elif kind == "supported_features":
            await self._reply(ws, ok())
        elif kind == "get_states":
            result = [self._full(s) for s in self.states.values()]
            await self._reply(ws, ok(result))
        elif kind == "get_config":
            await self._reply(ws, ok(self.config))
        elif kind == "subscribe_entities":
            wanted = set(msg.get("entity_ids") or self.states)
            self.subscriptions[ws] = (mid, wanted)
            init = {"a": {e: compress(s) for e, s in self.states.items() if e in wanted}}
            if self.withhold_initial_event:
                await self._reply(ws, ok())
            else:
                await self._reply(ws, ok(), {"id": mid, "type": "event", "event": init})
        elif kind == "config/entity_registry/list_for_display":
            await self._reply(ws, ok(self.entity_registry))
        elif kind == "config/device_registry/list":
            await self._reply(ws, ok(self.device_registry))
        elif kind == "config/area_registry/list":
            await self._reply(ws, ok(self.area_registry))
        elif kind == "energy/get_prefs":
            if self.energy_prefs is None:
                error = {"code": "not_found", "message": "No prefs"}
                await self._reply(
                    ws, {"id": mid, "type": "result", "success": False, "error": error}
                )
            else:
                await self._reply(ws, ok(self.energy_prefs))
        elif kind == "subscribe_events":
            self.event_subscriptions.setdefault(ws, {})[mid] = msg["event_type"]
            await self._reply(ws, ok())
        elif kind == "unsubscribe_events":
            self.subscriptions.pop(ws, None)
            await self._reply(ws, ok())
        elif kind == "call_service":
            await self._reply(
                ws, ok({"context": {"id": "ctx"}, "response": self.service_response(msg)})
            )
        else:
            error = {"code": "unknown_command", "message": "Unknown command."}
            await self._reply(ws, {"id": mid, "type": "result", "success": False, "error": error})

    @staticmethod
    def _full(state: dict[str, Any]) -> dict[str, Any]:
        iso = datetime.fromtimestamp(state.get("lc", 1000.0), UTC).isoformat()
        reported = state.get("last_reported", iso)
        return {
            "entity_id": state["entity_id"],
            "state": state["state"],
            "attributes": state["attributes"],
            "last_changed": iso,
            "last_reported": reported,
            "last_updated": iso,
            "context": {"id": "ctx", "parent_id": None, "user_id": None},
        }
