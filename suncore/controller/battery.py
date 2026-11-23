# SPDX-License-Identifier: Apache-2.0
"""The battery band of the plan -> ``BatteryAction`` intents (Phase 4 WP10, ADR-0013).

Deterministic, time injected, ``core`` only. Per tick the controller translates the band of the
plan interval covering ``now`` - only while the plan is fresh, like the gate - into the state it
wants the battery adapter to hold:

* ``grid_charge_max_w > 0`` -> ``grid_charge_w`` (rounded down to ``grid_step_w``, at most the
  battery's and the adapter's limit) - only if the adapter can force charging; otherwise no
  intent and the reason ``controller.battery.grid_charge_unsupported`` (the plan still shows
  the planned grid charging; Kostal REST has no charge set-point);
* ``discharge_lock`` -> ``max_discharge_w = 0`` where the adapter caps discharging, else (a
  ``min_soc`` adapter such as Kostal REST) ``min_soc_pct`` = the live SoC rounded **up** to
  the entity step, never below the effective reserve;
* nothing planned -> ``None`` = the adapter's baseline: a ``ReleaseAction`` while the gate
  still holds something, otherwise no intent at all.

The reserve is the floor of every ``min_soc_pct`` SunCore writes; SunCore does not hold a
permanent reserve floor on the inverter (that would keep the battery under SunCore's control
around the clock and could lower the user's own floor - ``None`` = baseline). Grid charging
wins over a lock in the same interval (the planner never plans both).

Against oscillation: intents only on change of the wanted state; a power change smaller than
``hysteresis_w`` keeps the held value; a ``min_soc`` lock is moved only when the SoC rose a full
step above it; at most one new intent per ``interval_s``; the same intent is repeated (after a
shadow, proposed or rejected decision) at most every ``repeat_s``. The battery stays in shadow
by default - the gate decides what is written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from suncore.controller.reasons import (
    BATTERY_BLOCKED,
    BATTERY_DISCHARGE_LOCK,
    BATTERY_DISCHARGE_LOCK_UNSUPPORTED,
    BATTERY_GRID_CHARGE,
    BATTERY_GRID_CHARGE_UNSUPPORTED,
    BATTERY_NO_PLAN,
    BATTERY_RELEASE,
)
from suncore.core.battery import (
    BATTERY_ASSET_ID,
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatteryRanges,
    round_up_to_step,
)
from suncore.core.control import Plan, battery_band_now
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    Origin,
    Reason,
    ReleaseAction,
)


@dataclass(frozen=True, slots=True)
class BatteryControlSettings:
    interval_s: float = 60.0
    """At most one new battery intent per this many seconds."""
    repeat_s: float = 900.0
    """The same intent again (after shadow/proposed/rejected) at most this often."""
    hysteresis_w: float = 300.0
    """A grid-charge power change below this keeps the held power."""
    grid_step_w: float = 100.0
    """Grid-charge powers are multiples of this (rounded down)."""
    pending_timeout_s: float = 300.0
    """An intent without a reported decision is forgotten after this (a lost submission)."""


@dataclass(frozen=True, slots=True)
class BatteryControlInputs:
    """What the composition root tells the controller about the battery adapter each tick."""

    capabilities: frozenset[BatteryCapability]
    ranges: BatteryRanges
    model: BatteryModel
    reserve_pct: float
    held: BatteryAction | None = None
    """What the gate holds (``runtime.last_written``; ``None`` = baseline / not controlled)."""
    under_control: bool = False
    blocked: Reason | None = None
    """Emergency stop, watchdog pause, override hold, pending release: no intents."""


@dataclass(frozen=True, slots=True)
class BatteryView:
    """The controller's battery decision of the tick (control state, "Why?")."""

    wanted: BatteryAction | None
    """The state the plan asks for (``None`` = baseline)."""
    reasons: tuple[Reason, ...]
    intent_id: str | None = None
    last_decision: DecisionKind | None = None


