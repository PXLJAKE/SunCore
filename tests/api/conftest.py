# SPDX-License-Identifier: Apache-2.0
"""Fakes and helpers for API tests: in-memory ``Services`` and a real in-process uvicorn."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from suncore.api import CandidateInfo, HAUnavailableError, HealthReport, PeerPolicy
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from suncore.core.signals import Quality
from suncore.core.site import EntityRef, GridConfig, SiteConfig
from suncore.storage import ImportRejectedError

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def make_site() -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
        pv=[EntityRef(entity_id="sensor.pv")],
    )


def make_decision(n: int = 1) -> Decision:
    intent = Intent(
        id=f"i{n}",
        ts=NOW,
        asset_id="asset.heater",
        action=SwitchAction(on=True),
        reasons=(Reason(key="controller.surplus", args={"watts": 1200}),),
        origin=Origin.CONTROLLER,
    )
    return Decision(
        intent=intent,
        kind=DecisionKind.SHADOW,
        ts=NOW,
        reasons=(Reason(key="safety.shadow_mode"),),
        mode=OperatingMode.SHADOW,
    )


def make_flows(pv: float = 3000.0) -> FlowSnapshot:
    return FlowSnapshot(
        grid_w=Flow(-1000.0, Quality.OK),
        pv_w=Flow(pv, Quality.OK),
        battery_w=Flow(500.0, Quality.OK),
        house_w=Flow(1500.0, Quality.STALE),
        battery_soc_pct=Flow(None, Quality.UNAVAILABLE),
        ts=NOW,
    )


class FakeServices:
    """In-memory ``Services`` with knobs for the tests."""

    version = "9.9.9"
    demo = False
    mode = "shadow"
    max_import_bytes = 200

    def __init__(self) -> None:
        self.peer_policy = PeerPolicy(enforce=False)
        self.static_dir: Path | None = None
        self.ha_state = "connected"
        self.db_ok = True
        self.site: SiteConfig | None = None
        self.flow: FlowSnapshot | None = make_flows()
        self.decision_list: list[Decision] = [make_decision(1)]
        self.candidate_list: list[CandidateInfo] = [
            CandidateInfo("sensor.pv", "PV", "power", "W", "1200", True)
        ]
        self.ha_down = False
        self.imported: list[object] = []
        self.listeners: list[Callable[[], None]] = []
        self.last_limit: int | None = None

    async def health(self) -> HealthReport:
        return HealthReport(self.ha_state, 1.5, self.db_ok, self.version)

    async def get_site(self) -> SiteConfig | None:
        return self.site

    async def put_site(self, site: SiteConfig) -> None:
        self.site = site

    async def candidates(self) -> Sequence[CandidateInfo]:
        if self.ha_down:
            raise HAUnavailableError("down")
        return self.candidate_list

    def flows(self) -> FlowSnapshot | None:
        return self.flow

    async def decisions(self, limit: int) -> list[Decision]:
        self.last_limit = limit
        return self.decision_list[:limit]

    async def export_config(self) -> dict[str, Any]:
        return {"suncore_export": 1, "sections": {}}

    async def import_config(self, data: object) -> list[str]:
        if not isinstance(data, dict) or data.get("suncore_export") != 1:
            raise ImportRejectedError("import: bad header")
        self.imported.append(data)
        return ["site"]

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self.listeners.remove(listener)

        return remove

    def trigger(self) -> None:
        for listener in list(self.listeners):
            listener()


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


def client_for(app: FastAPI, client: tuple[str, int] = ("127.0.0.1", 50000)) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=client)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@contextlib.asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[int]:
    """Run ``app`` on an ephemeral loopback port with real uvicorn; yield the port."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, lifespan="off", access_log=False
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        sockets = server.servers[0].sockets
        yield int(sockets[0].getsockname()[1])
    finally:
        server.should_exit = True
        await task
