# SPDX-License-Identifier: Apache-2.0
"""Inverter limit invariants X1-X6 (ADR-0018 decision 3, plan WP7) and the actuator protocol.

The inverter limit is the last link of the export chain: devices first, then the battery, then
an export limit (W) or an AC output limit (% of the nominal AC power) on the inverter. It is a
write that can **cause grid import** (an AC limit below the house load) and that can **remove
export protection** (a limit above the user's own). Every write therefore passes these pure
checks; the gate builds an :class:`InverterContext` from the intent, the adapter, the takeover
baseline (the user's own limit) and the live flows, and :func:`evaluate_inverter` returns every
violation (empty = may be written).

* X1 - never above the configured static export limit nor the baseline (:func:`check_bounds`);
* X2 - an AC limit (or a not yet verified export limit, read as an AC limit) keeps house load
  + battery charging + 500 W (:func:`check_no_import`);
* X3 - below the current export only when devices and battery cannot absorb more
  (:func:`check_chain`);
* X4 - write on change, rate limit, read-back, heartbeat only under the lease (the gate);
* X5 - stale or not-OK PV/grid/house inputs, island -> no write; the lease ends (baseline)
  (:func:`check_inputs`, :func:`lease_violations`);
* X6 - at most assisted until ``inverter.limit_verified`` (the gate, ``modes_of``).

Signs and units per ``core/units.py``: grid + = import, battery + = charging, W and %.
Property tests: ``tests/safety/test_inverter_invariants.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from suncore.core.balance import FlowSnapshot
from suncore.core.decisions import Action, InverterLimitAction, Reason
from suncore.core.grid import GridState
from suncore.core.inverter import (
    EXPORT_TOLERANCE_W,
    NO_IMPORT_MARGIN_W,
    LimitCapability,
    ac_limit_w,
    capability_of,
    export_cap_w,
    looser,
    same_limit,
)
from suncore.core.signals import Quality
from suncore.safety.actuator import Actuator

REASON_INVERTER_NOT_SUPPORTED: Final = Reason(key="safety.inverter_limit_not_supported")
REASON_INVERTER_NO_CHANGE: Final = Reason(key="safety.inverter_write_no_change")
REASON_INVERTER_CAPABILITY: Final = Reason(key="safety.inverter_capability_mismatch")
REASON_INVERTER_INPUTS_UNKNOWN: Final = Reason(key="safety.inverter_inputs_unknown")
REASON_INVERTER_BASELINE_UNKNOWN: Final = Reason(key="safety.inverter_baseline_unknown")
REASON_INVERTER_ISLAND: Final = Reason(key="safety.inverter_island")


# --- the actuator ---------------------------------------------------------------------------


@runtime_checkable
class InverterActuator(Actuator, Protocol):
    """An :class:`Actuator` for the inverter limit (``suncore.devices.inverter``).

    ``apply`` receives an intent with an :class:`InverterLimitAction` the gate judged;
    ``read_back`` reports the limit the inverter shows (``None`` = unknown). Only the gate calls
    ``apply``, ``release`` and ``keep_alive`` (``tests/safety/test_boundary.py``).
    """

    @property
    def capability(self) -> LimitCapability: ...

    @property
    def settle_s(self) -> float:
        """Delay before the read-back (REST: one settings poll; modbus: one sensor scan)."""
        ...

    @property
    def heartbeat_s(self) -> float | None:
        """Re-write period of a held limit for a volatile register (``None`` = not volatile)."""
        ...

    def limit_range(self) -> tuple[float, float]:
        """The writable range in the capability's unit (W or %)."""
        ...

    def refusal(self) -> Reason | None:
        """A precondition that forbids every write now (``None`` = may write)."""
        ...

    @property
    def default_limit(self) -> InverterLimitAction | None:
        """The default a volatile register falls back to (``None`` = not volatile)."""
        ...

    async def keep_alive(self, limit: InverterLimitAction) -> None:
        """Re-write ``limit`` - the confirmed limit the lease just checked (review A1)."""
        ...


# --- configuration and context ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InverterGateConfig:
    """What the gate needs to judge inverter intents (built by the composition root).

    ``identity`` is ``InverterConfig.identity_token()``: a runtime taken over under another
    identity belongs to another adapter (released with the old actuator, never written).
    """

    identity: str
    capability: LimitCapability
    verified: bool = False
    nominal_ac_w: float | None = None
    static_limit_w: float | None = None
    """The configured static export limit (``core.grid.static_export_limit_w``)."""
    margin_w: float = NO_IMPORT_MARGIN_W
    pv_kwp: float | None = None
    """The installed PV peak power (``SiteConfig.total_kwp``) for the plausibility of
    ``nominal_ac_w`` (review A4; ``None`` = not checked)."""


