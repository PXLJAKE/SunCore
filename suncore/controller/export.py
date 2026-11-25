# SPDX-License-Identifier: Apache-2.0
"""The export-limit chain (ADR-0018 decision 3, plan WP7; PR 6-G).

Deterministic and pure (``core`` only, time injected). The chain is

1. **devices** - the allocator gives surplus to the controlled devices (unchanged);
2. **battery** - the hybrid inverter charges the battery with what is left by itself;
   SunCore only lifts its *own* charge cap while the export exceeds the limit
   (:func:`lift_battery_charge_cap`);
3. **curtailment** - only then an :class:`~suncore.core.decisions.InverterLimitAction` through
   the inverter adapter (:class:`ExportChain`).

What :class:`ExportChain` wants each tick:

* the effective export limit (``core.grid.effective_export_limit_w``: static, 0 W for zero
  export, 0 W at a negative export price with a verified adapter) is *needed* only below the
  static limit, or always with ``enforce_static`` - the static limit itself stays in the
  inverter (plan §6 question 6: the maintainer's 13.5 kW);
* ``export_limit`` (W): that limit, rounded down to ``step_w``;
* ``ac_limit`` (%): ``house + limit``, at least ``house + battery charging + margin`` (X2),
  as the smallest whole percent of the nominal AC power that allows it;
* a limit **tighter** than the held one (or the first) only after the export stayed above
  the limit for ``curtail_delay_s`` (the devices had their hysteresis delay to rise) and while
  nothing could absorb more (``headroom_w`` = 0, X3); a **looser** one (the limit rose, the
  house load rose) at once - that is the safe direction;
* no limit needed any more, stale or not-``OK`` inputs (X5), island -> a ``ReleaseAction``
  (the user's own limit comes back); the first such release goes out at once.

Against oscillation: no release because the export fell (clouds) - a held limit costs nothing
while the PV is below it; an AC limit follows the house load downwards only by at least
``ac_hysteresis_pct`` and after the curtail delay; at most one new intent per ``interval_s``,
the same intent again at most every ``repeat_s``. The gate decides what is written; the
inverter asset starts in shadow and stays at most assisted until the limit is verified (X6).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from suncore.controller import reasons as rs
from suncore.controller.grid import GridInputs
from suncore.core.balance import FlowSnapshot
from suncore.core.decisions import (
    BatteryAction,
    Decision,
    DecisionKind,
    Intent,
    InverterLimitAction,
    Origin,
    Reason,
    ReleaseAction,
)
from suncore.core.grid import GridState
from suncore.core.inverter import (
    EXPORT_TOLERANCE_W,
    INVERTER_ASSET_ID,
    NO_IMPORT_MARGIN_W,
    LimitCapability,
    looser,
    pct_for_ac_w,
    same_limit,
)
from suncore.core.signals import Quality


@dataclass(frozen=True, slots=True)
class ExportControlSettings:
    curtail_delay_s: float = 120.0
    """The export must stay above the limit this long before the inverter is limited."""
    interval_s: float = 60.0
    """At most one new inverter intent per this many seconds."""
    repeat_s: float = 900.0
    """The same intent again (after shadow/proposed/rejected) at most this often."""
    step_w: float = 100.0
    """Export limits are multiples of this (rounded down)."""
    ac_hysteresis_pct: float = 3.0
    """An AC limit is lowered only when the new one is at least this much lower."""
    pending_timeout_s: float = 300.0


@dataclass(frozen=True, slots=True)
class ExportControlInputs:
    """What the composition root tells the controller about the inverter adapter each tick."""

    capability: LimitCapability
    nominal_ac_w: float | None = None
    static_limit_w: float | None = None
    enforce_static: bool = False
    verified: bool = False
    """``inverter.limit_verified``: until then an export limit is also kept at or above house
    + battery charging + margin, as the gate judges it (X2: its semantics are ``UNVERIFIED``)."""
    held: InverterLimitAction | None = None
    """What the gate holds (``runtime.last_written``; ``None`` = the user's own limit)."""
    under_control: bool = False
    blocked: Reason | None = None
    """Emergency stop, watchdog pause, override hold, pending release: no intents."""
    margin_w: float = NO_IMPORT_MARGIN_W


@dataclass(frozen=True, slots=True)
class ExportView:
    """The export decision of the tick (control state, "Why?")."""

    wanted: InverterLimitAction | None
    reasons: tuple[Reason, ...]
    needed_w: float | None = None
    export_w: float | None = None
    headroom_w: float | None = None
    intent_id: str | None = None
    last_decision: DecisionKind | None = None


@dataclass(frozen=True, slots=True)
class _Live:
    export_w: float
    house_w: float
    battery_w: float


def _live(flows: FlowSnapshot | None) -> _Live | None:
    """Grid, house and battery from ``OK`` readings only (X5); ``None`` otherwise."""
    if flows is None:
        return None
    values = []
    for flow in (flows.grid_w, flows.house_w, flows.battery_w, flows.pv_w):
        if flow.value is None or flow.quality is not Quality.OK:
            return None
        values.append(flow.value)
    grid, house, battery, _ = values
    return _Live(export_w=max(0.0, -grid), house_w=house, battery_w=battery)


def needed_limit_w(
    grid: GridInputs, static_limit_w: float | None, *, enforce_static: bool
) -> float | None:
    """The export limit SunCore must enforce now (``None`` = none: the inverter's own applies)."""
    limit = grid.export_limit_w
    if limit is None:
        return None
    if enforce_static or static_limit_w is None:
        return limit
    return limit if limit < static_limit_w - EXPORT_TOLERANCE_W else None


def export_over_limit(flows: FlowSnapshot | None, grid: GridInputs) -> bool:
    """The site exports more than the effective export limit right now (``OK`` readings)."""
    live = _live(flows)
    limit = grid.export_limit_w
    return live is not None and limit is not None and live.export_w > limit + EXPORT_TOLERANCE_W


def lift_battery_charge_cap(
    wanted: BatteryAction | None, *, over_limit: bool
) -> tuple[BatteryAction | None, bool]:
    """Chain step 2: while the export exceeds the limit SunCore holds no charge cap of its own.

    Returns the wanted battery state without ``max_charge_w`` (``None`` when nothing else is
    left) and whether a cap was lifted.
    """
    if wanted is None or not over_limit or wanted.max_charge_w is None:
        return wanted, False
    rest = wanted.model_copy(update={"max_charge_w": None})
    if all(v is None for v in rest.model_dump(exclude={"kind"}).values()):
        return None, True
    return rest, True


DEVICE_HEADROOM_W: Final = 100.0
"""A device on its way up counts at least this much absorb headroom (X3)."""
_CAP_TOLERANCE_W: Final = 50.0


def absorb_headroom_w(
    devices: Iterable[DeviceHeadroom],
    battery_held: BatteryAction | None,
    battery_w: float | None,
    *,
    battery_blocked: bool = False,
    battery_high_w: float | None = None,
) -> float:
    """Chain steps 1 and 2 not yet exhausted: what could still absorb more export (W, >= 0).

    * every device the allocator is moving up (``rising``) counts its step, at least
      :data:`DEVICE_HEADROOM_W` - it had its hysteresis delay and is on its way;
    * SunCore's own charge cap, while the battery charges at it: the cap is about to be lifted
      (chain step 2), so the battery absorbs more without curtailment. Without a SunCore cap
      the hybrid inverter already charges with what it can - no headroom from SunCore's side.

    A device that stays below its top in steady state is *not* headroom: the allocator decided
    it cannot rise (pause time, comfort, budget) and curtailing is the only way left.
    """
    total = 0.0
    for device in devices:
        if device.rising:
            total += max(DEVICE_HEADROOM_W, device.step_w)
    cap = None if battery_held is None else battery_held.max_charge_w
    if (
        cap is not None
        and not battery_blocked
        and battery_w is not None
        and battery_w >= cap - _CAP_TOLERANCE_W
    ):
        high = battery_high_w if battery_high_w is not None else cap + DEVICE_HEADROOM_W
        total += max(DEVICE_HEADROOM_W, high - cap)
    return total


@dataclass(frozen=True, slots=True)
class DeviceHeadroom:
    """One controlled device as the export chain sees it."""

    device_id: str
    rising: bool
    step_w: float = 0.0


class ExportChain:
    """Stateful translation of the export limit into inverter intents (one inverter)."""

    def __init__(self, settings: ExportControlSettings | None = None) -> None:
        self._settings = settings if settings is not None else ExportControlSettings()
        self._over_since: datetime | None = None
        self._last_submit: datetime | None = None
        self._last_action: InverterLimitAction | ReleaseAction | None = None
        self._last_kind: DecisionKind | None = None
        self._pending: str | None = None
        self._seq = 0
        self.view: ExportView | None = None

    @property
    def settings(self) -> ExportControlSettings:
        return self._settings

    # --- the wanted limit ---------------------------------------------------------------------

    def wanted(
        self, inputs: ExportControlInputs, live: _Live, needed_w: float
    ) -> InverterLimitAction | None:
        """The limit that enforces ``needed_w`` now (``None`` = cannot be expressed)."""
        floor = live.house_w + max(0.0, live.battery_w) + inputs.margin_w
        if inputs.capability == "export_limit":
            step = self._settings.step_w
            value = math.floor(needed_w / step) * step if step > 0 else needed_w
            if not inputs.verified:
                value = max(value, math.ceil(floor / step) * step if step > 0 else floor)
            return InverterLimitAction(max_export_w=max(0.0, value))
        nominal = inputs.nominal_ac_w
        if nominal is None:
            return None
        pct = pct_for_ac_w(max(live.house_w + needed_w, floor), nominal)
        held = inputs.held
        if (
            held is not None
            and held.max_ac_pct is not None
            and held.max_ac_pct >= pct
            and held.max_ac_pct - pct < self._settings.ac_hysteresis_pct
            and held.max_ac_pct / 100.0 * nominal >= floor
        ):
            return held  # within the hysteresis band: keep
        return InverterLimitAction(max_ac_pct=pct)

    # --- the intent ---------------------------------------------------------------------------

    def step(  # noqa: PLR0911, PLR0912, PLR0915 - one return per rule
        self,
        now: datetime,
        inputs: ExportControlInputs | None,
        flows: FlowSnapshot | None,
        grid: GridInputs,
        headroom_w: float | None,
    ) -> Intent | None:
        """The inverter intent of this tick (``None`` = nothing to submit)."""
        if inputs is None:
            self.view = None
            self._over_since = None
            return None
        if inputs.blocked is not None:
            self._over_since = None
            self.view = ExportView(None, (rs.plain(rs.EXPORT_BLOCKED), inputs.blocked))
            return None
        if (
            self._pending is not None
            and self._last_submit is not None
            and now - self._last_submit > timedelta(seconds=self._settings.pending_timeout_s)
        ):
            self._pending = None
        live = _live(flows)
        needed = needed_limit_w(grid, inputs.static_limit_w, enforce_static=inputs.enforce_static)
        reasons: tuple[Reason, ...]
        wanted: InverterLimitAction | None = None
        urgent = False
        if grid.grid_state is GridState.ISLAND:
            reasons, urgent = (rs.plain(rs.EXPORT_ISLAND),), True
            self._over_since = None
        elif live is None:
            reasons, urgent = (rs.plain(rs.EXPORT_INPUTS_UNKNOWN),), True
            self._over_since = None
        elif needed is None:
            reasons = (rs.plain(rs.EXPORT_NOT_NEEDED),)
            self._over_since = None
        else:
            over = live.export_w > needed + EXPORT_TOLERANCE_W
            if not over:
                self._over_since = None
            elif self._over_since is None:
                self._over_since = now
            wanted = self.wanted(inputs, live, needed)
            reasons = self._limit_reasons(inputs, needed)
            if wanted is None:
                reasons = (rs.plain(rs.EXPORT_INPUTS_UNKNOWN),)
        view = ExportView(
            wanted,
            reasons,
            needed_w=needed,
            export_w=None if live is None else live.export_w,
            headroom_w=headroom_w,
            last_decision=self._last_kind,
        )
        self.view = view
        action: InverterLimitAction | ReleaseAction | None
        if wanted is None:
            if not inputs.under_control:
                return None
            action = ReleaseAction()
            reasons = (rs.plain(rs.EXPORT_RELEASE), *reasons)
        elif inputs.under_control and inputs.held is not None and same_limit(inputs.held, wanted):
            return None  # the gate holds exactly this
        elif inputs.under_control and inputs.held is not None and looser(wanted, inputs.held):
            action = wanted  # safe direction: at once (subject to pacing)
        else:
            # Tighter (or the first): the chain must be exhausted (X3) and the excess persistent.
            if headroom_w is None or headroom_w > EXPORT_TOLERANCE_W:
                self.view = replace(view, reasons=(rs.plain(rs.EXPORT_ABSORBING), *reasons))
                return None
            since = self._over_since
            if since is None or now - since < timedelta(seconds=self._settings.curtail_delay_s):
                self.view = replace(view, reasons=(rs.plain(rs.EXPORT_WAITING), *reasons))
                return None
            action = wanted
        if self._pending is not None:
            return None
        first_urgent = urgent and isinstance(action, ReleaseAction) and action != self._last_action
        if not first_urgent and not self._due(now, action):
            return None
        self._seq += 1
        intent = Intent(
            id=f"inverter:{now.isoformat()}:{self._seq}",
            ts=now,
            asset_id=INVERTER_ASSET_ID,
            action=action,
            reasons=reasons,
            origin=Origin.CONTROLLER,
        )
        self._last_submit = now
        self._last_action = action
        self._last_kind = None
        self._pending = intent.id
        self.view = replace(self.view, intent_id=intent.id, last_decision=None)
        return intent

    @staticmethod
    def _limit_reasons(inputs: ExportControlInputs, needed: float) -> tuple[Reason, ...]:
        key = rs.EXPORT_LIMIT if inputs.capability == "export_limit" else rs.EXPORT_AC_LIMIT
        return (Reason(key=key, args={"limit_w": round(needed)}),)

    def _due(self, now: datetime, action: InverterLimitAction | ReleaseAction) -> bool:
        last = self._last_submit
        if last is None:
            return True
        elapsed = now - last
        if action != self._last_action or self._last_kind in (DecisionKind.FAILED, None):
            return elapsed >= timedelta(seconds=self._settings.interval_s)
        return elapsed >= timedelta(seconds=self._settings.repeat_s)

    def report(self, decision: Decision) -> bool:
        """The gate's decision on our intent; ``True`` if it was ours and pending."""
        if decision.intent.asset_id != INVERTER_ASSET_ID or decision.intent.id != self._pending:
            return False
        self._pending = None
        self._last_kind = decision.kind
        if self.view is not None:
            self.view = replace(self.view, last_decision=decision.kind)
        return True

    def reset_pending(self) -> None:
        """A submission was lost (transport error): the next tick may submit again."""
        self._pending = None


__all__ = [
    "DEVICE_HEADROOM_W",
    "DeviceHeadroom",
    "ExportChain",
    "ExportControlInputs",
    "ExportControlSettings",
    "ExportView",
    "absorb_headroom_w",
    "export_over_limit",
    "lift_battery_charge_cap",
    "needed_limit_w",
]
