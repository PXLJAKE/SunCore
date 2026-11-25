# SPDX-License-Identifier: Apache-2.0
"""PR 6-J review B2: the evcc link starts when the first loadpoint appears after start-up."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from suncore.app.runtime import Runtime
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    LoadpointControl,
    OnOffControl,
    PowerModel,
    PowerModelKind,
)
from suncore.core.site import EntityRef

PM = PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_000)


def loadpoint() -> DeviceConfig:
    return DeviceConfig(
        id="lp",
        template_id="evcc.loadpoint",
        name="evcc",
        kind=DeviceKind.WALLBOX,
        power_model=PM,
        control=LoadpointControl(kind="loadpoint", loadpoint_id=1),
    )


def plug() -> DeviceConfig:
    return DeviceConfig(
        id="pool",
        template_id="generic.switch",
        name="Pool",
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id="switch.pool")},
        power_model=PM,
        control=OnOffControl(kind="on_off", role="switch"),
    )


class Config:
    async def get(self, name: str) -> None:
        del name


def runtime(*, started: bool, demo: bool = False) -> Any:
    return SimpleNamespace(
        demo=demo,
        evcc_link=None,
        config=Config(),
        clock=lambda: datetime(2026, 11, 24, tzinfo=UTC),
        _started=started,
    )


def bus(rt: Any, devices: list[DeviceConfig]) -> Any:
    return Runtime._evcc_bus(rt, devices)


async def test_the_first_loadpoint_after_start_up_starts_the_link() -> None:
    rt = runtime(started=True)
    assert bus(rt, [plug()]) is None  # no loadpoint, no link
    link = bus(rt, [plug(), loadpoint()])
    assert link is not None
    assert link._task is not None
    assert not link._task.done()
    await link.stop()


async def test_before_start_the_link_is_only_created_and_started_by_start() -> None:
    rt = runtime(started=False)
    link = bus(rt, [loadpoint()])
    assert link is not None
    assert link._task is None
    await link.start()
    await asyncio.sleep(0)
    assert link._task is not None
    await link.stop()


async def test_a_later_rebuild_keeps_the_running_link() -> None:
    rt = runtime(started=True)
    first = bus(rt, [loadpoint()])
    task = first._task
    assert bus(rt, [loadpoint()]) is first
    assert first._task is task
    await first.stop()


def test_demo_mode_has_no_link() -> None:
    assert bus(runtime(started=True, demo=True), [loadpoint()]) is None
