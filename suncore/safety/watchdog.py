# SPDX-License-Identifier: Apache-2.0
"""Input plausibility watchdog and the safe-state machine per scope (architecture §5).

Scopes: the **site** (grid, PV, battery, house signals and the HA connection) and each
**device**. A site fault pauses every device, a device fault only that device. A scope
runs ``NORMAL -> SAFE`` on any fault, ``SAFE -> COOLDOWN`` when the faults clear and
``COOLDOWN -> NORMAL`` after ``safe_state_cooldown_s``; a new fault during the
cool-down goes back to ``SAFE``. While a scope is not ``NORMAL`` the gate rejects
intents for it (and releases devices under control when the site trips).

Checks per sample: unavailable, invalid (unit, non-numeric), stale, jumps
(``max_jump_w_per_s``), frozen PV in daylight (``frozen_pv_s``, inverter hang,
BRIEF §9) and the balance (measured house vs ``pv + grid - battery``).

Staleness and slow sensors: ``subscribe_entities`` carries no ``last_reported``
(research/ha-websocket-api.md), and many integrations do not even advance it for an
unchanged value, so a sensor whose value does not change is silent. The watchdog
therefore follows the cache's ``STALE`` flag, which the composition root restricts to
the grid meter (``EntityCache.set_heartbeats``): an unchanged state of charge or idle
power sensor is not a fault. A frozen integration is caught by ``unavailable``, by the
frozen-PV check (at least ``frozen_pv_min_w`` for ``frozen_pv_s``, so only in daylight),
by a silent grid meter and, with a measured house sensor, by the balance check. Not
caught directly: a battery, SoC or device reading that freezes while it stays available.
Its effect is bounded because the gate and the controller's deficit back-off judge the
real grid flow (import always counts as a deficit). For an entity with a known
**cadence** the flag must also be older than ``cadence * stale_cadence_factor``.
``UNVERIFIED``: whether a hung Plenticore turns its SunSpec entities ``unavailable`` or
keeps frozen registers.
Time is injected; the watchdog never reads the wall clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from typing import Final

from suncore.core.balance import compute_flows
from suncore.core.control import ControlSettings, DeviceState
from suncore.core.decisions import Reason
from suncore.core.signals import Quality, Sample
from suncore.core.site import SiteConfig
from suncore.core.units import Dimension

SITE_SCOPE: Final = "site"


@unique
class FaultKind(StrEnum):
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    JUMP = "jump"
    FROZEN = "frozen"
    BALANCE = "balance"
    CONNECTION_LOST = "connection_lost"


@unique
class ScopeState(StrEnum):
    NORMAL = "normal"
    SAFE = "safe"
    COOLDOWN = "cooldown"


@dataclass(frozen=True, slots=True)
class Fault:
    kind: FaultKind
    scope: str
    reason: Reason
    entity_id: str | None = None


REASON_COOLDOWN: Final = Reason(key="safety.watchdog_cooldown")
REASON_CONNECTION_LOST: Final = Reason(key="safety.connection_lost")


def _reason(kind: FaultKind, entity_id: str) -> Reason:  # noqa: PLR0911 - one per kind
    args: dict[str, str | int | float | bool] = {"entity_id": entity_id}
    match kind:
        case FaultKind.STALE:
            return Reason(key="safety.watchdog_stale", args=args)
        case FaultKind.UNAVAILABLE:
            return Reason(key="safety.watchdog_unavailable", args=args)
        case FaultKind.INVALID:
            return Reason(key="safety.watchdog_invalid", args=args)
        case FaultKind.JUMP:
            return Reason(key="safety.watchdog_jump", args=args)
        case FaultKind.FROZEN:
            return Reason(key="safety.watchdog_frozen", args=args)
        case FaultKind.BALANCE:
            return Reason(key="safety.watchdog_balance", args=args)
        case FaultKind.CONNECTION_LOST:
            return REASON_CONNECTION_LOST
    return REASON_CONNECTION_LOST  # pragma: no cover - exhaustive


@dataclass(slots=True)
class _Scope:
    state: ScopeState = ScopeState.NORMAL
    faults: tuple[Fault, ...] = ()
    cleared_at: datetime | None = None


class Watchdog:
    """Plausibility checks and the per-scope safe-state machine."""

    def __init__(
        self, settings: ControlSettings, *, cadence_s: Mapping[str, float] | None = None
    ) -> None:
        self._settings = settings
        self._cadence: dict[str, float] = dict(cadence_s or {})
        self._scopes: dict[str, _Scope] = {}
        self._last: dict[str, tuple[float, datetime]] = {}
        self._frozen_since: dict[str, tuple[float, datetime]] = {}
        self._connected = True

    # --- configuration --------------------------------------------------------

    def set_cadence(self, entity_id: str, cadence_s: float | None) -> None:
        """Expected update period of a slow sensor (``None`` removes it)."""
        if cadence_s is None:
            self._cadence.pop(entity_id, None)
        elif cadence_s <= 0:
            msg = "cadence_s must be positive"
            raise ValueError(msg)
        else:
            self._cadence[entity_id] = cadence_s

    def cadence_s(self, entity_id: str) -> float | None:
        return self._cadence.get(entity_id)

    def forget(self, scope: str) -> None:
        """Drop a scope (device removed)."""
        self._scopes.pop(scope, None)

    # --- connection -------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def connection_lost(self, now: datetime) -> None:
        self._connected = False
        self._update(
            SITE_SCOPE,
            (Fault(FaultKind.CONNECTION_LOST, SITE_SCOPE, REASON_CONNECTION_LOST),),
            now,
        )

    def connection_restored(self, now: datetime) -> None:
        """Back online: the site enters the cool-down (the next check may re-trip it)."""
        self._connected = True
        self._last.clear()  # the gap would look like a jump
        self._update(SITE_SCOPE, (), now)

    # --- checks -------------------------------------------------------------------

    def check_site(
        self, now: datetime, site: SiteConfig, samples: Mapping[str, Sample]
    ) -> tuple[Fault, ...]:
        """Check every site signal and the balance; update the site scope."""
        faults: list[Fault] = []
        if not self._connected:
            faults.append(Fault(FaultKind.CONNECTION_LOST, SITE_SCOPE, REASON_CONNECTION_LOST))
        grid = site.grid
        power_refs = [
            r for r in (grid.power, grid.import_power, grid.export_power) if r is not None
        ]
        power_refs.extend(site.pv_power_refs)
        if site.battery is not None:
            power_refs.append(site.battery.power)
        if site.house is not None:
            power_refs.append(site.house)
        pv_ids = {r.entity_id for r in site.pv_power_refs}
        for ref in power_refs:
            faults.extend(
                self._check_sample(
                    now,
                    SITE_SCOPE,
                    ref.entity_id,
                    samples.get(ref.entity_id),
                    dimension=Dimension.POWER,
                    frozen_check=ref.entity_id in pv_ids,
                )
            )
        if site.battery is not None:
            soc_id = site.battery.soc.entity_id
            faults.extend(
                self._check_sample(
                    now, SITE_SCOPE, soc_id, samples.get(soc_id), dimension=Dimension.RATIO
                )
            )
        if not faults and site.house is not None:
            balance = self._check_balance(site, samples)
            if balance is not None:
                faults.append(balance)
        result = tuple(faults)
        self._update(SITE_SCOPE, result, now)
        return result

    def check_device(
        self,
        now: datetime,
        device_id: str,
        samples: Mapping[str, Sample],
        state: DeviceState | None,
    ) -> tuple[Fault, ...]:
        """Check a device's numeric sensors and its control state; update its scope.

        ``samples`` are the device's power/energy sensors by entity id (control entities
        are not numeric and arrive as ``state``).
        """
        faults: list[Fault] = []
        for entity_id, sample in samples.items():
            faults.extend(self._check_sample(now, device_id, entity_id, sample, dimension=None))
        if state is None or state.quality is Quality.UNAVAILABLE:
            faults.append(
                Fault(FaultKind.UNAVAILABLE, device_id, _reason(FaultKind.UNAVAILABLE, device_id))
            )
        elif state.quality is Quality.INVALID:
            faults.append(
                Fault(FaultKind.INVALID, device_id, _reason(FaultKind.INVALID, device_id))
            )
        result = tuple(faults)
        self._update(device_id, result, now)
        return result

    def trip(self, scope: str, reason: Reason, now: datetime) -> None:
        """Force a scope into the safe state (e.g. repeated write failures)."""
        self._update(scope, (Fault(FaultKind.INVALID, scope, reason),), now)

    # --- state ----------------------------------------------------------------------

    def state(self, scope: str, now: datetime) -> ScopeState:
        entry = self._scopes.get(scope)
        if entry is None:
            return ScopeState.NORMAL
        if entry.state is ScopeState.COOLDOWN and self._cooldown_over(entry, now):
            entry.state = ScopeState.NORMAL
            entry.faults = ()
        return entry.state

    def faults(self, scope: str) -> tuple[Fault, ...]:
        entry = self._scopes.get(scope)
        return () if entry is None else entry.faults

    def is_paused(self, device_id: str, now: datetime) -> bool:
        """Whether writes to ``device_id`` are blocked (site or device not normal)."""
        return (
            not self._connected
            or self.state(SITE_SCOPE, now) is not ScopeState.NORMAL
            or self.state(device_id, now) is not ScopeState.NORMAL
        )

    def site_paused(self, now: datetime) -> bool:
        return not self._connected or self.state(SITE_SCOPE, now) is not ScopeState.NORMAL

    def pause_reason(self, device_id: str, now: datetime) -> Reason | None:
        """The first reason a device is paused, ``None`` if it is not."""
        if not self._connected:
            return REASON_CONNECTION_LOST
        for scope in (SITE_SCOPE, device_id):
            state = self.state(scope, now)
            if state is ScopeState.SAFE:
                faults = self.faults(scope)
                return faults[0].reason if faults else REASON_COOLDOWN
            if state is ScopeState.COOLDOWN:
                return REASON_COOLDOWN
        return None

    # --- internals ----------------------------------------------------------------

    def _cooldown_over(self, entry: _Scope, now: datetime) -> bool:
        if entry.cleared_at is None:
            return True
        return (now - entry.cleared_at).total_seconds() >= self._settings.safe_state_cooldown_s

    def _update(self, scope: str, faults: tuple[Fault, ...], now: datetime) -> None:
        entry = self._scopes.setdefault(scope, _Scope())
        if faults:
            entry.state = ScopeState.SAFE
            entry.faults = faults
            entry.cleared_at = None
            return
        if entry.state is ScopeState.SAFE:
            entry.state = ScopeState.COOLDOWN
            entry.cleared_at = now
        if entry.state is ScopeState.COOLDOWN and self._cooldown_over(entry, now):
            entry.state = ScopeState.NORMAL
            entry.faults = ()

    def _check_sample(
        self,
        now: datetime,
        scope: str,
        entity_id: str,
        sample: Sample | None,
        *,
        dimension: Dimension | None,
        frozen_check: bool = False,
    ) -> list[Fault]:
        if sample is None or sample.quality is Quality.UNAVAILABLE or sample.value is None:
            self._last.pop(entity_id, None)
            self._frozen_since.pop(entity_id, None)
            return [self._fault(FaultKind.UNAVAILABLE, scope, entity_id)]
        if sample.quality is Quality.INVALID or (
            dimension is not None
            and (sample.unit is None or sample.unit.dimension is not dimension)
        ):
            return [self._fault(FaultKind.INVALID, scope, entity_id)]
        faults: list[Fault] = []
        if self._is_stale(entity_id, sample, now):
            faults.append(self._fault(FaultKind.STALE, scope, entity_id))
        value = sample.value
        if dimension is Dimension.POWER:
            previous = self._last.get(entity_id)
            if previous is not None:
                dt = (sample.ts - previous[1]).total_seconds()
                if dt > 0 and abs(value - previous[0]) / dt > self._settings.max_jump_w_per_s:
                    faults.append(self._fault(FaultKind.JUMP, scope, entity_id))
            self._last[entity_id] = (value, sample.ts)
        if frozen_check:
            since = self._frozen_since.get(entity_id)
            if since is None or since[0] != value:
                self._frozen_since[entity_id] = (value, now)
            elif (
                value >= self._settings.frozen_pv_min_w
                and (now - since[1]).total_seconds() >= self._settings.frozen_pv_s
            ):
                faults.append(self._fault(FaultKind.FROZEN, scope, entity_id))
        return faults

    def _is_stale(self, entity_id: str, sample: Sample, now: datetime) -> bool:
        # The cache decides which entities must keep reporting (its heartbeats, the grid
        # meter); a value it still marks OK is valid however old. A known cadence only
        # lengthens the allowance of a heartbeat that reports slowly.
        if sample.quality is not Quality.STALE:
            return False
        cadence = self._cadence.get(entity_id)
        if cadence is None:
            return True
        age = (now - sample.ts).total_seconds()
        return age > cadence * self._settings.stale_cadence_factor

    def _check_balance(self, site: SiteConfig, samples: Mapping[str, Sample]) -> Fault | None:
        assert site.house is not None
        flows = compute_flows(site, samples)
        measured = flows.house_w.value
        pv, grid, battery = flows.pv_w.value, flows.grid_w.value, flows.battery_w.value
        if measured is None or pv is None or grid is None or battery is None:
            return None
        computed = pv + grid - battery
        tolerance = max(
            self._settings.balance_tolerance_w,
            abs(computed) * self._settings.balance_tolerance_pct / 100.0,
        )
        if abs(measured - computed) > tolerance:
            return self._fault(FaultKind.BALANCE, SITE_SCOPE, site.house.entity_id)
        return None

    @staticmethod
    def _fault(kind: FaultKind, scope: str, entity_id: str) -> Fault:
        return Fault(kind, scope, _reason(kind, entity_id), entity_id)


__all__ = [
    "REASON_CONNECTION_LOST",
    "REASON_COOLDOWN",
    "SITE_SCOPE",
    "Fault",
    "FaultKind",
    "ScopeState",
    "Watchdog",
]
