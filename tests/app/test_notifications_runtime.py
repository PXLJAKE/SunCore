# SPDX-License-Identifier: Apache-2.0
"""The confirm path of a notification against the real gate (Phase 6 WP5, control-safety).

The notification service runs on a stub connection; the proposals, the services layer and the
safety gate are the production ones (demo house, fake Home Assistant, no network).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from suncore.app.notifications import RuntimeObservationSource, RuntimeProposalPort
from suncore.app.runtime import Runtime
from suncore.core.config import NotificationsConfig, NotifyTarget
from suncore.core.control import GateInputs, PowerEstimate
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    SwitchAction,
)
from suncore.core.identity import identity_token
from suncore.notify import ConfirmOutcome, NotificationService
from suncore.sim.demo_devices import POOL_SWITCH_ID
from tests.app.test_control_runtime import POOL, device, idle, stack_of, wait_for
from tests.notify.test_service import StubConnection


@pytest.fixture
async def rig(
    demo_runtime: Runtime,
) -> AsyncIterator[tuple[Runtime, NotificationService, StubConnection]]:
    stack = await idle(stack_of(demo_runtime))
    await wait_for(lambda: stack.recovered and stack.cache.get(POOL_SWITCH_ID) is not None)
    await demo_runtime.set_global_mode(OperatingMode.ASSISTED)
    pool = device(demo_runtime, POOL)
    await demo_runtime.set_device_mode(POOL, OperatingMode.ASSISTED, identity_token(pool))
    stack.gate._inputs = lambda: GateInputs(
        surplus=PowerEstimate(value_w=9000.0, uncertainty_w=10.0),
        estimates={POOL: PowerEstimate(value_w=0.0, uncertainty_w=10.0)},
        ts=demo_runtime.clock(),
    )
    conn = StubConnection()

    async def load() -> NotificationsConfig:
        return NotificationsConfig(
            targets=(NotifyTarget(kind="service", id="mobile_app_phone"),),
            quiet_hours={"enabled": False},  # type: ignore[arg-type]
        )

    service = NotificationService(
        connection=conn,
        source=RuntimeObservationSource(demo_runtime),
        proposals=RuntimeProposalPort(demo_runtime),
        load_config=load,
        clock=demo_runtime.clock,
        poll_s=3600,
    )
    await service.start()
    for _ in range(10):
        await asyncio.sleep(0)
    yield demo_runtime, service, conn
    await service.stop()


async def propose(runtime: Runtime, intent_id: str = "n-1") -> Decision:
    stack = stack_of(runtime)
    view = stack.cache.get(POOL_SWITCH_ID)
    assert view is not None
    decision = await stack.gate.submit(
        Intent(
            id=intent_id,
            ts=runtime.clock(),
            asset_id=POOL,
            action=SwitchAction(on=view.state != "on"),
            origin=Origin.CONTROLLER,
        )
    )
    assert decision.kind is DecisionKind.PROPOSED
    return decision


async def offer(runtime: Runtime, service: NotificationService, conn: StubConnection) -> str:
    await propose(runtime)
    await service._tick()
    [push] = conn.pushes("mobile_app_phone")
    return str(next(b["action"] for b in push["data"]["actions"] if "CONFIRM" in b["action"]))


async def settle(service: NotificationService) -> None:
    for _ in range(10):
        tasks = list(service._tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks)


async def test_a_tap_confirms_through_the_same_gate_path_as_the_panel(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, service, conn = rig
    action = await offer(runtime, service, conn)
    conn.tap(action)
    await settle(service)
    log = await runtime.decisions(20, device_id=POOL)
    confirmed = [d for d in log if d.intent.id == "n-1" and d.intent.origin is Origin.USER]
    assert confirmed, "the confirmation is a USER decision of the gate"
    assert runtime.proposals() == []
    assert conn.pushes("mobile_app_phone")[-1]["title"] in {
        "SunCore: confirmed",
        "SunCore: not switched",
    }


async def test_a_second_tap_does_not_confirm_again(rig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime, service, conn = rig
    calls: list[str] = []
    original = Runtime.confirm_proposal

    async def spy(self: Runtime, intent_id: str) -> Decision:
        calls.append(intent_id)
        return await original(self, intent_id)

    monkeypatch.setattr(Runtime, "confirm_proposal", spy)
    action = await offer(runtime, service, conn)
    for _ in range(3):
        conn.tap(action)
    await settle(service)
    assert calls == ["n-1"]


async def test_a_proposal_the_panel_already_handled_is_not_confirmed_again(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, service, conn = rig
    action = await offer(runtime, service, conn)
    await runtime.confirm_proposal("n-1")  # the user was faster in the panel
    before = len(await runtime.decisions(50, device_id=POOL))
    conn.tap(action)
    await settle(service)
    assert len(await runtime.decisions(50, device_id=POOL)) == before
    assert conn.pushes("mobile_app_phone")[-1]["title"] == "SunCore: proposal no longer valid"


async def test_an_expired_proposal_cannot_be_confirmed(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, service, conn = rig
    action = await offer(runtime, service, conn)
    decision = runtime.proposals()[0]
    assert decision.expires_at is not None
    runtime.clock = lambda real=runtime.clock: real() + timedelta(hours=1)  # type: ignore[method-assign,misc]
    service._clock = runtime.clock
    conn.tap(action)
    await settle(service)
    log = await runtime.decisions(20, device_id=POOL)
    assert not [d for d in log if d.intent.origin is Origin.USER and d.intent.id == "n-1"]


async def test_lowering_the_mode_drops_the_proposal_and_the_tap_does_nothing(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, service, conn = rig
    action = await offer(runtime, service, conn)
    await runtime.set_device_mode(POOL, OperatingMode.SHADOW, None)
    conn.tap(action)
    await settle(service)
    log = await runtime.decisions(20, device_id=POOL)
    assert not [d for d in log if d.kind is DecisionKind.ACCEPTED]
    assert runtime.modes.device_mode(POOL) is OperatingMode.SHADOW  # never raised


async def test_a_tap_never_raises_the_mode_or_clears_the_emergency_stop(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, service, conn = rig
    action = await offer(runtime, service, conn)
    await runtime.emergency_stop()
    modes_before = (runtime.modes.global_mode(), runtime.modes.device_mode(POOL))
    conn.tap(action)
    await settle(service)
    assert runtime.control_state().emergency_stop is True
    assert (runtime.modes.global_mode(), runtime.modes.device_mode(POOL)) == modes_before
    log = await runtime.decisions(20, device_id=POOL)
    assert not [d for d in log if d.kind is DecisionKind.ACCEPTED and d.intent.id == "n-1"]


async def test_the_port_reports_gone_for_an_unknown_proposal(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, _, _ = rig
    assert await RuntimeProposalPort(runtime).confirm_proposal("nope") is ConfirmOutcome.GONE


async def test_the_observation_names_proposals_and_devices(rig) -> None:  # type: ignore[no-untyped-def]
    runtime, _, _ = rig
    await propose(runtime)
    observation = RuntimeObservationSource(runtime).observe(runtime.clock())
    assert [(p.intent_id, p.device_name) for p in observation.proposals] == [("n-1", "Pool pump")]
    assert {d.device_id for d in observation.devices} >= {POOL}
    assert observation.global_mode == "assisted"
