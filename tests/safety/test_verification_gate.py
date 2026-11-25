# SPDX-License-Identifier: Apache-2.0
"""The verification window in the gate (PR 7-J, ADR-0018 addendum, SEC-24).

The window is the gate's own (never persisted, never taken from the inputs provider); it opens
only in assisted mode without an emergency stop; it waives X3 only for ``Origin.USER`` intents
of that window; it ends on expiry, close, emergency stop, connection loss, handover, shutdown
and restart - and a limit written under it is released when it ends.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from suncore.core.decisions import DecisionKind, InverterLimitAction, OperatingMode, Origin
from suncore.core.inverter import INVERTER_ASSET_ID
from suncore.core.signals import Quality
from suncore.core.verification import VerificationWindow, verification_intent_id
from suncore.safety.gate import SafetyGate
from suncore.safety.runtime import MemoryRuntimeStore
from tests.safety.helpers import make_intent
from tests.safety.test_inverter_gate import ASSISTED, BASE_W, Rig, keys, rig

TEST_LIMIT = InverterLimitAction(max_export_w=5000.0)
"""Export 6 kW, house 1.5 kW (the rig's site): ``max(1.5 + 0.7, 6 - 1) kW``."""


def make_window(r: Rig, *, wid: str = "w1", length: float = 300.0) -> VerificationWindow:
    return VerificationWindow(
        id=wid,
        opened_at=r.clock.now,
        expires_at=r.clock.now + timedelta(seconds=length),
        lowest=TEST_LIMIT,
    )


def verify_rig(**kw: object) -> Rig:
    """Unverified adapter, assisted, devices could still absorb 2 kW (X3 would refuse)."""
    r = rig(mode=ASSISTED, verified=False, **kw)  # type: ignore[arg-type]
    r.at(headroom_w=2000.0)
    return r


async def submit_user(
    r: Rig,
    limit: InverterLimitAction = TEST_LIMIT,
    *,
    wid: str = "w1",
    origin: Origin = Origin.USER,
    intent_id: str | None = None,
):  # type: ignore[no-untyped-def]
    intent = make_intent(
        limit,
        asset_id=INVERTER_ASSET_ID,
        ts=r.clock.now,
        origin=origin,
        intent_id=intent_id or verification_intent_id(wid, "test"),
    )
    return await r.gate.submit(intent)


# --- the waiver ------------------------------------------------------------------------------


async def test_without_a_window_x3_refuses_the_test_limit() -> None:
    r = verify_rig()
    decision = await submit_user(r)
    assert keys(decision) == ["safety.inverter_chain_not_exhausted"]
    assert r.inverter.applied == []


async def test_inside_the_window_the_users_test_limit_is_written_and_read_back() -> None:
    r = verify_rig()
    assert r.gate.open_verification_window(make_window(r)) is None
    decision = await submit_user(r)
    assert decision.kind is DecisionKind.ACCEPTED
    assert r.inverter.applied == [TEST_LIMIT]
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert runtime.baseline == BASE_W


async def test_only_the_windows_own_user_intents_are_waived() -> None:
    r = verify_rig()
    assert r.gate.open_verification_window(make_window(r)) is None
    # Another window's id, a controller-style id, another origin: X3 as before.
    for kwargs in (
        {"intent_id": verification_intent_id("w2", "test")},
        {"intent_id": "inverter:2026-10-14T12:00:00+00:00:1"},
        {"origin": Origin.CONTROLLER},
        {"origin": Origin.PLANNER},
    ):
        decision = await submit_user(r, **kwargs)  # type: ignore[arg-type]
        assert keys(decision) == ["safety.inverter_chain_not_exhausted"], kwargs
    assert r.inverter.applied == []


async def test_a_confirmed_controller_proposal_cannot_use_the_window() -> None:
    r = verify_rig()
    r.at(headroom_w=0.0)  # the chain is exhausted: the controller's limit is proposed
    proposal = await r.submit(TEST_LIMIT)
    assert proposal.kind is DecisionKind.PROPOSED
    r.at(headroom_w=2000.0)
    assert r.gate.open_verification_window(make_window(r)) is None
    confirmed = await r.gate.confirm(proposal.intent.id)
    assert confirmed is not None
    assert keys(confirmed) == ["safety.inverter_chain_not_exhausted"]
    assert r.inverter.applied == []


async def test_nothing_below_the_windows_lowest_limit() -> None:
    r = verify_rig()
    assert r.gate.open_verification_window(make_window(r)) is None
    decision = await submit_user(r, InverterLimitAction(max_export_w=4000.0))
    assert keys(decision) == ["safety.inverter_chain_not_exhausted"]


async def test_x2_x5_and_x1_hold_inside_the_window() -> None:
    r = verify_rig()
    low = InverterLimitAction(max_export_w=1000.0)  # below house 1.5 kW + 500 W
    window = replace(make_window(r), lowest=low)
    assert r.gate.open_verification_window(window) is None
    assert "safety.inverter_may_cause_import" in keys(await submit_user(r, low))
    r.at(headroom_w=2000.0, quality=Quality.STALE)
    assert "safety.inverter_inputs_unknown" in keys(await submit_user(r))
    r.at(headroom_w=2000.0)
    above = InverterLimitAction(max_export_w=14_000.0)
    assert "safety.inverter_above_baseline" in keys(await submit_user(r, above))
    assert r.inverter.applied == []


async def test_a_window_from_the_inputs_provider_is_ignored() -> None:
    r = verify_rig()
    r.inputs["now"] = replace(r.inputs["now"], verification_window=make_window(r))
    decision = await submit_user(r)
    assert keys(decision) == ["safety.inverter_chain_not_exhausted"]
    assert r.gate.verification_window is None


# --- opening ---------------------------------------------------------------------------------


async def test_a_window_opens_only_in_assisted_mode_without_an_emergency_stop() -> None:
    shadow = rig(mode=OperatingMode.SHADOW, verified=False)
    assert shadow.gate.open_verification_window(make_window(shadow)) is not None
    automatic = rig(mode=OperatingMode.AUTOMATIC, verified=True)
    reason = automatic.gate.open_verification_window(make_window(automatic))
    assert reason is not None
    assert reason.key == "safety.verification_not_assisted"
    none = rig(mode=ASSISTED, verified=False, configured=False)
    reason = none.gate.open_verification_window(make_window(none))
    assert reason is not None
    assert reason.key == "safety.inverter_limit_not_supported"
    stopped = verify_rig()
    await stopped.gate.emergency_stop()
    reason = stopped.gate.open_verification_window(make_window(stopped))
    assert reason is not None
    assert reason.key == "safety.emergency_stop"


async def test_a_window_that_is_not_open_now_or_a_second_window_is_refused() -> None:
    r = verify_rig()
    later = VerificationWindow(
        id="w9",
        opened_at=r.clock.now + timedelta(seconds=10),
        expires_at=r.clock.now + timedelta(seconds=100),
        lowest=TEST_LIMIT,
    )
    reason = r.gate.open_verification_window(later)
    assert reason is not None
    assert reason.key == "safety.verification_window_invalid"
    assert r.gate.open_verification_window(make_window(r)) is None
    reason = r.gate.open_verification_window(make_window(r, wid="w2"))
    assert reason is not None
    assert reason.key == "safety.verification_window_busy"


# --- the end of a window ---------------------------------------------------------------------


def released_pending(r: Rig) -> bool:
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    return runtime is not None and runtime.under_control


async def accepted_under_window(r: Rig) -> None:
    assert r.gate.open_verification_window(make_window(r, length=60.0)) is None
    assert (await submit_user(r)).kind is DecisionKind.ACCEPTED


async def test_expiry_ends_the_waiver_and_releases_the_test_limit() -> None:
    r = verify_rig()
    await accepted_under_window(r)
    decisions = await r.tick(61.0)
    assert r.gate.verification_window is None
    assert any("safety.verification_window_closed" in keys(d) for d in decisions)
    assert r.inverter.released == [BASE_W]
    assert r.inverter.state == BASE_W
    # The release is read back; then a new test limit is X3-judged again.
    await r.tick(15.0)
    assert not released_pending(r)
    r.at(headroom_w=2000.0)
    assert keys(await submit_user(r)) == ["safety.inverter_chain_not_exhausted"]


async def test_closing_the_window_releases_a_held_test_limit_at_the_next_poll() -> None:
    r = verify_rig()
    await accepted_under_window(r)
    r.gate.close_verification_window("w1")
    assert r.gate.verification_window is None
    decisions = await r.tick(5.0)
    assert any("safety.verification_window_closed" in keys(d) for d in decisions)
    assert r.inverter.released == [BASE_W]


async def test_an_emergency_stop_clears_the_window_and_releases() -> None:
    r = verify_rig()
    await accepted_under_window(r)
    await r.gate.emergency_stop()
    assert r.inverter.released == [BASE_W]
    await r.gate.clear_emergency_stop()
    assert r.gate.verification_window is None
    await r.tick(15.0)
    assert not released_pending(r)
    r.at(headroom_w=2000.0)
    assert keys(await submit_user(r)) == ["safety.inverter_chain_not_exhausted"]


async def test_a_lost_connection_clears_the_window_and_the_restore_releases() -> None:
    r = verify_rig()
    await accepted_under_window(r)
    await r.gate.connection_lost()
    assert r.gate.verification_window is None
    await r.gate.connection_restored()
    await r.tick(15.0)
    assert r.inverter.released
    assert r.inverter.state == BASE_W


async def test_a_restart_starts_without_a_window_and_releases_the_test_limit() -> None:
    store = MemoryRuntimeStore()
    r = verify_rig(store=store)
    await accepted_under_window(r)
    # A crash: the process is gone; a new gate on the same store recovers.
    successor = SafetyGate(
        clock=r.clock,
        sink=r.gate._sink,
        actuators={INVERTER_ASSET_ID: r.inverter},
        modes=r.modes,
        inputs=lambda: r.inputs.get("now"),
        store=store,
        sleep=r.clock.sleep,
        inverter=r.gate.inverter,
    )
    await successor.recover()
    assert successor.verification_window is None
    assert r.inverter.released == [BASE_W]
    r.clock.advance(15.0)
    r.at(headroom_w=2000.0)
    await successor.poll()
    decision = await successor.submit(
        make_intent(
            TEST_LIMIT,
            asset_id=INVERTER_ASSET_ID,
            ts=r.clock.now,
            origin=Origin.USER,
            intent_id=verification_intent_id("w1", "test"),
        )
    )
    assert keys(decision) == ["safety.inverter_chain_not_exhausted"]


async def test_handover_and_shutdown_end_the_window() -> None:
    for end in ("handover", "shutdown"):
        r = verify_rig()
        await accepted_under_window(r)
        if end == "handover":
            await r.gate.handover(())
        else:
            await r.gate.shutdown()
        assert r.gate.verification_window is None, end
        assert r.inverter.state == BASE_W, end


async def test_a_mode_change_away_from_assisted_ends_the_waiver() -> None:
    r = verify_rig()
    assert r.gate.open_verification_window(make_window(r)) is None
    r.modes.set_inverter(OperatingMode.SHADOW)
    assert r.gate.verification_window is None
    decision = await submit_user(r)
    assert decision.kind is DecisionKind.SHADOW
    assert r.inverter.applied == []


def test_the_window_is_never_persisted() -> None:
    """Neither the runtime store nor the device runtime has a place for a window."""
    from suncore.safety.runtime import DeviceRuntime  # noqa: PLC0415

    assert not any("window" in name for name in DeviceRuntime.model_fields)
    assert not any("window" in name for name in dir(MemoryRuntimeStore))