@dataclass(frozen=True, slots=True)
class InverterLive:
    """The live site values the inverter invariants use (``None`` = unknown or not ``OK``)."""

    grid_w: float | None = None
    pv_w: float | None = None
    house_w: float | None = None
    battery_w: float | None = None
    fresh: bool = False
    """The controller's snapshot is recent enough (``inputs_max_age_ticks``)."""

    @classmethod
    def of(cls, flows: FlowSnapshot | None, *, fresh: bool) -> InverterLive:
        if flows is None:
            return cls(fresh=fresh)

        def ok(value: float | None, quality: Quality) -> float | None:
            return value if quality is Quality.OK else None

        return cls(
            grid_w=ok(flows.grid_w.value, flows.grid_w.quality),
            pv_w=ok(flows.pv_w.value, flows.pv_w.quality),
            house_w=ok(flows.house_w.value, flows.house_w.quality),
            battery_w=ok(flows.battery_w.value, flows.battery_w.quality),
            fresh=fresh,
        )

    @property
    def known(self) -> bool:
        """X5: everything the limit is judged with is fresh and ``OK``."""
        return self.fresh and None not in (self.grid_w, self.pv_w, self.house_w, self.battery_w)

    @property
    def export_w(self) -> float | None:
        return None if self.grid_w is None else max(0.0, -self.grid_w)


@dataclass(frozen=True, slots=True)
class InverterContext:
    """Everything the inverter invariants look at for one limit (or one lease renewal)."""

    requested: InverterLimitAction
    config: InverterGateConfig
    live: InverterLive
    baseline: InverterLimitAction | None
    """The user's own limit, read at takeover (``None`` = unknown)."""
    limit_range: tuple[float, float]
    headroom_w: float | None = None
    """``GateInputs.export_headroom_w`` (``None`` = unknown)."""
    grid_state: GridState = GridState.NOT_CONFIGURED


# --- the invariants --------------------------------------------------------------------------


def check_capability(ctx: InverterContext) -> Reason | None:
    """The limit is in the adapter's unit and range."""
    if capability_of(ctx.requested) != ctx.config.capability:
        return Reason(
            key=REASON_INVERTER_CAPABILITY.key, args={"capability": ctx.config.capability}
        )
    value = (
        ctx.requested.max_export_w
        if ctx.requested.max_export_w is not None
        else ctx.requested.max_ac_pct
    )
    assert value is not None
    low, high = ctx.limit_range
    if not low - 1e-9 <= value <= high + 1e-9:
        return Reason(
            key="safety.inverter_limit_out_of_range",
            args={"value": round(value, 1), "min": round(low, 1), "max": round(high, 1)},
        )
    return None


def check_bounds(ctx: InverterContext) -> Reason | None:
    """X1: never looser than the user's own limit (baseline), never above the static limit."""
    if ctx.baseline is None or capability_of(ctx.baseline) != capability_of(ctx.requested):
        return REASON_INVERTER_BASELINE_UNKNOWN
    if looser(ctx.requested, ctx.baseline):
        return Reason(key="safety.inverter_above_baseline")
    static = ctx.config.static_limit_w
    export = ctx.requested.max_export_w
    if export is not None and static is not None and export > static + EXPORT_TOLERANCE_W:
        return Reason(key="safety.inverter_above_static", args={"limit_w": round(static)})
    return None


def check_inputs(ctx: InverterContext) -> Reason | None:
    """X5: no limit on stale or not-``OK`` inputs, none in island (there is no export)."""
    if ctx.grid_state is GridState.ISLAND:
        return REASON_INVERTER_ISLAND
    if not ctx.live.known:
        return REASON_INVERTER_INPUTS_UNKNOWN
    return None


LEASE_MARGIN_SHARE: Final = 0.5
"""A held limit ends (X2 lease) only when less than this share of the margin is left: the
controller loosens it at once when the full margin is gone, so house-load noise never makes
the lease and the write check fight (no oscillation)."""


def no_import_floor_w(ctx: InverterContext, *, share: float = 1.0) -> float | None:
    """X2: the AC output the limit must leave (house + battery charging + margin)."""
    house, battery = ctx.live.house_w, ctx.live.battery_w
    if house is None or battery is None:
        return None
    return house + max(0.0, battery) + ctx.config.margin_w * share


def check_no_import(ctx: InverterContext, *, share: float = 1.0) -> Reason | None:
    """X2: an AC limit never below house load + battery charging + margin.

    A not yet verified export limit is judged the same way, read as an AC limit in W: the
    semantics of ``Inverter:ActivePowerLimitation`` are ``UNVERIFIED`` (it may limit the AC
    output), so until the verification test it must not be able to cause import either way.
    """
    requested = ctx.requested
    if requested.max_ac_pct is not None:
        ac = ac_limit_w(requested, ctx.config.nominal_ac_w)
        if ac is None:
            return Reason(key="safety.inverter_nominal_unknown")
    elif not ctx.config.verified:
        ac = requested.max_export_w
    else:
        return None
    floor = no_import_floor_w(ctx, share=share)
    if floor is None:
        return REASON_INVERTER_INPUTS_UNKNOWN
    assert ac is not None
    if ac + 1e-6 < floor:
        return Reason(
            key="safety.inverter_may_cause_import",
            args={"limit_w": round(ac), "needed_w": round(floor)},
        )
    return None


