# SPDX-License-Identifier: Apache-2.0
"""Frozen daily summaries and daily accuracy (Phase 5 WP3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from suncore.app.summaries import (
    META_DAY_SUMMARY_SEEDED,
    AccuracySource,
    DaySummaryJob,
    SummaryContext,
    battery_model_of,
    export_fallback_of,
    local_day_bucket,
    record_accuracy_days,
    tariff_fingerprint,
)
from suncore.core.config import ExportTariff, ImportTariff, TariffConfig
from suncore.core.measurements import SITE_BATTERY_W, SITE_GRID_W, SITE_HOUSE_W, SITE_PV_W
from suncore.core.series import PriceSeries, QuantileSeries, SeriesUnit, TimeSeries
from suncore.core.site import BatteryConfig, EntityRef, GridConfig, SiteConfig
from suncore.storage import open_timeseries
from suncore.storage.timeseries import TimeseriesStore

BERLIN = ZoneInfo("Europe/Berlin")
Q = timedelta(minutes=15)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TimeseriesStore]:
    async with await open_timeseries(tmp_path / "t.db") as db:
        yield TimeseriesStore(db)


def context(**kw: object) -> SummaryContext:
    base: dict[str, object] = {
        "tz": BERLIN,
        "tz_name": "Europe/Berlin",
        "price_source_id": "p",
        "currency": "EUR",
        "tariff_fingerprint": "fp1",
    }
    base.update(kw)
    return SummaryContext(**base)  # type: ignore[arg-type]


def ts(start: datetime, values: list[float | None], unit: SeriesUnit = SeriesUnit.W) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=unit)


async def fill_day(
    store: TimeseriesStore,
    day: date,
    *,
    pv: float = 1000.0,
    grid: float = -500.0,
    house: float = 500.0,
    battery: float = 0.0,
    gap_every: int | None = None,
    price: float | None = 0.30,
) -> int:
    """Constant powers over one local day; returns the slots of the day."""
    bucket = local_day_bucket(day, BERLIN)
    count = round(bucket.duration / Q)

    def values(v: float) -> list[float | None]:
        return [None if gap_every and i % gap_every == 0 else v for i in range(count)]

    for key, value in (
        (SITE_PV_W, pv),
        (SITE_GRID_W, grid),
        (SITE_BATTERY_W, battery),
        (SITE_HOUSE_W, house),
    ):
        await store.store_quarter_hours(key, ts(bucket.start, values(value)))
    if price is not None:
        await put_price(store, bucket.start, count, price)
    return count


async def put_price(store: TimeseriesStore, start: datetime, count: int, price: float) -> None:
    await store.store_prices(
        PriceSeries(
            source_id="p",
            price_includes="all_in",
            import_price=ts(start, [price] * count, SeriesUnit.PRICE),
            export_price=ts(start, [0.08] * count, SeriesUnit.PRICE),
        )
    )


def clock_at(moment: datetime):  # type: ignore[no-untyped-def]
    return lambda: moment


# --- one day --------------------------------------------------------------------------------


async def test_a_plain_day_is_summarised_with_both_baselines(store: TimeseriesStore) -> None:
    day = date(2026, 11, 10)
    await fill_day(store, day)
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 11, 3, 30, tzinfo=BERLIN)))
    report = await job.compute_range(day, day, context())
    assert (report.examined, report.written, report.no_data) == (1, 1, 0)
    row = await store.day_summaries.get(day)
    assert row is not None
    assert (row.slots, row.coverage, row.complete) == (96, 1.0, True)
    assert row.pv_wh == pytest.approx(24_000.0)
    assert row.house_wh == pytest.approx(12_000.0)
    assert row.export_wh == pytest.approx(12_000.0)
    assert row.import_wh == pytest.approx(0.0)
    assert row.autarky == pytest.approx(1.0)
    assert row.self_consumption == pytest.approx(0.5)
    assert row.equivalent_full_cycles is None  # no battery capacity known
    assert row.cost_net == pytest.approx(-12 * 0.08)
    assert row.baseline_no_pv_cost == pytest.approx(12 * 0.30)
    assert row.baseline_pv_only_cost == pytest.approx(-12 * 0.08)
    assert row.savings_vs_pv_only == pytest.approx(0.0)  # no battery, no shifting
    assert row.savings_vs_no_pv == pytest.approx(12 * 0.30 + 12 * 0.08)
    assert (row.currency, row.tariff_fingerprint, row.priced_slots) == ("EUR", "fp1", 96)


async def test_a_day_without_prices_has_flows_but_no_cost(store: TimeseriesStore) -> None:
    day = date(2026, 11, 10)
    await fill_day(store, day, price=None)
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 11, 3, tzinfo=BERLIN)))
    await job.compute_range(day, day, context())
    row = await store.day_summaries.get(day)
    assert row is not None
    assert row.pv_wh > 0
    assert row.priced_slots == 0
    assert (row.cost_net, row.baseline_no_pv_cost, row.currency) == (None, None, None)
    assert row.savings_vs_no_pv is None


async def test_the_fixed_export_price_fills_in_when_the_stored_prices_have_none(
    store: TimeseriesStore,
) -> None:
    day = date(2026, 11, 10)
    await fill_day(store, day, price=None)
    bucket = local_day_bucket(day, BERLIN)
    await store.store_prices(
        PriceSeries(
            source_id="p",
            price_includes="all_in",
            import_price=ts(bucket.start, [0.30] * 96, SeriesUnit.PRICE),
        )
    )
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 11, 3, tzinfo=BERLIN)))
    await job.compute_range(day, day, context(export_fallback=0.10))
    row = await store.day_summaries.get(day)
    assert row is not None
    assert row.cost_net == pytest.approx(-12 * 0.10)


@pytest.mark.parametrize(
    ("day", "slots"),
    [
        (date(2026, 3, 29), 92),  # spring forward: 23 hours
        (date(2026, 10, 25), 100),  # fall back: 25 hours
    ],
)
async def test_a_dst_day_has_its_real_length(store: TimeseriesStore, day: date, slots: int) -> None:
    count = await fill_day(store, day)
    assert count == slots
    job = DaySummaryJob(store, clock_at(datetime(2026, 12, 1, tzinfo=UTC)))
    await job.compute_range(day, day, context())
    row = await store.day_summaries.get(day)
    assert row is not None
    assert (row.slots, row.coverage, row.complete) == (slots, 1.0, True)
    hours = slots / 4
    assert row.pv_wh == pytest.approx(1000.0 * hours)
    assert row.house_wh == pytest.approx(500.0 * hours)
    assert row.end - row.start == timedelta(hours=hours)
    assert row.cost_net == pytest.approx(-(500.0 * hours) / 1000 * 0.08)


async def test_the_days_around_a_dst_change_tile_without_a_gap(store: TimeseriesStore) -> None:
    days = [date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)]
    for d in days:
        await fill_day(store, d)
    job = DaySummaryJob(store, clock_at(datetime(2026, 4, 5, tzinfo=UTC)))
    await job.compute_range(days[0], days[-1], context())
    rows = await store.day_summaries.between(days[0], days[-1])
    assert [r.slots for r in rows] == [96, 92, 96]
    assert rows[0].end == rows[1].start
    assert rows[1].end == rows[2].start
    assert sum(r.pv_wh for r in rows) == pytest.approx(1000.0 * (96 + 92 + 96) / 4)


async def test_incomplete_days_are_marked_and_empty_days_skipped(store: TimeseriesStore) -> None:
    incomplete, empty = date(2026, 11, 10), date(2026, 11, 11)
    await fill_day(store, incomplete, gap_every=3)  # a third of the slots missing
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 13, tzinfo=BERLIN)))
    report = await job.compute_range(incomplete, empty, context())
    assert (report.examined, report.written, report.no_data) == (2, 1, 1)
    row = await store.day_summaries.get(incomplete)
    assert row is not None
    assert row.complete is False
    assert 0.6 < row.coverage < 0.7
    assert await store.day_summaries.get(empty) is None


async def test_cycles_use_the_batterys_usable_capacity(store: TimeseriesStore) -> None:
    day = date(2026, 11, 10)
    # charge 1 kW for the first half of the day, discharge 1 kW for the second half
    bucket = local_day_bucket(day, BERLIN)
    half = [1000.0] * 48 + [-1000.0] * 48
    await fill_day(store, day, pv=0.0, grid=0.0, house=0.0)
    await store.store_quarter_hours(SITE_BATTERY_W, ts(bucket.start, half), derived=False)
    await store.store_quarter_hours(SITE_GRID_W, ts(bucket.start, half), derived=False)
    await store.store_quarter_hours(SITE_HOUSE_W, ts(bucket.start, [0.0] * 96), derived=False)
    site = SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.g")),
        battery=BatteryConfig(
            power=EntityRef(entity_id="sensor.b"),
            soc=EntityRef(entity_id="sensor.s"),
            capacity_wh=10_000.0,
            min_soc_pct=10.0,
            max_soc_pct=100.0,
        ),
    )
    model = battery_model_of(site)
    assert model is not None
    assert model.usable_wh == pytest.approx(9000.0)
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 12, tzinfo=BERLIN)))
    await job.compute_range(day, day, context(has_battery=True, battery=model))
    row = await store.day_summaries.get(day)
    assert row is not None
    assert row.charge_wh == pytest.approx(12_000.0)
    assert row.equivalent_full_cycles == pytest.approx((12_000.0 + 12_000.0) / 2 / 9000.0)


def test_battery_model_needs_a_capacity() -> None:
    site = SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.g")),
        battery=BatteryConfig(
            power=EntityRef(entity_id="sensor.b"), soc=EntityRef(entity_id="sensor.s")
        ),
    )
    assert battery_model_of(site) is None
    assert battery_model_of(None) is None


def tariff(fixed: float, export: float = 0.08) -> TariffConfig:
    return TariffConfig(
        import_=ImportTariff(kind="fixed", fixed_price=fixed),
        export=ExportTariff(kind="fixed", fixed_price=export),
    )


def test_tariff_fingerprint_changes_with_the_configuration() -> None:
    assert tariff_fingerprint(None) is None
    a, b = tariff_fingerprint(tariff(0.30)), tariff_fingerprint(tariff(0.31))
    assert a is not None
    assert len(a) == 12
    assert a == tariff_fingerprint(tariff(0.30))
    assert a != b
    assert export_fallback_of(tariff(0.30, export=0.07)) == 0.07
    assert export_fallback_of(None) is None


# --- frozen days, repair, catch-up ----------------------------------------------------------------


async def test_a_frozen_day_survives_a_tariff_change(store: TimeseriesStore) -> None:
    day = date(2026, 11, 10)
    await fill_day(store, day)
    now = datetime(2026, 11, 11, 3, 30, tzinfo=BERLIN)
    job = DaySummaryJob(store, clock_at(now))
    await job.compute_range(day, day, context(tariff_fingerprint="old"))
    before = await store.day_summaries.get(day)
    # the user edits the tariff: new fingerprint, new stored prices from now on ...
    bucket = local_day_bucket(day, BERLIN)
    await put_price(store, bucket.start, 96, 0.55)
    report = await job.compute_range(
        day, day, context(tariff_fingerprint="new", export_fallback=1.0)
    )
    assert (report.written, report.unchanged) == (0, 1)
    assert await store.day_summaries.get(day) == before
    assert before is not None
    assert before.tariff_fingerprint == "old"
    assert before.baseline_no_pv_cost == pytest.approx(12 * 0.30)


async def test_a_day_is_repaired_when_more_of_it_was_measured(store: TimeseriesStore) -> None:
    day = date(2026, 11, 10)
    await fill_day(store, day, gap_every=2)  # half of it
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 11, 3, 30, tzinfo=BERLIN)))
    await job.compute_range(day, day, context())
    first = await store.day_summaries.get(day)
    assert first is not None
    assert first.complete is False
    await fill_day(store, day)  # the statistics rebuild brought the rest
    report = await job.compute_range(day, day, context())
    assert report.written == 1
    repaired = await store.day_summaries.get(day)
    assert repaired is not None
    assert (repaired.slots, repaired.complete) == (96, True)
    assert repaired.pv_wh > first.pv_wh
    # and an unchanged day is left alone
    assert (await job.compute_range(day, day, context())).unchanged == 1


async def test_the_nightly_job_computes_yesterday_and_repairs_the_last_week(
    store: TimeseriesStore,
) -> None:
    today = date(2026, 11, 20)
    now = datetime.combine(today, datetime.min.time(), tzinfo=BERLIN) + timedelta(
        hours=3, minutes=30
    )
    job = DaySummaryJob(store, clock_at(now))
    await store.set_meta(META_DAY_SUMMARY_SEEDED, "x")  # already seeded: only the window counts
    for offset in (1, 3, 9):
        await fill_day(store, today - timedelta(days=offset))
    report = await job.nightly(context())
    assert report.examined == 7
    assert report.written == 2  # yesterday and three days ago; nine days ago is outside
    days = {r.day for r in await store.day_summaries.between(date(2026, 11, 1), today)}
    assert days == {today - timedelta(days=1), today - timedelta(days=3)}
    # the next night nothing changed
    assert (await job.nightly(context())).written == 0


async def test_the_first_nightly_run_catches_up_over_the_whole_history(
    store: TimeseriesStore,
) -> None:
    today = date(2026, 11, 20)
    now = datetime.combine(today, datetime.min.time(), tzinfo=BERLIN) + timedelta(
        hours=3, minutes=30
    )
    job = DaySummaryJob(store, clock_at(now))
    for offset in (2, 12, 40):
        await fill_day(store, today - timedelta(days=offset))
    report = await job.nightly(context())  # never seeded: walks the whole history
    assert report.written == 3
    assert await store.get_meta(META_DAY_SUMMARY_SEEDED) is not None
    assert await store.day_summaries.count() == 3


async def test_a_catch_up_on_an_empty_file_marks_it_seeded(store: TimeseriesStore) -> None:
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 20, 3, tzinfo=BERLIN)))
    report = await job.catch_up(context())
    assert report.examined == 0
    assert await store.get_meta(META_DAY_SUMMARY_SEEDED) is not None


async def test_derived_slots_are_counted(store: TimeseriesStore) -> None:
    day = date(2026, 11, 10)
    bucket = local_day_bucket(day, BERLIN)
    for key, value in (
        (SITE_PV_W, 1000.0),
        (SITE_GRID_W, -500.0),
        (SITE_BATTERY_W, 0.0),
        (SITE_HOUSE_W, 500.0),
    ):
        await store.store_quarter_hours(key, ts(bucket.start, [value] * 96), derived=True)
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 12, tzinfo=BERLIN)))
    await job.compute_range(day, day, context())
    row = await store.day_summaries.get(day)
    assert row is not None
    assert row.derived_slots == 96


async def test_the_numeric_work_leaves_the_event_loop(
    store: TimeseriesStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415
    import threading  # noqa: PLC0415

    from suncore.app import summaries  # noqa: PLC0415

    seen: list[bool] = []
    original = summaries._compute_chunk

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    monkeypatch.setattr(summaries, "_compute_chunk", spy)
    day = date(2026, 11, 10)
    await fill_day(store, day)
    job = DaySummaryJob(store, clock_at(datetime(2026, 11, 12, tzinfo=BERLIN)))
    await job.compute_range(day, day, context())
    assert seen == [False]
    del asyncio


# --- accuracy_day ---------------------------------------------------------------------------------


async def test_accuracy_days_hold_yesterdays_error_per_source(store: TimeseriesStore) -> None:
    today = date(2026, 11, 20)
    now = datetime.combine(today, datetime.min.time(), tzinfo=BERLIN) + timedelta(
        hours=3, minutes=30
    )
    yesterday = today - timedelta(days=1)
    bucket = local_day_bucket(yesterday, BERLIN)
    await store.store_quarter_hours(SITE_PV_W, ts(bucket.start, [1000.0] * 96))
    forecast = QuantileSeries(
        p10=ts(bucket.start, [600.0] * 96),
        p50=ts(bucket.start, [800.0] * 96),
        p90=ts(bucket.start, [900.0] * 96),
    )
    await store.store_forecast("solcast.pv", "pv", bucket.start, forecast)
    written = await record_accuracy_days(
        store,
        [AccuracySource("solcast.pv", "pv"), AccuracySource("broken", "pv")],
        now=now,
        tz=BERLIN,
    )
    assert written >= 1
    rows = await store.accuracy_days.between(yesterday, yesterday)
    assert {r.source_id for r in rows} == {"solcast.pv"}
    near = next(r for r in rows if r.horizon == "0_6h")
    # error = actual - forecast = +200 W at every daylight slot: MAE 200, bias +200
    assert near.mae_w == pytest.approx(200.0)
    assert near.bias_w == pytest.approx(200.0)
    assert near.n == 24
    # yesterday is recomputed, older days that have a row are not
    again = await record_accuracy_days(
        store, [AccuracySource("solcast.pv", "pv")], now=now, tz=BERLIN
    )
    assert again == written


async def test_a_failing_accuracy_source_does_not_stop_the_others(
    store: TimeseriesStore,
) -> None:
    now = datetime(2026, 11, 20, 3, 30, tzinfo=BERLIN)
    bucket = local_day_bucket(date(2026, 11, 19), BERLIN)

    async def explode(_window: object) -> TimeSeries:
        msg = "reader down"
        raise OSError(msg)

    await store.store_quarter_hours(SITE_PV_W, ts(bucket.start, [1000.0] * 96))
    forecast = QuantileSeries(
        p10=ts(bucket.start, [600.0] * 96),
        p50=ts(bucket.start, [800.0] * 96),
        p90=ts(bucket.start, [900.0] * 96),
    )
    await store.store_forecast("a", "pv", bucket.start, forecast)
    written = await record_accuracy_days(
        store,
        [AccuracySource("bad", "load", explode), AccuracySource("a", "pv")],
        now=now,
        tz=BERLIN,
    )
    assert written >= 1
    assert {
        r.source_id
        for r in await store.accuracy_days.between(date(2026, 11, 1), date(2026, 11, 30))
    } == {"a"}
