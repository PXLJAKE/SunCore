# SPDX-License-Identifier: Apache-2.0
"""Fixtures: a demo-mode runtime on an accelerated fake connection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection
from pathlib import Path

import pytest

from suncore.app.main import build_runtime, utc_now
from suncore.app.runtime import Runtime
from suncore.app.settings import Settings
from suncore.ha.connection import EntityState
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import load_scenario


class RecordingConnection(FakeHAConnection):
    def __init__(self, speed: float) -> None:
        super().__init__(load_scenario("summer_day"), clock=utc_now, speed=speed)
        self.subscriptions: list[frozenset[str]] = []
        self.subscribe_error: BaseException | None = None
        self.get_states_calls = 0

    async def get_states(self) -> list[EntityState]:
        self.get_states_calls += 1
        return await super().get_states()

    async def subscribe_entities(self, entity_ids: Collection[str]) -> None:
        self.subscriptions.append(frozenset(entity_ids))
        if self.subscribe_error is not None:
            raise self.subscribe_error
        await super().subscribe_entities(entity_ids)


def demo_settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(
        demo_mode=True,
        demo_speed=kw.pop("demo_speed", 6000.0),  # type: ignore[arg-type]
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "no-dist",
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
async def demo_runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    conn = RecordingConnection(speed=6000.0)  # one 60 s step per 10 ms
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


class GatedConnection(RecordingConnection):
    """A fake whose ``connect()`` waits until the test opens the gate (HA not reachable yet)."""

    def __init__(self, speed: float = 6000.0) -> None:
        super().__init__(speed)
        self.open = asyncio.Event()

    async def connect(self) -> None:
        await self.open.wait()
        await super().connect()
