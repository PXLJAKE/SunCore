# SPDX-License-Identifier: Apache-2.0
"""Frozen daily summaries and daily forecast accuracy (Phase 5 WP3, ADR-0014).

``DaySummaryJob`` turns the stored quarter-hour measurements and prices of a local day into one
``day_summary`` row with :mod:`suncore.analysis` (flows by the attribution rule, KPIs, grid
cost, both baselines, coverage). The row is *frozen*: it is written once, and rewritten only
when more of the day was measured than before (``slots`` grew: a statistics rebuild, late
data), never because the tariff configuration changed - the cost comes from the prices
``timeseries.db`` stored, and ``tariff_fingerprint`` names the configuration of that moment.

The nightly job (:meth:`DaySummaryJob.nightly`) looks at yesterday and the six days before
(repair); :meth:`DaySummaryJob.catch_up` walks the whole stored history (first start after the
update, after a statistics rebuild or backfill). The numeric work of a chunk of days runs in a
worker thread, the event loop only awaits SQLite.

``record_accuracy_days`` writes ``accuracy_day`` from the forecast statistics of
:mod:`suncore.forecast.accuracy`, one local day at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Final

import structlog

from suncore.analysis.cost import slot_prices
from suncore.analysis.energy import SiteEnergy, build_site_energy
from suncore.analysis.period import Bucket, Granularity, bucket_for
from suncore.analysis.summary import summarize
from suncore.core.battery import BatteryModel
from suncore.core.config import TariffConfig
from suncore.core.measurements import SITE_BATTERY_W, SITE_GRID_W, SITE_HOUSE_W, SITE_PV_W
from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    PriceSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
)
from suncore.core.site import SiteConfig
from suncore.forecast.accuracy import ActualReader, ClipFilter, compute_accuracy, no_clipping
from suncore.storage.summaries import AccuracyDayRow, DaySummaryRow, ForecastKind
from suncore.storage.timeseries import TimeseriesStore

log = structlog.get_logger(__name__)

META_DAY_SUMMARY_SEEDED: Final = "day_summary_seeded_at"
"""Set (ISO time) once the first catch-up over the stored history ran; it dies with the file."""

REPAIR_DAYS: Final = 7
"""The nightly job looks at this many complete days (yesterday and the six before)."""

CHUNK_DAYS: Final = 28
"""Days read and computed together (28 x 96 quarter hours, far below ``MAX_POINTS``)."""

ACCURACY_REPAIR_DAYS: Final = 7


@dataclass(frozen=True, slots=True)
class SummaryContext:
    """What the computation needs from the configuration of the moment."""

    tz: tzinfo
    tz_name: str
    has_battery: bool = False
    battery: BatteryModel | None = None
    """For the equivalent full cycles; ``None`` leaves them out."""
    price_source_id: str | None = None
    """The configured price source; its stored prices are preferred."""
    export_fallback: float | None = None
    """Feed-in price when the stored prices have no export price (a fixed tariff)."""
    tariff_fingerprint: str | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryReport:
    """What a run did."""

    examined: int
    written: int
    unchanged: int
    no_data: int


def battery_model_of(site: SiteConfig | None) -> BatteryModel | None:
    """A model for the cycle count from the site's battery; ``None`` if its capacity is unknown.

    Only capacity and the SoC corridor matter here (``BatteryModel.usable_wh``); the power
    limits default to the whole capacity per hour (1 C) when the site does not give them.
    """
    battery = None if site is None else site.battery
    if battery is None or battery.capacity_wh is None:
        return None
    lo = battery.min_soc_pct if battery.min_soc_pct is not None else 0.0
    hi = battery.max_soc_pct if battery.max_soc_pct is not None else 100.0
    if not lo < hi:
        return None
    capacity = battery.capacity_wh
    return BatteryModel(
        capacity_wh=capacity,
        max_charge_w=battery.max_charge_w or capacity,
        max_discharge_w=battery.max_discharge_w or capacity,
        reserve_pct=lo,
        bms_drift_margin_pct=0.0,
        max_soc_pct=hi,
    )


def tariff_fingerprint(config: TariffConfig | None) -> str | None:
    """Twelve hex characters naming the tariff configuration a cost was computed under."""
    if config is None:
        return None
    text = json.dumps(config.model_dump(mode="json", by_alias=True), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def export_fallback_of(config: TariffConfig | None) -> float | None:
    """The fixed feed-in price, if the tariff has one."""
    if config is not None and config.export.kind == "fixed":
        return config.export.fixed_price
    return None


# --- day summaries -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ChunkData:
    """Everything read for a chunk of days, ready for the worker thread."""

    buckets: Sequence[Bucket]
    pv: TimeSeries
    grid: TimeSeries
    battery: TimeSeries
    house: TimeSeries | None
    prices: Sequence[Sequence[PriceSeries]]
    """Per source (preferred first) the stored segments of the chunk's window."""
    derived: Mapping[date, int]


