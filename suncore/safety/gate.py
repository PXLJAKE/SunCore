# SPDX-License-Identifier: Apache-2.0
"""The safety gate: every intent passes here, nothing else touches hardware.

Phase 3 (``docs/plans/phase-3.md`` WP3). For every intent, in this order:

1. stale intent (older than ``intent_max_age_s``) -> ``rejected``;
2. global emergency stop -> ``rejected``;
3. effective mode ``min(global, device)`` from the injected :class:`ModeProvider`
   (default: everything shadow) is shadow -> ``shadow`` (invariants are still
   evaluated and reported, so "would have" entries are honest);
4. watchdog pause (site or device) -> ``rejected``;
5. manual override held -> ``held`` (intents with ``Origin.USER`` pass);
6. invariants (``invariants.evaluate``) -> ``rejected`` with every violation;
7. assisted mode and not a user intent -> ``proposed`` (valid ``proposal_ttl_s``;
   ``confirm`` re-submits with ``Origin.USER``);
8. automatic: rate limiter try-acquire -> takeover baseline (first write) ->
   ``apply`` -> settle -> read-back from the driver (entity cache) -> ``accepted`` or
   ``failed``.

Release ("back to normal", restores the takeover baseline) is triggered by the
emergency stop, ``shutdown`` (bounded by ``release_deadline_s``), a mode change to
shadow (``sync_modes``), a watchdog trip, a restored connection and the crash
recovery at start-up (``recover`` with the persisted runtime). Urgent triggers
(emergency stop, shutdown, crash recovery, site trip, repeated write failures) release
at once. Non-urgent triggers (mode change to shadow, device trip, restored connection)
stop writing at once but defer the release write until the device's minimum run or
pause time has elapsed; the deadline is persisted in the runtime and executed by
:meth:`SafetyGate.poll`, which the control loop calls every tick.

Concurrency: a per-device ``asyncio.Lock`` serialises the write path, the release and
the runtime updates. The write path re-checks the emergency stop, the effective mode,
the watchdog and the hold right before ``apply()`` (after the baseline read-back, which
awaits) and reloads the runtime after the settle wait: a device released meanwhile is
not taken over again (``safety.superseded``). Only this module calls ``apply()`` and
``release()`` (``tests/safety/test_boundary.py``). Time is injected (``clock``), the
settle wait is injected (``sleep``); nothing reads the wall clock.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from types import MappingProxyType
from typing import Final

import structlog

from suncore.core.control import ControlSettings, DeviceState, GateInputs
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    ReleaseAction,
    SetpointAction,
)
from suncore.core.devices import DeviceConfig, OverridePolicy
from suncore.core.signals import Sample
from suncore.core.site import SiteConfig
from suncore.safety import overrides
from suncore.safety.actuator import Actuator
from suncore.safety.invariants import (
    InvariantContext,
    action_matches,
    action_turns_on,
    evaluate,
    is_switch_transition,
    power_delta_w,
)
from suncore.safety.modes import AllShadow, ModeProvider, ModeView, view
from suncore.safety.ratelimit import WriteRateLimiter, is_stale_intent
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore, RuntimeStore
from suncore.safety.watchdog import (
    REASON_CONNECTION_LOST,
    SITE_SCOPE,
    Fault,
    ScopeState,
    Watchdog,
)

log = structlog.get_logger(__name__)

Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware ``datetime``."""

DecisionSink = Callable[[Decision], Awaitable[None]]
"""Receives every decision the gate makes (the decision log, the live feed)."""

StateReader = Callable[[str], DeviceState | None]
"""Observed state of a device from the entity cache (never ``get_states``)."""

DeviceLookup = Callable[[str], DeviceConfig | None]
"""Configuration of a device by id (the app provides it; safety imports no config store)."""

Sleeper = Callable[[float], Awaitable[None]]
"""Waits the settle time; tests inject a fake that advances the clock."""

