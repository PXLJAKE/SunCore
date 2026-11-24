# SPDX-License-Identifier: Apache-2.0
"""The queries behind the history endpoints (Phase 5 WP4, ADR-0014 decisions 1, 7 and 8).

Every function takes the :class:`HistorySources` of the moment and returns the wire model; the
router turns it into JSON or CSV, so a chart and its file always agree. Figures of whole days
come from the frozen ``day_summary`` rows (week, month and year sum them); a day that is not
frozen yet (today, yesterday before the nightly job) is computed live. Reads are bounded: a
series answers with at most :data:`SERIES_MAX_POINTS` points, whole-range reads of the quarter-hour
table go window by window (a series is limited to 20 000 points) and the number crunching of long
ranges runs in a worker thread, never on the event loop.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Final

from fastapi import HTTPException

from suncore.analysis.energy import MIN_COVERAGE
from suncore.analysis.flows import Flows
from suncore.analysis.kpi import Kpis, kpis
from suncore.analysis.period import Bucket, Granularity, bucket_for, buckets_between
from suncore.api.history_models import (
    BucketOut,
    CostOut,
    CycleDayOut,
    CyclesOut,
    DeviceDayOut,
    DeviceHistoryItemOut,
    DeviceHistoryOut,
    EnergyOut,
    FlowBreakdownOut,
    ForecastSkillOut,
    HeatmapOut,
    KpiOut,
    SankeyLinkOut,
    SankeyOut,
    SeriesOut,
    SeriesResolution,
    SeriesResponse,
    SkillPointOut,
    SkillSeriesOut,
    SummaryOut,
)
from suncore.api.services import HistorySources
from suncore.core.measurements import (
    SITE_KEYS,
    WEATHER_TEMPERATURE_C,
    device_on_key,
    device_power_key,
    is_percent_key,
    is_state_key,
    is_temperature_key,
)
from suncore.core.series import MAX_POINTS, SeriesUnit, TimeRange, TimeSeries
from suncore.storage.summaries import DaySummaryRow
from suncore.storage.timeseries import MINUTE_KEEP, TimeseriesStore

ERR_HISTORY_UNAVAILABLE: Final = "api.error.history_unavailable"
ERR_HISTORY_RANGE_INVALID: Final = "api.error.history_range_invalid"
ERR_HISTORY_KEY_INVALID: Final = "api.error.history_key_invalid"

SERIES_MAX_POINTS: Final = 2000
SERIES_MAX_KEYS: Final = 8
MAX_SPAN: Final = timedelta(days=400)
"""Longest range any history endpoint reads (the prices are kept 400 days)."""
MINUTE_SPAN: Final = timedelta(days=2)
QUARTER_SPAN: Final = timedelta(days=31)
CHUNK_BUCKETS: Final = 28
"""Local days read together from the quarter-hour table (28 x 96, far below ``MAX_POINTS``)."""
_LIVE_LOOKBACK: Final = 1
"""Days before today that are computed live while their frozen row is missing."""
QUARTER_H: Final = 0.25

_KEY_RE: Final = re.compile(r"^device\.[A-Za-z0-9_-]{1,64}\.(power_w|on|temperature_c)$")
_STEP_S: Final = {"1m": 60, "15m": 900, "1h": 3600}
_ORDER: Final[tuple[SeriesResolution, ...]] = ("1m", "15m", "1h", "1d")


def fail(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def timeseries_of(sources: HistorySources) -> TimeseriesStore:
    if sources.timeseries is None:
        raise fail(503, ERR_HISTORY_UNAVAILABLE)
    return sources.timeseries


def local(moment: datetime, tz: tzinfo) -> datetime:
    """``moment`` as site time; a naive one is taken as site time already."""
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


def today_of(sources: HistorySources) -> date:
    return sources.clock().astimezone(sources.context.tz).date()


def day_bucket_of(period: Granularity, day: date, tz: tzinfo) -> Bucket:
    return bucket_for(datetime.combine(day, time(12), tzinfo=tz), period, tz)


def last_day_of(bucket: Bucket, tz: tzinfo) -> date:
    return bucket.end.astimezone(tz).date() - timedelta(days=1)


def check_day_range(first: date, last: date) -> None:
    if last < first or (last - first) > MAX_SPAN:
        raise fail(422, ERR_HISTORY_RANGE_INVALID)


def unit_of(key: str) -> str:
    if is_state_key(key):
        return "share"
    if is_temperature_key(key):
        return SeriesUnit.CELSIUS.value
    return SeriesUnit.PERCENT.value if is_percent_key(key) else SeriesUnit.W.value


def check_key(key: str) -> str:
    if key not in SITE_KEYS and key != WEATHER_TEMPERATURE_C and _KEY_RE.match(key) is None:
        raise fail(422, ERR_HISTORY_KEY_INVALID)
    return key


# --- summaries -----------------------------------------------------------------------------


def _flows_of(rows: Sequence[DaySummaryRow]) -> Flows:
    return Flows(
        pv_to_house_wh=sum(r.pv_to_house_wh for r in rows),
        pv_to_battery_wh=sum(r.pv_to_battery_wh for r in rows),
        pv_to_grid_wh=sum(r.pv_to_grid_wh for r in rows),
        battery_to_house_wh=sum(r.battery_to_house_wh for r in rows),
        battery_to_grid_wh=sum(r.battery_to_grid_wh for r in rows),
        grid_to_house_wh=sum(r.grid_to_house_wh for r in rows),
        grid_to_battery_wh=sum(r.grid_to_battery_wh for r in rows),
        residual_wh=sum(r.residual_wh for r in rows),
    )


def _energy(flows: Flows) -> EnergyOut:
    return EnergyOut(
        pv_wh=flows.pv_wh,
        house_wh=flows.house_wh,
        import_wh=flows.import_wh,
        export_wh=flows.export_wh,
        charge_wh=flows.charge_wh,
        discharge_wh=flows.discharge_wh,
    )


def _breakdown(flows: Flows) -> FlowBreakdownOut:
    return FlowBreakdownOut(
        pv_to_house_wh=flows.pv_to_house_wh,
        pv_to_battery_wh=flows.pv_to_battery_wh,
        pv_to_grid_wh=flows.pv_to_grid_wh,
        battery_to_house_wh=flows.battery_to_house_wh,
        battery_to_grid_wh=flows.battery_to_grid_wh,
        grid_to_house_wh=flows.grid_to_house_wh,
        grid_to_battery_wh=flows.grid_to_battery_wh,
        residual_wh=flows.residual_wh,
    )


def _kpis(rows: Sequence[DaySummaryRow], flows: Flows) -> KpiOut:
    """Ratios from the summed flows (exact); cycles are the sum of the frozen daily cycles."""
    ratios: Kpis = kpis(flows)
    cycles = [r.equivalent_full_cycles for r in rows if r.equivalent_full_cycles is not None]
    return KpiOut(
        autarky=ratios.autarky,
        self_consumption=ratios.self_consumption,
        equivalent_full_cycles=sum(cycles) if cycles else None,
    )


def _cost(rows: Sequence[DaySummaryRow]) -> CostOut:
    priced = [r for r in rows if r.cost_net is not None]
    currencies = {r.currency for r in priced}
    if not priced or len(currencies) != 1:
        # Mixed currencies cannot be added up; the days are still counted.
        return CostOut(
            currency=None,
            priced_days=len(priced),
            import_cost=None,
            export_revenue=None,
            cost_net=None,
            baseline_no_pv_cost=None,
            baseline_pv_only_cost=None,
            savings_vs_no_pv=None,
            savings_vs_pv_only=None,
            pv_contribution=None,
        )

    def total(pick: Callable[[DaySummaryRow], float | None]) -> float:
        return sum(v for r in priced if (v := pick(r)) is not None)

    net = total(lambda r: r.cost_net)
    no_pv = total(lambda r: r.baseline_no_pv_cost)
    pv_only = total(lambda r: r.baseline_pv_only_cost)
    return CostOut(
        currency=next(iter(currencies)),
        priced_days=len(priced),
        import_cost=total(lambda r: r.import_cost),
        export_revenue=total(lambda r: r.export_revenue),
        cost_net=net,
        baseline_no_pv_cost=no_pv,
        baseline_pv_only_cost=pv_only,
        savings_vs_no_pv=no_pv - net,
        savings_vs_pv_only=pv_only - net,
        pv_contribution=no_pv - pv_only,
    )


@dataclass(frozen=True, slots=True)
class PeriodRows:
    bucket: Bucket
    rows: list[DaySummaryRow]
    live: frozenset[date]
    days_expected: int
    """Local days of the bucket up to and including today."""


async def period_rows(sources: HistorySources, period: Granularity, day: date) -> PeriodRows:
    """The day rows of the period containing ``day``, with the not yet frozen days computed."""
    store = timeseries_of(sources)
    tz = sources.context.tz
    bucket = day_bucket_of(period, day, tz)
    first, last = bucket.first_day, last_day_of(bucket, tz)
    rows = await store.day_summaries.between(first, last)
    have = {r.day for r in rows}
    today = today_of(sources)
    live: set[date] = set()
    if sources.live_day is not None:
        for back in range(_LIVE_LOOKBACK, -1, -1):
            missing = today - timedelta(days=back)
            if first <= missing <= last and missing not in have:
                row = await sources.live_day(missing)
                if row is not None:
                    rows.append(row)
                    live.add(missing)
    rows.sort(key=lambda r: r.day)
    expected = max(0, (min(last, today) - first).days + 1)
    return PeriodRows(bucket, rows, frozenset(live), expected)


def _complete(rows: Sequence[DaySummaryRow], expected: int) -> bool:
    return expected > 0 and len(rows) >= expected and all(r.complete for r in rows)


def _bucket_out(
    bucket: Bucket, rows: Sequence[DaySummaryRow], live: frozenset[date], expected: int, tz: tzinfo
) -> BucketOut:
    flows = _flows_of(rows)
    return BucketOut(
        key=bucket.key,
        start=bucket.start.astimezone(tz),
        end=bucket.end.astimezone(tz),
        days_with_data=len(rows),
        complete=_complete(rows, expected),
        live=any(r.day in live for r in rows),
        energy=_energy(flows),
        kpis=_kpis(rows, flows),
        cost=_cost(rows),
    )


def _buckets(sources: HistorySources, found: PeriodRows, period: Granularity) -> list[BucketOut]:
    """Bars: the days of a week or month, the months of a year, the day itself."""
    tz = sources.context.tz
    today = today_of(sources)
    step = Granularity.MONTH if period is Granularity.YEAR else Granularity.DAY
    now = sources.clock()
    out: list[BucketOut] = []
    for bucket in buckets_between(found.bucket.start, min(found.bucket.end, now), step, tz):
        rows = [r for r in found.rows if bucket.start <= r.start < bucket.end]
        last = last_day_of(bucket, tz)
        expected = max(0, (min(last, today) - bucket.first_day).days + 1)
        out.append(_bucket_out(bucket, rows, found.live, expected, tz))
    return out


async def summary(sources: HistorySources, period: Granularity, day: date) -> SummaryOut:
    found = await period_rows(sources, period, day)
    rows, bucket = found.rows, found.bucket
    flows = _flows_of(rows)
    slots = sum(r.slots for r in rows)
    return SummaryOut(
        period=period,
        key=bucket.key,
        start=bucket.start.astimezone(sources.context.tz),
        end=bucket.end.astimezone(sources.context.tz),
        tz=sources.context.tz_name,
        days=found.days_expected,
        days_with_data=len(rows),
        days_complete=sum(1 for r in rows if r.complete),
        complete=_complete(rows, found.days_expected),
        in_progress=bucket.end > sources.clock(),
        includes_live=bool(found.live),
        coverage=sum(r.coverage for r in rows) / len(rows) if rows else None,
        derived_share=sum(r.derived_slots for r in rows) / slots if slots else None,
        energy=_energy(flows),
        flows=_breakdown(flows),
        kpis=_kpis(rows, flows),
        cost=_cost(rows),
        buckets=_buckets(sources, found, period),
    )


async def sankey(sources: HistorySources, period: Granularity, day: date) -> SankeyOut:
    found = await period_rows(sources, period, day)
    flows = _flows_of(found.rows)
    links = [
        ("pv", "house", flows.pv_to_house_wh),
        ("pv", "battery_charge", flows.pv_to_battery_wh),
        ("pv", "grid_export", flows.pv_to_grid_wh),
        ("battery_discharge", "house", flows.battery_to_house_wh),
        ("battery_discharge", "grid_export", flows.battery_to_grid_wh),
        ("grid_import", "house", flows.grid_to_house_wh),
        ("grid_import", "battery_charge", flows.grid_to_battery_wh),
    ]
    return SankeyOut.model_validate(
        {
            "period": period,
            "key": found.bucket.key,
            "start": found.bucket.start.astimezone(sources.context.tz),
            "end": found.bucket.end.astimezone(sources.context.tz),
            "complete": _complete(found.rows, found.days_expected),
            "nodes": [
                "pv",
                "grid_import",
                "battery_discharge",
                "house",
                "battery_charge",
                "grid_export",
            ],
            "links": [
                SankeyLinkOut.model_validate({"source": s, "target": t, "wh": v})
                for s, t, v in links
            ],
            "residual_wh": flows.residual_wh,
        }
    )


# --- series ------------------------------------------------------------------------------


def _points(res: SeriesResolution, start: datetime, end: datetime, tz: tzinfo) -> int:
    if res == "1d":
        return len(buckets_between(start, end, Granularity.DAY, tz))
    step = _STEP_S[res]
    return math.ceil((end - start).total_seconds() / step) + 1


def pick_resolution(
    start: datetime,
    end: datetime,
    now: datetime,
    requested: SeriesResolution | None,
    tz: tzinfo,
) -> tuple[SeriesResolution, str]:
    """Decision 8: finest allowed by the span (1 min up to 2 days, 15 min up to 31 days, daily
    above) that keeps the series within :data:`SERIES_MAX_POINTS`, and within what is stored
    (1-minute rows are kept 30 days)."""
    span = end - start
    floor_idx = 0 if span <= MINUTE_SPAN else 1 if span <= QUARTER_SPAN else 3
    reason = "span"
    if floor_idx == 0 and start < now - MINUTE_KEEP:
        floor_idx, reason = 1, "retention"
    if requested is not None:
        idx = _ORDER.index(requested)
        if idx < floor_idx or _points(requested, start, end, tz) > SERIES_MAX_POINTS:
            raise fail(422, ERR_HISTORY_RANGE_INVALID)
        return requested, "requested"
    for idx in range(floor_idx, len(_ORDER)):
        res = _ORDER[idx]
        if _points(res, start, end, tz) <= SERIES_MAX_POINTS:
            return res, reason if idx == floor_idx else "points"
    raise fail(422, ERR_HISTORY_RANGE_INVALID)


def _mean(values: Sequence[float | None]) -> float | None:
    found = [v for v in values if v is not None]
    return sum(found) / len(found) if found else None


def _day_windows(buckets: Sequence[Bucket]) -> list[Sequence[Bucket]]:
    return [buckets[i : i + CHUNK_BUCKETS] for i in range(0, len(buckets), CHUNK_BUCKETS)]


async def read_per_bucket(
    store: TimeseriesStore, key: str, buckets: Sequence[Bucket]
) -> list[list[float | None]]:
    """The quarter-hour means of ``key`` per bucket (one list of values per bucket)."""
    out: list[list[float | None]] = []
    for window in _day_windows(buckets):
        series = await store.read_series(
            key, TimeRange(start=window[0].start, end=window[-1].end), "15m"
        )
        values = series.values
        for bucket in window:
            first = max(0, round((bucket.start - series.start) / series.step))
            last = max(first, round((bucket.end - series.start) / series.step))
            out.append(list(values[first:last]))
    return out


def _regroup(
    series: TimeSeries, by: Callable[[datetime], int]
) -> tuple[list[datetime], list[float | None]]:
    groups: dict[int, list[float | None]] = defaultdict(list)
    starts: dict[int, datetime] = {}
    for i, value in enumerate(series.values):
        at = series.slot_start(i)
        index = by(at)
        groups[index].append(value)
        starts.setdefault(index, at)
    keys = sorted(groups)
    return [starts[k] for k in keys], [_mean(groups[k]) for k in keys]


async def series(
    sources: HistorySources,
    keys: Sequence[str],
    start: datetime,
    end: datetime,
    requested: SeriesResolution | None,
) -> SeriesResponse:
    store = timeseries_of(sources)
    tz = sources.context.tz
    if not keys or len(keys) > SERIES_MAX_KEYS or len(set(keys)) != len(keys):
        raise fail(422, ERR_HISTORY_KEY_INVALID)
    for key in keys:
        check_key(key)
    start, end = local(start, tz), local(end, tz)
    if not start < end or end - start > MAX_SPAN:
        raise fail(422, ERR_HISTORY_RANGE_INVALID)
    res, reason = pick_resolution(start, end, sources.clock(), requested, tz)
    window = TimeRange(start=start, end=end)
    times: list[datetime] = []
    columns: list[list[float | None]] = []
    for key in keys:
        if res in ("1m", "15m"):
            read = await store.read_series(key, window, res)
            times = [read.slot_start(i) for i in range(len(read.values))]
            columns.append(list(read.values))
        elif res == "1h":
            read = await store.read_series(key, window, "15m")
            times, values = _regroup(read, lambda at: int(at.timestamp()) // 3600)
            columns.append(values)
        else:
            buckets = buckets_between(start, end, Granularity.DAY, tz)
            per = await read_per_bucket(store, key, buckets)
            times = [b.start for b in buckets]
            columns.append([_mean(v) for v in per])
    return SeriesResponse(
        start=start,
        end=end,
        resolution=res,
        resolution_reason=reason,
        times=[t.astimezone(tz) for t in times],
        series=[
            SeriesOut(key=k, unit=unit_of(k), values=[None if v is None else float(v) for v in c])
            for k, c in zip(keys, columns, strict=True)
        ],
    )


# --- heatmap -----------------------------------------------------------------------------


def _heat(
    chunks: Sequence[tuple[datetime, timedelta, Sequence[float | None]]], tz: tzinfo
) -> tuple[list[list[float | None]], list[list[int]]]:
    sums = [[0.0] * 24 for _ in range(7)]
    counts = [[0] * 24 for _ in range(7)]
    for first, step, values in chunks:
        for i, value in enumerate(values):
            if value is None:
                continue
            at = (first + i * step).astimezone(tz)
            sums[at.weekday()][at.hour] += value
            counts[at.weekday()][at.hour] += 1
    cells = [
        [sums[d][h] / counts[d][h] if counts[d][h] else None for h in range(24)] for d in range(7)
    ]
    return cells, counts


async def heatmap(sources: HistorySources, key: str, start: datetime, end: datetime) -> HeatmapOut:
    """Mean of ``key`` by local weekday and hour of day over the range (quarter-hour means)."""
    store = timeseries_of(sources)
    tz = sources.context.tz
    check_key(key)
    start, end = local(start, tz), local(end, tz)
    if not start < end or end - start > MAX_SPAN:
        raise fail(422, ERR_HISTORY_RANGE_INVALID)
    buckets = buckets_between(start, end, Granularity.DAY, tz)
    chunks: list[tuple[datetime, timedelta, Sequence[float | None]]] = []
    for window in _day_windows(buckets):
        read = await store.read_series(
            key,
            TimeRange(start=max(window[0].start, start), end=min(window[-1].end, end)),
            "15m",
        )
        chunks.append((read.start, read.step, read.values))
    cells, counts = await asyncio.to_thread(_heat, chunks, tz)
    return HeatmapOut(
        key=key,
        unit=unit_of(key),
        start=start,
        end=end,
        tz=sources.context.tz_name,
        values=cells,
        counts=counts,
    )


# --- cycles ------------------------------------------------------------------------------


async def cycles(sources: HistorySources, first: date, last: date) -> CyclesOut:
    store = timeseries_of(sources)
    tz = sources.context.tz
    check_day_range(first, last)
    rows = {r.day: r for r in await store.day_summaries.between(first, last)}
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    buckets = [day_bucket_of(Granularity.DAY, d, tz) for d in days]
    soc = await read_per_bucket(store, "site.soc_pct", buckets)
    out: list[CycleDayOut] = []
    for day, values in zip(days, soc, strict=True):
        row = rows.get(day)
        known = [v for v in values if v is not None]
        out.append(
            CycleDayOut(
                day=day,
                equivalent_full_cycles=None if row is None else row.equivalent_full_cycles,
                charge_wh=0.0 if row is None else row.charge_wh,
                discharge_wh=0.0 if row is None else row.discharge_wh,
                soc_min_pct=min(known) if known else None,
                soc_max_pct=max(known) if known else None,
                complete=row is not None and row.complete,
            )
        )
    cycles_ = [d.equivalent_full_cycles for d in out if d.equivalent_full_cycles is not None]
    return CyclesOut(
        start=first,
        end=last,
        total_equivalent_full_cycles=sum(cycles_) if cycles_ else None,
        days=out,
    )


# --- forecast skill ----------------------------------------------------------------------


async def forecast_skill(
    sources: HistorySources, first: date, last: date, source_id: str | None
) -> ForecastSkillOut:
    store = timeseries_of(sources)
    check_day_range(first, last)
    rows = await store.accuracy_days.between(first, last, source_id=source_id)
    groups: dict[tuple[str, str, str], list[SkillPointOut]] = defaultdict(list)
    for r in rows:
        groups[(r.source_id, r.kind, r.horizon)].append(
            SkillPointOut(day=r.day, mae_w=r.mae_w, bias_w=r.bias_w, n=r.n)
        )
    out: list[SkillSeriesOut] = []
    for (source, kind, horizon), points in sorted(groups.items()):
        n = sum(p.n for p in points)
        out.append(
            SkillSeriesOut.model_validate(
                {
                    "source_id": source,
                    "kind": kind,
                    "horizon": horizon,
                    "mae_w": sum(p.mae_w * p.n for p in points) / n if n else None,
                    "bias_w": sum(p.bias_w * p.n for p in points) / n if n else None,
                    "n": n,
                    "points": points,
                }
            )
        )
    return ForecastSkillOut(start=first, end=last, series=out)


# --- devices -----------------------------------------------------------------------------


def _starts(flat: Sequence[float | None]) -> int:
    count, previous_on = 0, False
    for value in flat:
        if value is None:
            continue
        on = value > 0
        if on and not previous_on:
            count += 1
        previous_on = on
    return count


def _sum_h(values: Sequence[float | None], scale: float) -> float | None:
    found = [v for v in values if v is not None]
    return sum(found) * scale if found else None


async def devices(sources: HistorySources, period: Granularity, day: date) -> DeviceHistoryOut:
    store = timeseries_of(sources)
    tz = sources.context.tz
    bucket = day_bucket_of(period, day, tz)
    today = today_of(sources)
    last = min(last_day_of(bucket, tz), today)
    days = [
        bucket.first_day + timedelta(days=i)
        for i in range(max(0, (last - bucket.first_day).days + 1))
    ]
    buckets = [day_bucket_of(Granularity.DAY, d, tz) for d in days]
    items: list[DeviceHistoryItemOut] = []
    for device_id, name in sorted(sources.context.devices.items()):
        on = await read_per_bucket(store, device_on_key(device_id), buckets) if buckets else []
        power = (
            await read_per_bucket(store, device_power_key(device_id), buckets) if buckets else []
        )
        flat_on = [v for per in on for v in per]
        per_day = [
            DeviceDayOut(day=d, run_hours=_sum_h(o, QUARTER_H), energy_wh=_sum_h(p, QUARTER_H))
            for d, o, p in zip(days, on, power, strict=True)
        ]
        items.append(
            DeviceHistoryItemOut(
                device_id=device_id,
                name=name,
                run_hours=_sum_h(flat_on, QUARTER_H),
                energy_wh=_sum_h([v for per in power for v in per], QUARTER_H),
                starts=_starts(flat_on) if any(v is not None for v in flat_on) else None,
                days=per_day,
            )
        )
    return DeviceHistoryOut(
        period=period,
        key=bucket.key,
        start=bucket.start.astimezone(tz),
        end=bucket.end.astimezone(tz),
        devices=items,
    )


__all__ = [
    "ERR_HISTORY_KEY_INVALID",
    "ERR_HISTORY_RANGE_INVALID",
    "ERR_HISTORY_UNAVAILABLE",
    "MAX_POINTS",
    "MIN_COVERAGE",
    "SERIES_MAX_POINTS",
    "UTC",
    "cycles",
    "devices",
    "forecast_skill",
    "heatmap",
    "sankey",
    "series",
    "summary",
]