def _zeros_like(series: TimeSeries) -> TimeSeries:
    return TimeSeries(
        start=series.start,
        step=series.step,
        values=tuple(0.0 for _ in series.values),
        unit=SeriesUnit.W,
    )


def _day_energy(bucket: Bucket, data: _ChunkData) -> SiteEnergy:
    return build_site_energy(
        pv_w=data.pv,
        grid_w=data.grid,
        battery_w=data.battery,
        house_w=data.house,
        grid=Grid(
            start=bucket.start, step=PLANNING_STEP, count=round(bucket.duration / PLANNING_STEP)
        ),
    )


def _overlaps(segment: PriceSeries, bucket: Bucket) -> bool:
    return segment.import_price.end > bucket.start and segment.import_price.start < bucket.end


def _compute_chunk(
    data: _ChunkData, context: SummaryContext, computed_at: datetime
) -> list[DaySummaryRow | None]:
    """The rows of a chunk (``None`` for a day without a single complete slot). Pure."""
    rows: list[DaySummaryRow | None] = []
    for bucket in data.buckets:
        energy = _day_energy(bucket, data)
        if not energy.slots:
            rows.append(None)
            continue
        prices: dict[datetime, tuple[float, float]] = {}
        for segments in data.prices:
            for segment in segments:
                if not _overlaps(segment, bucket):
                    continue
                found = slot_prices(segment, energy, export_fallback=context.export_fallback)
                for slot, price in found.items():
                    prices.setdefault(slot, price)
        summary = summarize(
            bucket, energy.slots, prices, step=PLANNING_STEP, battery=context.battery
        )
        flows, savings = summary.flows, summary.savings
        priced = len(energy.slots) - savings.skipped_slots
        has_cost = priced > 0
        rows.append(
            DaySummaryRow(
                day=bucket.first_day,
                start=bucket.start,
                end=bucket.end,
                tz=context.tz_name,
                slots=len(energy.slots),
                coverage=summary.coverage,
                complete=summary.complete,
                derived_slots=min(data.derived.get(bucket.first_day, 0), len(energy.slots)),
                pv_wh=flows.pv_wh,
                house_wh=flows.house_wh,
                import_wh=flows.import_wh,
                export_wh=flows.export_wh,
                charge_wh=flows.charge_wh,
                discharge_wh=flows.discharge_wh,
                pv_to_house_wh=flows.pv_to_house_wh,
                pv_to_battery_wh=flows.pv_to_battery_wh,
                pv_to_grid_wh=flows.pv_to_grid_wh,
                battery_to_house_wh=flows.battery_to_house_wh,
                battery_to_grid_wh=flows.battery_to_grid_wh,
                grid_to_house_wh=flows.grid_to_house_wh,
                grid_to_battery_wh=flows.grid_to_battery_wh,
                residual_wh=flows.residual_wh,
                autarky=summary.kpis.autarky,
                self_consumption=summary.kpis.self_consumption,
                equivalent_full_cycles=summary.kpis.equivalent_full_cycles,
                priced_slots=priced,
                import_cost=savings.actual.import_cost if has_cost else None,
                export_revenue=savings.actual.export_revenue if has_cost else None,
                cost_net=savings.actual.net if has_cost else None,
                baseline_no_pv_cost=savings.baseline_no_pv.net if has_cost else None,
                baseline_pv_only_cost=savings.baseline_pv_only.net if has_cost else None,
                currency=context.currency if has_cost else None,
                tariff_fingerprint=context.tariff_fingerprint if has_cost else None,
                computed_at=computed_at,
            )
        )
    return rows


def local_day_bucket(day: date, tz: tzinfo) -> Bucket:
    """The bucket of one local day (23 or 25 hours on a DST day)."""
    return bucket_for(datetime.combine(day, time(12), tzinfo=tz), Granularity.DAY, tz)


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


