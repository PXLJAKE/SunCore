# SPDX-License-Identifier: Apache-2.0
"""Deterministic demo history (Phase 5 WP3, plan decision 9).

Fills the demo's *throw-away* databases with ``days`` days (default 90) of a made-up house so
the history view, the plan chart and the forecast-skill panel show real charts in the Playwright
run and the screenshots: quarter-hour measurements (``measurement_15m``), prices, PV and load
forecasts (the last 40 days, the forecast retention is 45), the frozen ``day_summary`` rows
(computed with the production job), ``accuracy_day`` and a few plan snapshots and decisions.

Every number comes from ``random.Random`` seeded per day, so two seedings with the same
``now`` and ``days`` are identical. :func:`seed_demo_history` refuses a database inside
``/data`` - it never runs in production and never touches the app's persistent storage.

The made-up house: 6.5 kWp PV, a 10 kWh battery (+/- 4 kW, 10-100 %, charges from the grid at
night before a cloudy day), a pool pump (800 W around noon on sunny days) and a household
base load; prices follow a night/day/evening pattern with a few negative midday hours.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import structlog

from suncore.app.summaries import (
    AccuracySource,
    DaySummaryJob,
    SummaryContext,
    local_day_bucket,
    record_accuracy_days,
)
from suncore.core.battery import BatteryModel
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from suncore.core.measurements import (
    SITE_BATTERY_W,
    SITE_GRID_W,
    SITE_HOUSE_W,
    SITE_PV_W,
    SITE_SOC_PCT,
    device_on_key,
    device_power_key,
)
from suncore.core.series import (
    PLANNING_STEP,
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
)
from suncore.storage.decisions import DecisionLog
from suncore.storage.plans import PlanSnapshotStore
from suncore.storage.timeseries import TimeseriesStore

log = structlog.get_logger(__name__)

DEMO_HISTORY_DAYS: Final = 90
FORECAST_DAYS: Final = 40
DERIVED_AFTER_DAYS: Final = 28
"""Days older than this are stored as *derived* hourly step-holds (as the statistics backfill
would), so the history view shows the marker."""

PRICE_SOURCE: Final = "tibber.price"
PV_SOURCES: Final[tuple[tuple[str, float, float], ...]] = (
    ("solcast.pv", 0.05, 0.12),
    ("open_meteo_solar_forecast.pv", -0.10, 0.20),
)
"""``(source id, bias, noise)`` of the made-up PV forecast sources."""
LOAD_SOURCE: Final = "load_profile"
PUMP_ID: Final = "pool_pump"
TZ_NAME: Final = "Europe/Berlin"

PEAK_PV_W: Final = 6500.0
BATTERY_WH: Final = 10_000.0
BATTERY_W: Final = 4000.0
EXPORT_PRICE: Final = 0.08


class DemoSeedError(RuntimeError):
    """The seeding was asked to touch a production location."""


def _bump(hour: float, centre: float, width: float) -> float:
    return math.exp(-(((hour - centre) / width) ** 2))


def _half_day(day: date) -> float:
    """Half the daylight span in hours (about 4 in winter, 7.5 in midsummer)."""
    season = 0.5 + 0.5 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 80) / 365)
    return 4.0 + 3.5 * season


def _clear_sky_w(local: datetime) -> float:
    half = _half_day(local.date())
    hour = local.hour + local.minute / 60
    offset = abs(hour - 13.0)
    if offset >= half:
        return 0.0
    return float(
        PEAK_PV_W
        * (0.45 + 0.55 * _half_day(local.date()) / 7.5)
        * math.cos(math.pi / 2 * offset / half) ** 1.5
    )


def _cloud(day: date) -> float:
    """The day's true clearness in [0.15, 1] (deterministic per date)."""
    return float(
        0.15 + 0.85 * random.Random(f"suncore-demo-cloud-{day.isoformat()}").random() ** 0.8
    )


def _load_profile_w(local: datetime) -> float:
    hour = local.hour + local.minute / 60
    weekend = 120.0 if local.weekday() >= 5 else 0.0
    return (
        320.0
        + weekend
        + 650.0 * _bump(hour, 7.5, 1.0)
        + 950.0 * _bump(hour, 19.0, 1.6)
        + 250.0 * _bump(hour, 12.5, 1.5)
    )