def check_chain(ctx: InverterContext) -> Reason | None:
    """X3: curtail below the current export only when devices and battery cannot absorb more."""
    export = ctx.live.export_w
    cap = export_cap_w(ctx.requested, ctx.config.nominal_ac_w, ctx.live.house_w)
    if export is None or cap is None:
        return REASON_INVERTER_INPUTS_UNKNOWN
    if cap >= export - EXPORT_TOLERANCE_W:
        return None
    headroom = ctx.headroom_w
    if headroom is None:
        return Reason(key="safety.inverter_headroom_unknown")
    if headroom > EXPORT_TOLERANCE_W:
        return Reason(
            key="safety.inverter_chain_not_exhausted", args={"headroom_w": round(headroom)}
        )
    return None


NOMINAL_PER_KWP_RANGE: Final = (0.3, 2.0)
"""A plausible ``nominal_ac_w`` in kW per installed kWp (review A4). A too large nominal power
makes every percent allow less than X2 assumes; the verification test checks the base itself."""


def check_nominal(ctx: InverterContext) -> Reason | None:
    """X2 premise: the percent base of an AC limit fits the installed PV (rough check)."""
    nominal, kwp = ctx.config.nominal_ac_w, ctx.config.pv_kwp
    if ctx.requested.max_ac_pct is None or nominal is None or kwp is None or kwp <= 0:
        return None
    low, high = NOMINAL_PER_KWP_RANGE
    if not low * kwp * 1000.0 <= nominal <= high * kwp * 1000.0:
        return Reason(
            key="safety.inverter_nominal_implausible",
            args={"nominal_w": round(nominal), "kwp": round(kwp, 1)},
        )
    return None


def check_takeover_baseline(
    baseline: InverterLimitAction,
    *,
    default: InverterLimitAction | None,
    static_limit_w: float | None,
) -> Reason | None:
    """Review A2: take the inverter over only from a baseline a release can safely restore.

    A volatile register must show the inverter's default (a release writes that default; any
    other value means a mis-mapped or scaled sensor, a foreign writer or a stuck own value, and
    restoring it would throttle the inverter for good). An export limit below the static limit
    is not the user's normal value (a stuck SunCore value or a mapping error).
    """
    if default is not None and not same_limit(baseline, default):
        value = baseline.max_ac_pct if baseline.max_ac_pct is not None else baseline.max_export_w
        default_value = (
            default.max_ac_pct if default.max_ac_pct is not None else default.max_export_w
        )
        return Reason(
            key="safety.inverter_baseline_not_default",
            args={"value": round(value or 0.0, 1), "default": round(default_value or 0.0, 1)},
        )
    export = baseline.max_export_w
    if (
        export is not None
        and static_limit_w is not None
        and export < static_limit_w - EXPORT_TOLERANCE_W
    ):
        return Reason(
            key="safety.inverter_baseline_below_static",
            args={"value_w": round(export), "static_w": round(static_limit_w)},
        )
    return None


_CHECKS: Final[tuple[Callable[[InverterContext], Reason | None], ...]] = (
    check_capability,
    check_nominal,
    check_inputs,
    check_bounds,
    check_no_import,
    check_chain,
)


def _lease_no_import(ctx: InverterContext) -> Reason | None:
    return check_no_import(ctx, share=LEASE_MARGIN_SHARE)


_LEASE_CHECKS: Final[tuple[Callable[[InverterContext], Reason | None], ...]] = (
    check_inputs,
    check_bounds,
    _lease_no_import,
)


def evaluate_inverter(ctx: InverterContext) -> tuple[Reason, ...]:
    """Every violation of a new limit (empty = may be written)."""
    return tuple(r for check in _CHECKS if (r := check(ctx)) is not None)


def lease_violations(ctx: InverterContext) -> tuple[Reason, ...]:
    """What ends a held limit (re-checked every tick): X5 inputs, X1 bounds, X2 import with
    half the margin (:data:`LEASE_MARGIN_SHARE`).

    X3 is not re-judged: while the limit curtails, the export equals the limit by design.
    """
    if ctx.grid_state is GridState.ISLAND:
        return (REASON_INVERTER_ISLAND,)
    return tuple(r for check in _LEASE_CHECKS if (r := check(ctx)) is not None)


def as_limit(action: Action | None) -> InverterLimitAction | None:
    return action if isinstance(action, InverterLimitAction) else None


__all__ = [
    "LEASE_MARGIN_SHARE",
    "NOMINAL_PER_KWP_RANGE",
    "REASON_INVERTER_BASELINE_UNKNOWN",
    "REASON_INVERTER_CAPABILITY",
    "REASON_INVERTER_INPUTS_UNKNOWN",
    "REASON_INVERTER_ISLAND",
    "REASON_INVERTER_NOT_SUPPORTED",
    "REASON_INVERTER_NO_CHANGE",
    "InverterActuator",
    "InverterContext",
    "InverterGateConfig",
    "InverterLive",
    "as_limit",
    "check_bounds",
    "check_capability",
    "check_chain",
    "check_inputs",
    "check_no_import",
    "check_nominal",
    "check_takeover_baseline",
    "evaluate_inverter",
    "lease_violations",
    "no_import_floor_w",
]
