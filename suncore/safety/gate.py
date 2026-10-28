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
:meth:`SafetyGate.poll`, which the control loop calls every tick. A configuration change
(:meth:`SafetyGate.handover`) releases devices that stay the same non-urgently - the next
gate keeps the deferral (``recover(after_handover=True)``) - and changed devices at once.
Every release first judges the observed state like :meth:`SafetyGate.observe`: a manual
change that was not judged yet becomes the baseline (what the user left is given back).
A switch is recorded in the runtime when it is sent, not when the read-back confirms it.

Battery (Phase 4 WP10, ADR-0013): intents for the asset ``battery`` take the same order with
the battery invariants B1-B6/B9 (``safety.battery``) instead of the device invariants, judged
against the takeover baseline (``None`` = baseline), the live flows, the band of a fresh plan
interval, the live import price and the import limit; the adapter's precondition (Kostal 1080)
and capabilities; write only on change; an adapter with a hardware fallback gets it armed and
read back before the first limit, and the effective mode is capped at assisted until the
fallback is measured. An accepted state is a lease that :meth:`SafetyGate.poll` re-judges every
tick (heartbeat re-writes only while it holds, B7); a mode fall to shadow, a watchdog pause
(site or the battery scope of :meth:`SafetyGate.check_battery`, B8), stale inputs or any failed
invariant end it and restore the baseline. A change outside SunCore is an override (no write).
Every Phase 3 release path covers the battery. Without a battery adapter a ``BatteryAction`` is
rejected (``safety.battery_not_supported``), as before WP10.

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
from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from types import MappingProxyType
from typing import Final

import structlog

