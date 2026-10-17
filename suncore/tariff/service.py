# SPDX-License-Identifier: Apache-2.0
"""The tariff service: keeps import and export prices fresh and serves them on the 15-minute grid.

One service per configured tariff. It owns a :class:`~suncore.tariff.sources.SegmentSource`
(template adapter, fixed or HT/NT), the price formula, the export side and the region's
negative-price rule. Time is injected (``clock``); nothing here sleeps except :meth:`run`,
whose ``sleep`` is injected too.

**When it refreshes** (:meth:`due`, the one decision function; :meth:`next_refresh` mirrors it):

* ``initial``: never fetched;
* ``changed``: the price entity changed (:meth:`notify_changed`) - at once for attribute
  sources (cheap: reads the entity cache), for action sources only when the current slot has no
  price (an action call costs a request to the cloud service; the sensor changes every slot);
* ``interval``: the last fetch is ``refresh_interval_s`` old (default one hour);
* ``tomorrow``: a dynamic source does not cover the whole next local day yet and the local
  publication time (``tomorrow_after``, default 13:00, ``UNVERIFIED`` for Tibber and Nord
  Pool) has passed: ask again every ``tomorrow_poll_s`` until it does;
* ``retry``: after a failure, with a doubling back-off (``retry_interval_s`` ... one hour).

Action calls are additionally limited by the connection's per-action rate limiter (at least 30 s
apart); a throttled call is not a failure, the last data stays and the service asks again when
the limiter would admit it. A failed or empty fetch never replaces good data; the data of an
earlier fetch beyond the end of the new data is kept (a source that briefly forgets tomorrow's
prices does not blank them).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Final, Literal, Protocol

import structlog

from suncore.core.config import ExportTariff
from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    PriceSeries,
    TimeRange,
    floor_to_step,
)
from suncore.core.tariff import PriceFormula
from suncore.tariff.errors import (
    SourceThrottledError,
    SourceUnavailableError,
    TariffError,
)
from suncore.tariff.parse import ParseReport, clip_series
from suncore.tariff.pricing import export_base_price, planning_prices
from suncore.tariff.report import (
    PriceErrorOut,
    PriceFreshness,
    PriceQuality,
    PriceReport,
    PriceSlot,
    PriceSourceInfo,
)
from suncore.tariff.sources import SegmentSource, SourceData
from suncore.templates.models import NegativePriceRule

log = structlog.get_logger(__name__)

RefreshReason = Literal["initial", "changed", "interval", "tomorrow", "retry", "forced"]

MIN_HORIZON: Final = timedelta(hours=24)
"""The series is at least this long when a window of it is requested (``None`` beyond the data)."""
FETCH_HORIZON: Final = timedelta(hours=72)
MIN_REFRESH_INTERVAL_S: Final = 60.0
"""Below the connection's 30 s action floor with margin; a smaller setting is raised."""
POLL_S: Final = 30.0


class PriceStore(Protocol):
    """``TimeseriesStore.store_prices``; ``None`` = do not persist."""

    async def store_prices(self, prices: PriceSeries) -> int: ...


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """Timing of the refresh logic (all configurable, defaults per the Phase 4 plan)."""

    refresh_interval_s: float = 3600.0
    """Tibber and Nord Pool: once per hour."""
    retry_interval_s: float = 300.0
    """First retry after a failure; doubles up to one hour."""
    tomorrow_after: time = time(13, 0)
    """Local time from which tomorrow's day-ahead prices are expected (``UNVERIFIED``)."""
    tomorrow_poll_s: float = 900.0
    """Spacing of the asks between the publication time and the arrival of tomorrow's prices."""

    def __post_init__(self) -> None:
        if self.refresh_interval_s < MIN_REFRESH_INTERVAL_S:
            object.__setattr__(self, "refresh_interval_s", MIN_REFRESH_INTERVAL_S)
        if self.retry_interval_s < MIN_REFRESH_INTERVAL_S:
            object.__setattr__(self, "retry_interval_s", MIN_REFRESH_INTERVAL_S)
        if self.tomorrow_poll_s < MIN_REFRESH_INTERVAL_S:
            object.__setattr__(self, "tomorrow_poll_s", MIN_REFRESH_INTERVAL_S)