class DaySummaryJob:
    """Computes and stores ``day_summary`` rows."""

    def __init__(self, store: TimeseriesStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    async def nightly(self, context: SummaryContext) -> SummaryReport:
        """Yesterday and the six days before (new rows, or rows whose day got more data).

        The first run on a file that was never summarised walks the whole stored history
        instead (:meth:`catch_up`).
        """
        if await self._store.get_meta(META_DAY_SUMMARY_SEEDED) is None:
            return await self.catch_up(context)
        today = self._clock().astimezone(context.tz).date()
        return await self.compute_range(
            today - timedelta(days=REPAIR_DAYS), today - timedelta(days=1), context
        )

    async def catch_up(self, context: SummaryContext) -> SummaryReport:
        """Every complete local day from the first stored quarter hour to yesterday."""
        today = self._clock().astimezone(context.tz).date()
        first_ts = await self._store.first_quarter(SITE_GRID_W)
        report = SummaryReport(0, 0, 0, 0)
        if first_ts is not None:
            first = first_ts.astimezone(context.tz).date()
            if local_day_bucket(first, context.tz).start < first_ts:
                first += timedelta(days=1)  # a cut-off first day would only be incomplete
            report = await self.compute_range(first, today - timedelta(days=1), context)
        await self._store.set_meta(META_DAY_SUMMARY_SEEDED, self._clock().isoformat())
        return report

    async def compute_range(
        self, first: date, last: date, context: SummaryContext
    ) -> SummaryReport:
        """Compute ``first..last`` (inclusive, local days); write what is new or improved."""
        examined = written = unchanged = no_data = 0
        day = first
        while day <= last:
            stop = min(last, day + timedelta(days=CHUNK_DAYS - 1))
            counts = await self._store.day_summaries.slot_counts(day, stop)
            rows = await self._chunk(day, stop, context)
            for current, row in zip(_days(day, stop), rows, strict=True):
                examined += 1
                if row is None:
                    no_data += 1
                elif current in counts and row.slots <= counts[current]:
                    unchanged += 1
                else:
                    await self._store.day_summaries.put(row)
                    written += 1
            day = stop + timedelta(days=1)
        if examined:
            log.info(
                "day_summary.computed",
                first=first.isoformat(),
                last=last.isoformat(),
                written=written,
                unchanged=unchanged,
                no_data=no_data,
            )
        return SummaryReport(examined, written, unchanged, no_data)

    async def _chunk(
        self, first: date, last: date, context: SummaryContext
    ) -> list[DaySummaryRow | None]:
        buckets = [local_day_bucket(d, context.tz) for d in _days(first, last)]
        window = TimeRange(start=buckets[0].start, end=buckets[-1].end)
        read = self._store.read_series
        pv = await read(SITE_PV_W, window, "15m", SeriesUnit.W)
        grid = await read(SITE_GRID_W, window, "15m", SeriesUnit.W)
        battery = (
            await read(SITE_BATTERY_W, window, "15m", SeriesUnit.W)
            if context.has_battery
            else _zeros_like(pv)
        )
        house_series = await read(SITE_HOUSE_W, window, "15m", SeriesUnit.W)
        house = house_series if any(v is not None for v in house_series.values) else None
        sources = await self._store.price_source_ids(window)
        if context.price_source_id in sources:
            sources.remove(context.price_source_id)
            sources.insert(0, context.price_source_id)
        prices = [await self._store.read_prices(source, window) for source in sources]
        derived = {
            b.first_day: await self._store.derived_quarters(
                SITE_PV_W, TimeRange(start=b.start, end=b.end)
            )
            for b in buckets
        }
        data = _ChunkData(
            buckets=buckets,
            pv=pv,
            grid=grid,
            battery=battery,
            house=house,
            prices=prices,
            derived=derived,
        )
        return await asyncio.to_thread(_compute_chunk, data, context, self._clock())


# --- daily forecast accuracy -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccuracySource:
    source_id: str
    kind: ForecastKind
    """``pv`` or ``load``."""
    actual_reader: ActualReader | None = None


async def record_accuracy_days(
    store: TimeseriesStore,
    sources: Sequence[AccuracySource],
    *,
    now: datetime,
    tz: tzinfo,
    is_clipped: ClipFilter = no_clipping,
    repair_days: int = ACCURACY_REPAIR_DAYS,
) -> int:
    """Write ``accuracy_day`` for yesterday (always, replacing) and the ``repair_days - 1`` days
    before (when the source has no row for the day yet). One failing source is logged and skipped.
    Returns the number of rows written."""
    today = now.astimezone(tz).date()
    yesterday = today - timedelta(days=1)
    first = today - timedelta(days=repair_days)
    written = 0
    for source in sources:
        kind = source.kind
        try:
            have = await store.accuracy_days.days_with_rows(
                source.source_id, kind, first, yesterday
            )
            rows: list[AccuracyDayRow] = []
            for day in _days(first, yesterday):
                if day in have and day != yesterday:
                    continue
                bucket = local_day_bucket(day, tz)
                records = await compute_accuracy(
                    store,
                    source.source_id,
                    bucket.end,
                    start=bucket.start,
                    is_clipped=is_clipped,
                    kind=kind,
                    actual_reader=source.actual_reader,
                )
                rows.extend(
                    AccuracyDayRow(
                        source_id=r.source_id,
                        kind=kind,
                        day=day,
                        horizon=r.horizon.value,
                        mae_w=r.mae_w,
                        bias_w=r.bias_w,
                        n=r.n,
                        computed_at=now.astimezone(UTC),
                    )
                    for r in records
                    if r.n > 0
                )
            written += await store.accuracy_days.put_many(rows)
        except Exception:
            log.exception("accuracy_day.failed", source=source.source_id)
    return written


__all__ = [
    "ACCURACY_REPAIR_DAYS",
    "CHUNK_DAYS",
    "META_DAY_SUMMARY_SEEDED",
    "REPAIR_DAYS",
    "AccuracySource",
    "DaySummaryJob",
    "SummaryContext",
    "SummaryReport",
    "battery_model_of",
    "export_fallback_of",
    "local_day_bucket",
    "record_accuracy_days",
    "tariff_fingerprint",
]