def _price(local: datetime, day_offset: float, negative: bool) -> float:
    hour = local.hour + local.minute / 60
    if negative and 11 <= hour < 15:
        return -0.02
    if hour < 6:
        base = 0.22
    elif 17 <= hour < 21:
        base = 0.38
    else:
        base = 0.29
    return round(base + day_offset, 4)


@dataclass(slots=True)
class _Day:
    day: date
    starts: list[datetime]
    pv: list[float]
    house: list[float]
    battery: list[float]
    grid: list[float]
    soc: list[float]
    pump_w: list[float]
    pump_on: list[float]
    price: list[float]


def _simulate(
    first: date, days: int, tz: tzinfo, soc: float, rng_label: str
) -> tuple[list[_Day], float]:
    out: list[_Day] = []
    for index in range(days):
        day = first + timedelta(days=index)
        bucket = local_day_bucket(day, tz)
        rng = random.Random(f"{rng_label}-{day.isoformat()}")
        cloud = _cloud(day)
        sunny = cloud > 0.55
        cheap_night_charge = cloud < 0.4
        offset = rng.uniform(-0.03, 0.03)
        negative = index % 23 == 5
        slots = round(bucket.duration / PLANNING_STEP)
        record = _Day(day, [], [], [], [], [], [], [], [], [])
        wobble = 1.0
        for slot in range(slots):
            instant = bucket.start + PLANNING_STEP * slot
            local = instant.astimezone(tz)
            hour = local.hour + local.minute / 60
            wobble = min(1.25, max(0.5, wobble + rng.uniform(-0.08, 0.08)))
            pv = max(0.0, _clear_sky_w(local) * cloud * min(wobble, 1.1))
            pump = 800.0 if sunny and 11.0 <= hour < 14.0 else 0.0
            house = _load_profile_w(local) + rng.uniform(0.0, 90.0) + pump
            surplus = pv - house
            wh_room = (100.0 - soc) / 100.0 * BATTERY_WH
            wh_have = (soc - 10.0) / 100.0 * BATTERY_WH
            if cheap_night_charge and 1.0 <= hour < 5.0 and soc < 60.0:
                battery = min(3000.0, wh_room * 4)
            elif surplus > 0:
                battery = min(surplus, BATTERY_W, wh_room * 4)
            else:
                battery = -min(-surplus, BATTERY_W, wh_have * 4)
            soc += (battery * 0.25 * (0.95 if battery > 0 else 1 / 0.95)) / BATTERY_WH * 100.0
            soc = min(100.0, max(10.0, soc))
            record.starts.append(instant)
            record.pv.append(round(pv, 1))
            record.house.append(round(house, 1))
            record.battery.append(round(battery, 1))
            record.grid.append(round(house + battery - pv, 1))
            record.soc.append(round(soc, 2))
            record.pump_w.append(pump)
            record.pump_on.append(1.0 if pump > 0 else 0.0)
            record.price.append(_price(local, offset, negative))
        out.append(record)
    return out, soc


def _series(
    start: datetime, values: Sequence[float], unit: SeriesUnit = SeriesUnit.W
) -> TimeSeries:
    return TimeSeries(start=start, step=PLANNING_STEP, values=tuple(values), unit=unit)


def _step_hold(values: Sequence[float], starts: Sequence[datetime]) -> list[float]:
    """Hourly mean held over the four quarters of each UTC hour (what the backfill stores)."""
    out: list[float] = []
    for i in range(0, len(values), 4):
        chunk = values[i : i + 4]
        mean = sum(chunk) / len(chunk)
        out.extend([round(mean, 1)] * len(chunk))
    assert len(out) == len(starts)
    return out


