# SPDX-License-Identifier: Apache-2.0
"""The tariff service: refresh decisions with injected time, caching, storing, serving."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

import pytest

from suncore.core.config import ExportTariff, ExportTier
from suncore.core.series import PriceSeries, TimeRange, TimeSeries
from suncore.tariff import (
    ExportConfigError,
    FixedPriceSource,
    MalformedPayloadError,
    ServiceSettings,
    SourceThrottledError,
    SourceUnavailableError,
    TariffPlan,
    TariffService,
)
from suncore.tariff.sources import SourceData, SourceKind
from suncore.templates.models import NegativePriceRule
from tests.tariff.helpers import BERLIN

Q = timedelta(minutes=15)
H = timedelta(hours=1)
FIXED_EXPORT = ExportTariff(kind="fixed", fixed_price=0.08)


def local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=BERLIN)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def hourly(start: datetime, values: Iterable[float | None]) -> PriceSeries:
    series = TimeSeries(start=start, step=H, values=tuple(values), unit="currency/kWh")  # type: ignore[arg-type]
    return PriceSeries(source_id="fake", price_includes="all_in", import_price=series)


@dataclass
class FakeSource:
    """A dynamic source whose answer the test controls."""

    segments: tuple[PriceSeries, ...] = ()
    error: Exception | None = None
    kind: SourceKind = "dynamic"
    uses_action: bool = True
    price_includes: str = "all_in"
    source_id: str = "fake"
    watched_entities: tuple[str, ...] = ("sensor.price",)
    calls: list[datetime] = field(default_factory=list)

    async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData:  # noqa: ARG002
        self.calls.append(now)
        if self.error is not None:
            raise self.error
        return SourceData(segments=self.segments)


def day_prices(day: int, days: int = 1, value: float = 0.30) -> PriceSeries:
    return hourly(local(day, 0), [value] * 24 * days)


class Store:
    def __init__(self, *, fail: bool = False) -> None:
        self.written: list[PriceSeries] = []
        self.fail = fail

    async def store_prices(self, prices: PriceSeries) -> int:
        if self.fail:
            msg = "disk full"
            raise OSError(msg)
        self.written.append(prices)
        return len(prices)


def make(
    source: FakeSource | FixedPriceSource,
    now: datetime,
    *,
    store: Store | None = None,
    export: ExportTariff = FIXED_EXPORT,
    rule: NegativePriceRule = NegativePriceRule.NONE,
    settings: ServiceSettings | None = None,
    kwp: float | None = None,
) -> tuple[TariffService, Clock]:
    clock = Clock(now)
    plan = TariffPlan(
        source=source,  # type: ignore[arg-type]
        export_tariff=export,
        negative_rule=rule,
        tz=BERLIN,
        currency="EUR",
        total_kwp=kwp,
    )
    return TariffService(plan, clock=clock, store=store, settings=settings), clock


# --- when to refresh --------------------------------------------------------------------


async def test_first_refresh_is_initial_then_nothing_is_due_until_the_interval() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 7))
    assert service.due() == "initial"
    result = await service.tick()
    assert result is not None
    assert result.ok
    assert result.reason == "initial"
    assert service.due() is None
    clock.advance(minutes=59)
    assert service.due() is None
    clock.advance(minutes=1)
    assert service.due() == "interval"
    assert service.next_refresh() == clock.now
    await service.tick()
    assert len(source.calls) == 2


async def test_tomorrow_is_asked_from_the_publication_time_every_poll_interval() -> None:
    source = FakeSource(segments=(day_prices(16),))  # today only
    service, clock = make(source, local(16, 12, 30))
    await service.refresh("initial")
    assert not service.report(48).quality.has_tomorrow
    assert service.next_refresh() == local(16, 13, 0)  # publication time, before the hourly one
    clock.now = local(16, 12, 59)
    assert service.due() is None
    clock.now = local(16, 13, 0)
    assert service.due() == "tomorrow"
    await service.tick()  # still not published
    clock.now = local(16, 13, 14)
    assert service.due() is None  # the poll interval (15 min) has not passed
    clock.now = local(16, 13, 15)
    assert service.due() == "tomorrow"
    source.segments = (day_prices(16, 2),)
    await service.tick()
    assert service.report(48).quality.has_tomorrow
    clock.now = local(16, 13, 40)
    assert service.due() is None  # covered: back to the hourly rhythm
    assert service.next_refresh() == local(16, 14, 15)


async def test_static_sources_do_not_poll_for_tomorrow() -> None:
    service, clock = make(FixedPriceSource(price=0.3), local(16, 14, 0))
    await service.refresh("initial")
    clock.now = local(16, 14, 30)
    assert service.due() is None
    assert service.report().freshness.status == "static"


async def test_an_entity_change_refreshes_attribute_sources_at_once() -> None:
    source = FakeSource(segments=(day_prices(16, 2),), uses_action=False)
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    clock.advance(minutes=5)
    assert service.due() is None
    service.notify_changed()
    assert service.due() == "changed"
    result = await service.tick()
    assert result is not None
    assert result.reason == "changed"
    assert service.due() is None  # the flag is cleared by a successful refresh


async def test_an_entity_change_does_not_hit_an_action_source_while_the_slot_is_known() -> None:
    source = FakeSource(segments=(day_prices(16, 2),), uses_action=True)
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    clock.advance(minutes=15)
    service.notify_changed()
    assert service.due() is None  # an action costs a request, the sensor changes every slot
    clock.now = local(18, 0, 0)  # the known prices ran out: now the change is worth a call
    assert service.due() == "changed"


async def test_a_change_before_the_first_fetch_does_not_wait_for_the_back_off() -> None:
    source = FakeSource(error=SourceUnavailableError("cache empty"), uses_action=False)
    service, _ = make(source, local(16, 10, 0))
    await service.refresh("initial")
    assert service.due() is None  # backing off
    source.error = None
    source.segments = (day_prices(16, 2),)
    service.notify_changed()  # the entity arrived
    assert service.due() == "changed"


# --- failures ---------------------------------------------------------------------------


async def test_failures_back_off_exponentially_and_success_resets() -> None:
    source = FakeSource(error=SourceUnavailableError("down"))
    service, clock = make(source, local(16, 10, 0))
    first = await service.refresh("initial")
    assert first is not None
    assert not first.ok
    assert first.error is not None
    assert service.due() is None
    clock.advance(minutes=5)
    assert service.due() == "initial"
    await service.refresh("initial")
    clock.advance(minutes=9)
    assert service.due() is None  # second failure: 10 minutes
    clock.advance(minutes=1)
    assert service.due() == "initial"
    source.error = None
    source.segments = (day_prices(16, 2),)
    ok = await service.refresh("initial")
    assert ok is not None
    assert ok.ok
    assert service.report().freshness.last_error is None
    assert service.next_refresh() == clock.now + H


async def test_back_off_is_capped_at_one_hour() -> None:
    source = FakeSource(error=MalformedPayloadError("bad"))
    service, clock = make(source, local(16, 10, 0))
    for _ in range(10):
        await service.refresh("retry")
        clock.advance(hours=2)
    assert service._state.retry_at is not None
    assert service._state.retry_at - (clock.now - timedelta(hours=2)) == H


async def test_a_failure_keeps_the_good_data_and_reports_the_error_key() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    source.error = MalformedPayloadError("bad", attribute="prices")
    clock.advance(hours=1)
    await service.tick()
    report = service.report(24)
    assert report.quality.known_slots == report.quality.slots  # still served
    assert report.freshness.last_error is not None
    assert report.freshness.last_error.key == "tariff.error.malformed_payload"
    assert report.freshness.last_error.args == {"attribute": "prices"}
    assert report.freshness.status == "stale"


async def test_an_empty_answer_never_replaces_good_data() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    source.segments = ()
    clock.advance(hours=1)
    result = await service.tick()
    assert result is not None
    assert not result.ok
    assert service.report(24).quality.known_slots > 0


async def test_a_throttled_call_is_not_a_failure() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    source.error = SourceThrottledError(40.0)
    clock.advance(hours=1)
    result = await service.tick()
    assert result is not None
    assert result.throttled
    assert service._state.failures == 0
    assert service.report().freshness.last_error is None
    assert service.next_refresh() == clock.now + timedelta(seconds=40)
    clock.advance(seconds=39)
    assert service.due() is None
    clock.advance(seconds=1)
    assert service.due() == "retry"


async def test_an_unexpected_exception_becomes_a_failure_not_a_crash() -> None:
    source = FakeSource(error=RuntimeError("boom"))
    service, _ = make(source, local(16, 10, 0))
    result = await service.refresh()
    assert result is not None
    assert not result.ok
    assert result.error is not None
    assert result.error.key == "tariff.error.action_failed"


async def test_reset_backoff_after_reconnect() -> None:
    source = FakeSource(error=SourceUnavailableError("down"))
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    service.reset_backoff()
    assert service.due() == "initial"
    clock.advance(seconds=1)
    assert service.report().freshness.last_error is not None  # never succeeded: the error stays


async def test_a_refresh_in_progress_blocks_a_second_one() -> None:
    gate = asyncio.Event()

    class Slow(FakeSource):
        async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData:
            await gate.wait()
            return await super().fetch_segments(window, now)

    source = Slow(segments=(day_prices(16, 2),))
    service, _ = make(source, local(16, 10, 0))
    first = asyncio.create_task(service.refresh("initial"))
    await asyncio.sleep(0)
    assert await service.refresh("forced") is None
    gate.set()
    result = await first
    assert result is not None
    assert result.ok


# --- merging and serving ----------------------------------------------------------------


async def test_slots_beyond_the_new_data_are_kept_when_a_source_forgets_tomorrow() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 0))
    await service.refresh("initial")
    source.segments = (hourly(local(16, 0), [0.40] * 24),)  # today only, new values
    clock.advance(hours=1)
    await service.refresh()
    report = service.report(48)
    assert report.quality.has_tomorrow
    prices = service.prices(48).import_price.values
    assert prices[0] == 0.40  # new data wins where it has data
    assert prices[-1] == 0.30  # tomorrow comes from the earlier fetch


async def test_the_series_is_at_least_24_hours_and_ends_with_the_known_prices() -> None:
    now = local(16, 10, 7)
    source = FakeSource(segments=(hourly(local(16, 10), [0.3] * 12),))  # 12 h known
    service, _ = make(source, now)
    await service.refresh("initial")
    short = service.prices(48)
    assert (
        len(short) == 24 * 4 + 1
    )  # 24 h from the quarter hour of "now" (10:00 -> 10:00 next day, +1)
    assert short.import_price.values[: 12 * 4].count(None) == 0
    assert short.import_price.values[12 * 4 :].count(None) == len(short) - 12 * 4
    source.segments = (hourly(local(16, 10), [0.3] * 36),)
    await service.refresh()
    assert len(service.prices(48)) == 36 * 4
    assert len(service.prices(30)) == 30 * 4 + 1  # capped by the request
    assert service.prices(48).step == Q


async def test_fixed_prices_fill_the_whole_request() -> None:
    service, _ = make(FixedPriceSource(price=0.31), local(16, 10, 7))
    await service.refresh("initial")
    out = service.prices(48)
    assert len(out) >= 192
    assert set(out.import_price.values) == {0.31}
    assert out.export_price is not None
    assert set(out.export_price.values) == {0.08}


async def test_prices_before_any_fetch_are_unknown() -> None:
    service, _ = make(FakeSource(), local(16, 10, 0))
    out = service.prices(48)
    assert len(out) == 24 * 4
    assert set(out.import_price.values) == {None}
    assert service.report().freshness.status == "missing"
    assert service.report().freshness.next_refresh_at is not None


# --- report -----------------------------------------------------------------------------


async def test_report_describes_source_formula_rule_and_freshness() -> None:
    from suncore.core.tariff import PriceFormula  # noqa: PLC0415

    source = FakeSource(
        segments=(hourly(local(16, 0), [0.10, -0.05] * 36),), price_includes="spot_only"
    )
    spot = PriceSeries(
        source_id="fake", price_includes="spot_only", import_price=source.segments[0].import_price
    )
    source.segments = (spot,)
    clock = Clock(local(16, 10, 7))
    plan = TariffPlan(
        source=source,  # type: ignore[arg-type]
        export_tariff=FIXED_EXPORT,
        negative_rule=NegativePriceRule.NO_REMUNERATION,
        tz=BERLIN,
        currency="EUR",
        formula=PriceFormula(grid_fee=0.1, vat_pct=19),
    )
    service = TariffService(plan, clock=clock)
    await service.refresh("initial")
    report = service.report(24)
    assert report.unit == "currency/kWh"
    assert report.step_min == 15
    info = report.source
    assert info.import_source_id == "fake"
    assert info.import_kind == "dynamic"
    assert info.source_price_includes == "spot_only"
    assert info.price_includes == "all_in"
    assert info.formula_applied
    assert info.export_kind == "fixed"
    assert info.negative_price_rule == "no_remuneration"
    assert info.export_zeroed_slots > 0
    assert info.native_step_min == 60
    assert info.currency == "EUR"
    assert report.freshness.status == "fresh"
    assert report.freshness.fetched_at == clock.now
    assert report.freshness.age_s == 0
    assert report.freshness.known_until == local(16, 0).astimezone(UTC) + 72 * H
    assert report.quality.has_tomorrow
    assert report.slots[0].start == local(16, 10).astimezone(UTC)
    zero = [s for s in report.slots if s.export_price == 0.0]
    assert zero
    assert all(s.import_price is not None for s in zero)


async def test_freshness_becomes_stale_with_age_or_a_missing_current_slot() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(
        source, local(16, 10, 0), settings=ServiceSettings(refresh_interval_s=3600)
    )
    await service.refresh("initial")
    assert service.report().freshness.status == "fresh"
    source.error = SourceUnavailableError("down")
    clock.advance(hours=2, minutes=1)
    await service.tick()
    assert service.report().freshness.status == "stale"
    clock.now = local(18, 0)  # the prices ended
    assert service.report().freshness.status == "stale"


async def test_report_counts_dropped_points() -> None:
    from suncore.tariff.parse import ParseReport  # noqa: PLC0415

    class Reporting(FakeSource):
        async def fetch_segments(
            self,
            window: TimeRange,  # noqa: ARG002
            now: datetime,  # noqa: ARG002
        ) -> SourceData:
            return SourceData(
                segments=self.segments,
                report=ParseReport(points=24, unreadable_values=2, duplicates=1, overlaps=3),
            )

    service, _ = make(Reporting(segments=(day_prices(16, 2),)), local(16, 10, 0))
    await service.refresh("initial")
    assert service.report().quality.dropped_points == 6


# --- export wiring ----------------------------------------------------------------------


async def test_tiered_export_is_resolved_with_the_plant_size_at_construction() -> None:
    tiered = ExportTariff(
        kind="tiered", tiers=(ExportTier(up_to_kwp=10, price=0.08), ExportTier(price=0.07))
    )
    service, _ = make(FixedPriceSource(price=0.3), local(16, 10, 0), export=tiered, kwp=17.0)
    await service.refresh("initial")
    out = service.prices(24)
    assert out.export_price is not None
    assert out.export_price.values[0] == pytest.approx((10 * 0.08 + 7 * 0.07) / 17)
    with pytest.raises(ExportConfigError):
        make(FixedPriceSource(price=0.3), local(16, 10, 0), export=tiered, kwp=None)


# --- storing ----------------------------------------------------------------------------


async def test_prices_are_stored_trimmed_to_the_known_range_and_only_when_changed() -> None:
    store = Store()
    source = FakeSource(segments=(hourly(local(16, 10), [0.3, 0.31, 0.32]),))
    service, clock = make(source, local(16, 10, 7), store=store)
    await service.refresh("initial")
    (written,) = store.written
    assert written.source_id == "fake"
    assert written.start == local(16, 10).astimezone(UTC)
    assert written.import_price.values == (0.3,) * 4 + (0.31,) * 4 + (0.32,) * 4
    assert written.export_price is not None
    assert set(written.export_price.values) == {0.08}
    clock.advance(minutes=1)
    await service.refresh()
    assert len(store.written) == 1  # identical: not written again
    source.segments = (hourly(local(16, 10), [0.3, 0.31, 0.33]),)
    await service.refresh()
    assert len(store.written) == 2


async def test_a_failing_store_does_not_fail_the_refresh() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, _ = make(source, local(16, 10, 0), store=Store(fail=True))
    result = await service.refresh("initial")
    assert result is not None
    assert result.ok


async def test_nothing_is_stored_without_prices() -> None:
    store = Store()
    service, _ = make(
        FakeSource(segments=(hourly(local(1, 0), [0.3]),)), local(16, 10, 0), store=store
    )
    await service.refresh("initial")
    assert store.written == []  # the only price lies in the past


# --- listeners, watch, run --------------------------------------------------------------


async def test_listeners_hear_about_new_prices_and_can_be_removed() -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 0))
    heard: list[int] = []

    def bad() -> None:
        msg = "listener bug"
        raise RuntimeError(msg)

    remove = service.add_listener(lambda: heard.append(1))
    service.add_listener(bad)
    await service.refresh("initial")
    assert heard == [1]
    remove()
    remove()  # idempotent
    clock.advance(hours=1)
    await service.refresh()
    assert heard == [1]


def test_watched_entities() -> None:
    service, _ = make(FakeSource(watched_entities=("sensor.a",)), local(16, 10, 0))
    assert service.watched_entities == ("sensor.a",)
    assert not service.watched_entities_isdisjoint(["sensor.b", "sensor.a"])
    assert service.watched_entities_isdisjoint(["sensor.b"])


async def test_run_ticks_until_cancelled_and_survives_a_crashing_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeSource(segments=(day_prices(16, 2),))
    service, clock = make(source, local(16, 10, 0))
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds=seconds)
        if len(sleeps) == 2:
            monkeypatch.setattr(service, "tick", crash)
        if len(sleeps) >= 4:
            raise asyncio.CancelledError

    async def crash() -> None:
        msg = "tick bug"
        raise RuntimeError(msg)

    with pytest.raises(asyncio.CancelledError):
        await service.run(sleep, poll_s=30.0)
    assert sleeps == [30.0] * 4
    assert (
        len(source.calls) == 1
    )  # the first tick fetched, the later ones found nothing due / crashed


def test_settings_have_a_floor_below_the_action_limiter() -> None:
    low = ServiceSettings(
        refresh_interval_s=1, retry_interval_s=1, tomorrow_poll_s=1, tomorrow_after=time(12)
    )
    assert (low.refresh_interval_s, low.retry_interval_s, low.tomorrow_poll_s) == (60.0, 60.0, 60.0)