REASON_SHADOW_MODE: Final = Reason(key="safety.shadow_mode")
REASON_EMERGENCY_STOP: Final = Reason(key="safety.emergency_stop")
REASON_INTENT_STALE: Final = Reason(key="safety.intent_stale")
REASON_UNKNOWN_DEVICE: Final = Reason(key="safety.unknown_device")
REASON_NO_ACTUATOR: Final = Reason(key="safety.no_actuator")
REASON_STATE_UNKNOWN: Final = Reason(key="safety.state_unknown")
REASON_PROPOSED: Final = Reason(key="safety.proposed")
REASON_PROPOSAL_EXPIRED: Final = Reason(key="safety.proposal_expired")
REASON_CONFIRMED: Final = Reason(key="safety.confirmed")
REASON_OVERRIDE_HELD: Final = Reason(key="safety.override_held")
REASON_WRITE_FAILED: Final = Reason(key="safety.write_failed")
REASON_READBACK_MISMATCH: Final = Reason(key="safety.readback_mismatch")
REASON_RELEASED: Final = Reason(key="safety.released")
REASON_RELEASE_FAILED: Final = Reason(key="safety.release_failed")
REASON_RELEASE_TIMEOUT: Final = Reason(key="safety.release_timeout")
REASON_NOT_UNDER_CONTROL: Final = Reason(key="safety.not_under_control")
REASON_SHUTDOWN: Final = Reason(key="safety.shutdown")
REASON_MODE_SHADOW: Final = Reason(key="safety.mode_shadow")
REASON_RECOVERY: Final = Reason(key="safety.crash_recovery")
REASON_WRITE_FAILURES: Final = Reason(key="safety.repeated_failures")
REASON_SUPERSEDED: Final = Reason(key="safety.superseded")
REASON_LATE_CONFIRMED: Final = Reason(key="safety.late_confirmed")
REASON_RELEASE_DEFERRED: Final = Reason(key="safety.release_deferred")
REASON_RELEASE_PENDING: Final = Reason(key="safety.release_pending")
REASON_MODE_CHANGED: Final = Reason(key="safety.mode_changed")
REASON_RATE_LIMITED: Final = Reason(key="safety.rate_limited")

MAX_CONSECUTIVE_FAILURES: Final = 3
"""After this many failed writes in a row the device scope trips (watchdog)."""


InputsProvider = Callable[[], GateInputs | None]