async def _store_history(store: TimeseriesStore, days: Sequence[_Day], now: datetime) -> None:
    keys = (
        SITE_PV_W,
        SITE_GRID_W,
        SITE_BATTERY_W,
        SITE_HOUSE_W,
        SITE_SOC_PCT,
        device_power_key(PUMP_ID),
        device_on_key(PUMP_ID),
    )
    derived_until = now - timedelta(days=DERIVED_AFTER_DAYS)
    split = 0
    for index, record in enumerate(days):
        if record.starts[0] < derived_until:
            split = index + 1
    for part, derived in ((days[:split], True), (days[split:], False)):
        if not part:
            continue
        columns: dict[str, list[float]] = {key: [] for key in keys}
        starts: list[datetime] = []
        for record in part:
            starts.extend(record.starts)
            columns[SITE_PV_W].extend(record.pv)
            columns[SITE_GRID_W].extend(record.grid)
            columns[SITE_BATTERY_W].extend(record.battery)
            columns[SITE_HOUSE_W].extend(record.house)
            columns[SITE_SOC_PCT].extend(record.soc)
            columns[device_power_key(PUMP_ID)].extend(record.pump_w)
            columns[device_on_key(PUMP_ID)].extend(record.pump_on)
        for key, values in columns.items():
            if derived and key != device_on_key(PUMP_ID):
                values = _step_hold(values, starts)  # noqa: PLW2901
            await store.store_quarter_hours(
                key, _series(starts[0], values, SeriesUnit.W), derived=derived
            )


async def _store_prices(store: TimeseriesStore, days: Sequence[_Day]) -> None:
    prices = [p for record in days for p in record.price]
    start = days[0].starts[0]
    await store.store_prices(
        PriceSeries(
            source_id=PRICE_SOURCE,
            price_includes="all_in",
            import_price=_series(start, prices, SeriesUnit.PRICE),
            export_price=_series(start, [EXPORT_PRICE] * len(prices), SeriesUnit.PRICE),
        )
    )


async def _store_forecasts(store: TimeseriesStore, first: date, last: date, tz: tzinfo) -> None:
    """One issue per day at local midnight, 48 h of quarter hours, for the last days."""
    day = first
    while day <= last:
        bucket = local_day_bucket(day, tz)
        nxt = local_day_bucket(day + timedelta(days=1), tz)
        instants = [
            bucket.start + PLANNING_STEP * i
            for i in range(round((nxt.end - bucket.start) / PLANNING_STEP))
        ]
        for source_id, bias, noise in PV_SOURCES:
            rng = random.Random(f"suncore-demo-fc-{source_id}-{day.isoformat()}")
            believed = {
                d: min(1.0, max(0.1, _cloud(d) * (1 + bias) + rng.gauss(0.0, noise)))
                for d in (day, day + timedelta(days=1))
            }
            p50 = [
                max(0.0, _clear_sky_w(i.astimezone(tz)) * believed[i.astimezone(tz).date()])
                for i in instants
            ]
            await _store_quantiles(
                store,
                source_id=source_id,
                kind="pv",
                start=bucket.start,
                p50=p50,
                low=0.55,
                high=1.25,
            )
        load = [_load_profile_w(i.astimezone(tz)) + 40.0 for i in instants]
        await _store_quantiles(
            store,
            source_id=LOAD_SOURCE,
            kind="load",
            start=bucket.start,
            p50=load,
            low=0.8,
            high=1.3,
        )
        day += timedelta(days=1)


async def _store_quantiles(
    store: TimeseriesStore,
    *,
    source_id: str,
    kind: str,
    start: datetime,
    p50: Sequence[float],
    low: float,
    high: float,
) -> None:
    series = QuantileSeries(
        p10=_series(start, [round(v * low, 1) for v in p50]),
        p50=_series(start, [round(v, 1) for v in p50]),
        p90=_series(start, [round(v * high, 1) for v in p50]),
    )
    assert kind in ("pv", "load")
    await store.store_forecast(source_id, "pv" if kind == "pv" else "load", start, series)


def _refuse_production(path: Path) -> None:
    resolved = path.resolve()
    if resolved == Path("/data") or Path("/data") in resolved.parents:
        msg = f"demo history is never seeded into {resolved}"
        raise DemoSeedError(msg)


