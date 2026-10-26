# SPDX-License-Identifier: Apache-2.0
"""Battery invariants B1-B9 (ADR-0013) as pure functions, and the battery actuator protocol.

The battery is the first asset SunCore writes to that can **cause grid import on purpose**
(planned grid charging, a discharge lock) and that guards energy the user relies on (the
reserve). ADR-0013 replaces the Phase 3 rule "no SunCore-caused import" for the battery - and
only for the battery - by a bounded exception: a battery action that can cause import is
accepted only when a *fresh* plan interval covering ``now`` explicitly allows it and every
live bound holds at execution time. Devices keep the Phase 3 rule unchanged.

Everything here is pure: the gate builds a :class:`BatteryContext` from the intent, the
adapter's capabilities and live ranges, the takeover baseline, the live flows, the plan band
covering ``now``, the live import price and the battery configuration, and
:func:`evaluate_battery` returns every violation (empty = may be written). :func:`lease_violations`
is the subset the gate re-checks every tick while it holds an accepted battery state (the
lease, ADR-0013 decision 5): when the plan interval ends, the price leaves the policy, the SoC
reaches the maximum or the import limit would break, the lease ends and the gate restores the
baseline. Property tests: ``tests/safety/test_battery_invariants.py``.

Signs and units per ``core/units.py``: grid + = import, battery + = charging, W and %.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Final, Protocol, runtime_checkable

from suncore.core.balance import FlowSnapshot
from suncore.core.battery import (
    BATTERY_ACTION_FIELDS,
    FIELD_CAPABILITY,
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatteryRanges,
    GridChargePolicy,
    action_fields,
    round_up_to_step,
)
from suncore.core.control import PLAN_FRESH_INTERVALS, BatteryBand, Plan, battery_band_now
from suncore.core.decisions import Action, Reason
from suncore.core.signals import Quality
from suncore.safety.actuator import Actuator

POWER_TOLERANCE_W: Final = 1.0
"""Read-back and bound tolerance for battery powers (the adapters write whole watts)."""
SOC_TOLERANCE_PCT: Final = 0.01

REASON_BATTERY_MODEL_MISSING: Final = Reason(key="safety.battery_model_missing")
REASON_BATTERY_NO_CHANGE: Final = Reason(key="safety.battery_write_no_change")


# --- the battery actuator --------------------------------------------------------------------


@runtime_checkable
class BatteryActuator(Actuator, Protocol):
    """An :class:`Actuator` for the battery asset (``suncore.devices.battery``).

    ``apply`` receives an intent whose ``BatteryAction`` the gate already resolved (``None`` =
    baseline) and quantised; ``read_back`` reports the adapter's readable limits as a
    ``BatteryAction`` (fields it cannot read are ``None``). Only the gate calls ``apply``,
    ``release``, ``arm_fallback`` and ``keep_alive`` (``tests/safety/test_boundary.py``).
    """

    @property
    def capabilities(self) -> frozenset[BatteryCapability]:
        """What the adapter can execute (ADR-0013 decision 1)."""
        ...

    @property
    def settle_s(self) -> float:
        """Delay before the read-back (Kostal REST: one poll interval of 30 s plus margin)."""
        ...

    @property
    def heartbeat_s(self) -> float | None:
        """Re-write period of an accepted state while the lease holds (``None`` = none)."""
        ...

    def ranges(self) -> BatteryRanges:
        """The live ranges of the writable limits (from the entities' attributes)."""
        ...

    def refusal(self) -> Reason | None:
        """A precondition that forbids every write right now (e.g. Kostal register 1080 is not
        ``0x02``: external battery management is off); ``None`` = may write."""
        ...

    def prepared(self) -> bool:
        """The hardware fallback is in place and confirmed by a read-back (``True`` for
        adapters without one)."""
        ...

    async def arm_fallback(self) -> None:
        """Write the hardware fallback (Kostal §3.5: 1284/1286 = own limits, 1288 = timeout)
        before the first limit; the gate reads back with :meth:`prepared`."""
        ...

    async def keep_alive(self) -> None:
        """Re-write the last applied state (idempotent; called only while the lease holds)."""
        ...

    async def takeover_baseline(self) -> Action | None:
        """The values a ``None`` field means and a release restores, read at takeover.

        Usually :meth:`read_back`; the Kostal §3.5 adapter answers the battery's own limits
        (1076/1078), because its limit registers may hold a stale value (review 4-G, M1).
        """
        ...


# --- configuration -----------------------------------------------------------------------------


PlanSource = Callable[[datetime], Plan]
"""``now -> Plan``: the plan the controller uses this tick (``ControlStack.current_plan``)."""
PriceSource = Callable[[datetime], float | None]
"""``now -> live import price`` (currency/kWh, ``None`` = unknown)."""


def _no_price(_: datetime) -> float | None:
    return None


def _default_plan(_: datetime) -> Plan:
    return Plan.default()


@dataclass(frozen=True, slots=True)
class BatteryGateConfig:
    """What the gate needs to judge battery intents (built by the composition root).

    ``identity`` is ``core.identity.battery_identity_token`` of the configured battery: a runtime
    taken over under another identity belongs to another battery (released, never written).
    ``reserve_pct`` is ``core.battery.effective_reserve_pct``; ``policy`` the effective grid-charge
    policy; ``fallback_verified`` the user's ``battery.fallback_measured`` (until it is set the
    gate caps an adapter with a hardware fallback at assisted, ADR-0013 decision 6).
    """

    identity: str
    model: BatteryModel | None
    reserve_pct: float | None
    policy: GridChargePolicy = field(default_factory=GridChargePolicy)
    fallback_verified: bool = False
    plan: PlanSource = _default_plan
    import_price: PriceSource = _no_price
    watch: BatteryWatch | None = None


# --- context ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BatteryLive:
    """The live site values the battery invariants use (``None`` = unknown or not ``OK``)."""

    soc_pct: float | None = None
    grid_w: float | None = None
    battery_w: float | None = None

    @classmethod
    def of(cls, flows: FlowSnapshot | None) -> BatteryLive:
        if flows is None:
            return cls()

        def ok(value: float | None, quality: Quality) -> float | None:
            return value if quality is Quality.OK else None

        return cls(
            soc_pct=ok(flows.battery_soc_pct.value, flows.battery_soc_pct.quality),
            grid_w=ok(flows.grid_w.value, flows.grid_w.quality),
            battery_w=ok(flows.battery_w.value, flows.battery_w.quality),
        )

    @property
    def house_net_w(self) -> float | None:
        """House minus PV (``grid - battery``): what the grid supplies without the battery."""
        if self.grid_w is None or self.battery_w is None:
            return None
        return self.grid_w - self.battery_w

    @property
    def deficit_w(self) -> float | None:
        """The power the battery would cover now (``max(0, house - pv)``)."""
        net = self.house_net_w
        return None if net is None else max(0.0, net)


@dataclass(frozen=True, slots=True)
class BatteryContext:
    """Everything the battery invariants look at for one action (or one lease renewal)."""

    requested: BatteryAction
    """The intent's action as the controller sent it (``None`` fields = baseline)."""
    target: BatteryAction
    """``requested`` resolved against the baseline and quantised: what would be written."""
    capabilities: frozenset[BatteryCapability]
    ranges: BatteryRanges
    model: BatteryModel | None
    reserve_pct: float | None
    """``core.battery.effective_reserve_pct`` (``None`` without a model)."""
    live: BatteryLive
    band: BatteryBand | None
    """The battery band of the plan interval covering ``now`` (``None`` = no interval)."""
    plan_fresh: bool
    policy: GridChargePolicy
    """The effective grid-charge policy (the battery policy with the goals schedule, computed
    by the composition root)."""
    import_price: float | None
    """The **live** import price now (currency/kWh; ``None`` = unknown)."""
    tz: tzinfo
    import_limit_w: float | None
    reserve_w: float
    """``ControlSettings.reserve_w``: kept free below the import limit."""
    now: datetime
    baseline: BatteryAction | None = None


def plan_band(plan: Plan, now: datetime) -> tuple[BatteryBand | None, bool]:
    """The band of the interval covering ``now`` and whether the plan is fresh
    (``core.control.battery_band_now``)."""
    return battery_band_now(plan, now)


def quantise(action: BatteryAction, ranges: BatteryRanges) -> BatteryAction:
    """Snap ``min_soc_pct`` **up** to the entity step (ADR-0013: Kostal step 5, rounded up).

    Powers are not snapped here: the adapters write whole watts and the gate compares with
    :data:`POWER_TOLERANCE_W`.
    """
    if action.min_soc_pct is None:
        return action
    soc_range = ranges.min_soc_pct
    if soc_range is None or soc_range.step is None:
        return action
    rounded = min(100.0, round_up_to_step(action.min_soc_pct, soc_range.step, soc_range.low))
    if math.isclose(rounded, action.min_soc_pct, abs_tol=1e-9):
        return action
    return action.model_copy(update={"min_soc_pct": rounded})


# --- derived facts ---------------------------------------------------------------------------


def grid_charge_w(ctx: BatteryContext) -> float:
    """The forced grid-charge power the target asks for (0 = none)."""
    return ctx.target.grid_charge_w or 0.0


def is_discharge_lock(ctx: BatteryContext) -> bool:
    """The state that would be **written** keeps the battery from covering the house
    (ADR-0013 decision 4).

    Judged on ``ctx.target`` - the resolved full state that is written and re-sent by the
    heartbeat - not on the request: a discharge cap or floor inherited from the baseline (for
    example a stale ``max_discharge_w = 0``) is written too and counts (review 4-G, M1).
    ``max_discharge_w`` below the current house deficit, or ``min_soc_pct`` at or above the
    live SoC. While the target forces grid charging the battery charges anyway, so it is no
    lock (its import is bounded by B2/B5). Unknown live values count as a lock (the conservative
    direction).
    """
    target = ctx.target
    if (target.grid_charge_w or 0.0) > 0:
        return False
    if target.max_discharge_w is not None:
        deficit = ctx.live.deficit_w
        if deficit is None or target.max_discharge_w + POWER_TOLERANCE_W < deficit:
            return True
    if target.min_soc_pct is not None:
        soc = ctx.live.soc_pct
        if soc is None or target.min_soc_pct >= soc - SOC_TOLERANCE_PCT:
            return True
    return False


def can_cause_import(ctx: BatteryContext) -> bool:
    """Grid charging or a discharge lock: the action may make the house import."""
    return grid_charge_w(ctx) > 0 or is_discharge_lock(ctx)


def projected_import_w(ctx: BatteryContext) -> float | None:
    """Import under the action, assuming the battery covers nothing (conservative).

    ``max(0, house - pv) + grid_charge_w``; ``None`` when grid or battery power is unknown.
    """
    deficit = ctx.live.deficit_w
    return None if deficit is None else deficit + grid_charge_w(ctx)


def grid_charge_bound_w(ctx: BatteryContext) -> float | None:
    """B2: ``min(model max charge, adapter limit, band.grid_charge_max_w, import headroom)``.

    ``None`` when any bound is unknown (then no grid charging at all). Import headroom =
    ``import_limit_w - max(0, house - pv) - reserve_w`` (the current import without the
    battery, so a battery already charging from the grid does not count twice).
    """
    model, band, limit = ctx.model, ctx.band, ctx.import_limit_w
    adapter = ctx.ranges.grid_charge_w
    deficit = ctx.live.deficit_w
    if (
        model is None
        or band is None
        or band.grid_charge_max_w is None
        or adapter is None
        or limit is None
        or deficit is None
    ):
        return None
    headroom = limit - deficit - ctx.reserve_w
    return max(0.0, min(model.max_charge_w, adapter.high, band.grid_charge_max_w, headroom))


# --- invariants ------------------------------------------------------------------------------


def check_capabilities(ctx: BatteryContext) -> Reason | None:
    """B9: no field the adapter cannot execute."""
    for name, value in action_fields(ctx.requested).items():
        if value is not None and FIELD_CAPABILITY[name] not in ctx.capabilities:
            return Reason(key="safety.battery_capability_missing", args={"field": name})
    return None


def check_model(ctx: BatteryContext) -> Reason | None:
    """Without a battery model (and so without a reserve) the battery is observe-only."""
    if ctx.model is None or ctx.reserve_pct is None:
        return REASON_BATTERY_MODEL_MISSING
    return None


def check_min_soc(ctx: BatteryContext) -> Reason | None:
    """B1: a requested floor (after rounding up) is never below the reserve nor the baseline.

    Below the reserve SunCore would allow a discharge into the user's backup energy; below the
    baseline it would allow more discharge than the user's own inverter setting (SunCore's
    limits only ever restrict). Together with B6 (no cap above the baseline) this is "the
    battery is never discharged below the reserve by SunCore" (BRIEF §5).
    """
    value = ctx.target.min_soc_pct if ctx.requested.min_soc_pct is not None else None
    if value is None or ctx.reserve_pct is None:
        return None
    if value < ctx.reserve_pct - SOC_TOLERANCE_PCT:
        return Reason(
            key="safety.battery_min_soc_below_reserve",
            args={"min_soc_pct": round(value, 1), "reserve_pct": round(ctx.reserve_pct, 1)},
        )
    base = None if ctx.baseline is None else ctx.baseline.min_soc_pct
    if base is not None and value < base - SOC_TOLERANCE_PCT:
        return Reason(
            key="safety.battery_min_soc_below_baseline",
            args={"min_soc_pct": round(value, 1), "baseline_pct": round(base, 1)},
        )
    return None


def check_ranges(ctx: BatteryContext) -> Reason | None:
    """B6: every requested value inside the adapter's live range and the battery's limits;
    a power cap never above the baseline (SunCore never widens the user's own limits)."""
    model = ctx.model
    for name in BATTERY_ACTION_FIELDS:
        if getattr(ctx.requested, name) is None:
            continue
        value = getattr(ctx.target, name)
        assert value is not None
        allowed = ctx.ranges.of(name)
        if allowed is None:
            return Reason(key="safety.battery_range_unknown", args={"field": name})
        tolerance = SOC_TOLERANCE_PCT if name == "min_soc_pct" else POWER_TOLERANCE_W
        if not allowed.contains(value, tolerance):
            return Reason(
                key="safety.battery_out_of_range",
                args={
                    "field": name,
                    "value": round(value, 1),
                    "low": round(allowed.low, 1),
                    "high": round(allowed.high, 1),
                },
            )
        if model is not None and name != "min_soc_pct":
            cap = model.max_discharge_w if name == "max_discharge_w" else model.max_charge_w
            if value > cap + POWER_TOLERANCE_W:
                return Reason(
                    key="safety.battery_out_of_range",
                    args={"field": name, "value": round(value, 1), "low": 0, "high": round(cap)},
                )
        base = None if ctx.baseline is None else getattr(ctx.baseline, name)
        if (
            name in ("max_charge_w", "max_discharge_w")
            and base is not None
            and value > base + POWER_TOLERANCE_W
        ):
            return Reason(
                key="safety.battery_above_baseline",
                args={"field": name, "value": round(value), "baseline": round(base)},
            )
    return None


def check_grid_charge(ctx: BatteryContext) -> Reason | None:  # noqa: PLR0911 - one return per rule
    """B2 + B3: forced grid charging only from a fresh plan interval that allows it, inside the
    grid-charge policy at the **live** price, below the maximum SoC and within every bound."""
    power = grid_charge_w(ctx)
    if power <= 0:
        return None
    band = ctx.band
    if band is None or band.grid_charge_max_w is None or band.grid_charge_max_w <= 0:
        return Reason(key="safety.battery_grid_charge_unplanned")
    if not ctx.plan_fresh:
        return Reason(key="safety.battery_plan_stale")
    if not ctx.policy.allows(ctx.now, ctx.import_price, ctx.tz):
        price = ctx.import_price
        return Reason(
            key="safety.battery_grid_charge_policy",
            args={"price": "unknown" if price is None else f"{price:.4f}"},
        )
    soc = ctx.live.soc_pct
    if soc is None:
        return Reason(key="safety.battery_soc_unknown")
    model = ctx.model
    if model is None:
        return REASON_BATTERY_MODEL_MISSING
    ceiling = min(band.max_soc_pct if band.max_soc_pct is not None else 100.0, model.max_soc_pct)
    if soc >= ceiling - SOC_TOLERANCE_PCT:
        return Reason(
            key="safety.battery_full", args={"soc_pct": round(soc, 1), "max_soc_pct": ceiling}
        )
    if ctx.import_limit_w is None:
        return Reason(key="safety.battery_import_limit_unknown")
    bound = grid_charge_bound_w(ctx)
    if bound is None:
        return Reason(key="safety.battery_grid_unknown")
    if bound <= 0 or power > bound + POWER_TOLERANCE_W:
        return Reason(
            key="safety.battery_grid_charge_bound",
            args={"grid_charge_w": round(power), "bound_w": round(bound)},
        )
    return None


def check_discharge_lock(ctx: BatteryContext) -> Reason | None:
    """B4: a discharge lock only while a fresh plan interval carries ``discharge_lock``."""
    if not is_discharge_lock(ctx):
        return None
    band = ctx.band
    if band is None or not band.discharge_lock:
        return Reason(key="safety.battery_unplanned_import")
    if not ctx.plan_fresh:
        return Reason(key="safety.battery_plan_stale")
    return None


def check_import_limit(ctx: BatteryContext) -> Reason | None:
    """B5: the site import limit holds for every battery action that can cause import."""
    if not can_cause_import(ctx) or ctx.import_limit_w is None:
        return None
    projected = projected_import_w(ctx)
    if projected is None:
        return Reason(key="safety.battery_grid_unknown")
    if projected > ctx.import_limit_w + POWER_TOLERANCE_W:
        return Reason(
            key="safety.battery_import_limit",
            args={"projected_w": round(projected), "limit_w": round(ctx.import_limit_w)},
        )
    return None


STATE_CHECKS: Final = (
    check_capabilities,
    check_model,
    check_min_soc,
    check_ranges,
    check_grid_charge,
    check_discharge_lock,
    check_import_limit,
)
"""B1-B6 and B9: judged for every intent and re-judged for the lease every tick."""


def evaluate_battery(ctx: BatteryContext) -> tuple[Reason, ...]:
    """Every violated battery invariant (B1-B6, B9), in check order; empty = may be written.

    The write rate, write-on-change (B7) and the watchdog (B8) are judged by the gate, which
    owns the runtime and the observed state.
    """
    return tuple(r for r in (check(ctx) for check in STATE_CHECKS) if r is not None)


def lease_violations(ctx: BatteryContext) -> tuple[Reason, ...]:
    """What ends the lease of an accepted state (re-checked every tick; ADR-0013 decision 5)."""
    return evaluate_battery(ctx)


def same_state(a: Action | None, b: Action | None, fields: Sequence[str]) -> bool:
    """Read-back comparison on ``fields`` (powers within 1 W, SoC within 0.01 %).

    A field the expected action sets must be read back with the same value; an unreadable
    (``None``) observed value is a mismatch.
    """
    if not isinstance(a, BatteryAction) or not isinstance(b, BatteryAction):
        return False
    for name in fields:
        want, got = getattr(a, name), getattr(b, name)
        if want is None:
            continue
        if got is None:
            return False
        tolerance = SOC_TOLERANCE_PCT if name == "min_soc_pct" else POWER_TOLERANCE_W
        if abs(want - got) > tolerance:
            return False
    return True


def readable_fields(capabilities: frozenset[BatteryCapability]) -> tuple[str, ...]:
    """The action fields an adapter with ``capabilities`` writes and reads back."""
    return tuple(name for name in BATTERY_ACTION_FIELDS if FIELD_CAPABILITY[name] in capabilities)


# --- watchdog (B8) ---------------------------------------------------------------------------


@dataclass(slots=True)
class BatteryWatch:
    """Hung-battery detection the site watchdog does not cover (B8).

    The site watchdog already pauses everything on an unavailable or invalid SoC/battery power
    and on frozen PV in daylight. This adds, for the battery scope only:

    * **frozen battery power**: the battery power keeps one value of at least ``min_w`` for
      ``frozen_s`` while the grid meter moves by more than ``grid_move_w`` (an inverter whose
      battery reading hangs while the house changes);
    * **frozen SoC**: the SoC keeps one value while the battery moved more than
      ``soc_energy_pct`` of the capacity (a BMS/integration that stopped reporting).

    A fault pauses battery control and releases the battery; it clears when the values move.
    """

    frozen_s: float = 600.0
    min_w: float = 50.0
    grid_move_w: float = 500.0
    soc_energy_pct: float = 3.0
    _power_since: tuple[float, datetime] | None = None
    _grid: list[tuple[datetime, float]] = field(default_factory=list)
    _soc_since: tuple[float, datetime] | None = None
    _energy_wh: float = 0.0
    _last: tuple[datetime, float] | None = None

    def reset(self) -> None:
        self._power_since = None
        self._grid = []
        self._soc_since = None
        self._energy_wh = 0.0
        self._last = None

    def check(self, now: datetime, live: BatteryLive, capacity_wh: float | None) -> Reason | None:
        """Feed one tick; the fault reason (``None`` = fine)."""
        power, grid, soc = live.battery_w, live.grid_w, live.soc_pct
        if power is None or grid is None:
            self.reset()  # the site watchdog judges missing values
            return None
        self._grid.append((now, grid))
        start = now - timedelta(seconds=self.frozen_s)
        self._grid = [(t, g) for t, g in self._grid if t >= start]
        if self._power_since is None or self._power_since[0] != power:
            self._power_since = (power, now)
        frozen_power = (
            abs(power) >= self.min_w
            and (now - self._power_since[1]).total_seconds() >= self.frozen_s
        )
        values = [g for t, g in self._grid if t >= self._power_since[1]]
        if frozen_power and values and max(values) - min(values) > self.grid_move_w:
            return Reason(key="safety.battery_frozen", args={"battery_w": round(power)})
        if self._last is not None:
            dt_h = (now - self._last[0]).total_seconds() / 3600
            self._energy_wh += abs(self._last[1]) * max(0.0, dt_h)
        self._last = (now, power)
        if soc is None:
            self._soc_since = None
            self._energy_wh = 0.0
            return None
        if self._soc_since is None or self._soc_since[0] != soc:
            self._soc_since = (soc, now)
            self._energy_wh = 0.0
            return None
        if capacity_wh is not None and capacity_wh > 0:
            moved_pct = 100 * self._energy_wh / capacity_wh
            if moved_pct >= self.soc_energy_pct:
                return Reason(
                    key="safety.battery_soc_frozen",
                    args={"soc_pct": round(soc, 1), "moved_pct": round(moved_pct, 1)},
                )
        return None


__all__ = [
    "PLAN_FRESH_INTERVALS",
    "POWER_TOLERANCE_W",
    "REASON_BATTERY_MODEL_MISSING",
    "REASON_BATTERY_NO_CHANGE",
    "STATE_CHECKS",
    "BatteryActuator",
    "BatteryContext",
    "BatteryGateConfig",
    "BatteryLive",
    "BatteryWatch",
    "can_cause_import",
    "check_capabilities",
    "check_discharge_lock",
    "check_grid_charge",
    "check_import_limit",
    "check_min_soc",
    "check_model",
    "check_ranges",
    "evaluate_battery",
    "grid_charge_bound_w",
    "grid_charge_w",
    "is_discharge_lock",
    "lease_violations",
    "plan_band",
    "projected_import_w",
    "quantise",
    "readable_fields",
    "same_state",
]