async def _asyncio_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class SafetyGate:
    """Single writer boundary between the control layers and the actuators.

    Holds the actuator references so that no other object can. Everything else is
    injected: modes, device configs, observed states, site inputs, the runtime store
    and the clock. Without a mode provider every asset is shadow.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        sink: DecisionSink,
        actuators: Mapping[str, Actuator] | None = None,
        modes: ModeProvider | None = None,
        devices: DeviceLookup | None = None,
        states: StateReader | None = None,
        inputs: InputsProvider | None = None,
        store: RuntimeStore | None = None,
        settings: ControlSettings | None = None,
        watchdog: Watchdog | None = None,
        tz: tzinfo = UTC,
        sleep: Sleeper | None = None,
    ) -> None:
        self._clock = clock
        self._sink = sink
        self._actuators: Mapping[str, Actuator] = MappingProxyType(dict(actuators or {}))
        self._modes: ModeProvider = modes if modes is not None else AllShadow()
        self._devices: DeviceLookup = devices if devices is not None else _no_device
        self._states: StateReader = states if states is not None else _no_state
        self._inputs: InputsProvider = inputs if inputs is not None else _no_inputs
        self._store: RuntimeStore = store if store is not None else MemoryRuntimeStore()
        self._settings = settings if settings is not None else ControlSettings()
        self._watchdog = watchdog if watchdog is not None else Watchdog(self._settings)
        self._tz = tz
        self._sleep: Sleeper = sleep if sleep is not None else _asyncio_sleep
        self._limiter = WriteRateLimiter(self._settings.min_write_interval_s)
        self._emergency_stop = False
        self._runtimes: dict[str, DeviceRuntime] = {}
        self._proposals: dict[str, Decision] = {}
        self._failures: dict[str, int] = {}
        self._site_tripped = False
        self._locks: dict[str, asyncio.Lock] = {}
        # Load increases let through against one GateInputs snapshot (key: its ts).
        self._claims_ts: datetime | None = None
        self._claims: dict[str, float] = {}

    # --- read-only views ----------------------------------------------------------

    @property
    def mode(self) -> OperatingMode:
        """The global operating mode as the provider reports it."""
        return self._modes.global_mode()

    def modes_of(self, device_id: str) -> ModeView:
        return view(self._modes, device_id)

    @property
    def emergency_stopped(self) -> bool:
        return self._emergency_stop

    @property
    def asset_ids(self) -> frozenset[str]:
        """Assets with a registered actuator (never the actuators themselves)."""
        return frozenset(self._actuators)

    @property
    def settings(self) -> ControlSettings:
        return self._settings

    @property
    def watchdog(self) -> Watchdog:
        return self._watchdog

    def runtime_of(self, device_id: str) -> DeviceRuntime | None:
        return self._runtimes.get(device_id)

    def proposals(self) -> tuple[Decision, ...]:
        """Open (unexpired) assisted-mode proposals, oldest first."""
        self._prune_proposals(self._now())
        return tuple(self._proposals.values())

    def _prune_proposals(self, now: datetime) -> None:
        expired = [
            k
            for k, d in self._proposals.items()
            if d.expires_at is not None and now >= d.expires_at
        ]
        for key in expired:
            del self._proposals[key]

    # --- lifecycle ---------------------------------------------------------------

    async def recover(self) -> None:
        """Start-up: load the persisted runtime; release devices a crash left under control.

        A release that fails because HA is not connected yet leaves the device under
        control in the runtime; :meth:`connection_restored` releases it once online.
        """
        self._emergency_stop = await self._store.load_emergency_stop()
        for runtime in await self._store.all_runtimes():
            self._runtimes[runtime.device_id] = runtime
            self._limiter.prime(runtime.device_id, runtime.last_write_ts)
        await self._release_all(REASON_RECOVERY, origin=Origin.SAFETY)

    async def shutdown(self) -> None:
        """Release every device under control within ``release_deadline_s``."""
        await self._release_all(
            REASON_SHUTDOWN, origin=Origin.SAFETY, deadline_s=self._settings.release_deadline_s
        )

    async def emergency_stop(self) -> None:
        """Global "back to normal": persisted, works from any state, idempotent.

        Every intent is rejected until :meth:`clear_emergency_stop`; devices under
        control are released (baseline restored) within ``release_deadline_s``.
        """
        self._emergency_stop = True
        await self._store.save_emergency_stop(True)
        await self._release_all(
            REASON_EMERGENCY_STOP,
            origin=Origin.USER,
            deadline_s=self._settings.release_deadline_s,
        )

    async def clear_emergency_stop(self) -> None:
        """Clear the emergency stop; idempotent. Modes are unchanged (shadow stays shadow)."""
        self._emergency_stop = False
        await self._store.save_emergency_stop(False)

    async def sync_modes(self) -> None:
        """Call after the operating modes changed: devices that fell to shadow are released
        (deferred until their minimum run/pause time has elapsed)."""
        for device_id in list(self._runtimes):
            runtime = self._runtimes[device_id]
            if runtime.under_control and self.modes_of(device_id).effective is OperatingMode.SHADOW:
                await self.release_device(
                    device_id, REASON_MODE_SHADOW, origin=Origin.USER, urgent=False
                )

    async def poll(self) -> tuple[Decision, ...]:
        """Execute deferred releases whose protection time has elapsed (call every tick)."""
        now = self._now()
        decisions: list[Decision] = []
        for device_id in list(self._runtimes):
            runtime = self._runtimes[device_id]
            if runtime.release_due_ts is None or now < runtime.release_due_ts:
                continue
            reason = (
                Reason(key=runtime.release_due_reason)
                if runtime.release_due_reason is not None
                else REASON_RELEASED
            )
            decisions.append(await self.release_device(device_id, reason, origin=Origin.SAFETY))
        return tuple(decisions)

    # --- watchdog feed -------------------------------------------------------------------

    async def check_site(
        self, site: SiteConfig, samples: Mapping[str, Sample]
    ) -> tuple[Fault, ...]:
        """Run the site plausibility checks; a trip releases every device under control."""
        now = self._now()
        faults = self._watchdog.check_site(now, site, samples)
        await self._after_site_check(now, faults)
        return faults

    async def check_device(
        self, device_id: str, samples: Mapping[str, Sample], state: DeviceState | None
    ) -> tuple[Fault, ...]:
        """Run a device's checks; a trip releases that device."""
        now = self._now()
        faults = self._watchdog.check_device(now, device_id, samples, state)
        if faults:
            runtime = self._runtimes.get(device_id)
            # A release already deferred by an earlier tick stays scheduled; re-deferring it
            # every tick would only flood the decision log.
            if runtime is not None and runtime.under_control and not runtime.release_pending:
                await self.release_device(
                    device_id, faults[0].reason, origin=Origin.SAFETY, urgent=False
                )
        return faults

    async def connection_lost(self) -> None:
        """HA connection gone: everything pauses; nothing can be written, so no release."""
        self._watchdog.connection_lost(self._now())
        self._site_tripped = True

    async def connection_restored(self) -> None:
        """Back online: release what was under control (the user may have changed things)."""
        self._watchdog.connection_restored(self._now())
        await self._release_all(REASON_CONNECTION_LOST, origin=Origin.SAFETY, urgent=False)
        self._site_tripped = self._watchdog.state(SITE_SCOPE, self._now()) is ScopeState.SAFE

    async def _after_site_check(self, now: datetime, faults: tuple[Fault, ...]) -> None:
        tripped = self._watchdog.state(SITE_SCOPE, now) is ScopeState.SAFE
        if tripped and not self._site_tripped and faults:
            await self._release_all(faults[0].reason, origin=Origin.SAFETY)
        self._site_tripped = tripped

    # --- overrides -----------------------------------------------------------------

    async def observe(
        self, device_id: str, observed: Action, *, context_id: str | None = None
    ) -> overrides.OverrideVerdict:
        """Feed an observed device state (from the cache) for override detection.

        Only configured devices are judged. A value that matches a write whose
        read-back had failed is a late confirmation: the write becomes ``last_written``
        and is logged as ``accepted`` (``safety.late_confirmed``), never as an override.
        """
        device = self._devices(device_id)
        if device is None:
            return overrides.NO_OVERRIDE
        async with self._lock(device_id):
            now = self._now()
            runtime = await self._runtime(device_id)
            settle = self._settle_s(device)
            if overrides.pending_expired(runtime, now, settle):
                runtime = overrides.clear_pending(runtime)
                await self._save(runtime)
            verdict = overrides.judge(
                observed, runtime, now, settle_s=settle, context_id=context_id
            )
            if verdict.late_confirm:
                await self._late_confirm(device, runtime, observed, now)
            elif verdict.override:
                updated = overrides.apply_verdict(
                    runtime, verdict, now, device.override_policy, self._tz
                )
                await self._save(updated)
                log.info(
                    "safety.override_detected",
                    device_id=device_id,
                    until=updated.override_until,
                    reason=verdict.reason.key if verdict.reason else None,
                )
            return verdict

    async def _late_confirm(
        self, device: DeviceConfig, runtime: DeviceRuntime, observed: Action, now: datetime
    ) -> None:
        pending = runtime.pending_action
        assert pending is not None
        confirmed = overrides.confirm_pending(runtime)
        if _was_transition(pending, device, runtime):
            target = action_turns_on(pending, device)
            if target is not None:
                confirmed = confirmed.with_switch(runtime.pending_ts or now, on=target)
        await self._save(confirmed)
        count = self._failures.get(device.id, 0)
        if count <= 1:
            self._failures.pop(device.id, None)
        else:
            self._failures[device.id] = count - 1
        await self._decide(
            Intent(
                id=f"late:{device.id}:{now.isoformat()}",
                ts=now,
                asset_id=device.id,
                action=pending,
                reasons=(REASON_LATE_CONFIRMED,),
                origin=Origin.SAFETY,
            ),
            DecisionKind.ACCEPTED,
            now,
            (REASON_LATE_CONFIRMED,),
            self.modes_of(device.id).effective,
            observed=observed,
        )

    async def hold(self, device_id: str, until: datetime | None) -> DeviceRuntime:
        """Explicit user override (boost / pause until); ``None`` holds until ``resume``."""
        async with self._lock(device_id):
            runtime = await self._runtime(device_id)
            updated = overrides.hold_until(runtime, until)
            await self._save(updated)
            return updated

    async def resume(self, device_id: str) -> DeviceRuntime:
        """ "Resume now": SunCore may control the device again."""
        async with self._lock(device_id):
            runtime = await self._runtime(device_id)
            updated = overrides.resume(runtime)
            await self._save(updated)
            return updated

    # --- proposals -----------------------------------------------------------------------

    async def confirm(self, intent_id: str) -> Decision | None:
        """Confirm an assisted-mode proposal: re-submit it as the user's intent."""
        proposal = self._proposals.pop(intent_id, None)
        if proposal is None:
            return None
        now = self._now()
        if proposal.expires_at is not None and now >= proposal.expires_at:
            return await self._decide(
                proposal.intent,
                DecisionKind.REJECTED,
                now,
                (REASON_PROPOSAL_EXPIRED,),
                proposal.mode,
            )
        intent = proposal.intent.model_copy(
            update={
                "origin": Origin.USER,
                "ts": now,
                "reasons": (*proposal.intent.reasons, REASON_CONFIRMED),
            }
        )
        return await self.submit(intent)

    def dismiss(self, intent_id: str) -> bool:
        return self._proposals.pop(intent_id, None) is not None

    # --- the gate ------------------------------------------------------------------------

    async def submit(self, intent: Intent) -> Decision:  # noqa: PLR0911 - one return per verdict
        """Judge one intent, emit the decision to the sink and return it."""
        now = self._now()
        self._prune_proposals(now)
        device_id = intent.asset_id
        mode = self.modes_of(device_id).effective
        if is_stale_intent(intent.ts, now, self._settings.intent_max_age_s):
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_INTENT_STALE,), mode
            )
        if self._emergency_stop:
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_EMERGENCY_STOP,), mode
            )
        runtime = overrides.expire(await self._runtime(device_id), now)
        if runtime is not self._runtimes.get(device_id):
            await self._save(runtime)
        device = self._devices(device_id)
        if mode is OperatingMode.SHADOW:
            reasons: tuple[Reason, ...] = (REASON_SHADOW_MODE,)
            if device is not None and not isinstance(intent.action, ReleaseAction):
                reasons += evaluate(self._context(intent, device, runtime, now))
            return await self._decide(intent, DecisionKind.SHADOW, now, reasons, mode)
        pause = self._watchdog.pause_reason(device_id, now)
        if pause is not None:
            return await self._decide(intent, DecisionKind.REJECTED, now, (pause,), mode)
        if runtime.release_pending and not isinstance(intent.action, ReleaseAction):
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_RELEASE_PENDING,), mode
            )
        if runtime.is_held(now) and intent.origin is not Origin.USER:
            return await self._decide(
                intent,
                DecisionKind.HELD,
                now,
                (REASON_OVERRIDE_HELD,),
                mode,
                held_until=runtime.override_until,
            )
        if device is None:
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_UNKNOWN_DEVICE,), mode
            )
        if isinstance(intent.action, ReleaseAction):
            return await self.release_device(
                device_id, REASON_RELEASED, origin=intent.origin, intent=intent
            )
        ctx = self._context(intent, device, runtime, now)
        violations = evaluate(ctx)
        if violations:
            return await self._decide(intent, DecisionKind.REJECTED, now, violations, mode)
        if mode is OperatingMode.ASSISTED and intent.origin is not Origin.USER:
            expires = now + timedelta(seconds=self._settings.proposal_ttl_s)
            decision = await self._decide(
                intent, DecisionKind.PROPOSED, now, (REASON_PROPOSED,), mode, expires_at=expires
            )
            self._proposals[intent.id] = decision
            return decision
        return await self._claimed_write(intent, device, runtime, now, mode, ctx)

    async def _claimed_write(  # noqa: PLR0917
        self,
        intent: Intent,
        device: DeviceConfig,
        runtime: DeviceRuntime,
        now: datetime,
        mode: OperatingMode,
        ctx: InvariantContext,
    ) -> Decision:
        """Claim the increase before the first await, then write (review 3-B, G1).

        A concurrent intent judged against the same snapshot must see the increase. The
        claim is kept when the write is accepted and returned otherwise; a new snapshot
        (new ``GateInputs.ts``) starts from zero. Must be called without an ``await``
        between ``evaluate(ctx)`` and here.
        """
        claim_ts = self._claim(intent.id, power_delta_w(ctx))
        try:
            decision = await self._write(intent, device, runtime, now, mode)
        except BaseException:
            self._unclaim(intent.id, claim_ts)
            raise
        if decision.kind is not DecisionKind.ACCEPTED:
            self._unclaim(intent.id, claim_ts)
        return decision

    # --- per-snapshot claims ---------------------------------------------------------------

    def _snapshot_ts(self) -> datetime | None:
        inputs = self._inputs()
        return None if inputs is None else inputs.ts

    def _claimed_w(self) -> float:
        """Increases already let through against the current snapshot."""
        ts = self._snapshot_ts()
        if ts is None or ts != self._claims_ts:
            return 0.0
        return sum(self._claims.values())

    def _claim(self, intent_id: str, delta: float | None) -> datetime | None:
        ts = self._snapshot_ts()
        if ts != self._claims_ts:
            self._claims_ts = ts
            self._claims = {}
        if ts is None or delta is None or delta <= 0:
            return None
        self._claims[intent_id] = delta
        return ts

    def _unclaim(self, intent_id: str, ts: datetime | None) -> None:
        if ts is not None and ts == self._claims_ts:
            self._claims.pop(intent_id, None)

    async def _write(  # noqa: PLR0911 - one return per verdict
        self,
        intent: Intent,
        device: DeviceConfig,
        runtime: DeviceRuntime,
        now: datetime,
        mode: OperatingMode,
    ) -> Decision:
        device_id = device.id
        actuator = self._actuators.get(device_id)
        if actuator is None:
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_NO_ACTUATOR,), mode
            )
        failed_write = False
        context_id: str | None = None
        async with self._lock(device_id):
            runtime = self._runtimes.get(device_id, runtime)
            blocked = self._blocked(intent, runtime, now)
            if blocked is not None:
                return await self._decide(intent, DecisionKind.REJECTED, now, (blocked,), mode)
            previous_slot = self._limiter.last_write(device_id)
            if not self._limiter.try_acquire(device_id, now):
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_RATE_LIMITED,), mode
                )
            if not runtime.under_control:
                baseline = await actuator.read_back()
                if baseline is None:
                    self._limiter.release_slot(device_id, previous_slot)
                    return await self._decide(
                        intent, DecisionKind.REJECTED, now, (REASON_STATE_UNKNOWN,), mode
                    )
                # The read-back awaited: anything may have changed in between.
                blocked = self._blocked(intent, self._runtimes.get(device_id, runtime), now)
                if blocked is not None:
                    self._limiter.release_slot(device_id, previous_slot)
                    return await self._decide(intent, DecisionKind.REJECTED, now, (blocked,), mode)
                runtime = runtime.model_copy(
                    update={
                        "baseline": baseline,
                        "baseline_ts": now,
                        "base_setpoint_c": baseline.setpoint_c
                        if isinstance(baseline, SetpointAction)
                        else runtime.base_setpoint_c,
                    }
                )
                await self._save(runtime)
            try:
                context_id = await actuator.apply(intent)
            except Exception as exc:
                log.warning("safety.write_failed", device_id=device_id, error=str(exc)[:200])
                failed_write = True
            runtime = runtime.model_copy(update={"last_write_ts": now})
            await self._save(runtime)
        if failed_write:
            await self._count_failure(device_id, now)
            return await self._decide(
                intent, DecisionKind.FAILED, now, (REASON_WRITE_FAILED,), mode
            )
        await self._sleep(self._settle_s(device))
        observed = await actuator.read_back()
        return await self._confirm(
            intent, device, now, observed=observed, context_id=context_id, mode=mode
        )

    async def _confirm(
        self,
        intent: Intent,
        device: DeviceConfig,
        now: datetime,
        *,
        observed: Action | None,
        context_id: str | None,
        mode: OperatingMode,
    ) -> Decision:
        """After the settle wait: compare the read-back, record the outcome."""
        device_id = device.id
        after = self._now()
        mismatch = False
        async with self._lock(device_id):
            runtime = self._runtimes.get(device_id)
            if runtime is None or not runtime.under_control:
                # Released (emergency stop, watchdog, mode change) during the settle wait:
                # the release restored the baseline; do not take the device over again.
                return await self._decide(
                    intent,
                    DecisionKind.FAILED,
                    after,
                    (REASON_SUPERSEDED,),
                    mode,
                    observed=observed,
                )
            if not action_matches(intent.action, observed):
                mismatch = True
                runtime = runtime.model_copy(
                    update={
                        "last_write_context_id": context_id,
                        "pending_action": intent.action,
                        "pending_ts": now,
                    }
                )
            else:
                self._failures.pop(device_id, None)
                if is_switch_transition(
                    intent.action, device, self._states(device_id)
                ) or _was_transition(intent.action, device, runtime):
                    target = action_turns_on(intent.action, device)
                    if target is not None:
                        runtime = runtime.with_switch(now, on=target)
                update: dict[str, object] = {
                    "last_written": intent.action,
                    "last_write_context_id": context_id,
                    "pending_action": None,
                    "pending_ts": None,
                }
                if isinstance(intent.action, SetpointAction):
                    update["base_setpoint_c"] = intent.action.base_c
                runtime = runtime.model_copy(update=update)
            await self._save(runtime)
        if mismatch:
            await self._count_failure(device_id, after)
            return await self._decide(
                intent,
                DecisionKind.FAILED,
                after,
                (REASON_READBACK_MISMATCH,),
                mode,
                observed=observed,
            )
        return await self._decide(
            intent, DecisionKind.ACCEPTED, after, intent.reasons, mode, observed=observed
        )

    def _blocked(self, intent: Intent, runtime: DeviceRuntime, now: datetime) -> Reason | None:
        """Re-check, right before ``apply()``, everything that may have changed meanwhile."""
        if self._emergency_stop:
            return REASON_EMERGENCY_STOP
        mode = self.modes_of(intent.asset_id).effective
        if mode is OperatingMode.SHADOW or (
            mode is OperatingMode.ASSISTED and intent.origin is not Origin.USER
        ):
            return REASON_MODE_CHANGED
        pause = self._watchdog.pause_reason(intent.asset_id, now)
        if pause is not None:
            return pause
        if runtime.release_pending:
            return REASON_RELEASE_PENDING
        if runtime.is_held(now) and intent.origin is not Origin.USER:
            return REASON_OVERRIDE_HELD
        return None

    # --- release -------------------------------------------------------------------------

    async def release_device(
        self,
        device_id: str,
        reason: Reason,
        *,
        origin: Origin = Origin.SAFETY,
        intent: Intent | None = None,
        urgent: bool = True,
    ) -> Decision:
        """Restore the device's takeover baseline and stop controlling it.

        Bypasses modes, the emergency stop, the watchdog and the invariants: releasing is
        the safe direction. ``urgent=False`` (mode change, device trip, reconnect) defers
        the release write until the minimum run/pause time has elapsed and answers
        ``held`` with ``held_until``; writing stops at once and :meth:`poll` executes
        the release later. The decision is otherwise ``accepted`` (released),
        ``rejected`` (not under control / no actuator) or ``failed``.
        """
        async with self._lock(device_id):
            now = self._now()
            mode = self.modes_of(device_id).effective
            runtime = await self._runtime(device_id)
            if intent is None:
                intent = Intent(
                    id=f"release:{device_id}:{now.isoformat()}",
                    ts=now,
                    asset_id=device_id,
                    action=ReleaseAction(),
                    reasons=(reason,),
                    origin=origin,
                )
            actuator = self._actuators.get(device_id)
            if not runtime.under_control:
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_NOT_UNDER_CONTROL,), mode
                )
            if actuator is None:
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_NO_ACTUATOR,), mode
                )
            device = self._devices(device_id)
            if not urgent:
                due = _protection_due(device, runtime, now)
                if due is not None:
                    deferred = runtime.model_copy(
                        update={"release_due_ts": due, "release_due_reason": reason.key}
                    )
                    await self._save(deferred)
                    return await self._decide(
                        intent,
                        DecisionKind.HELD,
                        now,
                        (REASON_RELEASE_DEFERRED, reason),
                        mode,
                        held_until=due,
                    )
            try:
                await actuator.release(runtime.baseline)
            except Exception as exc:
                log.warning("safety.release_failed", device_id=device_id, error=str(exc)[:200])
                return await self._decide(
                    intent, DecisionKind.FAILED, now, (REASON_RELEASE_FAILED, reason), mode
                )
            released = runtime.model_copy(
                update={
                    "baseline": None,
                    "baseline_ts": None,
                    "last_written": None,
                    "last_write_context_id": None,
                    "last_write_ts": now,
                    "pending_action": None,
                    "pending_ts": None,
                    "release_due_ts": None,
                    "release_due_reason": None,
                }
            )
            if (
                device is not None
                and runtime.last_written is not None
                and runtime.baseline is not None
            ):
                before = action_turns_on(runtime.last_written, device)
                target = action_turns_on(runtime.baseline, device)
                if before is not None and target is not None and before != target:
                    released = released.with_switch(now, on=target)
            await self._save(released)
            self._proposals = {
                k: v for k, v in self._proposals.items() if v.intent.asset_id != device_id
            }
            return await self._decide(
                intent, DecisionKind.ACCEPTED, now, (REASON_RELEASED, reason), mode
            )

    async def _release_all(
        self,
        reason: Reason,
        *,
        origin: Origin,
        deadline_s: float | None = None,
        urgent: bool = True,
    ) -> None:
        ids = [d for d, rt in self._runtimes.items() if rt.under_control]
        if not ids:
            return
        tasks = {
            asyncio.ensure_future(self.release_device(d, reason, origin=origin, urgent=urgent)): d
            for d in ids
        }
        done, pending = await asyncio.wait(tasks, timeout=deadline_s)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            device_id = tasks[task]
            log.error("safety.release_timeout", device_id=device_id)
            await self._decide(
                Intent(
                    id=f"release:{device_id}:timeout",
                    ts=self._now(),
                    asset_id=device_id,
                    action=ReleaseAction(),
                    reasons=(reason,),
                    origin=origin,
                ),
                DecisionKind.FAILED,
                self._now(),
                (REASON_RELEASE_TIMEOUT, reason),
                self.modes_of(device_id).effective,
            )
        for task in done:
            exc = task.exception()
            if exc is not None:
                log.error("safety.release_error", device_id=tasks[task], error=str(exc)[:200])

    # --- internals -----------------------------------------------------------------------

    def _context(
        self, intent: Intent, device: DeviceConfig, runtime: DeviceRuntime, now: datetime
    ) -> InvariantContext:
        inputs = self._inputs() or GateInputs()
        max_age = timedelta(seconds=self._settings.inputs_max_age_ticks * self._settings.tick_s)
        fresh = inputs.ts is not None and now - inputs.ts <= max_age
        return InvariantContext(
            action=intent.action,
            device=device,
            state=self._states(device.id),
            runtime=runtime,
            now=now,
            settings=self._settings,
            flows=inputs.flows,
            surplus=inputs.surplus if fresh else None,
            estimate=inputs.estimates.get(device.id),
            import_limit_w=inputs.import_limit_w,
            reserve_soc_pct=inputs.reserve_soc_pct,
            claimed_w=self._claimed_w() if inputs.ts is not None else 0.0,
        )

    def _settle_s(self, device: DeviceConfig | None) -> float:
        if device is not None and device.settle_s is not None:
            return float(device.settle_s)
        return self._settings.default_settle_s

    async def _count_failure(self, device_id: str, now: datetime) -> None:
        count = self._failures.get(device_id, 0) + 1
        self._failures[device_id] = count
        if count >= MAX_CONSECUTIVE_FAILURES:
            self._failures.pop(device_id, None)
            self._watchdog.trip(device_id, REASON_WRITE_FAILURES, now)
            await self.release_device(device_id, REASON_WRITE_FAILURES, origin=Origin.SAFETY)

    def _lock(self, device_id: str) -> asyncio.Lock:
        lock = self._locks.get(device_id)
        if lock is None:
            lock = self._locks[device_id] = asyncio.Lock()
        return lock

    async def _runtime(self, device_id: str) -> DeviceRuntime:
        runtime = self._runtimes.get(device_id)
        if runtime is None:
            runtime = await self._store.load_runtime(device_id)
            if runtime is None:
                runtime = DeviceRuntime(device_id=device_id)
            self._runtimes[device_id] = runtime
        return runtime

    async def _save(self, runtime: DeviceRuntime) -> None:
        self._runtimes[runtime.device_id] = runtime
        await self._store.save_runtime(runtime)

    async def _decide(
        self,
        intent: Intent,
        kind: DecisionKind,
        now: datetime,
        reasons: tuple[Reason, ...],
        mode: OperatingMode,
        *,
        expires_at: datetime | None = None,
        held_until: datetime | None = None,
        observed: Action | None = None,
    ) -> Decision:
        decision = Decision(
            intent=intent,
            kind=kind,
            ts=now,
            reasons=reasons,
            mode=mode,
            expires_at=expires_at,
            held_until=held_until,
            observed=observed,
        )
        await self._sink(decision)
        return decision

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "SafetyGate clock must return a timezone-aware datetime"
            raise ValueError(msg)
        return now


