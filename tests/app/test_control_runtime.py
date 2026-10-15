# SPDX-License-Identifier: Apache-2.0
"""The production composition (Phase 3 WP9): drivers, modes, life cycle, cadences, outages.

Everything runs on the fake Home Assistant of ``suncore.sim``; no network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from suncore.app.cadence import (
    DEFAULT_COUNTER_CADENCE_S,
    IDLE_REFRESH_S,
    device_cadences,
)
from suncore.app.control import ControlStack
from suncore.app.main import build_runtime
from suncore.app.modes import ConfigModes
from suncore.app.runtime import Runtime
from suncore.core.config import DevicesConfig, OperationConfig, RegionConfig
from suncore.core.control import GateInputs, PowerEstimate
from suncore.core.controlstate import (
    DeviceIdentityChangedError,
    ModeRaiseRejectedError,
    OverrideCommand,
    OverrideUntilPastError,
    UnknownDeviceError,
    UnknownProposalError,
)
from suncore.core.decisions import (
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig, DeviceKind
from suncore.core.identity import identity_token
from suncore.ha.connection import (
    ConnectionState,
    EntityState,
    EntityUpdate,
    StateContext,
)
from suncore.safety.runtime import DeviceRuntime
from suncore.sim.demo_devices import AC_B_POWER_ID, POOL_POWER_ID, POOL_SWITCH_ID
from tests.app.conftest import GatedConnection, RecordingConnection, demo_settings

POOL = "pool_pump"
AC = "living_room_ac"
ROD = "heating_rod"


async def idle(stack: ControlStack) -> ControlStack:
    """Stop the background tick so a test can drive ticks itself."""
    await stack._stop_tasks()
    return stack


def stack_of(runtime: Runtime) -> ControlStack:
    assert runtime.stack is not None
    return runtime.stack


async def wait_for(predicate: Any, timeout_s: float = 5.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def has_shadow_entry(runtime: Runtime) -> bool:
    return any(
        d.kind is DecisionKind.SHADOW and d.reasons[0].key == "safety.shadow_mode"
        for d in await runtime.decisions(50)
    )


def device(runtime: Runtime, device_id: str) -> DeviceConfig:
    assert runtime.devices is not None
    return next(d for d in runtime.devices.devices if d.id == device_id)


# --- composition -----------------------------------------------------------------------------


async def test_the_stack_is_composed_per_configured_device(demo_runtime: Runtime) -> None:
    stack = stack_of(demo_runtime)
    assert set(stack.drivers) == {POOL, AC, ROD}
    assert set(stack.models) == {POOL, AC, ROD}
    assert stack.gate.asset_ids == frozenset({POOL, AC, ROD})
    assert demo_runtime.gate is stack.gate
    # the gate sees the stored operating modes through the provider, the cache is the runtime's
    assert isinstance(demo_runtime.modes, ConfigModes)
    assert stack.cache is demo_runtime.cache
    assert stack.store is demo_runtime.runtime_store
    assert stack.gate.modes_of(POOL).effective is OperatingMode.SHADOW


async def test_the_inputs_provider_is_the_controller_loop(demo_runtime: Runtime) -> None:
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.cache.get(POOL_SWITCH_ID) is not None)
    assert stack.gate._inputs() is None  # before the first tick: unknown surplus
    result = await stack.tick()
    assert result is not None
    assert stack.gate._inputs() is result.gate_inputs  # the snapshot of the newest tick
    assert stack.gate._tz == demo_runtime._timezone()
    assert str(stack.gate._tz) == "Europe/Berlin"  # the region's, not UTC


async def test_shadow_mode_never_calls_a_service(tmp_path: Path) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    await runtime.start()
    try:
        stack = await idle(stack_of(runtime))
        await wait_for(lambda: stack.recovered)
        for _ in range(5):
            await stack.tick()
        # intents for every device, from every origin, with fresh inputs: still only "would have"
        stack.gate._inputs = lambda: GateInputs(
            surplus=PowerEstimate(value_w=9000.0, uncertainty_w=10.0), ts=runtime.clock()
        )
        for device_id, action in ((POOL, SwitchAction(on=True)), (AC, SwitchAction(on=False))):
            for origin in (Origin.CONTROLLER, Origin.USER):
                decision = await stack.gate.submit(
                    Intent(
                        id=f"{device_id}-{origin.value}",
                        ts=runtime.clock(),
                        asset_id=device_id,
                        action=action,
                        origin=origin,
                    )
                )
                assert decision.kind is DecisionKind.SHADOW
        assert conn.call_log == []
    finally:
        await runtime.stop()


async def test_an_automatic_device_writes_only_its_own_entity(tmp_path: Path) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    await runtime.start()
    try:
        stack = await idle(stack_of(runtime))
        await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
        pool = device(runtime, POOL)
        await runtime.set_global_mode(OperatingMode.AUTOMATIC)
        await runtime.set_device_mode(POOL, OperatingMode.AUTOMATIC, identity_token(pool))
        stack.gate._inputs = lambda: GateInputs(
            surplus=PowerEstimate(value_w=9000.0, uncertainty_w=10.0),
            estimates={POOL: PowerEstimate(value_w=0.0, uncertainty_w=10.0)},
            ts=runtime.clock(),
        )
        view = stack.cache.get(POOL_SWITCH_ID)
        assert view is not None
        on = view.state == "on"
        decision = await stack.gate.submit(
            Intent(
                id="pool-flip",
                ts=runtime.clock(),
                asset_id=POOL,
                action=SwitchAction(on=not on),
                origin=Origin.CONTROLLER,
            )
        )
        assert decision.kind in (DecisionKind.ACCEPTED, DecisionKind.FAILED, DecisionKind.REJECTED)
        written = {c.entity_ids for c in conn.call_log if c.error is None}
        assert written <= {(POOL_SWITCH_ID,)}  # the one entity of the one device, nothing else
        # the other devices stay in shadow: their intents are only logged
        other = await stack.gate.submit(
            Intent(
                id="ac-off",
                ts=runtime.clock(),
                asset_id=AC,
                action=SwitchAction(on=False),
                origin=Origin.CONTROLLER,
            )
        )
        assert other.kind is DecisionKind.SHADOW
        assert {c.entity_ids for c in conn.call_log if c.error is None} <= {(POOL_SWITCH_ID,)}
    finally:
        await runtime.stop()


async def test_the_demo_runs_the_controller_in_shadow_on_the_accelerated_clock(
    tmp_path: Path,
) -> None:
    """The real demo wiring: DemoClock, tick in simulated seconds, "would have" decisions."""
    settings = demo_settings(tmp_path, demo_speed=600.0)
    runtime = await build_runtime(settings, env={})
    try:
        assert runtime.demo
        assert runtime.clock is not None
        await runtime.start()
        stack = stack_of(runtime)
        assert stack.settings.tick_s == pytest.approx(60.0)  # 0.1 s of real time at speed 600
        assert stack.watchdog.cadence_s(AC_B_POWER_ID) == 300.0  # the polled sensor of the demo
        await wait_for(lambda: stack.ready)
        await wait_for(lambda: stack.last is not None)
        # "would have" entries appear in the decision log; nothing is ever written
        async with asyncio.timeout(20):
            while not await has_shadow_entry(runtime):  # noqa: ASYNC110 - polling the log
                await asyncio.sleep(0.1)
        conn = runtime.connection
        assert conn.call_log == []  # type: ignore[attr-defined]
        state = runtime.control_state()
        assert state.active
        assert {d.device_id for d in state.devices} == {POOL, AC, ROD}
        assert state.site is not None
        assert all(d.effective_mode is OperatingMode.SHADOW for d in state.devices)
    finally:
        await runtime.stop()
    assert not settings.data_dir.exists()  # the demo never touches /data


# --- life cycle ------------------------------------------------------------------------------


async def test_recover_runs_after_the_first_connect_not_before(tmp_path: Path) -> None:
    conn = GatedConnection()
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    # a crash left the pool pump under control, switched on by SunCore (the user had it off)
    await runtime.runtime_store.save_runtime(
        DeviceRuntime(
            device_id=POOL,
            baseline=SwitchAction(on=False),
            last_written=SwitchAction(on=True),
        )
    )
    await runtime.runtime_store.save_emergency_stop(True)
    try:
        await runtime.start()
        stack = stack_of(runtime)
        await asyncio.sleep(0.1)
        assert not stack.recovered  # Home Assistant is not connected: nothing to release with
        assert conn.call_log == []
        assert not stack.gate.emergency_stopped  # the persisted flag is loaded by recover()
        conn.open.set()
        await wait_for(lambda: stack.recovered)
        assert stack.gate.emergency_stopped
        # the crash leftover was released (the baseline is given back, whether or not a write
        # was needed) and the decision says why
        stored = await runtime.runtime_store.load_runtime(POOL)
        assert stored is not None
        assert stored.baseline is None
        recovery = [
            d
            for d in await runtime.decisions(20, device_id=POOL)
            if any(r.key == "safety.crash_recovery" for r in d.reasons)
        ]
        assert recovery
        assert recovery[0].kind is DecisionKind.ACCEPTED
    finally:
        await runtime.stop()


async def test_a_lost_connection_pauses_and_the_restore_releases(demo_runtime: Runtime) -> None:
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered)
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    # put the pump under control as if SunCore had switched it on
    await stack.gate._save(
        DeviceRuntime(
            device_id=POOL, baseline=SwitchAction(on=False), last_written=SwitchAction(on=True)
        )
    )
    # DISCONNECTED: the fake turns RECONNECTING back into CONNECTED on its next step (10 ms),
    # which made this test race the restore under load
    conn._set_state(ConnectionState.DISCONNECTED)
    await wait_for(lambda: stack.lost)
    await wait_for(lambda: not stack.watchdog.connected)
    assert not stack.ready
    assert stack.control_state().active is False
    before = len(conn.call_log)
    conn._set_state(ConnectionState.CONNECTED)
    await wait_for(lambda: not stack.lost)
    await wait_for(lambda: stack.watchdog.connected)
    # the restore releases what was under control (deferred or at once; the pump has no minimum
    # run time in the demo), the baseline comes back
    await wait_for(
        lambda: len(conn.call_log) > before or not stack.gate.runtime_of(POOL).under_control
    )  # type: ignore[union-attr]


async def test_no_failed_release_decisions_while_the_connection_is_lost(
    demo_runtime: Runtime,
) -> None:
    """WP8 found 230 failed-release entries in a 10 minute outage: ticks are skipped now."""
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    await stack.gate._save(
        DeviceRuntime(
            device_id=POOL, baseline=SwitchAction(on=False), last_written=SwitchAction(on=True)
        )
    )
    await runtime_decisions_reset(demo_runtime)
    conn._set_state(ConnectionState.RECONNECTING)
    await wait_for(lambda: stack.lost)
    # a deferred release that came due and a device fault would have been retried every tick
    stack.watchdog.trip(POOL, Reason(key="safety.watchdog_unavailable"), demo_runtime.clock())
    for _ in range(20):
        assert await stack.tick() is None
    assert await demo_runtime.decisions(100) == []
    assert not [c for c in conn.call_log if c.error == "not_connected"]
    # ... and the work resumes with the connection
    conn._set_state(ConnectionState.CONNECTED)
    await wait_for(lambda: stack.ready)
    assert await stack.tick() is not None


async def runtime_decisions_reset(runtime: Runtime) -> None:
    await runtime.db.execute("DELETE FROM decision")


async def test_shutdown_order_is_loop_gate_then_connection(tmp_path: Path) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    await runtime.start()
    stack = stack_of(runtime)
    await wait_for(lambda: stack.recovered)
    order: list[str] = []
    real_close, real_shutdown = stack.loop.close, stack.gate.shutdown
    real_conn_close = conn.close

    async def loop_close() -> None:
        order.append("loop.close")
        await real_close()

    async def gate_shutdown() -> None:
        order.append("gate.shutdown")
        await real_shutdown()

    async def conn_close() -> None:
        order.append("connection.close")
        await real_conn_close()

    stack.loop.close = loop_close  # type: ignore[method-assign]
    stack.gate.shutdown = gate_shutdown  # type: ignore[method-assign]
    conn.close = conn_close  # type: ignore[method-assign]
    await runtime.stop()
    assert order == ["loop.close", "gate.shutdown", "connection.close"]
    assert stack.stopped


async def test_shutdown_releases_what_is_under_control_before_the_connection_closes(
    tmp_path: Path,
) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    await runtime.start()
    stack = await idle(stack_of(runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    view = stack.cache.get(POOL_SWITCH_ID)
    assert view is not None
    await stack.gate._save(
        DeviceRuntime(
            device_id=POOL,
            baseline=SwitchAction(on=view.state != "on"),  # a state that differs: a real write
            last_written=SwitchAction(on=view.state == "on"),
        )
    )
    await runtime.stop()
    writes = [c for c in conn.call_log if c.entity_ids == (POOL_SWITCH_ID,) and c.error is None]
    assert writes, "the baseline was not restored on shutdown"


# --- configuration changes -----------------------------------------------------------------------


async def test_changing_the_devices_rebuilds_the_stack_and_releases_the_old_one(
    demo_runtime: Runtime,
) -> None:
    old = stack_of(demo_runtime)
    await wait_for(lambda: old.recovered)
    devices = demo_runtime.devices
    assert devices is not None
    same = await demo_runtime.put_section("devices", devices)
    assert demo_runtime.stack is old  # nothing changed: no rebuild
    assert isinstance(same, DevicesConfig)
    renamed = [devices.devices[0].model_copy(update={"name": "Renamed"}), *devices.devices[1:]]
    await demo_runtime.put_section("devices", DevicesConfig(devices=renamed))
    new = stack_of(demo_runtime)
    assert new is not old
    assert old.stopped
    assert new.device_configs[renamed[0].id].name == "Renamed"
    assert demo_runtime.gate is new.gate
    await wait_for(lambda: new.recovered)  # the new stack recovers on the connected link


async def test_site_and_region_changes_rebuild_the_stack(demo_runtime: Runtime) -> None:
    old = stack_of(demo_runtime)
    region = await demo_runtime.get_section("region")
    assert isinstance(region, RegionConfig)
    await demo_runtime.put_section("region", region.model_copy(update={"timezone": "UTC"}))
    assert stack_of(demo_runtime) is not old
    assert str(stack_of(demo_runtime).gate._tz) == "UTC"
    # goals do not rebuild
    stack = stack_of(demo_runtime)
    goals = await demo_runtime.get_section("goals")
    assert goals is not None
    await demo_runtime.put_section("goals", goals)
    assert stack_of(demo_runtime) is stack


async def test_the_emergency_stop_survives_a_rebuild(demo_runtime: Runtime) -> None:
    await demo_runtime.emergency_stop()
    assert demo_runtime.control_state().emergency_stop
    devices = demo_runtime.devices
    assert devices is not None
    renamed = [devices.devices[0].model_copy(update={"name": "Again"}), *devices.devices[1:]]
    await demo_runtime.put_section("devices", DevicesConfig(devices=renamed))
    new = stack_of(demo_runtime)
    await wait_for(lambda: new.recovered)
    assert new.gate.emergency_stopped
    await demo_runtime.clear_emergency_stop()
    assert not demo_runtime.control_state().emergency_stop
    assert await demo_runtime.runtime_store.load_emergency_stop() is False


# --- operating modes ---------------------------------------------------------------------------


async def test_modes_follow_the_stored_operation_section(demo_runtime: Runtime) -> None:
    gate = demo_runtime.gate
    pool = device(demo_runtime, POOL)
    assert gate.modes_of(POOL).effective is OperatingMode.SHADOW
    await demo_runtime.set_device_mode(POOL, OperatingMode.AUTOMATIC, identity_token(pool))
    # the global mode is still shadow: min(global, device)
    assert gate.modes_of(POOL).device_mode is OperatingMode.AUTOMATIC
    assert gate.modes_of(POOL).effective is OperatingMode.SHADOW
    await demo_runtime.set_global_mode(OperatingMode.AUTOMATIC)
    assert gate.modes_of(POOL).effective is OperatingMode.AUTOMATIC
    assert demo_runtime.mode == "automatic"
    assert gate.modes_of(AC).effective is OperatingMode.SHADOW  # the AC has no mode of its own
    op = await demo_runtime.get_section("operation")
    assert op == OperationConfig(
        mode=OperatingMode.AUTOMATIC, device_modes={POOL: OperatingMode.AUTOMATIC}
    )
    # lowering through the full document works and takes effect at once
    await demo_runtime.put_section("operation", OperationConfig())
    assert gate.modes_of(POOL).effective is OperatingMode.SHADOW
    assert demo_runtime.mode == "shadow"


async def test_lowering_a_mode_releases_the_device(demo_runtime: Runtime) -> None:
    stack = await idle(stack_of(demo_runtime))
    pool = device(demo_runtime, POOL)
    await demo_runtime.set_global_mode(OperatingMode.AUTOMATIC)
    await demo_runtime.set_device_mode(POOL, OperatingMode.AUTOMATIC, identity_token(pool))
    await wait_for(lambda: stack.recovered)
    await stack.gate._save(
        DeviceRuntime(
            device_id=POOL, baseline=SwitchAction(on=False), last_written=SwitchAction(on=True)
        )
    )
    await demo_runtime.set_device_mode(POOL, OperatingMode.SHADOW, None)
    runtime = stack.gate.runtime_of(POOL)
    assert runtime is not None
    released = [
        d
        for d in await demo_runtime.decisions(20, device_id=POOL)
        if d.reasons and d.reasons[0].key == "safety.released"
    ]
    assert released or not runtime.under_control


async def test_raising_a_mode_needs_the_device_identity(demo_runtime: Runtime) -> None:
    pool = device(demo_runtime, POOL)
    with pytest.raises(DeviceIdentityChangedError):
        await demo_runtime.set_device_mode(POOL, OperatingMode.AUTOMATIC, None)
    with pytest.raises(DeviceIdentityChangedError):
        await demo_runtime.set_device_mode(POOL, OperatingMode.ASSISTED, "stale")
    with pytest.raises(UnknownDeviceError):
        await demo_runtime.set_device_mode("nope", OperatingMode.SHADOW, None)
    assert demo_runtime.modes.device_mode(POOL) is OperatingMode.SHADOW
    # shadow needs no token; a re-mapped device invalidates the old one
    token = identity_token(pool)
    moved = pool.model_copy(
        update={
            "entities": {
                r: ref.model_copy(update={"entity_id": ref.entity_id + "_x"})
                for r, ref in pool.entities.items()
            }
        }
    )
    assert identity_token(moved) != token
    assert identity_token(pool.model_copy(update={"name": "other name"})) == token


async def test_the_full_document_cannot_raise_a_mode(demo_runtime: Runtime) -> None:
    with pytest.raises(ModeRaiseRejectedError):
        await demo_runtime.put_section("operation", OperationConfig(mode=OperatingMode.ASSISTED))
    with pytest.raises(ModeRaiseRejectedError):
        await demo_runtime.put_section(
            "operation", OperationConfig(device_modes={POOL: OperatingMode.AUTOMATIC})
        )
    assert demo_runtime.mode == "shadow"
    assert await demo_runtime.get_section("operation") == OperationConfig()


# --- overrides and proposals ----------------------------------------------------------


async def test_override_boost_pause_resume(demo_runtime: Runtime) -> None:
    stack = await idle(stack_of(demo_runtime))
    now = demo_runtime.clock()
    state = await demo_runtime.override_device(POOL, OverrideCommand(action="boost", minutes=30))
    assert state.held
    assert state.held_until is not None
    assert timedelta(minutes=29) < state.held_until - now <= timedelta(minutes=31)
    runtime = stack.gate.runtime_of(POOL)
    assert runtime is not None
    assert runtime.is_held(now)
    # under control as if SunCore had switched the pump on: a pause gives it back
    await stack.gate._save(
        runtime.model_copy(
            update={"baseline": SwitchAction(on=False), "last_written": SwitchAction(on=True)}
        )
    )
    state = await demo_runtime.override_device(POOL, OverrideCommand(action="pause"))
    assert state.held
    assert state.held_until is None  # until "resume now"
    paused = stack.gate.runtime_of(POOL)
    assert paused is not None
    assert not paused.under_control or paused.release_pending
    state = await demo_runtime.override_device(POOL, OverrideCommand(action="resume"))
    assert not state.held
    with pytest.raises(OverrideUntilPastError):
        await demo_runtime.override_device(
            POOL, OverrideCommand(action="pause", until=demo_runtime.clock() - timedelta(minutes=1))
        )
    assert not stack.gate.runtime_of(POOL).is_held(demo_runtime.clock())  # type: ignore[union-attr]
    with pytest.raises(UnknownDeviceError):
        await demo_runtime.override_device("nope", OverrideCommand(action="resume"))


async def test_proposals_are_listed_confirmed_and_dismissed(demo_runtime: Runtime) -> None:
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    pool = device(demo_runtime, POOL)
    await demo_runtime.set_global_mode(OperatingMode.ASSISTED)
    await demo_runtime.set_device_mode(POOL, OperatingMode.ASSISTED, identity_token(pool))
    stack.gate._inputs = lambda: GateInputs(
        surplus=PowerEstimate(value_w=9000.0, uncertainty_w=10.0),
        estimates={POOL: PowerEstimate(value_w=0.0, uncertainty_w=10.0)},
        ts=demo_runtime.clock(),
    )
    view = stack.cache.get(POOL_SWITCH_ID)
    assert view is not None
    on = view.state == "on"
    proposed = []
    for n in (1, 2):
        decision = await stack.gate.submit(
            Intent(
                id=f"prop-{n}",
                ts=demo_runtime.clock(),
                asset_id=POOL,
                action=SwitchAction(on=not on),
                origin=Origin.CONTROLLER,
            )
        )
        proposed.append(decision)
    assert proposed[0].kind is DecisionKind.PROPOSED
    listed = demo_runtime.proposals()
    assert listed[0].intent.id == "prop-1"
    assert demo_runtime.control_state().proposals[0].intent.id == "prop-1"
    await demo_runtime.dismiss_proposal("prop-1")
    with pytest.raises(UnknownProposalError):
        await demo_runtime.dismiss_proposal("prop-1")
    with pytest.raises(UnknownProposalError):
        await demo_runtime.confirm_proposal("never-existed")
    remaining = [d.intent.id for d in demo_runtime.proposals()]
    if remaining:  # the second proposal was refused by the invariants (still one switch pending)
        confirmed = await demo_runtime.confirm_proposal(remaining[0])
        assert confirmed.intent.origin is Origin.USER


async def test_the_control_state_lists_the_devices_with_their_estimates(
    demo_runtime: Runtime,
) -> None:
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.cache.get(POOL_POWER_ID) is not None)
    await stack.tick()
    state = demo_runtime.control_state()
    assert state.ts is not None
    pool = next(d for d in state.devices if d.device_id == POOL)
    assert pool.identity == identity_token(device(demo_runtime, POOL))
    assert pool.estimate is not None
    assert pool.state is not None
    assert pool.phase is not None
    assert (pool.global_mode, pool.device_mode, pool.effective_mode) == (OperatingMode.SHADOW,) * 3
    assert state.site is not None
    assert state.site.deficit_w >= 0.0


# --- context parsing feeds the override detection --------------------------------------


async def test_the_state_context_id_reaches_the_gate_observation(demo_runtime: Runtime) -> None:
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    seen: list[tuple[str, Any, str | None]] = []

    async def spy(device_id: str, observed: Any, *, context_id: str | None = None) -> Any:
        seen.append((device_id, observed, context_id))

    stack.gate.observe = spy  # type: ignore[method-assign]
    flipped = EntityState(
        entity_id=POOL_SWITCH_ID,
        state="on" if stack.cache.get(POOL_SWITCH_ID).state.state == "off" else "off",  # type: ignore[union-attr]
        context=StateContext(id="ctx-from-the-wire", user_id="alice"),
    )
    update = EntityUpdate(changed={POOL_SWITCH_ID: flipped})
    demo_runtime.cache.ingest(update)
    stack.on_update(update)
    await stack.drain()
    assert seen
    assert seen[0][0] == POOL
    assert seen[0][2] == "ctx-from-the-wire"
    # the same value again (a resync) is not fed twice
    stack.on_update(update)
    await stack.drain()
    assert len(seen) == 1
    # a state without context still reaches the gate
    other = EntityState(entity_id=POOL_SWITCH_ID, state="off" if flipped.state == "on" else "on")
    demo_runtime.cache.ingest(EntityUpdate(changed={POOL_SWITCH_ID: other}))
    stack.on_update(EntityUpdate(changed={POOL_SWITCH_ID: other}))
    await stack.drain()
    assert seen[-1][2] is None


# --- cadences ------------------------------------------------------------------------------------


async def test_cadences_of_the_power_model_sensors(demo_runtime: Runtime) -> None:
    stack = stack_of(demo_runtime)
    ac = device(demo_runtime, AC)
    # an unconfigured sensor is judged against the client's resync (idle sensors only refresh there)
    assert device_cadences([ac]) == {AC_B_POWER_ID: IDLE_REFRESH_S}
    assert stack.watchdog.cadence_s(AC_B_POWER_ID) == IDLE_REFRESH_S == 60.0
    assert stack.cache.max_age_s(AC_B_POWER_ID) == 120.0
    # the polled smartthinq sensor is configured slow: 5 minutes, stale after 10
    slow = ac.model_copy(
        update={"power_model": ac.power_model.model_copy(update={"power_update_s": 300})}
    )
    assert device_cadences([slow]) == {AC_B_POWER_ID: 300.0}
    devices = demo_runtime.devices
    assert devices is not None
    await demo_runtime.put_section(
        "devices", DevicesConfig(devices=[slow if d.id == AC else d for d in devices.devices])
    )
    rebuilt = stack_of(demo_runtime)
    assert rebuilt.watchdog.cadence_s(AC_B_POWER_ID) == 300.0
    assert rebuilt.cache.max_age_s(AC_B_POWER_ID) == 600.0
    assert rebuilt.models[AC].cadence_s == 300.0  # type: ignore[attr-defined]


def test_counter_cadence_defaults() -> None:
    from suncore.core.devices import OnOffControl, PowerModel, PowerModelKind  # noqa: PLC0415
    from suncore.core.site import EntityRef  # noqa: PLC0415

    def dev(**pm: Any) -> DeviceConfig:
        return DeviceConfig(
            id="d",
            template_id="generic.switch",
            name="d",
            kind=DeviceKind.SWITCH,
            entities={
                "switch": EntityRef(entity_id="switch.d"),
                "energy": EntityRef(entity_id="sensor.d_energy"),
            },
            power_model=PowerModel(
                kind=PowerModelKind.LEARNED, nominal_w=500, energy_role="energy", **pm
            ),
            control=OnOffControl(kind="on_off", role="switch"),
        )

    assert device_cadences([dev()]) == {"sensor.d_energy": DEFAULT_COUNTER_CADENCE_S}
    assert device_cadences([dev(counter_update_s=3600)]) == {"sensor.d_energy": 3600.0}


async def test_only_what_the_power_model_reads_is_watched(demo_runtime: Runtime) -> None:
    """A quiet informational sensor (the daily ``energy_yesterday``) cannot trip a device."""
    stack = stack_of(demo_runtime)
    ac = device(demo_runtime, AC)
    watched = stack._watched(ac, stack.models[AC])
    assert watched == [AC_B_POWER_ID]  # not the climate entity, not the energy counters


# --- power model persistence ---------------------------------------------------------------------


async def test_power_models_are_restored_saved_periodically_and_on_shutdown(
    tmp_path: Path,
) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    try:
        await runtime.power_store.put(POOL, {"restored": 1})
        stack = await idle(stack_of(runtime))

        class Model:
            def __init__(self) -> None:
                self.loaded: dict[str, Any] | None = None
                self.state: dict[str, Any] = {"a": 1}
                self.inputs: tuple[str, ...] = ()

            def dump_state(self) -> dict[str, Any]:
                return dict(self.state)

            def load_state(self, data: Any) -> None:
                self.loaded = dict(data)

            def observe(self, *_: Any) -> None: ...

            def estimate(self, *_: Any) -> PowerEstimate:
                return PowerEstimate(value_w=0.0, uncertainty_w=0.0)

        fake = Model()
        stack.models[POOL] = fake  # type: ignore[assignment]
        await stack._restore_models()
        assert fake.loaded == {"restored": 1}
        await stack.save_models()
        assert await runtime.power_store.get(POOL) == {"a": 1}
        # periodic: due after the interval, not before
        fake.state = {"a": 2}
        stack._last_save = runtime.clock()
        stack._maybe_save_models(runtime.clock())
        await stack.drain()
        assert await runtime.power_store.get(POOL) == {"a": 1}
        stack._maybe_save_models(runtime.clock() + timedelta(seconds=stack._power_save_interval_s))
        await stack.drain()
        assert await runtime.power_store.get(POOL) == {"a": 2}
        fake.state = {"a": 3}
        await stack.shutdown()  # ... and on shutdown
        assert await runtime.power_store.get(POOL) == {"a": 3}
    finally:
        await runtime.stop()


# --- review 3-B (control-safety): rebuilds and stale connection handlers ------------------------


async def test_a_rename_hands_a_running_device_over_without_breaking_its_minimum_run_time(
    demo_runtime: Runtime,
) -> None:
    """An edit that keeps the device (here: another device is renamed) used to release every
    device at once (``shutdown``), switching a compressor off seconds after SunCore had switched
    it on. The release is deferred until the minimum run time has elapsed now."""
    devices = demo_runtime.devices
    assert devices is not None
    slow = [
        d.model_copy(update={"min_run_s": 86_400}) if d.id == POOL else d for d in devices.devices
    ]
    await demo_runtime.put_section("devices", DevicesConfig(devices=slow))
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    await conn.service_caller().write("switch", "turn_on", target={"entity_id": POOL_SWITCH_ID})
    await wait_for(
        lambda: (view := stack.cache.get(POOL_SWITCH_ID)) is not None and view.state.state == "on"
    )
    now = demo_runtime.clock()
    await stack.gate._save(
        DeviceRuntime(
            device_id=POOL,
            baseline=SwitchAction(on=False),
            last_written=SwitchAction(on=True),
            last_write_ts=now,
            last_on_ts=now,
            switch_times=(now,),
        )
    )
    before = len(conn.call_log)
    renamed = [d.model_copy(update={"name": "Renamed"}) if d.id == AC else d for d in slow]
    await demo_runtime.put_section("devices", DevicesConfig(devices=renamed))
    new = await idle(stack_of(demo_runtime))
    assert new is not stack
    await wait_for(lambda: new.recovered)
    pool_writes = [c for c in conn.call_log[before:] if c.entity_ids == (POOL_SWITCH_ID,)]
    assert pool_writes == []
    runtime = new.gate.runtime_of(POOL)
    assert runtime is not None
    assert runtime.under_control
    assert runtime.release_due_ts == now + timedelta(seconds=86_400)


async def test_only_devices_that_stay_the_same_are_handed_over(demo_runtime: Runtime) -> None:
    stack = stack_of(demo_runtime)
    previous = dict(stack.device_configs)
    assert demo_runtime._unchanged_devices(previous) == frozenset(previous)
    pool = previous[POOL]
    remapped = pool.model_copy(
        update={
            "entities": {
                **pool.entities,
                "switch": pool.entities["switch"].model_copy(
                    update={"entity_id": "switch.elsewhere"}
                ),
            }
        }
    )
    assert POOL not in demo_runtime._unchanged_devices({**previous, POOL: remapped})


async def test_a_late_connected_handler_does_not_end_a_new_outage(demo_runtime: Runtime) -> None:
    """A handler of an earlier CONNECTED that runs after the link dropped again (its recover had
    to wait) used to clear the outage: ticks resumed and the watchdog read "connected"."""
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered)
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    # DISCONNECTED: the fake turns RECONNECTING back into CONNECTED on its next step
    conn._set_state(ConnectionState.DISCONNECTED)
    await wait_for(lambda: stack.lost and not stack.watchdog.connected)
    await stack._connected()  # the stale handler
    assert stack.lost
    assert not stack.watchdog.connected
    assert await stack.tick() is None
    conn._set_state(ConnectionState.CONNECTED)
    await wait_for(lambda: not stack.lost and stack.watchdog.connected)


async def test_a_device_removed_while_ha_is_down_is_released_once_it_is_back(
    demo_runtime: Runtime,
) -> None:
    """Final review 3-B (B1): the handover's release of a removed device failed (HA down) and the
    runtime was orphaned - the new stack had no actuator for it, the relay stayed on for ever."""
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    await conn.service_caller().write("switch", "turn_on", target={"entity_id": POOL_SWITCH_ID})
    await wait_for(
        lambda: (view := stack.cache.get(POOL_SWITCH_ID)) is not None and view.state.state == "on"
    )
    pool = device(demo_runtime, POOL)
    async with stack.gate._lock(POOL):
        await stack.gate._save(
            DeviceRuntime(
                device_id=POOL,
                identity=identity_token(pool),
                baseline=SwitchAction(on=False),
                last_written=SwitchAction(on=True),
                last_write_ts=demo_runtime.clock(),
            )
        )
    conn._set_state(ConnectionState.DISCONNECTED)
    await wait_for(lambda: stack.lost)
    devices = demo_runtime.devices
    assert devices is not None
    await demo_runtime.put_section(
        "devices", DevicesConfig(devices=[d for d in devices.devices if d.id != POOL])
    )
    new = stack_of(demo_runtime)
    assert POOL not in new.device_configs
    runtime = new.store and await new.store.load_runtime(POOL)
    assert runtime is not None
    assert runtime.under_control  # the failed release is kept, not dropped
    before = len(conn.call_log)
    conn._set_state(ConnectionState.CONNECTED)
    await wait_for(lambda: new.recovered)
    await wait_for(lambda: not new.gate.is_orphaned(POOL))
    # the old entity is back at its baseline (no longer subscribed: read the call log)
    offs = [
        c
        for c in conn.call_log[before:]
        if c.entity_ids == (POOL_SWITCH_ID,) and c.service == "turn_off" and c.error is None
    ]
    assert offs


async def test_the_identity_is_checked_under_the_operation_lock(demo_runtime: Runtime) -> None:
    """Final review 3-B (N5): the token was checked before the lock, so a devices put that ran
    while the mode change waited could re-map the device under a granted mode."""
    pool = device(demo_runtime, POOL)
    token = identity_token(pool)
    async with demo_runtime._operation_lock:
        pending = asyncio.ensure_future(
            demo_runtime.set_device_mode(POOL, OperatingMode.AUTOMATIC, token)
        )
        await asyncio.sleep(0)
        # what a devices put does while holding the lock: the pool is re-mapped
        devices = demo_runtime.devices
        assert devices is not None
        switch = pool.entities["switch"].model_copy(update={"entity_id": "switch.another_relay"})
        remapped = pool.model_copy(update={"entities": {**pool.entities, "switch": switch}})
        demo_runtime.devices = devices.model_copy(
            update={"devices": [remapped if d.id == POOL else d for d in devices.devices]}
        )
    with pytest.raises(DeviceIdentityChangedError):
        await pending
    assert demo_runtime.modes.device_mode(POOL) is OperatingMode.SHADOW


async def test_the_ha_time_zone_is_learnt_before_the_first_recover(tmp_path: Path) -> None:
    """Final review 3-B (N7): without a region the time zone learnt after the first connect
    rebuilt the stack while (or right after) it recovered; now the stack waits for it."""

    class SlowConfig(GatedConnection):
        async def get_config(self) -> Mapping[str, Any]:
            await asyncio.sleep(0.05)  # a real round trip: the recovery would run meanwhile
            return await super().get_config()

    conn = SlowConfig()
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    runtime.region = None  # as if no region was ever stored
    runtime.stack = runtime._new_stack()
    try:
        await runtime.start()
        first = stack_of(runtime)
        conn.open.set()
        await wait_for(lambda: (s := runtime.stack) is not None and s is not first and s.recovered)
        assert not first.recovered  # the stack built before the zone was known never recovered
        assert str(stack_of(runtime).gate._tz) == str(runtime._timezone())
        assert runtime._ha_timezone is not None
    finally:
        await runtime.stop()
