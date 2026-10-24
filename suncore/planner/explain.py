# SPDX-License-Identifier: Apache-2.0
"""Why the plan does what it does, per interval (Phase 4 WP8): structured reasons only.

The reasons come from the *binding* parts of the plan and from price comparisons, never from
free text (keys in :mod:`suncore.planner.reasons`, all in the en/de catalogues):

* a target at this slot: the risk model's own reason (``planner.battery.morning_buffer``,
  ``planner.battery.evening_target``) or ``planner.battery.user_target``; a shortfall adds
  ``planner.battery.target_shortfall`` (the soft target could not be met);
* grid charging: ``planner.battery.grid_charge`` with the SoC from/to, the price paid and the
  best known later price and its time (the spread the rule compared);
* a held battery while the house imports (``discharge_lock``): ``hold_for_evening`` when a
  later evening/user target is binding, else ``hold_for_price`` with the later, higher import
  price the energy is kept for;
* devices: ``planner.device.surplus`` (planned on PV surplus) or ``planner.device.throttled``
  (a binding battery target takes the surplus first);
* export: ``planner.export.negative_price_avoided`` (export price <= 0 and surplus kept in
  the battery/devices or curtailed), ``planner.export.limit``, ``planner.export.surplus``;
* otherwise the flow that dominates (``charge_surplus``, ``discharge_house``, ``at_reserve``,
  ``full``, ``planner.grid.import``) or ``planner.interval.balanced``.

Times in reason arguments are local ``HH:MM`` of the inputs' zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Final

from suncore.core.decisions import Reason
from suncore.planner import reasons as rk
from suncore.planner.inputs import PlannerInputs, SocGoal
from suncore.planner.model import BUFFER_KINDS, Economics, Schedule, goal_shortfall_wh

EPS_W: Final = 1.0
"""Flows below this (W) count as zero in the explanation and the bands."""
TIGHT_PCT: Final = 1.0
"""A target counts as binding when the planned SoC is within this many percent of it."""
TIGHT_HOLD_PCT: Final = 3.0
"""Hysteresis (control-safety review m2): a slot the previous plan throttled devices in stays
tight while the target is within this many percent (and the device budget within
:data:`TIGHT_HOLD_WH`), so consecutive plans do not flip a band allowed/throttled."""
TIGHT_HOLD_WH: Final = 100.0
HOLD_KINDS: Final = ("evening_target", "user_target")


def _hhmm(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%H:%M")


def _pct(inputs: PlannerInputs, wh: float) -> float:
    battery = inputs.battery
    return 0.0 if battery is None else round(battery.model.wh_to_soc(wh), 1)


@dataclass(frozen=True, slots=True)
class SlotFacts:
    """Per slot: what the bands and the reasons both need."""

    discharge_lock: bool
    tight: bool
    """A pending battery target binds: surplus goes to the battery before devices."""
    hold_goal: SocGoal | None
    """The binding evening/user target a lock holds energy for (``None`` = price)."""
    priority_until_pct: float | None


def _binding(
    inputs: PlannerInputs, econ: Economics, schedule: Schedule, margin_pct: float
) -> list[bool]:
    battery = inputs.battery
    out = []
    for goal, slot in zip(inputs.goals, econ.goal_slots, strict=True):
        if battery is None or slot is None:
            out.append(False)
            continue
        planned = battery.model.wh_to_soc(schedule.soc_wh[slot])
        out.append(planned <= goal.soc_pct + margin_pct)
    return out


def previously_throttled(inputs: PlannerInputs) -> set[datetime]:
    """Interval starts in which the previous plan blocked or throttled a device."""
    previous = inputs.previous
    if previous is None:
        return set()
    return {
        interval.start
        for interval in previous.intervals
        if any(
            not band.allowed or band.max_power_w is not None for band in interval.devices.values()
        )
    }


def slot_facts(inputs: PlannerInputs, econ: Economics, schedule: Schedule) -> list[SlotFacts]:
    """Discharge locks, binding targets and the battery-first rule per slot."""
    battery = inputs.battery
    binding = _binding(inputs, econ, schedule, TIGHT_PCT)
    holding = _binding(inputs, econ, schedule, TIGHT_HOLD_PCT)
    throttled_before = previously_throttled(inputs)
    out: list[SlotFacts] = []
    # Battery first only until the buffer's goal slot (its day), not over the horizon (m3).
    buffer_goal = next(
        (
            (goal.soc_pct, slot)
            for goal, slot in zip(inputs.goals, econ.goal_slots, strict=True)
            if slot is not None and goal.kind in BUFFER_KINDS
        ),
        None,
    )
    buffer_open = buffer_goal is not None and battery is not None
    used = {
        slot: sum(schedule.device_total_w(t) for t in range(slot + 1)) * inputs.dt_h
        for slot, _ in econ.device_budget
    }
    for t in range(inputs.n):
        prev = battery.soc_wh if battery is not None and t == 0 else schedule.soc_wh[t - 1]
        sticky = inputs.starts[t] in throttled_before
        pending = [
            (goal, b)
            for goal, slot, b in zip(
                inputs.goals, econ.goal_slots, holding if sticky else binding, strict=True
            )
            if slot is not None and slot >= t
        ]
        slack_wh = TIGHT_HOLD_WH if sticky else 1.0
        budget_bound = any(
            slot >= t and used[slot] >= budget - slack_wh for slot, budget in econ.device_budget
        )
        tight = (any(b for _, b in pending) or budget_bound) and schedule.pv_charge_w[t] > EPS_W
        deficit = econ.deficit_w[t]
        lock = False
        hold: SocGoal | None = None
        if battery is not None and deficit > EPS_W:
            floor = min(battery.reserve_wh, battery.soc_wh)
            room_w = (prev - floor) * battery.model.discharge_efficiency / inputs.dt_h
            possible = min(deficit, battery.max_discharge_w, room_w)
            lock = schedule.battery_load_w[t] < possible - EPS_W
            if lock:
                hold = next((g for g, b in pending if b and g.kind in HOLD_KINDS), None)
        priority: float | None = None
        if buffer_open and battery is not None and buffer_goal is not None:
            if t > buffer_goal[1]:
                buffer_open = False
            else:
                priority = buffer_goal[0]
                if battery.model.wh_to_soc(schedule.soc_wh[t]) >= buffer_goal[0] - 0.05:
                    buffer_open = False
        out.append(SlotFacts(lock, tight, hold, priority))
    return out


def _best_later(inputs: PlannerInputs, t: int) -> tuple[float, int] | None:
    best: tuple[float, int] | None = None
    for tau in range(t + 1, inputs.n):
        price = inputs.import_known[tau]
        if price is not None and (best is None or price > best[0]):
            best = (price, tau)
    return best


def _later_use(inputs: PlannerInputs, schedule: Schedule, t: int) -> tuple[float, int] | None:
    """The most expensive later slot in which the battery serves the house."""
    best: tuple[float, int] | None = None
    for tau in range(t + 1, inputs.n):
        price = inputs.import_known[tau]
        # Only real prices are cited, never a median stand-in (review m5).
        used = schedule.battery_load_w[tau] > EPS_W and price is not None
        if used and price is not None and (best is None or price > best[0]):
            best = (price, tau)
    return best


def interval_reasons(  # noqa: PLR0912, PLR0915 - one branch per rule of the module doc
    inputs: PlannerInputs,
    econ: Economics,
    schedule: Schedule,
    facts: list[SlotFacts],
    t: int,
) -> tuple[Reason, ...]:
    """The reasons of slot ``t`` (at least one)."""
    out: list[Reason] = []
    tz = inputs.tz
    battery = inputs.battery
    fact = facts[t]
    shortfalls = goal_shortfall_wh(inputs, econ, schedule)
    for goal, slot, short in zip(inputs.goals, econ.goal_slots, shortfalls, strict=True):
        if slot != t:
            continue
        out.append(goal.reason)
        if short > 1.0:
            out.append(
                Reason(
                    key=rk.TARGET_SHORTFALL,
                    args={
                        "goal": goal.kind,
                        "target_pct": round(goal.soc_pct, 1),
                        "planned_pct": _pct(inputs, schedule.soc_wh[t]),
                        "time": _hhmm(goal.at, tz),
                    },
                )
            )
    prev = battery.soc_wh if battery is not None and t == 0 else schedule.soc_wh[max(0, t - 1)]
    if schedule.grid_charge_w[t] > EPS_W:
        later = _best_later(inputs, t)
        price = inputs.import_price[t]
        out.append(
            Reason(
                key=rk.GRID_CHARGE,
                args={
                    "from": _pct(inputs, prev),
                    "to": _pct(inputs, schedule.soc_wh[t]),
                    "price": round(price, 4),
                    "later_price": round(price if later is None else later[0], 4),
                    "later_time": _hhmm(inputs.starts[t if later is None else later[1]], tz),
                },
            )
        )
    if fact.discharge_lock:
        if fact.hold_goal is not None:
            out.append(
                Reason(
                    key=rk.HOLD_FOR_EVENING,
                    args={
                        "soc_pct": round(fact.hold_goal.soc_pct, 1),
                        "time": _hhmm(fact.hold_goal.at, tz),
                    },
                )
            )
        else:
            use = _later_use(inputs, schedule, t)
            price = inputs.import_price[t]
            out.append(
                Reason(
                    key=rk.HOLD_FOR_PRICE,
                    args={
                        "price": round(price, 4),
                        "later_price": round(price if use is None else use[0], 4),
                        "later_time": _hhmm(inputs.starts[t if use is None else use[1]], tz),
                    },
                )
            )
    elif schedule.battery_load_w[t] > EPS_W:
        out.append(
            Reason(
                key=rk.DISCHARGE_HOUSE,
                args={
                    "power_w": round(schedule.battery_load_w[t]),
                    "soc_pct": _pct(inputs, schedule.soc_wh[t]),
                },
            )
        )
    elif battery is not None and econ.deficit_w[t] > EPS_W:
        out.append(Reason(key=rk.AT_RESERVE, args={"soc_pct": _pct(inputs, prev)}))
    if schedule.pv_charge_w[t] > EPS_W:
        out.append(
            Reason(
                key=rk.CHARGE_SURPLUS,
                args={
                    "power_w": round(schedule.pv_charge_w[t]),
                    "soc_pct": _pct(inputs, schedule.soc_wh[t]),
                },
            )
        )
    for j, device in enumerate(inputs.devices):
        power = schedule.devices_w[j][t]
        if power > EPS_W:
            out.append(
                Reason(
                    key=rk.DEVICE_SURPLUS,
                    args={"device": device.device_id, "power_w": round(power)},
                )
            )
        elif fact.tight and device.allowed[t]:
            upcoming = next(
                (
                    g
                    for g, s in zip(inputs.goals, econ.goal_slots, strict=True)
                    if s is not None and s >= t
                ),
                None,
            )
            if upcoming is not None:
                out.append(
                    Reason(
                        key=rk.DEVICE_THROTTLED,
                        args={
                            "device": device.device_id,
                            "soc_pct": round(upcoming.soc_pct, 1),
                            "time": _hhmm(upcoming.at, tz),
                        },
                    )
                )
    surplus = econ.surplus_w[t]
    export_price = inputs.export_price[t]
    if surplus > EPS_W:
        avoided = schedule.export_w[t] <= EPS_W or schedule.export_w[t] <= 0.5 * surplus
        if export_price <= 0 and avoided:
            out.append(
                Reason(
                    key=rk.EXPORT_NEGATIVE_PRICE_AVOIDED,
                    args={
                        "price": round(export_price, 4),
                        "import_price": round(inputs.import_price[t], 4),
                    },
                )
            )
        elif (
            inputs.export_limit_w is not None
            and schedule.curtail_w[t] > EPS_W
            and schedule.export_w[t] >= inputs.export_limit_w - EPS_W
        ):
            out.append(Reason(key=rk.EXPORT_LIMIT, args={"limit_w": round(inputs.export_limit_w)}))
        elif schedule.export_w[t] > EPS_W:
            if battery is not None and schedule.soc_wh[t] >= battery.max_wh - 1.0:
                out.append(
                    Reason(key=rk.BATTERY_FULL, args={"soc_pct": _pct(inputs, schedule.soc_wh[t])})
                )
            out.append(
                Reason(
                    key=rk.EXPORT_SURPLUS,
                    args={"power_w": round(schedule.export_w[t]), "price": round(export_price, 4)},
                )
            )
    if not out and schedule.grid_load_w[t] > EPS_W:
        out.append(
            Reason(
                key=rk.GRID_IMPORT,
                args={
                    "power_w": round(schedule.grid_load_w[t]),
                    "price": round(inputs.import_price[t], 4),
                },
            )
        )
    if not out:
        out.append(Reason(key=rk.BALANCED))
    return tuple(out)


__all__ = ["SlotFacts", "interval_reasons", "slot_facts"]