def _protection_due(
    device: DeviceConfig | None, runtime: DeviceRuntime, now: datetime
) -> datetime | None:
    """When a release may switch the device without breaking min run/pause (``None`` = now).

    Restoring the baseline is a switch only if the last written action and the baseline
    differ in on/off; then the minimum run time (switching off) or pause time (switching
    on) counted from the last switch must have elapsed.
    """
    if device is None or runtime.last_written is None or runtime.baseline is None:
        return None
    before = action_turns_on(runtime.last_written, device)
    target = action_turns_on(runtime.baseline, device)
    if before is None or target is None or before == target:
        return None
    if target:
        since, limit = runtime.last_off_ts, device.min_pause_s
    else:
        since, limit = runtime.last_on_ts, device.min_run_s
    if since is None or limit <= 0:
        return None
    due = since + timedelta(seconds=limit)
    return due if due > now else None


def _was_transition(action: Action, device: DeviceConfig, runtime: DeviceRuntime) -> bool:
    """Fallback when no state reader exists: compare with what we last wrote."""
    if runtime.last_written is None:
        return False
    before = action_turns_on(runtime.last_written, device)
    after = action_turns_on(action, device)
    return before is not None and after is not None and before != after


def _no_device(_: str) -> DeviceConfig | None:
    return None


def _no_state(_: str) -> DeviceState | None:
    return None


def _no_inputs() -> GateInputs | None:
    return None


_DEFAULT_POLICY: Final = OverridePolicy()


__all__ = [
    "MAX_CONSECUTIVE_FAILURES",
    "REASON_EMERGENCY_STOP",
    "REASON_SHADOW_MODE",
    "Clock",
    "DecisionSink",
    "DeviceLookup",
    "GateInputs",
    "InputsProvider",
    "SafetyGate",
    "Sleeper",
    "StateReader",
]
