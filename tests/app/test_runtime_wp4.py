# SPDX-License-Identifier: Apache-2.0
"""WP4 wiring: hourly decision retention, SqliteRuntimeStore in the gate, device identity."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from suncore.app.main import build_runtime
from suncore.app.runtime import PRUNE_INTERVAL_S, Runtime
from suncore.core.config import DevicesConfig, OperationConfig
from suncore.core.decisions import OperatingMode
from suncore.core.identity import identity_token
from suncore.storage import SqliteRuntimeStore
from tests.storage.test_decision_log import make_decision

from .conftest import RecordingConnection, demo_settings


async def test_prune_runs_at_start_and_hourly_with_injected_sleep(
    demo_runtime: Runtime,
) -> None:
    task = demo_runtime._prune_task
    assert task is not None
    task.cancel()  # replace the default loop with a driven one
    await asyncio.gather(task, return_exceptions=True)
    now = demo_runtime.clock()
    old = make_decision(0).model_copy(update={"ts": now - timedelta(days=40)})
    fresh = make_decision(1).model_copy(update={"ts": now})
    await demo_runtime.decision_log.append(old)

    ticks: asyncio.Queue[float] = asyncio.Queue()
    release = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        await ticks.put(seconds)
        await release.wait()
        release.clear()

    demo_runtime.sleep = fake_sleep
    task = asyncio.create_task(demo_runtime._prune_loop())
    assert await asyncio.wait_for(ticks.get(), 5) == PRUNE_INTERVAL_S == 3600.0
    assert await demo_runtime.decision_log.count() == 0  # pruned on the first pass
    await demo_runtime.decision_log.append(old)
    await demo_runtime.decision_log.append(fresh)
    release.set()  # "one hour later"
    await asyncio.wait_for(ticks.get(), 5)
    assert await demo_runtime.decision_log.count() == 1
    task.cancel()


async def test_prune_failure_is_logged_not_raised(demo_runtime: Runtime) -> None:
    await demo_runtime.db.close()
    assert await demo_runtime.prune_decisions() == 0
    demo_runtime._prune_task = None


async def test_gate_uses_sqlite_store_inside_the_demo_tempdir(tmp_path: Path) -> None:
    runtime = await build_runtime(
        demo_settings(tmp_path), env={}, connection=RecordingConnection(speed=6000.0)
    )
    try:
        assert isinstance(runtime.gate._store, SqliteRuntimeStore)
        assert not (tmp_path / "data").exists()  # the demo never touches the data dir
        await runtime.gate.emergency_stop()
        assert await SqliteRuntimeStore(runtime.db).load_emergency_stop() is True
    finally:
        await runtime.stop()


async def test_put_devices_with_changed_entities_resets_the_device_mode(
    demo_runtime: Runtime,
) -> None:
    devices = demo_runtime.devices
    assert devices is not None
    first = devices.devices[0]
    await demo_runtime.put_section("devices", devices)  # same identities
    await demo_runtime.set_device_mode(first.id, OperatingMode.AUTOMATIC, identity_token(first))
    await demo_runtime.put_section("devices", devices)
    op = await demo_runtime.get_section("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {first.id: OperatingMode.AUTOMATIC}
    assert demo_runtime.modes.device_mode(first.id) is OperatingMode.AUTOMATIC
    moved = first.model_copy(
        update={
            "entities": {
                r: ref.model_copy(update={"entity_id": ref.entity_id + "_x"})
                for r, ref in first.entities.items()
            }
        }
    )
    await demo_runtime.put_section("devices", DevicesConfig(devices=[moved, *devices.devices[1:]]))
    op = await demo_runtime.get_section("operation")
    assert isinstance(op, OperationConfig)
    assert op.device_modes == {}
    assert demo_runtime.modes.device_mode(first.id) is OperatingMode.SHADOW  # the gate sees it