class BatteryIntents:
    """Stateful translation of the plan's battery band into gate intents (one battery)."""

    def __init__(self, settings: BatteryControlSettings | None = None) -> None:
        self._settings = settings if settings is not None else BatteryControlSettings()
        self._last_submit: datetime | None = None
        self._last_action: BatteryAction | ReleaseAction | None = None
        self._last_kind: DecisionKind | None = None
        self._pending: str | None = None
        self._seq = 0
        self.view: BatteryView | None = None

    @property
    def settings(self) -> BatteryControlSettings:
        return self._settings

    # --- the wanted state --------------------------------------------------------------------

    def wanted(  # noqa: PLR0911 - one return per rule
        self, now: datetime, plan: Plan, inputs: BatteryControlInputs, soc_pct: float | None
    ) -> tuple[BatteryAction | None, tuple[Reason, ...]]:
        """The state the plan asks for now and why (pure apart from the held state)."""
        band, fresh = battery_band_now(plan, now)
        if band is None or not fresh:
            return None, (Reason(key=BATTERY_NO_PLAN),)
        caps = inputs.capabilities
        grid_max = band.grid_charge_max_w or 0.0
        if grid_max > 0:
            grid = self._grid_power(grid_max, inputs)
            if grid is None:
                return None, (
                    Reason(key=BATTERY_GRID_CHARGE_UNSUPPORTED, args={"power_w": round(grid_max)}),
                )
            return BatteryAction(grid_charge_w=grid), (
                Reason(key=BATTERY_GRID_CHARGE, args={"power_w": round(grid)}),
            )
        if band.discharge_lock:
            if (
                BatteryCapability.DISCHARGE_LIMIT in caps
                and inputs.ranges.max_discharge_w is not None
            ):
                return BatteryAction(max_discharge_w=0.0), (Reason(key=BATTERY_DISCHARGE_LOCK),)
            if (
                BatteryCapability.MIN_SOC in caps
                and inputs.ranges.min_soc_pct is not None
                and soc_pct is not None
            ):
                floor = self._lock_floor(soc_pct, inputs)
                return BatteryAction(min_soc_pct=floor), (
                    Reason(key=BATTERY_DISCHARGE_LOCK, args={"min_soc_pct": round(floor)}),
                )
            return None, (Reason(key=BATTERY_DISCHARGE_LOCK_UNSUPPORTED),)
        return None, ()

    def _grid_power(self, planned_w: float, inputs: BatteryControlInputs) -> float | None:
        allowed = inputs.ranges.grid_charge_w
        if BatteryCapability.GRID_CHARGE not in inputs.capabilities or allowed is None:
            return None
        if math.isclose(allowed.low, allowed.high):
            # A switch or a mode option: all or nothing - only if the plan allows the full power.
            return allowed.high if planned_w + 1.0 >= allowed.high else None
        step = self._settings.grid_step_w
        power = min(planned_w, inputs.model.max_charge_w, allowed.high)
        power = math.floor(power / step) * step if step > 0 else power
        if power <= 0:
            return None
        held = inputs.held.grid_charge_w if inputs.held is not None else None
        if held is not None and held > 0 and abs(held - power) < self._settings.hysteresis_w:
            return held
        return power

    def _lock_floor(self, soc_pct: float, inputs: BatteryControlInputs) -> float:
        soc_range = inputs.ranges.min_soc_pct
        assert soc_range is not None
        step = soc_range.step
        held = inputs.held.min_soc_pct if inputs.held is not None else None
        if held is not None and held >= inputs.reserve_pct and soc_pct < held + (step or 1.0):
            # Still (almost) a lock: moved only when the SoC rose a full step above it, so a
            # SoC creeping up under PV does not rewrite the floor every few minutes.
            return held
        floor = max(inputs.reserve_pct, soc_pct)
        return min(soc_range.high, round_up_to_step(floor, step, soc_range.low))

    # --- the intent --------------------------------------------------------------------------

    def step(
        self,
        now: datetime,
        plan: Plan,
        inputs: BatteryControlInputs | None,
        soc_pct: float | None,
    ) -> Intent | None:
        """The battery intent of this tick (``None`` = nothing to submit)."""
        if inputs is None:
            self.view = None
            return None
        if inputs.blocked is not None:
            self.view = BatteryView(None, (Reason(key=BATTERY_BLOCKED), inputs.blocked))
            return None
        if (
            self._pending is not None
            and self._last_submit is not None
            and now - self._last_submit > timedelta(seconds=self._settings.pending_timeout_s)
        ):
            self._pending = None
        wanted, reasons = self.wanted(now, plan, inputs, soc_pct)
        action: BatteryAction | ReleaseAction | None = wanted
        if wanted is None:
            action = ReleaseAction() if inputs.under_control else None
            if action is not None:
                reasons = (Reason(key=BATTERY_RELEASE), *reasons)
        elif inputs.under_control and inputs.held == wanted:
            action = None  # the gate holds exactly this
        self.view = BatteryView(wanted, reasons, last_decision=self._last_kind)
        if action is None or self._pending is not None or not self._due(now, action):
            return None
        self._seq += 1
        intent = Intent(
            id=f"battery:{now.isoformat()}:{self._seq}",
            ts=now,
            asset_id=BATTERY_ASSET_ID,
            action=action,
            reasons=reasons,
            origin=Origin.CONTROLLER,
        )
        self._last_submit = now
        self._last_action = action
        self._last_kind = None
        self._pending = intent.id
        self.view = BatteryView(wanted, reasons, intent_id=intent.id)
        return intent

    def _due(self, now: datetime, action: BatteryAction | ReleaseAction) -> bool:
        last = self._last_submit
        if last is None:
            return True
        elapsed = now - last
        if action != self._last_action:
            return elapsed >= timedelta(seconds=self._settings.interval_s)
        if self._last_kind in (DecisionKind.FAILED, None):
            return elapsed >= timedelta(seconds=self._settings.interval_s)
        return elapsed >= timedelta(seconds=self._settings.repeat_s)

    def report(self, decision: Decision) -> bool:
        """The gate's decision on our intent; ``True`` if it was ours and pending."""
        if decision.intent.asset_id != BATTERY_ASSET_ID or decision.intent.id != self._pending:
            return False
        self._pending = None
        self._last_kind = decision.kind
        if self.view is not None:
            self.view = BatteryView(
                self.view.wanted,
                self.view.reasons,
                intent_id=decision.intent.id,
                last_decision=decision.kind,
            )
        return True

    def reset_pending(self) -> None:
        """A submission was lost (transport error): the next tick may submit again."""
        self._pending = None


__all__ = [
    "BatteryControlInputs",
    "BatteryControlSettings",
    "BatteryIntents",
    "BatteryView",
]
