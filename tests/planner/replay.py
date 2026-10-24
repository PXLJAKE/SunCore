# SPDX-License-Identifier: Apache-2.0
"""A rolling replay of plans against an actual day (test helper, Phase 4 WP8).

Every ``replan`` the situation is planned again from the live SoC (the forecast itself does not
learn: it stays as optimistic as it was), then the next slots run on the *actual* PV with the
plan's bands interpreted the way the controller and the inverter will (ADR-0013 limits):

* surplus: the battery takes first while the live SoC is below ``priority_until_soc_pct`` or
  ``charge_from_surplus`` is set (devices get what the battery cannot take); otherwise devices
  first. A device runs only where its band is ``allowed`` and ``prefer_on``, capped at
  ``max_power_w``.
* deficit: the battery covers it down to the reserve unless the band has ``discharge_lock``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from suncore.core.control import BandPreference
from suncore.planner.run import run
from suncore.sim.planner_scenarios import Situation, forecasts, situation_inputs

STEP_H = 0.25


@dataclass(frozen=True, slots=True)
class Replay:
    soc_pct: dict[datetime, float]
    device_wh: float
    targets: dict[datetime, float]
    """Evening target (%) per replan time."""


def replay(
    situation: Situation,
    *,
    hours: int,
    actual_share: float,
    replan_slots: int = 4,
) -> Replay:
    """Plan and run ``hours`` hours; actual PV = ``actual_share`` x the forecast P50."""
    starts, pv, _load = forecasts(situation)
    actual = {
        s: v * actual_share for s, v in zip(starts, pv.p50.values, strict=True) if v is not None
    }
    soc = situation.soc_pct
    now = situation.now
    end = now + timedelta(hours=hours)
    socs: dict[datetime, float] = {}
    targets: dict[datetime, float] = {}
    device_wh = 0.0
    while now < end:
        inputs = situation_inputs(replace(situation, now=now, soc_pct=soc))
        battery = inputs.battery
        assert battery is not None
        model = battery.model
        targets.update({g.at: g.soc_pct for g in inputs.goals if g.kind == "evening_target"})
        plan = run(inputs).plan
        devices = {d.device_id: d for d in inputs.devices}
        for t in range(min(replan_slots, inputs.n)):
            interval = plan.intervals[t]
            band = interval.battery
            start = inputs.starts[t]
            pv_w = actual.get(start, 0.0)
            load_w = inputs.load_p50[t]
            e = model.soc_to_wh(soc)
            net = pv_w - load_w
            if net >= 0:
                room = max(0.0, battery.max_wh - e) / (STEP_H * model.charge_efficiency)
                battery_cap = min(battery.max_charge_w, room)
                first = (
                    band.priority_until_soc_pct is not None and soc < band.priority_until_soc_pct
                ) or band.charge_from_surplus
                charge = min(net, battery_cap) if first else 0.0
                rest = net - charge
                for device_id, dband in interval.devices.items():
                    if not dband.allowed or dband.preference is not BandPreference.PREFER_ON:
                        continue
                    cap = (
                        devices[device_id].max_w if dband.max_power_w is None else dband.max_power_w
                    )
                    take = min(cap, rest)
                    rest -= take
                    device_wh += take * STEP_H
                if not first:
                    charge = min(rest, battery_cap)
                e += charge * STEP_H * model.charge_efficiency
            elif not band.discharge_lock:
                avail = max(0.0, e - battery.reserve_wh) * model.discharge_efficiency / STEP_H
                out = min(-net, battery.max_discharge_w, avail)
                e -= out * STEP_H / model.discharge_efficiency
            soc = model.wh_to_soc(e)
            socs[start + inputs.step] = soc
        now = inputs.starts[0] + inputs.step * replan_slots
    return Replay(socs, device_wh, targets)


def battery_first_soc(situation: Situation, *, until: datetime, actual_share: float) -> float:
    """SoC (%) at ``until`` if every actual surplus went to the battery and deficits drew on it."""
    starts, pv, load = forecasts(situation)
    inputs = situation_inputs(situation)
    battery = inputs.battery
    assert battery is not None
    model = battery.model
    e = battery.soc_wh
    for start, pv_w, load_w in zip(starts, pv.p50.values, load.p50.values, strict=True):
        if start >= until:
            break
        net = (pv_w or 0.0) * actual_share - (load_w or 0.0)
        if net > 0:
            gain = min(net, battery.max_charge_w) * STEP_H * model.charge_efficiency
            e = min(battery.max_wh, e + gain)
        else:
            loss = min(-net, battery.max_discharge_w) * STEP_H / model.discharge_efficiency
            e = max(min(e, battery.reserve_wh), e - loss)
    return model.wh_to_soc(e)