async def seed_demo_history(
    store: TimeseriesStore,
    *,
    now: datetime,
    days: int = DEMO_HISTORY_DAYS,
    tz_name: str = TZ_NAME,
) -> int:
    """Seed ``days`` complete local days before ``now`` into the demo's ``timeseries.db``.

    Returns the number of days written. Raises :class:`DemoSeedError` for a database under
    ``/data``. Idempotent for the same arguments (rows are replaced by the same values).
    """
    _refuse_production(Path(store.database.path))
    if days < 1:
        return 0
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    first = today - timedelta(days=days)
    simulated, _soc = _simulate(first, days, tz, 50.0, "suncore-demo")
    await _store_history(store, simulated, now)
    await _store_prices(store, simulated)
    forecast_first = today - timedelta(days=min(days, FORECAST_DAYS))
    await _store_forecasts(store, forecast_first, today - timedelta(days=1), tz)
    context = SummaryContext(
        tz=tz,
        tz_name=tz_name,
        has_battery=True,
        battery=BatteryModel(
            capacity_wh=BATTERY_WH,
            max_charge_w=BATTERY_W,
            max_discharge_w=BATTERY_W,
            reserve_pct=10.0,
            bms_drift_margin_pct=0.0,
        ),
        price_source_id=PRICE_SOURCE,
        export_fallback=EXPORT_PRICE,
        currency="EUR",
    )
    await DaySummaryJob(store, lambda: now).catch_up(context)
    sources = [AccuracySource(source_id, "pv") for source_id, _b, _n in PV_SOURCES]

    async def house_load(window: TimeRange) -> TimeSeries:
        return await store.read_series(SITE_HOUSE_W, window, "15m", SeriesUnit.W)

    await record_accuracy_days(
        store,
        [*sources, AccuracySource(LOAD_SOURCE, "load", house_load)],
        now=now,
        tz=tz,
        repair_days=min(days, FORECAST_DAYS),
    )
    log.info("demo.history_seeded", days=days)
    return days


async def seed_demo_decisions(
    log_: DecisionLog, *, now: datetime, days: int = 14, tz_name: str = TZ_NAME
) -> int:
    """A deterministic shadow-mode decision log for the pool pump (two switches per sunny day)."""
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    written = 0
    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        if _cloud(day) <= 0.55:
            continue
        for on, hour in ((True, 11), (False, 14)):
            moment = datetime.combine(day, time(hour), tzinfo=tz).astimezone(UTC)
            intent = Intent(
                id=f"demo-{day.isoformat()}-{'on' if on else 'off'}",
                ts=moment,
                asset_id=PUMP_ID,
                action=SwitchAction(on=on),
                reasons=(
                    Reason(key="controller.surplus_on", args={"need_w": 800, "surplus_w": 2600})
                    if on
                    else Reason(key="controller.surplus_gone", args={}),
                ),
                origin=Origin.CONTROLLER,
            )
            await log_.append(
                Decision(
                    intent=intent,
                    kind=DecisionKind.SHADOW,
                    ts=moment,
                    reasons=(Reason(key="safety.shadow_mode"),),
                    mode=OperatingMode.SHADOW,
                )
            )
            written += 1
    return written


async def seed_demo_plans(plans: PlanSnapshotStore, *, now: datetime, count: int = 3) -> int:
    """A few plan snapshots of the planner's reference scenario (one per recent day).

    They come from ``planner_scenarios.reference_situation`` (the maintainer's house): the plan
    chart needs real, solved plans to show. Its two climate devices are replaced by the demo's
    own air conditioner, so the stored plans name a device the demo configures (the panel shows
    its name, not an unknown id). Each run is short (budget 1 s) and runs in a worker thread.
    """
    from suncore.planner.run import plan  # noqa: PLC0415
    from suncore.sim.demo_devices import AC_DEVICE_ID  # noqa: PLC0415
    from suncore.sim.planner_scenarios import (  # noqa: PLC0415
        climate,
        reference_situation,
        situation_inputs,
    )

    written = 0
    base = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    for k in range(count, 0, -1):
        at = base - timedelta(days=k, hours=-1)
        situation = replace(
            reference_situation(now=at), devices=(climate(AC_DEVICE_ID, 1500.0, value=0.12),)
        )
        built = await asyncio.to_thread(plan, situation_inputs(situation), 1.0)
        await plans.save(
            built.model_dump(mode="json"),
            source=built.source,
            schema_version=built.schema_version,
            created_at=at,
        )
        written += 1
    return written


__all__ = [
    "DEMO_HISTORY_DAYS",
    "DemoSeedError",
    "seed_demo_decisions",
    "seed_demo_history",
    "seed_demo_plans",
]
