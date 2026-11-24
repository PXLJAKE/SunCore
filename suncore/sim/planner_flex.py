# SPDX-License-Identifier: Apache-2.0
"""Planner scenarios with flexible loads and grid limits (Phase 6 WP8, plan §4).

Built on :mod:`suncore.sim.planner_scenarios` (same PV, load and price shapes). Devices are
reasoned examples, not vendor data: an 11 kW wallbox (16 A, three phases, 6 A floor) with a
60 kWh vehicle charging at 90 % efficiency; an air/water heat pump of 3 kW electric with the
generic COP assumption, as a house store (10 kWh/K, 180 W/K, comfort 20-23 °C) in winter or a
hot-water tank (0.35 kWh/K, 2 W/K, 45-55 °C) in summer; a washing machine (2 h) and a
dishwasher (3 h) with 15-minute profiles.

Scenarios (:data:`FLEX_SCENARIOS`):

* ``full_house`` - the reference house plus one EV (target 80 % by 07:00 tomorrow), the
  hot-water heat pump and both programs (the benchmark of plan §1.7);
* ``ev_arrives_late`` - BRIEF §4.14 "Auto kommt spät": the car arrives at 23:00 with 30 %,
  80 % by 07:00, cheap night prices 01:00-05:00;
* ``ev_pv_only`` - mode ``pv`` on a sunny day: surplus only, never grid;
* ``program_deadline`` - washing machine armed at 06:00, finished by 18:00, sunny day;
* ``program_deadline_infeasible`` - armed at 17:30 with a 2 h program due at 18:00;
* ``heat_pump_preheat`` / ``heat_pump_preheat_grid`` - winter day, evening price peak,
  ``grid_preheat`` off / on;
* ``peak_shaving`` - winter night with an EV target and a capacity price;
* ``para14a_dimming`` - §14a active with 4.2 kW, EV ``now`` and heat pump flagged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

from suncore.core.devices import (
    ChargerControl,
    ComfortBand,
    DeviceConfig,
    DeviceKind,
    EvSpec,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    ProgramSpec,
    SgContacts,
    SgReadyControl,
    ThermalSpec,
)
from suncore.core.flex import EvMode, EvState, EvTarget, ProgramJob
from suncore.core.grid import Para14aBudget, Para14aStatus, PeakShaving
from suncore.core.series import PLANNING_STEP, SeriesUnit, TimeSeries, planning_grid
from suncore.core.site import EntityRef
from suncore.core.thermal import LearnedThermal, ThermalModel
from suncore.planner.flex import EvLive, FlexRequest, GridRequest
from suncore.planner.inputs import PLANNER_HORIZON, PlannerInputs
from suncore.sim.planner_scenarios import (
    SUMMER,
    TZ,
    WINTER,
    Situation,
    reference_situation,
    situation_inputs,
    tibber_price,
)

FITTED: Final = datetime(2026, 1, 1, tzinfo=UTC)
WASHER_PROFILE_WH: Final = (500.0, 120.0, 60.0, 60.0, 60.0, 60.0, 250.0, 120.0)
DISHWASHER_PROFILE_WH: Final = (
    450.0,
    30.0,
    20.0,
    20.0,
    20.0,
    20.0,
    20.0,
    20.0,
    400.0,
    30.0,
    20.0,
    10.0,
)


def wallbox(device_id: str = "wallbox", *, para14a: bool = False) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="generic.wallbox",
        name=device_id,
        kind=DeviceKind.WALLBOX,
        entities={
            "current": EntityRef(entity_id=f"number.{device_id}_current"),
            "soc": EntityRef(entity_id=f"sensor.{device_id}_car_soc"),
        },
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040.0),
        control=ChargerControl(kind="charger", role="current", max_current_a=16.0),
        ev=EvSpec(capacity_wh=60_000.0, vehicle_soc_role="soc"),
        para14a=para14a,
    )


def heat_pump(
    device_id: str = "heat_pump",
    *,
    winter: bool = True,
    grid_preheat: bool = False,
    para14a: bool = False,
) -> DeviceConfig:
    model = (
        ThermalModel(capacity_wh_per_k=10_000.0, loss_w_per_k=180.0)
        if winter
        else ThermalModel(capacity_wh_per_k=350.0, loss_w_per_k=2.0)
    )
    comfort = ComfortBand(min_c=20.0, max_c=23.0) if winter else ComfortBand(min_c=45.0, max_c=55.0)
    return DeviceConfig(
        id=device_id,
        template_id="generic.heat_pump_sg_ready",
        name=device_id,
        kind=DeviceKind.HEAT_PUMP,
        entities={
            "a": EntityRef(entity_id=f"switch.{device_id}_a"),
            "b": EntityRef(entity_id=f"switch.{device_id}_b"),
        },
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=3_000.0),
        control=SgReadyControl(
            kind="sg_ready",
            contact_a="a",
            contact_b="b",
            normal=SgContacts(a=False, b=False),
            intensified=SgContacts(a=True, b=False),
        ),
        comfort=comfort,
        thermal=ThermalSpec(
            learned=LearnedThermal(model=model, nrmse=0.1, history_days=30, fitted_at=FITTED),
            grid_preheat=grid_preheat,
        ),
        para14a=para14a,
    )


def program(device_id: str, profile_wh: Sequence[float], method: str = "plug") -> DeviceConfig:
    runtime = len(profile_wh) * 15
    return DeviceConfig(
        id=device_id,
        template_id="generic.program_plug",
        name=device_id,
        kind=DeviceKind.PROGRAM,
        entities={"switch": EntityRef(entity_id=f"switch.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=2_000.0),
        control=OnOffControl(kind="on_off", role="switch"),
        program=ProgramSpec(
            runtime_min=runtime,
            profile_wh=tuple(profile_wh),
            start_method="plug" if method == "plug" else "notify",
            resume_confirmed=method == "plug",
        ),
    )


def job(device_id: str, armed: datetime, deadline: datetime) -> ProgramJob:
    return ProgramJob(id=f"{device_id}-1", device_id=device_id, deadline=deadline, armed_at=armed)


def outdoor(now: datetime, *, winter: bool) -> TimeSeries:
    """Outdoor temperature (°C): a daily sine, coldest at 06:00."""
    grid = planning_grid(now, PLANNER_HORIZON + timedelta(hours=1), PLANNING_STEP)
    mean, swing = (-2.0, 4.0) if winter else (18.0, 6.0)
    values = []
    for at in grid.starts():
        local = at.astimezone(TZ)
        hour = local.hour + local.minute / 60
        values.append(round(mean - swing * math.cos(2 * math.pi * (hour - 6.0) / 24), 2))
    return TimeSeries(
        start=grid.start, step=grid.step, values=tuple(values), unit=SeriesUnit.CELSIUS
    )


def _at(day: datetime, hour: int, minute: int = 0, days: int = 0) -> datetime:
    base = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return base + timedelta(days=days)


def _evening_peak(at: datetime) -> float:
    local = at.astimezone(TZ)
    hour = local.hour + local.minute / 60
    return 0.55 if 17 <= hour < 20 else 0.25


def _night_cheap(at: datetime) -> float:
    local = at.astimezone(TZ)
    hour = local.hour + local.minute / 60
    return 0.12 if 1 <= hour < 5 else 0.32


def full_house_situation(now: datetime | None = None) -> Situation:
    """The reference house plus one EV, one heat pump and two programs (plan §1.7)."""
    base = reference_situation(now)
    start = base.now
    day = datetime(start.year, start.month, start.day, tzinfo=TZ)
    devices = (
        *base.devices,
        wallbox(),
        heat_pump(winter=False),
        program("washer", WASHER_PROFILE_WH),
        program("dishwasher", DISHWASHER_PROFILE_WH, "notify"),
    )
    flex = FlexRequest(
        ev_states={
            "wallbox": EvState(
                device_id="wallbox",
                mode=EvMode.TARGET,
                target=EvTarget(soc_pct=80.0, by=_at(day, 7, days=1)),
                updated_at=start,
            )
        },
        ev_live={"wallbox": EvLive(soc_pct=35.0, connected=True)},
        jobs=(
            job("washer", start, _at(day, 18)),
            job("dishwasher", start, _at(day, 7, days=1)),
        ),
        temperatures_c={"heat_pump": 47.0},
        outdoor_c=outdoor(start, winter=False),
    )
    return replace(
        base,
        devices=devices,
        flex=flex,
        import_limit_w=None,
        grid_limits=GridRequest(fuse_limit_w=24_000.0, static_export_limit_w=13_500.0),
    )


def flex_situation(name: str) -> Situation:  # noqa: PLR0911 - one scenario per branch
    """The named flexible-load scenario (module doc)."""
    if name == "full_house":
        return full_house_situation()
    if name == "ev_arrives_late":
        now = _at(datetime(WINTER.year, WINTER.month, WINTER.day, tzinfo=TZ), 18)
        day = datetime(WINTER.year, WINTER.month, WINTER.day, tzinfo=TZ)
        return Situation(
            now=now,
            summer=False,
            day_factors=(0.2, 0.2, 0.2),
            soc_pct=40.0,
            import_price=_night_cheap,
            devices=(wallbox(),),
            flex=FlexRequest(
                ev_states={
                    "wallbox": EvState(
                        device_id="wallbox",
                        mode=EvMode.TARGET,
                        target=EvTarget(soc_pct=80.0, by=_at(day, 7, days=1)),
                        updated_at=now,
                    )
                },
                ev_live={"wallbox": EvLive(soc_pct=30.0, connected=False, arrival=_at(day, 23))},
            ),
            grid_limits=GridRequest(fuse_limit_w=17_000.0),
        )
    if name == "ev_pv_only":
        now = _at(datetime(SUMMER.year, SUMMER.month, SUMMER.day, tzinfo=TZ), 6)
        return Situation(
            now=now,
            kwp=12.0,
            devices=(wallbox(),),
            flex=FlexRequest(ev_live={"wallbox": EvLive(soc_pct=20.0, connected=True)}),
        )
    if name in ("program_deadline", "program_deadline_infeasible"):
        day = datetime(SUMMER.year, SUMMER.month, SUMMER.day, tzinfo=TZ)
        now = _at(day, 6) if name == "program_deadline" else _at(day, 17, 30)
        return Situation(
            now=now,
            devices=(program("washer", WASHER_PROFILE_WH),),
            flex=FlexRequest(jobs=(job("washer", now, _at(day, 18)),)),
        )
    if name in ("heat_pump_preheat", "heat_pump_preheat_grid"):
        day = datetime(WINTER.year, WINTER.month, WINTER.day, tzinfo=TZ)
        now = _at(day, 9)
        return Situation(
            now=now,
            summer=False,
            day_factors=(0.6, 0.3, 0.3),
            soc_pct=30.0,
            import_price=_evening_peak,
            devices=(heat_pump(grid_preheat=name == "heat_pump_preheat_grid"),),
            flex=FlexRequest(
                temperatures_c={"heat_pump": 20.5}, outdoor_c=outdoor(now, winter=True)
            ),
        )
    if name == "peak_shaving":
        day = datetime(WINTER.year, WINTER.month, WINTER.day, tzinfo=TZ)
        now = _at(day, 20)
        return Situation(
            now=now,
            summer=False,
            day_factors=(0.2, 0.2, 0.2),
            soc_pct=50.0,
            import_price=lambda _at: 0.30,
            devices=(wallbox(),),
            flex=FlexRequest(
                ev_states={
                    "wallbox": EvState(
                        device_id="wallbox",
                        mode=EvMode.TARGET,
                        target=EvTarget(energy_wh=20_000.0, by=_at(day, 7, days=1)),
                        updated_at=now,
                    )
                },
                ev_live={"wallbox": EvLive(soc_pct=None, connected=True)},
            ),
            grid_limits=GridRequest(
                fuse_limit_w=17_000.0,
                peak=PeakShaving(
                    enabled=True, peak_floor_w=2_500.0, capacity_price_per_kw_month=8.0
                ),
                month_peak_w=6_000.0,
            ),
        )
    if name == "para14a_dimming":
        day = datetime(WINTER.year, WINTER.month, WINTER.day, tzinfo=TZ)
        now = _at(day, 18)
        return Situation(
            now=now,
            summer=False,
            day_factors=(0.2, 0.2, 0.2),
            soc_pct=50.0,
            import_price=tibber_price,
            devices=(wallbox(para14a=True), heat_pump(para14a=True)),
            flex=FlexRequest(
                ev_states={
                    "wallbox": EvState(device_id="wallbox", mode=EvMode.NOW, updated_at=now)
                },
                ev_live={"wallbox": EvLive(soc_pct=50.0, connected=True)},
                temperatures_c={"heat_pump": 20.2},
                outdoor_c=outdoor(now, winter=True),
            ),
            grid_limits=GridRequest(
                fuse_limit_w=17_000.0,
                para14a=Para14aBudget(Para14aStatus.ACTIVE, 4_200.0),
                para14a_hold=timedelta(hours=1),
            ),
        )
    msg = f"unknown flex scenario {name!r}"
    raise KeyError(msg)


FLEX_SCENARIOS: Final = (
    "full_house",
    "ev_arrives_late",
    "ev_pv_only",
    "program_deadline",
    "program_deadline_infeasible",
    "heat_pump_preheat",
    "heat_pump_preheat_grid",
    "peak_shaving",
    "para14a_dimming",
)


def flex_inputs(name: str) -> PlannerInputs:
    return situation_inputs(flex_situation(name))


__all__ = [
    "FLEX_SCENARIOS",
    "flex_inputs",
    "flex_situation",
    "full_house_situation",
    "heat_pump",
    "job",
    "outdoor",
    "program",
    "wallbox",
]
