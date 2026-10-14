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
shadow (``sync_modes``), a site watchdog trip, a restored connection and the crash
recovery at start-up (``recover`` with the persisted runtime). Only this module calls
``apply()`` and ``release()`` (``tests/safety/test_boundary.py``). Time is injected
(``clock``), the settle wait is injected (``sleep``); nothing reads the wall clock.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from types import MappingProxyType
from typing import Final

import structlog

from suncore.core.balance import FlowSnapshot
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
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

MAX_CONSECUTIVE_FAILURES: Final = 3
"""After this many failed writes in a row the device scope trips (watchdog)."""


@dataclass(frozen=True, slots=True)
class GateInputs:
    """Site view handed to the invariants (the controller refreshes it every tick)."""

    flows: FlowSnapshot | None = None
    surplus: PowerEstimate | None = None
    estimates: Mapping[str, PowerEstimate] = field(default_factory=dict)
    """Current power per device id."""
    import_limit_w: float | None = None
    reserve_soc_pct: float | None = None


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
        now = self._now()
        return tuple(
            d for d in self._proposals.values() if d.expires_at is None or now < d.expires_at
        )

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
        """Call after the operating modes changed: devices that fell to shadow are released."""
        for device_id in list(self._runtimes):
            runtime = self._runtimes[device_id]
            if runtime.under_control and self.modes_of(device_id).effective is OperatingMode.SHADOW:
                await self.release_device(device_id, REASON_MODE_SHADOW, origin=Origin.USER)

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
            if runtime is not None and runtime.under_control:
                await self.release_device(device_id, faults[0].reason, origin=Origin.SAFETY)
        return faults

    async def connection_lost(self) -> None:
        """HA connection gone: everything pauses; nothing can be written, so no release."""
        self._watchdog.connection_lost(self._now())
        self._site_tripped = True

    async def connection_restored(self) -> None:
        """Back online: release what was under control (the user may have changed things)."""
        self._watchdog.connection_restored(self._now())
        await self._release_all(REASON_CONNECTION_LOST, origin=Origin.SAFETY)
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
        """Feed an observed device state (from the cache) for override detection."""
        now = self._now()
        runtime = await self._runtime(device_id)
        device = self._devices(device_id)
        settle = self._settle_s(device)
        verdict = overrides.judge(observed, runtime, now, settle_s=settle, context_id=context_id)
        if verdict.override:
            policy = device.override_policy if device is not None else _DEFAULT_POLICY
            updated = overrides.apply_verdict(runtime, verdict, now, policy, self._tz)
            await self._save(updated)
            log.info(
                "safety.override_detected",
                device_id=device_id,
                until=updated.override_until,
                reason=verdict.reason.key if verdict.reason else None,
            )
        return verdict

    async def hold(self, device_id: str, until: datetime | None) -> DeviceRuntime:
        """Explicit user override (boost / pause until); ``None`` holds until ``resume``."""
        runtime = await self._runtime(device_id)
        updated = overrides.hold_until(runtime, until)
        await self._save(updated)
        return updated

    async def resume(self, device_id: str) -> DeviceRuntime:
        """ "Resume now": SunCore may control the device again."""
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
        violations = evaluate(self._context(intent, device, runtime, now))
        if violations:
            return await self._decide(intent, DecisionKind.REJECTED, now, violations, mode)
        if mode is OperatingMode.ASSISTED and intent.origin is not Origin.USER:
            expires = now + timedelta(seconds=self._settings.proposal_ttl_s)
            decision = await self._decide(
                intent, DecisionKind.PROPOSED, now, (REASON_PROPOSED,), mode, expires_at=expires
            )
            self._proposals[intent.id] = decision
            return decision
        return await self._write(intent, device, runtime, now, mode)

    async def _write(
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
        previous_slot = self._limiter.last_write(device_id)
        if not self._limiter.try_acquire(device_id, now):
            return await self._decide(
                intent, DecisionKind.REJECTED, now, (Reason(key="safety.rate_limited"),), mode
            )
        if not runtime.under_control:
            baseline = await actuator.read_back()
            if baseline is None:
                self._limiter.release_slot(device_id, previous_slot)
                return await self._decide(
                    intent, DecisionKind.REJECTED, now, (REASON_STATE_UNKNOWN,), mode
                )
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
            runtime = runtime.model_copy(update={"last_write_ts": now})
            await self._save(runtime)
            await self._count_failure(device_id, now)
            return await self._decide(
                intent, DecisionKind.FAILED, now, (REASON_WRITE_FAILED,), mode
            )
        await self._sleep(self._settle_s(device))
        observed = await actuator.read_back()
        after = self._now()
        if not action_matches(intent.action, observed):
            runtime = runtime.model_copy(
                update={"last_write_ts": now, "last_write_context_id": context_id}
            )
            await self._save(runtime)
            await self._count_failure(device_id, after)
            return await self._decide(
                intent,
                DecisionKind.FAILED,
                after,
                (REASON_READBACK_MISMATCH,),
                mode,
                observed=observed,
            )
        self._failures.pop(device_id, None)
        if is_switch_transition(intent.action, device, self._states(device_id)) or _was_transition(
            intent.action, device, runtime
        ):
            target = action_turns_on(intent.action, device)
            if target is not None:
                runtime = runtime.with_switch(now, on=target)
        update: dict[str, object] = {
            "last_written": intent.action,
            "last_write_ts": now,
            "last_write_context_id": context_id,
        }
        if isinstance(intent.action, SetpointAction):
            update["base_setpoint_c"] = intent.action.base_c
        runtime = runtime.model_copy(update=update)
        await self._save(runtime)
        return await self._decide(
            intent, DecisionKind.ACCEPTED, after, intent.reasons, mode, observed=observed
        )

    # --- release -------------------------------------------------------------------------

    async def release_device(
        self,
        device_id: str,
        reason: Reason,
        *,
        origin: Origin = Origin.SAFETY,
        intent: Intent | None = None,
    ) -> Decision:
        """Restore the device's takeover baseline and stop controlling it.

        Bypasses modes, the emergency stop, the watchdog and the invariants: releasing is
        the safe direction. The decision is ``accepted`` (released), ``rejected`` (not
        under control / no actuator) or ``failed``.
        """
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
            }
        )
        device = self._devices(device_id)
        if device is not None and runtime.last_written is not None and runtime.baseline is not None:
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
        self, reason: Reason, *, origin: Origin, deadline_s: float | None = None
    ) -> None:
        ids = [d for d, rt in self._runtimes.items() if rt.under_control]
        if not ids:
            return
        tasks = {
            asyncio.ensure_future(self.release_device(d, reason, origin=origin)): d for d in ids
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
        return InvariantContext(
            action=intent.action,
            device=device,
            state=self._states(device.id),
            runtime=runtime,
            now=now,
            settings=self._settings,
            flows=inputs.flows,
            surplus=inputs.surplus,
            estimate=inputs.estimates.get(device.id),
            import_limit_w=inputs.import_limit_w,
            reserve_soc_pct=inputs.reserve_soc_pct,
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