@dataclass(frozen=True, slots=True)
class TariffPlan:
    """The configured tariff, resolved: what the service needs to turn source data into prices."""

    source: SegmentSource
    export_tariff: ExportTariff
    negative_rule: NegativePriceRule
    tz: tzinfo
    currency: str
    formula: PriceFormula | None = None
    total_kwp: float | None = None


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Outcome of one :meth:`TariffService.refresh`."""

    reason: RefreshReason
    ok: bool
    throttled: bool = False
    error: TariffError | None = None
    known_until: datetime | None = None


@dataclass(slots=True)
class _State:
    segments: tuple[PriceSeries, ...] = ()
    fetched_at: datetime | None = None
    report: ParseReport = field(default_factory=ParseReport)
    error: TariffError | None = None
    failures: int = 0
    retry_at: datetime | None = None
    dirty: bool = False
    last_attempt: datetime | None = None
    """Start of the latest fetch, successful or not."""
    carried: int = 0
    """Slots of the current data that an earlier fetch supplied because the latest had none."""

    @property
    def known_end(self) -> datetime | None:
        return max((s.import_price.end for s in self.segments), default=None)


def _midnight(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def _value_at(segments: Sequence[PriceSeries], at: datetime) -> float | None:
    for series in segments:
        ts = series.import_price
        if ts.start <= at < ts.end:
            return ts.values[int((at - ts.start) // ts.step)]
    return None


def _fill_gaps(
    new: Sequence[PriceSeries], old: Sequence[PriceSeries]
) -> tuple[tuple[PriceSeries, ...], int]:
    """``new`` with every unknown slot replaced by the earlier fetch's value (if it had one).

    Returns the segments and how many slots were carried over; a source that suddenly reports
    ``None`` for a price it knew an hour ago does not blank it.
    """
    carried = 0
    out: list[PriceSeries] = []
    for series in new:
        ts = series.import_price
        values = list(ts.values)
        for i, value in enumerate(values):
            if value is None and (kept := _value_at(old, ts.slot_start(i))) is not None:
                values[i] = kept
                carried += 1
        out.append(
            series.model_copy(
                update={"import_price": ts.model_copy(update={"values": tuple(values)})}
            )
        )
    return tuple(out), carried


def _old_tail(
    new: Sequence[PriceSeries], old: Sequence[PriceSeries], floor: datetime
) -> list[PriceSeries]:
    """The old slots that begin at or after the new data's end (and are not past).

    Each old segment is cut at its own slot boundary (rounded up), so the tail never overlaps
    the new data even if the old slots were hourly and the new data ends inside an hour.
    """
    new_end = max(s.import_price.end for s in new)
    limit = max(new_end, floor)
    tail: list[PriceSeries] = []
    for previous in old:
        ts = previous.import_price
        if previous.price_includes != new[0].price_includes or ts.end <= limit:
            continue
        first = max(0, -(-(limit - ts.start) // ts.step))
        piece = clip_series(ts, ts.slot_start(first), ts.end)
        if piece is not None:
            tail.append(
                PriceSeries(
                    source_id=previous.source_id,
                    price_includes=previous.price_includes,
                    import_price=piece,
                )
            )
    return tail


class TariffService:
    """Keeps the prices fresh and answers :meth:`prices` / :meth:`report` from the cache."""

    def __init__(
        self,
        plan: TariffPlan,
        *,
        clock: Callable[[], datetime],
        store: PriceStore | None = None,
        settings: ServiceSettings | None = None,
    ) -> None:
        self._plan = plan
        self._clock = clock
        self._store = store
        self._settings = settings or ServiceSettings()
        self._state = _State()
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._export_base = export_base_price(plan.export_tariff, plan.total_kwp)
        self._last_written: tuple[object, ...] | None = None

    # --- identity ----------------------------------------------------------------------

    @property
    def plan(self) -> TariffPlan:
        return self._plan

    @property
    def watched_entities(self) -> tuple[str, ...]:
        return self._plan.source.watched_entities

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every successful refresh (new prices); returns the remover."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # --- when to refresh ---------------------------------------------------------------

    def watched_entities_isdisjoint(self, entity_ids: Iterable[str]) -> bool:
        """Whether none of ``entity_ids`` is an entity this service watches."""
        return set(self._plan.source.watched_entities).isdisjoint(entity_ids)

    def reset_backoff(self) -> None:
        """Forget the failure back-off (Home Assistant is connected again)."""
        self._state.failures = 0
        self._state.retry_at = None
        self._state.error = None if self._state.fetched_at is not None else self._state.error

    def notify_changed(self) -> None:
        """A watched entity changed state; see ``changed`` in the module docstring."""
        self._state.dirty = True

    def _current_slot_known(self, now: datetime) -> bool:
        step = floor_to_step(now)
        for series in self._state.segments:
            ts = series.import_price
            if ts.start <= step < ts.end:
                index = int((step - ts.start) // ts.step)
                return ts.values[index] is not None
        return False

    def _tomorrow_covered(self, now: datetime) -> bool:
        tz = self._plan.tz
        today = now.astimezone(tz).date()
        end = self._state.known_end
        return end is not None and end >= _midnight(today + timedelta(days=2), tz)

    def _schedule(self, now: datetime) -> tuple[datetime, RefreshReason] | None:
        """The earliest time-based refresh (``None`` = nothing scheduled)."""
        state = self._state
        if state.fetched_at is None:
            return (state.retry_at or now, "initial")
        candidates: list[tuple[datetime, RefreshReason]] = [
            (state.fetched_at + timedelta(seconds=self._settings.refresh_interval_s), "interval")
        ]
        if state.retry_at is not None:
            candidates.append((state.retry_at, "retry"))
        if self._plan.source.kind == "dynamic" and not self._tomorrow_covered(now):
            tz = self._plan.tz
            published = datetime.combine(
                now.astimezone(tz).date(), self._settings.tomorrow_after, tzinfo=tz
            )
            earliest = state.fetched_at + timedelta(seconds=self._settings.tomorrow_poll_s)
            candidates.append((max(published, earliest), "tomorrow"))
        if state.retry_at is not None:
            # after a failure or a throttled call only the retry time counts, not the regular
            # schedule (which may be overdue because the fetch did not work)
            return (state.retry_at, "retry")
        return min(candidates, key=lambda c: c[0])

    def due(self, now: datetime | None = None) -> RefreshReason | None:
        """Why a refresh is due now, else ``None``."""
        now = now or self._clock()
        state = self._state
        in_backoff = state.retry_at is not None and now < state.retry_at
        if state.dirty:
            source = self._plan.source
            recent = state.last_attempt is not None and (
                now - state.last_attempt < timedelta(seconds=POLL_S)
            )
            if not source.uses_action and not recent:
                return "changed"  # reads the entity cache: free (but not in a tight loop)
            if (
                state.fetched_at is not None
                and not in_backoff
                and not self._current_slot_known(now)
            ):
                return "changed"
        scheduled = self._schedule(now)
        if scheduled is not None and scheduled[0] <= now:
            return scheduled[1]
        return None

    def next_refresh(self, now: datetime | None = None) -> datetime | None:
        """When the next time-based refresh is due (a state change can only make it earlier)."""
        now = now or self._clock()
        scheduled = self._schedule(now)
        return None if scheduled is None else max(scheduled[0], now)

    # --- refreshing --------------------------------------------------------------------

    async def tick(self) -> RefreshResult | None:
        """One scheduler step: refresh if :meth:`due`."""
        reason = self.due()
        return None if reason is None else await self.refresh(reason)

    async def run(
        self,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_s: float = POLL_S,
    ) -> None:
        """Poll :meth:`tick` every ``poll_s`` until cancelled; never raises (errors are state)."""
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tariff.tick_crashed")
            await sleep(poll_s)

    async def refresh(self, reason: RefreshReason = "forced") -> RefreshResult | None:
        """Fetch now. ``None`` when another refresh is already running."""
        if self._lock.locked():
            return None
        async with self._lock:
            now = self._clock()
            self._state.last_attempt = now
            window = TimeRange(start=floor_to_step(now), end=floor_to_step(now) + FETCH_HORIZON)
            try:
                data = await self._plan.source.fetch_segments(window, now)
                if not any(v is not None for s in data.segments for v in s.import_price.values):
                    raise SourceUnavailableError("the source returned no price")
                self._accept(data, now)
            except SourceThrottledError as exc:
                self._state.retry_at = now + timedelta(seconds=max(exc.retry_after_s, 1.0))
                return RefreshResult(reason, ok=False, throttled=True, error=exc)
            except TariffError as exc:
                return self._failed(reason, now, exc)
            except Exception as exc:
                log.exception("tariff.fetch_crashed", source=self._plan.source.source_id)
                return self._failed(reason, now, TariffError(f"{type(exc).__name__}: {exc}"))
        await self._persist(now)
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                log.exception("tariff.listener_failed")
        return RefreshResult(reason, ok=True, known_until=self._state.known_end)

    def _failed(self, reason: RefreshReason, now: datetime, error: TariffError) -> RefreshResult:
        state = self._state
        state.failures += 1
        state.error = error
        state.dirty = False  # this fetch has seen the change; only a newer one counts
        backoff = min(3600.0, self._settings.retry_interval_s * 2 ** (state.failures - 1))
        state.retry_at = now + timedelta(seconds=backoff)
        log.warning(
            "tariff.refresh_failed",
            source=self._plan.source.source_id,
            reason=reason,
            error=error.key,
            failures=state.failures,
            retry_in_s=backoff,
        )
        return RefreshResult(reason, ok=False, error=error)

    def _accept(self, data: SourceData, now: datetime) -> None:
        """Take over a fetch: fill its holes from the earlier data, keep the earlier tail.

        Everything is computed before the state is touched, so an error leaves the earlier
        data as it was.
        """
        old = self._state.segments
        filled, carried = _fill_gaps(data.segments, old)
        merged = (*filled, *_old_tail(filled, old, floor_to_step(now)))
        state = self._state
        state.segments = merged
        state.fetched_at = now
        state.report = data.report
        state.carried = carried
        state.error = None
        state.failures = 0
        state.retry_at = None
        state.dirty = False

    async def _persist(self, now: datetime) -> None:
        if self._store is None:
            return
        try:
            series = self._trimmed(now)
            if series is None:
                return
            key = (
                series.start,
                series.import_price.values,
                series.export_price.values if series.export_price else None,
            )
            if key == self._last_written:
                return
            await self._store.store_prices(series)
        except Exception:
            log.exception("tariff.store_failed", source=self._plan.source.source_id)
            return
        self._last_written = key

    def _trimmed(self, now: datetime) -> PriceSeries | None:
        """The planning prices from now to the end of the known prices without ``None`` edges."""
        grid = self._grid(now, FETCH_HORIZON)
        prices, _ = self._planning(grid)
        values = prices.import_price.values
        known = [i for i, v in enumerate(values) if v is not None]
        if not known:
            return None
        lo, hi = known[0], known[-1] + 1
        export = prices.export_price
        return PriceSeries(
            source_id=prices.source_id,
            price_includes=prices.price_includes,
            import_price=prices.import_price.model_copy(
                update={"start": prices.import_price.slot_start(lo), "values": values[lo:hi]}
            ),
            export_price=None
            if export is None
            else export.model_copy(
                update={"start": export.slot_start(lo), "values": export.values[lo:hi]}
            ),
        )

    # --- serving -----------------------------------------------------------------------

    def _grid(self, now: datetime, horizon: timedelta) -> Grid:
        """From the interval containing ``now`` to the end of the known prices.

        At least 24 h (``None`` beyond the data) and at most ``horizon``.
        """
        end = self._state.known_end
        floor = floor_to_step(now)
        lower = now + MIN_HORIZON
        upper = now + horizon
        target = min(lower if end is None else max(end, lower), upper)
        count = max(1, -(-(target - floor) // PLANNING_STEP))
        return Grid(start=floor, step=PLANNING_STEP, count=count)

    def _planning(self, grid: Grid) -> tuple[PriceSeries, int]:
        plan = self._plan
        return planning_prices(
            self._state.segments,
            grid,
            formula=plan.formula,
            export_price=self._export_base,
            rule=plan.negative_rule,
            tz=plan.tz,
            source_id=plan.source.source_id,
        )

    def prices(self, hours: float = 48, now: datetime | None = None) -> PriceSeries:
        """Import and export prices on the 15-minute grid from now (see :meth:`_grid`)."""
        now = now or self._clock()
        prices, _ = self._planning(self._grid(now, timedelta(hours=hours)))
        return prices

    def report(self, hours: float = 48, now: datetime | None = None) -> PriceReport:
        """The API view: prices, source, freshness and quality."""
        now = now or self._clock()
        plan, state = self._plan, self._state
        grid = self._grid(now, timedelta(hours=hours))
        prices, zeroed = self._planning(grid)
        export = prices.export_price
        slots = [
            PriceSlot(
                start=prices.import_price.slot_start(i),
                import_price=v,
                export_price=None if export is None else export.values[i],
            )
            for i, v in enumerate(prices.import_price.values)
        ]
        known = sum(s.import_price is not None for s in slots)
        native = [s.step for s in state.segments]
        has_formula = plan.formula is not None and plan.source.price_includes == "spot_only"
        steps = (
            min(native) // timedelta(minutes=1)
            if native and plan.source.kind == "dynamic"
            else None
        )
        return PriceReport(
            start=grid.start,
            slots=slots,
            source=PriceSourceInfo(
                import_source_id=plan.source.source_id,
                import_kind=plan.source.kind,
                source_price_includes=plan.source.price_includes,
                price_includes=prices.price_includes,
                formula_applied=has_formula,
                export_kind=plan.export_tariff.kind,
                negative_price_rule=plan.negative_rule.value,
                export_zeroed_slots=zeroed,
                native_step_min=int(steps) if steps is not None else None,
                currency=plan.currency,
            ),
            freshness=self._freshness(now),
            quality=PriceQuality(
                slots=len(slots),
                known_slots=known,
                has_tomorrow=self._tomorrow_covered(now),
                dropped_points=state.report.unreadable_values
                + state.report.duplicates
                + state.report.overlaps,
                carried_slots=state.carried,
            ),
        )

    def _freshness(self, now: datetime) -> PriceFreshness:
        state = self._state
        error = (
            None
            if state.error is None
            else PriceErrorOut(key=state.error.key, args=_json_args(state.error))
        )
        age = None if state.fetched_at is None else (now - state.fetched_at).total_seconds()
        status: Literal["fresh", "stale", "missing", "static"]
        if state.fetched_at is None or not state.segments:
            status = "missing"
        elif self._plan.source.kind != "dynamic":
            status = "static"
        else:
            limit = 2 * self._settings.refresh_interval_s
            status = (
                "fresh"
                if (age or 0.0) <= limit and self._current_slot_known(now) and state.error is None
                else "stale"
            )
        return PriceFreshness(
            status=status,
            fetched_at=state.fetched_at,
            age_s=age,
            known_until=state.known_end,
            next_refresh_at=self.next_refresh(now),
            last_error=error,
        )


def _json_args(error: TariffError) -> dict[str, str | float | int | list[str]]:
    out: dict[str, str | float | int | list[str]] = {}
    for key, value in error.message_args.items():
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            out[key] = value
        elif isinstance(value, list):
            out[key] = [str(v) for v in value]
        else:
            out[key] = str(value)
    return out


__all__ = [
    "FETCH_HORIZON",
    "MIN_HORIZON",
    "PriceStore",
    "RefreshReason",
    "RefreshResult",
    "ServiceSettings",
    "TariffPlan",
    "TariffService",
]