from suncore.core.balance import FlowSnapshot
from suncore.core.battery import BATTERY_ASSET_ID, BatteryCapability, resolve_battery_action
from suncore.core.control import ControlSettings, DeviceState, GateInputs
from suncore.core.decisions import (
    Action,
    BatteryAction,
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
from suncore.core.identity import identity_token
from suncore.core.signals import Sample
from suncore.core.site import SiteConfig
from suncore.safety import overrides
from suncore.safety.actuator import Actuator
from suncore.safety.battery import (
    REASON_BATTERY_NO_CHANGE,
    BatteryActuator,
    BatteryContext,
    BatteryGateConfig,
    BatteryLive,
    BatteryWatch,
    evaluate_battery,
    lease_violations,
    plan_band,
    quantise,
    readable_fields,
    restore_target,
    same_state,
    user_changed_fields,
)
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
REASON_RECONFIGURED: Final = Reason(key="safety.reconfigured")
REASON_ORPHANED: Final = Reason(key="safety.orphaned")
"""The device SunCore controlled was removed or re-mapped and could not be released."""
REASON_ORPHAN_DROPPED: Final = Reason(key="safety.orphan_dropped")
"""An orphaned runtime without the old actuator: reported, then dropped (manual check needed)."""
REASON_BATTERY_NOT_SUPPORTED: Final = Reason(key="safety.battery_not_supported")
"""A battery action without a battery adapter (observe-only, no model, another asset id):
rejected, never written. Since WP10 a battery intent for the configured adapter is judged by the
ADR-0013 invariants instead (``control-safety`` sign-off of the ADR-0011 addendum)."""
REASON_BATTERY_FALLBACK_UNCONFIRMED: Final = Reason(key="safety.battery_fallback_unconfirmed")
"""The hardware fallback (Kostal 1284/1286/1288) did not read back: no limit is written."""
REASON_BATTERY_RELEASE_UNCONFIRMED: Final = Reason(key="safety.battery_release_unconfirmed")
"""After a release the battery does not show its baseline: retried with back-off, battery control
stays blocked (latched in the runtime) until it does or the user changes the limits."""
REASON_BATTERY_RELEASE_CHECK: Final = Reason(key="safety.battery_release_check")
"""The deferred read-back of a battery release (an adapter without hardware fallback)."""
REASON_BATTERY_RELEASE_CONFIRMED: Final = Reason(key="safety.battery_release_confirmed")
"""The battery shows its own settings again: control of it has ended."""
BATTERY_RELEASE_BACKOFF_S: Final = 30.0
BATTERY_RELEASE_BACKOFF_MAX_S: Final = 600.0
REASON_BATTERY_OVERRIDE: Final = Reason(key="safety.battery_override")
"""The battery's limits changed outside SunCore while it held them: given back to the user."""
REASON_BATTERY_PLAN_ENDED: Final = Reason(key="safety.battery_plan_ended")
"""No fresh plan interval covers the held battery state any more: given back."""
REASON_BATTERY_INPUTS_STALE: Final = Reason(key="safety.battery_inputs_stale")
"""The controller's inputs are too old to renew the battery's lease."""
BATTERY_SCOPE: Final = BATTERY_ASSET_ID
"""The watchdog scope of the battery (its own faults; site faults pause it too)."""

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
        predecessor: SafetyGate | None = None,
        battery: BatteryGateConfig | None = None,
    ) -> None:
        self._clock = clock
        # Actuators of assets a configuration change removed or re-mapped while their release
        # failed (``handover``): used only to release them, never to write (the identity of the
        # runtime selects them). Dropped once the release succeeded.
        self._release_only: dict[str, tuple[Actuator, str, DeviceConfig | None]] = (
            predecessor._leftovers() if predecessor is not None else {}
        )
        self._battery = battery
        self._battery_watch = (
            battery.watch if battery is not None and battery.watch is not None else BatteryWatch()
        )
        self._battery_beat: datetime | None = None
        self._release_attempts: dict[str, int] = {}
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
        if device_id == BATTERY_ASSET_ID:
            mode = self._modes.battery_mode()
            if self.battery_mode_capped and mode.rank > OperatingMode.ASSISTED.rank:
                mode = OperatingMode.ASSISTED
            return ModeView(self._modes.global_mode(), mode)
        return view(self._modes, device_id)

    @property
    def battery_mode_capped(self) -> bool:
        """The battery adapter has a hardware fallback that is not measured yet: at most
        assisted (ADR-0013 decision 6, plan §6 question 3)."""
        actuator = self._battery_actuator()
        return (
            actuator is not None
            and self._battery is not None
            and not self._battery.fallback_verified
            and BatteryCapability.HARDWARE_FALLBACK in actuator.capabilities
        )

    @property
    def battery(self) -> BatteryGateConfig | None:
        return self._battery

    def _battery_actuator(self) -> BatteryActuator | None:
        actuator = self._actuators.get(BATTERY_ASSET_ID)
        if self._battery is None or not isinstance(actuator, BatteryActuator):
            return None
        return actuator

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

    async def recover(self, *, after_handover: bool = False) -> None:
        """Start-up: load the persisted runtime; release devices a crash left under control.

        A release that fails because HA is not connected yet leaves the device under
        control in the runtime; :meth:`connection_restored` releases it once online.

        ``after_handover``: this gate replaces one that :meth:`handover` stopped (a
        configuration change in the same process). A release that gate deferred for the
        device's minimum run/pause time stays deferred (:meth:`poll` executes it); everything
        else under control is released at once, and so is everything under an emergency stop.
        """
        self._emergency_stop = await self._store.load_emergency_stop()
        for runtime in await self._store.all_runtimes():
            self._runtimes[runtime.device_id] = runtime
            self._limiter.prime(runtime.device_id, runtime.last_write_ts)
        only: list[str] | None = None
        if after_handover and not self._emergency_stop:
            now = self._now()
            only = [
                d
                for d, rt in self._runtimes.items()
                if rt.release_due_ts is None or rt.release_due_ts <= now
            ]
        await self._release_all(REASON_RECOVERY, origin=Origin.SAFETY, only=only)

    async def shutdown(self) -> None:
        """Release every device under control within ``release_deadline_s``."""
        await self._release_all(
            REASON_SHUTDOWN, origin=Origin.SAFETY, deadline_s=self._settings.release_deadline_s
        )

    async def handover(self, keep: Collection[str]) -> None:
        """A configuration change replaces this gate: release without breaking protections.

        Devices in ``keep`` stay the same device in the new configuration (identity and
        control unchanged): their release respects the minimum run/pause time like a mode
        change (deferred and persisted, executed by the next gate after
        ``recover(after_handover=True)``), so a harmless edit (a rename, another device, the
        site) cannot short-cycle a compressor. Every other device under control is released at
        once: its entities or control changed, and only this gate's actuator knows how to
        restore its baseline. Both within ``release_deadline_s``. A release that fails (Home
        Assistant down) is not lost: the successor gate is built with ``predecessor=self`` and
        keeps this gate's actuator for that release only (``recover``, ``connection_restored``).
        """
        kept = frozenset(keep)
        others = [d for d, rt in self._runtimes.items() if rt.under_control and d not in kept]
        deadline = self._settings.release_deadline_s
        await asyncio.gather(
            self._release_all(
                REASON_RECONFIGURED, origin=Origin.SAFETY, deadline_s=deadline, only=others
            ),
            self._release_all(
                REASON_RECONFIGURED,
                origin=Origin.SAFETY,
                deadline_s=deadline,
                urgent=False,
                only=kept,
            ),
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
        """Execute deferred releases whose protection time has elapsed (call every tick); renew
        or end the battery's lease (its heartbeat) and check a battery release (WP10)."""
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
        if not self._emergency_stop:
            lease = await self._battery_lease(now)
            if lease is not None:
                decisions.append(lease)
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
            if self._is_orphan(device_id, runtime):
                return overrides.NO_OVERRIDE  # another device's runtime: nothing to judge
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
                updated = self._apply_override(device, runtime, verdict, now)
                # What the user left is what a release gives back: restoring the takeover
                # state would overwrite the user's own change (their temperature, their "off").
                updated = updated.model_copy(update={"baseline": observed, "baseline_ts": now})
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
        # The switch (if it was one) was recorded when the write was sent (``_write``).
        confirmed = overrides.confirm_pending(runtime)
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

    async def submit(self, intent: Intent) -> Decision:  # noqa: PLR0911, PLR0912 - one return per verdict
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
        if isinstance(intent.action, BatteryAction) or device_id == BATTERY_ASSET_ID:
            return await self._submit_battery(intent, now, mode)
        current = await self._runtime(device_id)
        runtime = overrides.expire(current, now)
        if runtime is not current:  # only the expiry is saved, never a stale copy
            await self._save(runtime)
        runtime = await self._own_runtime(device_id, runtime)
        device = self._devices(device_id)
        if mode is OperatingMode.SHADOW:
            reasons: tuple[Reason, ...] = (REASON_SHADOW_MODE,)
            if device is not None and not isinstance(intent.action, ReleaseAction):
                reasons += evaluate(self._context(intent, device, runtime, now))
            return await self._decide(intent, DecisionKind.SHADOW, now, reasons, mode)
        pause = self._watchdog.pause_reason(device_id, now)
        if pause is not None:
            return await self._decide(intent, DecisionKind.REJECTED, now, (pause,), mode)
        held_back = self._held_back(intent, runtime)
        if held_back is not None:
            return await self._decide(intent, DecisionKind.REJECTED, now, (held_back,), mode)
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

    # --- battery (Phase 4 WP10, ADR-0013) --------------------------------------------------

    async def _submit_battery(  # noqa: PLR0911, PLR0912 - one return per verdict
        self, intent: Intent, now: datetime, mode: OperatingMode
    ) -> Decision:
        """The battery's path through the gate (same order as a device's, ADR-0013 invariants).

        Without a battery adapter (observe-only, no model) or for another asset id every
        battery action is rejected with ``safety.battery_not_supported`` - nothing is written.
        """
        actuator = self._battery_actuator()
        if actuator is None or intent.asset_id != BATTERY_ASSET_ID:
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_BATTERY_NOT_SUPPORTED,), mode
            )
        current = await self._runtime(BATTERY_ASSET_ID)
        runtime = overrides.expire(current, now)
        if runtime is not current:
            await self._save(runtime)
        runtime = await self._own_runtime(BATTERY_ASSET_ID, runtime)
        action = intent.action
        if mode is OperatingMode.SHADOW:
            reasons: tuple[Reason, ...] = (REASON_SHADOW_MODE,)
            if isinstance(action, BatteryAction):
                baseline = (
                    runtime.baseline
                    if runtime.under_control
                    else await actuator.takeover_baseline()
                )
                reasons += evaluate_battery(self._battery_context(action, actuator, baseline, now))
            return await self._decide(intent, DecisionKind.SHADOW, now, reasons, mode)
        pause = self._watchdog.pause_reason(BATTERY_SCOPE, now)
        if pause is not None:
            return await self._decide(intent, DecisionKind.REJECTED, now, (pause,), mode)
        held_back = self._held_back(intent, runtime)
        if held_back is not None:
            return await self._decide(intent, DecisionKind.REJECTED, now, (held_back,), mode)
        if runtime.is_held(now) and intent.origin is not Origin.USER:
            return await self._decide(
                intent,
                DecisionKind.HELD,
                now,
                (REASON_OVERRIDE_HELD,),
                mode,
                held_until=runtime.override_until,
            )
        if not isinstance(action, BatteryAction):
            if isinstance(action, ReleaseAction):
                return await self.release_device(
                    BATTERY_ASSET_ID, REASON_RELEASED, origin=intent.origin, intent=intent
                )
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_BATTERY_NOT_SUPPORTED,), mode
            )
        refusal = actuator.refusal()
        if refusal is not None:
            return await self._decide(intent, DecisionKind.REJECTED, now, (refusal,), mode)
        observed = await actuator.read_back()
        if observed is None:
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_STATE_UNKNOWN,), mode
            )
        baseline = runtime.baseline if runtime.under_control else await actuator.takeover_baseline()
        ctx = self._battery_context(action, actuator, baseline, now)
        violations = evaluate_battery(ctx)
        if violations:
            return await self._decide(intent, DecisionKind.REJECTED, now, violations, mode)
        fields = readable_fields(actuator.capabilities)
        held = runtime.last_written if runtime.under_control else None
        # A §3.5-style limit is in force only while SunCore keeps writing it: registers that
        # merely *show* the value (left by an earlier controller) are no reason to skip the
        # takeover (review 4-G, minor 6).
        foreign = (
            not runtime.under_control
            and BatteryCapability.HARDWARE_FALLBACK in actuator.capabilities
        )
        if (
            same_state(ctx.target, observed, fields)
            and not foreign
            and (not runtime.under_control or held == action)
        ):
            # Write only on change (B7): the battery already holds this state.
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (REASON_BATTERY_NO_CHANGE,), mode
            )
        if mode is OperatingMode.ASSISTED and intent.origin is not Origin.USER:
            expires = now + timedelta(seconds=self._settings.proposal_ttl_s)
            decision = await self._decide(
                intent, DecisionKind.PROPOSED, now, (REASON_PROPOSED,), mode, expires_at=expires
            )
            self._proposals[intent.id] = decision
            return decision
        return await self._battery_write(intent, action, actuator, now, mode)

    def _battery_live(self) -> BatteryLive:
        inputs = self._inputs()
        if inputs is None or inputs.ts is None:
            return BatteryLive()
        max_age = timedelta(seconds=self._settings.inputs_max_age_ticks * self._settings.tick_s)
        if self._now() - inputs.ts > max_age:
            return BatteryLive()  # stale inputs count as unknown
        return BatteryLive.of(inputs.flows)

    def _battery_context(
        self,
        requested: BatteryAction,
        actuator: BatteryActuator,
        baseline: Action | None,
        now: datetime,
    ) -> BatteryContext:
        config = self._battery
        assert config is not None
        base = baseline if isinstance(baseline, BatteryAction) else None
        ranges = actuator.ranges()
        try:
            plan = config.plan(now)
        except Exception:
            log.exception("safety.battery_plan_failed")
            plan = None
        band, fresh = (None, False) if plan is None else plan_band(plan, now)
        try:
            price = config.import_price(now)
        except Exception:
            log.exception("safety.battery_price_failed")
            price = None
        inputs = self._inputs() or GateInputs()
        return BatteryContext(
            requested=requested,
            target=quantise(resolve_battery_action(requested, base), ranges),
            capabilities=actuator.capabilities,
            ranges=ranges,
            model=config.model,
            reserve_pct=config.reserve_pct,
            live=self._battery_live(),
            band=band,
            plan_fresh=fresh,
            policy=config.policy,
            import_price=price,
            tz=self._tz,
            import_limit_w=inputs.import_limit_w,
            reserve_w=self._settings.reserve_w,
            now=now,
            baseline=base,
        )

    async def _battery_write(  # noqa: PLR0911, PLR0912, PLR0915 - one return per verdict
        self,
        intent: Intent,
        requested: BatteryAction,
        actuator: BatteryActuator,
        now: datetime,
        mode: OperatingMode,
    ) -> Decision:
        """Takeover (hardware fallback first, then the baseline), judge again, write, read back.

        The settle wait after arming the hardware fallback runs **outside** the gate lock, so an
        emergency stop or release is never delayed by it; everything that may have changed in
        that wait (mode, emergency stop, watchdog, the invariants) is judged again under the
        lock right before ``apply()`` (review 4-G, minor 3).
        """
        config = self._battery
        assert config is not None
        failed: Reason | None = None
        context_id: str | None = None
        armed = False
        async with self._lock(BATTERY_ASSET_ID):
            runtime = self._runtimes.get(BATTERY_ASSET_ID) or DeviceRuntime(
                device_id=BATTERY_ASSET_ID
            )
            blocked = self._blocked(intent, runtime, now)
            if blocked is not None:
                return await self._decide(intent, DecisionKind.REJECTED, now, (blocked,), mode)
            previous_slot = self._limiter.last_write(BATTERY_ASSET_ID)
            if not self._limiter.try_acquire(BATTERY_ASSET_ID, now):
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_RATE_LIMITED,), mode
                )
            if not runtime.under_control and not actuator.prepared():
                # Kostal §3.5: the fallback registers are written and read back before the
                # first limit; otherwise nothing else is written (ADR-0013 decision 5).
                armed = True
                try:
                    await actuator.arm_fallback()
                except Exception as exc:
                    log.warning("safety.battery_fallback_failed", error=str(exc)[:200])
                    failed = REASON_WRITE_FAILED
                await self._save(runtime.model_copy(update={"last_write_ts": now}))
        if failed is None and armed:
            await self._sleep(actuator.settle_s)
        async with self._lock(BATTERY_ASSET_ID):
            runtime = self._runtimes.get(BATTERY_ASSET_ID) or DeviceRuntime(
                device_id=BATTERY_ASSET_ID
            )
            took_over = False
            if failed is None:
                blocked = self._blocked(intent, runtime, now)
                if blocked is None:
                    blocked = actuator.refusal()
                if blocked is not None:
                    if not armed:
                        self._limiter.release_slot(BATTERY_ASSET_ID, previous_slot)
                    return await self._decide(intent, DecisionKind.REJECTED, now, (blocked,), mode)
            if failed is None and not runtime.under_control:
                if not actuator.prepared():
                    failed = REASON_BATTERY_FALLBACK_UNCONFIRMED
                else:
                    baseline = await actuator.takeover_baseline()
                    if baseline is None:
                        if not armed:
                            self._limiter.release_slot(BATTERY_ASSET_ID, previous_slot)
                        return await self._decide(
                            intent, DecisionKind.REJECTED, now, (REASON_STATE_UNKNOWN,), mode
                        )
                    runtime = runtime.model_copy(
                        update={
                            "baseline": baseline,
                            "baseline_ts": now,
                            "identity": config.identity,
                            "attempted": (),
                        }
                    )
                    took_over = True
            if failed is None:
                ctx = self._battery_context(requested, actuator, runtime.baseline, now)
                violations = evaluate_battery(ctx)
                if violations:
                    # Judged again right before the write: something changed meanwhile.
                    if not armed:
                        self._limiter.release_slot(BATTERY_ASSET_ID, previous_slot)
                    return await self._decide(intent, DecisionKind.REJECTED, now, violations, mode)
                # Every target SunCore sends is remembered (and saved) *before* the write: a
                # write that lands late or only in part is never taken for the user's own
                # state by a release (review 4-G re-check).
                if took_over or ctx.target not in runtime.attempted:
                    if ctx.target not in runtime.attempted:
                        runtime = runtime.model_copy(
                            update={"attempted": (*runtime.attempted, ctx.target)}
                        )
                    await self._save(runtime)
                written = intent.model_copy(update={"action": ctx.target})
                try:
                    context_id = await actuator.apply(written)
                except Exception as exc:
                    log.warning("safety.battery_write_failed", error=str(exc)[:200])
                    failed = REASON_WRITE_FAILED
                # A failed write may have reached part of the entities: it is pending, so a
                # release never mistakes SunCore's partial state for the user's (review B1).
                update: dict[str, object] = {"last_write_ts": now}
                if failed is not None:
                    update.update({"pending_action": requested, "pending_ts": now})
                runtime = runtime.model_copy(update=update)
                await self._save(runtime)
        if failed is not None:
            await self._count_failure(BATTERY_ASSET_ID, now)
            return await self._decide(intent, DecisionKind.FAILED, now, (failed,), mode)
        await self._sleep(actuator.settle_s)
        observed = await actuator.read_back()
        return await self._battery_confirm(
            intent, requested, actuator, now, observed=observed, context_id=context_id, mode=mode
        )

    async def _battery_confirm(
        self,
        intent: Intent,
        requested: BatteryAction,
        actuator: BatteryActuator,
        now: datetime,
        *,
        observed: Action | None,
        context_id: str | None,
        mode: OperatingMode,
    ) -> Decision:
        """Read-back after the settle wait (mandatory: both Kostal paths fail silently)."""
        after = self._now()
        mismatch = False
        async with self._lock(BATTERY_ASSET_ID):
            runtime = self._runtimes.get(BATTERY_ASSET_ID)
            if runtime is None or not runtime.under_control:
                return await self._decide(
                    intent,
                    DecisionKind.FAILED,
                    after,
                    (REASON_SUPERSEDED,),
                    mode,
                    observed=observed,
                )
            base = runtime.baseline if isinstance(runtime.baseline, BatteryAction) else None
            target = quantise(resolve_battery_action(requested, base), actuator.ranges())
            if not same_state(target, observed, readable_fields(actuator.capabilities)):
                mismatch = True
                runtime = runtime.model_copy(
                    update={
                        "last_write_context_id": context_id,
                        "pending_action": requested,
                        "pending_ts": now,
                    }
                )
            else:
                self._failures.pop(BATTERY_ASSET_ID, None)
                runtime = runtime.model_copy(
                    update={
                        "last_written": requested,
                        "last_write_context_id": context_id,
                        "pending_action": None,
                        "pending_ts": None,
                    }
                )
                # The write itself was the first beat: the next one is due ``heartbeat_s`` after the
                # write, not after the read-back (the inverter's timer started at the write).
                self._battery_beat = now
            await self._save(runtime)
        if mismatch:
            await self._count_failure(BATTERY_ASSET_ID, after)
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

    async def _battery_lease(self, now: datetime) -> Decision | None:  # noqa: PLR0912
        """Every tick while SunCore holds battery limits (ADR-0013 decision 5, B7).

        The lease ends - and the baseline is restored - when the mode fell to shadow, the
        watchdog paused the battery or the site, the controller's inputs are stale, or any
        battery invariant no longer holds for the held state (the plan interval ended or is no
        longer fresh, the price left the policy, the SoC reached the maximum, the import limit
        would break). A change of the limits outside SunCore is a manual override: SunCore lets
        go without writing and holds off per the default override policy. While the lease holds,
        an adapter with a heartbeat re-writes the held state every ``heartbeat_s``.
        """
        actuator = self._battery_actuator()
        runtime = self._runtimes.get(BATTERY_ASSET_ID)
        if (
            actuator is None
            or runtime is None
            or not runtime.under_control
            or runtime.release_pending
            or self._is_orphan(BATTERY_ASSET_ID, runtime)
        ):
            return None
        held = runtime.last_written or runtime.pending_action
        if not isinstance(held, BatteryAction):
            return None
        reason: Reason | None = None
        if self.modes_of(BATTERY_ASSET_ID).effective is OperatingMode.SHADOW:
            reason = REASON_MODE_SHADOW
        if reason is None:
            reason = self._watchdog.pause_reason(BATTERY_SCOPE, now)
        inputs = self._inputs()
        max_age = timedelta(seconds=self._settings.inputs_max_age_ticks * self._settings.tick_s)
        if reason is None and (inputs is None or inputs.ts is None or now - inputs.ts > max_age):
            reason = REASON_BATTERY_INPUTS_STALE
        if reason is None:
            # The adapter's precondition went away (Kostal 1080, register 5): no heartbeat into
            # a refusing inverter - the lease ends with that reason (review 4-G, minor 2).
            reason = actuator.refusal()
        if reason is None:
            ctx = self._battery_context(held, actuator, runtime.baseline, now)
            if ctx.band is None or not ctx.plan_fresh:
                # ADR-0013 5B: every held state - also one that cannot cause import - is held
                # only while a fresh plan interval covers it (review 4-G, minor 8).
                reason = REASON_BATTERY_PLAN_ENDED
            else:
                violations = lease_violations(ctx)
                reason = violations[0] if violations else None
        if reason is not None:
            return await self.release_device(BATTERY_ASSET_ID, reason, origin=Origin.SAFETY)
        if runtime.pending_action is None and runtime.last_written is not None:
            override = await self._battery_override(actuator, runtime, now)
            if override is not None:
                return override
        heartbeat = actuator.heartbeat_s
        if (
            heartbeat is not None
            and runtime.last_written is not None
            and (
                self._battery_beat is None
                or (now - self._battery_beat).total_seconds() >= heartbeat
            )
        ):
            self._battery_beat = now
            try:
                await actuator.keep_alive()
            except Exception as exc:
                log.warning("safety.battery_heartbeat_failed", error=str(exc)[:200])
                await self._count_failure(BATTERY_ASSET_ID, now)
        return None

    async def _battery_override(
        self, actuator: BatteryActuator, runtime: DeviceRuntime, now: datetime
    ) -> Decision | None:
        """The held limits changed outside SunCore (after the settle time): let go, no write."""
        last = runtime.last_write_ts
        if last is None or (now - last).total_seconds() < 2 * actuator.settle_s:
            return None
        held = runtime.last_written
        assert isinstance(held, BatteryAction)
        base = runtime.baseline if isinstance(runtime.baseline, BatteryAction) else None
        target = quantise(resolve_battery_action(held, base), actuator.ranges())
        observed = await actuator.read_back()
        fields = readable_fields(actuator.capabilities)
        if observed is None or same_state(target, observed, fields):
            return None
        if not user_changed_fields(observed, runtime.baseline, self._own_targets(runtime), fields):
            # Only SunCore's own earlier values (a late-landing write): no override; the next
            # write or heartbeat brings the held state back (review 4-G re-check).
            log.info("safety.battery_own_state_late", observed=observed.model_dump())
            return None
        async with self._lock(BATTERY_ASSET_ID):
            until = overrides.resume_at(_DEFAULT_POLICY, now, self._tz)
            released = overrides.hold_until(runtime, until).model_copy(
                update={
                    "baseline": None,
                    "baseline_ts": None,
                    "last_written": None,
                    "pending_action": None,
                    "pending_ts": None,
                    "attempted": (),
                }
            )
            await self._save(released)
        self._battery_beat = None
        log.info("safety.battery_override", until=until)
        return await self._decide(
            Intent(
                id=f"override:{BATTERY_ASSET_ID}:{now.isoformat()}",
                ts=now,
                asset_id=BATTERY_ASSET_ID,
                action=observed,
                reasons=(REASON_BATTERY_OVERRIDE,),
                origin=Origin.SAFETY,
            ),
            DecisionKind.HELD,
            now,
            (REASON_BATTERY_OVERRIDE,),
            self.modes_of(BATTERY_ASSET_ID).effective,
            held_until=until,
            observed=observed,
        )

    async def check_battery(self, flows: FlowSnapshot | None) -> Reason | None:
        """B8: the battery watch on this tick's flows; a fault pauses battery control and
        releases the battery (the site watchdog covers missing and implausible values)."""
        config = self._battery
        if config is None or self._battery_actuator() is None:
            return None
        now = self._now()
        capacity = None if config.model is None else config.model.capacity_wh
        reason = self._battery_watch.check(now, BatteryLive.of(flows), capacity)
        faults = self._watchdog.report(BATTERY_SCOPE, () if reason is None else (reason,), now)
        if faults:
            runtime = self._runtimes.get(BATTERY_ASSET_ID)
            if runtime is not None and runtime.under_control and not runtime.release_pending:
                await self.release_device(BATTERY_ASSET_ID, faults[0].reason, origin=Origin.SAFETY)
        return reason

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
                        "identity": identity_token(device),
                        "base_setpoint_c": baseline.setpoint_c
                        if isinstance(baseline, SetpointAction)
                        else runtime.base_setpoint_c,
                    }
                )
                await self._save(runtime)
            # Judged *before* the write: after the settle wait the cache already shows the new
            # state, and a transition would no longer look like one (min run/pause and the
            # switch counter would never engage with a real driver).
            switched = is_switch_transition(
                intent.action, device, self._states(device_id)
            ) or _was_transition(intent.action, device, runtime)
            try:
                context_id = await actuator.apply(intent)
            except Exception as exc:
                log.warning("safety.write_failed", device_id=device_id, error=str(exc)[:200])
                failed_write = True
            runtime = runtime.model_copy(update={"last_write_ts": now})
            target = action_turns_on(intent.action, device) if switched else None
            if target is not None:
                # Recorded when the switch is *sent*, not when it is confirmed: a slow echo
                # (late confirmation), a release or a rebuild during the settle wait, or a
                # cancelled submission must not leave a switch without its protection time.
                # A write that did not take effect only over-protects (the safe direction).
                runtime = runtime.with_switch(now, on=target)
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

    def _blocked(  # noqa: PLR0911 - one return per verdict
        self, intent: Intent, runtime: DeviceRuntime, now: datetime
    ) -> Reason | None:
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
        if runtime.under_control and self._is_orphan(intent.asset_id, runtime):
            return REASON_ORPHANED
        if runtime.is_held(now) and intent.origin is not Origin.USER:
            return REASON_OVERRIDE_HELD
        return None

    # --- release -------------------------------------------------------------------------

    async def release_device(  # noqa: PLR0911 - one return per verdict
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
            if not runtime.under_control:
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_NOT_UNDER_CONTROL,), mode
                )
            actuator, device = self._release_target(device_id, runtime)
            if actuator is None:
                if self._is_orphan(device_id, runtime):
                    return await self._drop_orphan(intent, runtime, now, mode)
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_NO_ACTUATOR,), mode
                )
            orphan = self._is_orphan(device_id, runtime)
            if device_id == BATTERY_ASSET_ID and not orphan:
                return await self._release_battery(intent, runtime, actuator, reason, now, mode)
            observed = await actuator.read_back()
            runtime = await self._adopt_user_state(device, runtime, observed, now)
            if not urgent and not orphan:
                due = _protection_due(device, runtime, now, observed)
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
                reasons = (REASON_RELEASE_FAILED, reason)
                if orphan:
                    reasons = (REASON_RELEASE_FAILED, REASON_ORPHANED)
                return await self._decide(intent, DecisionKind.FAILED, now, reasons, mode)
            if orphan:
                # The old device is back at its baseline; a device now using the id starts clean.
                self._release_only.pop(device_id, None)
                await self._save(DeviceRuntime(device_id=device_id))
                return await self._decide(
                    intent, DecisionKind.ACCEPTED, now, (REASON_RELEASED, REASON_ORPHANED), mode
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
            current = observed if observed is not None else runtime.last_written
            if device is not None and current is not None and runtime.baseline is not None:
                before = action_turns_on(current, device)
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

    async def _release_battery(  # noqa: PLR0917
        self,
        intent: Intent,
        runtime: DeviceRuntime,
        actuator: Actuator,
        reason: Reason,
        now: datetime,
        mode: OperatingMode,
    ) -> Decision:
        """The battery's release: restore the **original** baseline and prove it (lock held).

        A release is complete only when the battery shows its baseline again (read-back), or
        the user changed the limits to a third value (then theirs is kept, nothing is written).
        Until then the runtime stays under control with the original baseline and a pending
        release: no takeover, no write, no heartbeat - only the release is retried, with
        back-off, also during an emergency stop (``poll`` executes it). A failed release
        write, an unavailable entity or a read-back that still shows SunCore's state are never
        taken for a baseline (review 4-G, B1). An adapter with a hardware fallback (Kostal §3.5)
        is complete once its heartbeat stopped and the write went through.
        """
        self._battery_beat = None
        baseline = runtime.baseline
        caps = actuator.capabilities if isinstance(actuator, BatteryActuator) else frozenset()
        fields = readable_fields(caps)
        fallback = BatteryCapability.HARDWARE_FALLBACK in caps
        observed = await actuator.read_back()
        checking = runtime.release_due_reason == REASON_BATTERY_RELEASE_CHECK.key
        if observed is not None and same_state(baseline, observed, fields):
            return await self._battery_released(
                intent,
                runtime,
                now,
                mode,
                (REASON_RELEASED, REASON_BATTERY_RELEASE_CONFIRMED if checking else reason),
            )
        if observed is not None and not fallback:
            # A field is the user's only if it differs from the baseline *and* from every value
            # SunCore wrote or attempted for it; everything else goes back to the baseline and
            # is proven by read-back (review 4-G re-check: a late-landing or partly applied
            # write of SunCore's never passes for a user value).
            user = user_changed_fields(observed, baseline, self._own_targets(runtime), fields)
            if user:
                baseline = restore_target(baseline, observed, user)
                if baseline != runtime.baseline:
                    until = overrides.resume_at(_DEFAULT_POLICY, now, self._tz)
                    runtime = overrides.hold_until(runtime, until).model_copy(
                        update={"baseline": baseline}
                    )
                    log.info("safety.battery_override", until=until, fields=user)
                if same_state(baseline, observed, fields):
                    return await self._battery_released(
                        intent, runtime, now, mode, (REASON_RELEASED, REASON_BATTERY_OVERRIDE)
                    )
                await self._save(runtime)
        attempt = self._release_attempts.get(BATTERY_ASSET_ID, 0)
        try:
            await actuator.release(baseline)
        except Exception as exc:
            log.warning("safety.battery_release_failed", error=str(exc)[:200])
            await self._retry_battery_release(runtime, now, reason.key, attempt + 1)
            return await self._decide(
                intent, DecisionKind.FAILED, now, (REASON_RELEASE_FAILED, reason), mode
            )
        if fallback:
            return await self._battery_released(
                intent, runtime, now, mode, (REASON_RELEASED, reason)
            )
        settle = actuator.settle_s if isinstance(actuator, BatteryActuator) else 0.0
        due = now + timedelta(seconds=max(settle, self._release_backoff_s(attempt)))
        await self._save(
            runtime.model_copy(
                update={
                    "release_due_ts": due,
                    "release_due_reason": REASON_BATTERY_RELEASE_CHECK.key,
                    "last_write_ts": now,
                }
            )
        )
        self._release_attempts[BATTERY_ASSET_ID] = attempt + 1
        if checking:
            return await self._decide(
                intent,
                DecisionKind.FAILED,
                now,
                (REASON_BATTERY_RELEASE_UNCONFIRMED,),
                mode,
                observed=observed,
            )
        return await self._decide(
            intent, DecisionKind.ACCEPTED, now, (REASON_RELEASED, reason), mode, observed=observed
        )

    @staticmethod
    def _release_backoff_s(attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        return float(
            min(BATTERY_RELEASE_BACKOFF_MAX_S, BATTERY_RELEASE_BACKOFF_S * 2.0 ** (attempt - 1))
        )

    async def _retry_battery_release(
        self, runtime: DeviceRuntime, now: datetime, reason_key: str, attempt: int
    ) -> None:
        self._release_attempts[BATTERY_ASSET_ID] = attempt
        due = now + timedelta(seconds=self._release_backoff_s(attempt))
        await self._save(
            runtime.model_copy(update={"release_due_ts": due, "release_due_reason": reason_key})
        )

    async def _battery_released(
        self,
        intent: Intent,
        runtime: DeviceRuntime,
        now: datetime,
        mode: OperatingMode,
        reasons: tuple[Reason, ...],
    ) -> Decision:
        self._release_attempts.pop(BATTERY_ASSET_ID, None)
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
                "attempted": (),
            }
        )
        await self._save(released)
        self._proposals = {
            k: v for k, v in self._proposals.items() if v.intent.asset_id != BATTERY_ASSET_ID
        }
        return await self._decide(intent, DecisionKind.ACCEPTED, now, reasons, mode)

    def _own_targets(self, runtime: DeviceRuntime) -> tuple[Action, ...]:
        """Every battery state SunCore wrote or attempted while under control (resolved)."""
        own: list[Action] = list(runtime.attempted)
        actuator = self._battery_actuator()
        base = runtime.baseline if isinstance(runtime.baseline, BatteryAction) else None
        for held in (runtime.last_written, runtime.pending_action):
            if isinstance(held, BatteryAction):
                own.append(resolve_battery_action(held, base))
                if actuator is not None:
                    own.append(quantise(resolve_battery_action(held, base), actuator.ranges()))
        return tuple(own)

    def _identity_of(self, asset_id: str) -> str | None:
        """The configured identity token of an asset (``None`` = not configured)."""
        if asset_id == BATTERY_ASSET_ID and self._battery is not None:
            return self._battery.identity
        device = self._devices(asset_id)
        return None if device is None else identity_token(device)

    def _is_orphan(self, device_id: str, runtime: DeviceRuntime) -> bool:
        """The runtime belongs to an asset that is no longer configured as it was taken over."""
        if runtime.identity is None:
            return False
        return self._identity_of(device_id) != runtime.identity

    def _held_back(self, intent: Intent, runtime: DeviceRuntime) -> Reason | None:
        """Writing waits for a release: of another device's runtime, or a deferred one."""
        if runtime.under_control and self._is_orphan(intent.asset_id, runtime):
            return REASON_ORPHANED
        if runtime.release_pending and not isinstance(intent.action, ReleaseAction):
            return REASON_RELEASE_PENDING
        return None

    async def _own_runtime(self, device_id: str, runtime: DeviceRuntime) -> DeviceRuntime:
        """Released history of a removed/re-mapped device is not the configured device's: the
        device now using the id starts clean (protection times, holds)."""
        if runtime.under_control or not self._is_orphan(device_id, runtime):
            return runtime
        fresh = DeviceRuntime(device_id=device_id)
        await self._save(fresh)
        return fresh

    def _leftovers(self) -> dict[str, tuple[Actuator, str, DeviceConfig | None]]:
        """For the successor gate: what can still release an asset this gate left under control."""
        out = dict(self._release_only)
        for device_id, runtime in self._runtimes.items():
            actuator = self._actuators.get(device_id)
            identity = self._identity_of(device_id)
            if runtime.under_control and actuator is not None and identity is not None:
                out[device_id] = (actuator, identity, self._devices(device_id))
        return out

    def is_orphaned(self, device_id: str) -> bool:
        """A removed or re-mapped device is still under control (its release is pending)."""
        runtime = self._runtimes.get(device_id)
        return runtime is not None and runtime.under_control and self._is_orphan(device_id, runtime)

    def _release_target(
        self, device_id: str, runtime: DeviceRuntime
    ) -> tuple[Actuator | None, DeviceConfig | None]:
        """The actuator and configuration that can restore ``runtime``'s baseline."""
        if not self._is_orphan(device_id, runtime):
            return self._actuators.get(device_id), self._devices(device_id)
        entry = self._release_only.get(device_id)
        if entry is None or entry[1] != runtime.identity:
            return None, None
        return entry[0], entry[2]

    async def _drop_orphan(
        self, intent: Intent, runtime: DeviceRuntime, now: datetime, mode: OperatingMode
    ) -> Decision:
        """No actuator knows the old device any more (a restart since the change): report it.

        The runtime is dropped so that a device now using the id never inherits a foreign
        baseline (a release would write it to the *new* entities). The decision tells the user
        to check the old device by hand.
        """
        log.error(
            "safety.orphan_dropped",
            device_id=runtime.device_id,
            baseline=None if runtime.baseline is None else runtime.baseline.model_dump(),
        )
        await self._save(DeviceRuntime(device_id=runtime.device_id))
        return await self._decide(
            intent, DecisionKind.FAILED, now, (REASON_ORPHAN_DROPPED, REASON_ORPHANED), mode
        )

    async def _adopt_user_state(
        self,
        device: DeviceConfig | None,
        runtime: DeviceRuntime,
        observed: Action | None,
        now: datetime,
    ) -> DeviceRuntime:
        """Right before a release: a manual change not judged yet becomes the baseline.

        The override detection runs on the entity feed; a change can reach the release first
        (made while SunCore was down - observations are only judged after ``recover()`` - or
        during an outage, with the resync racing the release of ``connection_restored``).
        The same value rule as :meth:`observe` decides; a detected change is what the user
        left, so it is what the release gives back, and it is held per the override policy.
        """
        if device is None or observed is None:
            return runtime
        verdict = overrides.judge(observed, runtime, now, settle_s=self._settle_s(device))
        if not verdict.override:
            return runtime
        updated = self._apply_override(device, runtime, verdict, now)
        updated = updated.model_copy(update={"baseline": observed, "baseline_ts": now})
        await self._save(updated)
        log.info(
            "safety.override_detected_at_release",
            device_id=device.id,
            until=updated.override_until,
            reason=verdict.reason.key if verdict.reason else None,
        )
        return updated

    def _apply_override(
        self,
        device: DeviceConfig,
        runtime: DeviceRuntime,
        verdict: overrides.OverrideVerdict,
        now: datetime,
    ) -> DeviceRuntime:
        """Apply a detected manual change; a hold already running is never shortened.

        A "pause until resume" stays indefinite, a longer "pause until" stays as it is; a shorter
        hold (a boost) is extended to the override policy's resume time.
        """
        updated = overrides.apply_verdict(runtime, verdict, now, device.override_policy, self._tz)
        if runtime.is_held(now) and (
            runtime.override_until is None
            or (
                updated.override_until is not None
                and runtime.override_until > updated.override_until
            )
        ):
            updated = updated.model_copy(update={"override_until": runtime.override_until})
        return updated

    async def _release_all(
        self,
        reason: Reason,
        *,
        origin: Origin,
        deadline_s: float | None = None,
        urgent: bool = True,
        only: Collection[str] | None = None,
    ) -> None:
        ids = [
            d
            for d, rt in self._runtimes.items()
            if rt.under_control and (only is None or d in only)
        ]
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
            loaded = await self._store.load_runtime(device_id)
            # Saved while the store was read (a concurrent write path): that one is newer.
            runtime = self._runtimes.get(device_id)
            if runtime is None:
                runtime = loaded if loaded is not None else DeviceRuntime(device_id=device_id)
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
    device: DeviceConfig | None,
    runtime: DeviceRuntime,
    now: datetime,
    observed: Action | None = None,
) -> datetime | None:
    """When a release may switch the device without breaking min run/pause (``None`` = now).

    Restoring the baseline is a switch only if the device's current state (observed, else
    the last written action) and the baseline differ in on/off; then the minimum run time
    (switching off) or pause time (switching on) counted from the last switch must have
    elapsed.
    """
    current = observed if observed is not None else runtime.last_written
    if device is None or current is None or runtime.baseline is None:
        return None
    before = action_turns_on(current, device)
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
    "REASON_ORPHANED",
    "REASON_OVERRIDE_HELD",
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
