# SPDX-License-Identifier: Apache-2.0
"""Phase 5 WP3 storage: migrations up from the previous schemas, ``day_summary``,
``accuracy_day`` and ``backtest_run`` repositories."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from suncore.storage import Database, open_timeseries
from suncore.storage.backtests import KEEP_RESULTS, BacktestRunStore
from suncore.storage.errors import SchemaTooNewError
from suncore.storage.migrations import latest_version, load_migrations, run_migrations
from suncore.storage.summaries import AccuracyDayRow, DaySummaryRow
from suncore.storage.timeseries import TimeseriesStore, timeseries_migrations

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


# --- migrations -----------------------------------------------------------------------------


async def test_timeseries_db_of_schema_1_upgrades_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "ts.db"
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, timeseries_migrations()[:1]) == 1
        await conn.execute(
            "INSERT INTO measurement_15m (key, ts, mean, min, max, n, derived) "
            "VALUES ('site.pv_w', 900, 5.0, 5.0, 5.0, 1, 0)"
        )
    async with await open_timeseries(path) as db:
        assert await db.user_version() == len(timeseries_migrations()) == 2
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
        assert {"day_summary", "accuracy_day"} <= tables
        assert await db.fetchone("SELECT mean FROM measurement_15m") == (5.0,)


async def test_timeseries_schema_1_reader_refuses_schema_2(tmp_path: Path) -> None:
    path = tmp_path / "ts.db"
    async with await open_timeseries(path):
        pass
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        with pytest.raises(SchemaTooNewError) as info:
            await run_migrations(conn, timeseries_migrations()[:1])
    assert (info.value.db_version, info.value.code_version) == (2, 1)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (2,)  # left untouched
    con.close()


async def test_suncore_db_of_schema_4_upgrades_to_5(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, load_migrations()[:4]) == 4
        await conn.execute(
            "INSERT INTO plan_snapshot (created_at, source, schema_version, payload) "
            "VALUES ('t', 'x', 1, x'00')"
        )
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version() == 5
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
        assert "backtest_run" in tables
        assert await db.fetchone("SELECT COUNT(*) FROM plan_snapshot") == (1,)


async def test_suncore_db_schema_4_reader_refuses_schema_5(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    async with await Database.open(path):
        pass
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        with pytest.raises(SchemaTooNewError) as info:
            await run_migrations(conn, load_migrations()[:4])
    assert (info.value.db_version, info.value.code_version) == (5, 4)


# --- day_summary ------------------------------------------------------------------------------


def day_row(day: date = date(2026, 11, 22), **kw: object) -> DaySummaryRow:
    base: dict[str, object] = {
        "day": day,
        "start": datetime(2026, 11, 21, 23, tzinfo=UTC),
        "end": datetime(2026, 11, 22, 23, tzinfo=UTC),
        "tz": "Europe/Berlin",
        "slots": 96,
        "coverage": 1.0,
        "complete": True,
        "derived_slots": 0,
        "pv_wh": 10_000.0,
        "house_wh": 12_000.0,
        "import_wh": 5_000.0,
        "export_wh": 3_000.0,
        "charge_wh": 2_000.0,
        "discharge_wh": 1_900.0,
        "pv_to_house_wh": 6_000.0,
        "pv_to_battery_wh": 1_000.0,
        "pv_to_grid_wh": 3_000.0,
        "battery_to_house_wh": 1_900.0,
        "battery_to_grid_wh": 0.0,
        "grid_to_house_wh": 4_100.0,
        "grid_to_battery_wh": 1_000.0,
        "residual_wh": 0.0,
        "autarky": 0.66,
        "self_consumption": 0.7,
        "equivalent_full_cycles": 0.2,
        "priced_slots": 96,
        "import_cost": 1.5,
        "export_revenue": 0.24,
        "cost_net": 1.26,
        "baseline_no_pv_cost": 3.6,
        "baseline_pv_only_cost": 2.0,
        "currency": "EUR",
        "tariff_fingerprint": "abc123abc123",
        "computed_at": NOW,
    }
    base.update(kw)
    return DaySummaryRow(**base)  # type: ignore[arg-type]


async def test_day_summary_round_trip_and_savings(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db).day_summaries
        row = day_row()
        await store.put(row)
        assert await store.get(row.day) == row
        assert row.savings_vs_no_pv == pytest.approx(3.6 - 1.26)
        assert row.savings_vs_pv_only == pytest.approx(2.0 - 1.26)
        unpriced = day_row(
            date(2026, 11, 21),
            cost_net=None,
            baseline_no_pv_cost=None,
            baseline_pv_only_cost=None,
            import_cost=None,
            export_revenue=None,
            currency=None,
            tariff_fingerprint=None,
            priced_slots=0,
            autarky=None,
        )
        await store.put(unpriced)
        got = await store.between(date(2026, 11, 1), date(2026, 11, 30))
        assert [r.day for r in got] == [date(2026, 11, 21), date(2026, 11, 22)]
        assert got[0].savings_vs_pv_only is None
        assert got[0].autarky is None
        assert await store.slot_counts(date(2026, 11, 1), date(2026, 11, 30)) == {
            date(2026, 11, 21): 96,
            date(2026, 11, 22): 96,
        }
        assert await store.count() == 2


async def test_day_summary_rejects_impossible_rows(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db).day_summaries
        with pytest.raises(sqlite3.IntegrityError):
            await store.put(day_row(coverage=1.5))


async def test_accuracy_day_round_trip_and_replace(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db).accuracy_days
        day = date(2026, 11, 22)
        row = AccuracyDayRow("solcast.pv", "pv", day, "0_6h", 120.0, -10.0, 24, NOW)
        assert (
            await store.put_many(
                [row, AccuracyDayRow("load_profile", "load", day, "0_6h", 80.0, 5.0, 24, NOW)]
            )
            == 2
        )
        await store.put_many([AccuracyDayRow("solcast.pv", "pv", day, "0_6h", 90.0, -2.0, 24, NOW)])
        rows = await store.between(day, day, source_id="solcast.pv")
        assert [(r.mae_w, r.bias_w) for r in rows] == [(90.0, -2.0)]
        assert await store.days_with_rows("solcast.pv", "pv", day - timedelta(days=3), day) == {day}
        assert (
            await store.days_with_rows("solcast.pv", "load", day - timedelta(days=3), day) == set()
        )
        assert await store.put_many([]) == 0


# --- backtest_run -----------------------------------------------------------------------------


async def test_backtest_run_keeps_the_last_five(tmp_path: Path) -> None:
    clock = [NOW]
    async with await Database.open(tmp_path / "s.db") as db:
        store = BacktestRunStore(db, clock=lambda: clock[0])
        ids = []
        for i in range(KEEP_RESULTS + 3):
            clock[0] = NOW + timedelta(minutes=i)
            ids.append(
                await store.start(
                    {"days": i}, window_start=NOW - timedelta(days=14), window_end=NOW
                )
            )
        kept = await store.headers()
        assert [m.id for m in kept] == ids[::-1][:KEEP_RESULTS]
        assert all(m.status == "running" for m in kept)
        assert await store.get(ids[0]) is None  # pruned
        assert await store.finish(ids[-1], "done", {"cost": 1.25}) is True
        assert await store.finish(ids[0], "done", {}) is False
        run = await store.get(ids[-1])
        assert run is not None
        assert run.meta.status == "done"
        assert run.request == {"days": KEEP_RESULTS + 2}
        assert run.result == {"cost": 1.25}
        assert await store.delete(ids[-1]) is True
        assert len(await store.headers()) == KEEP_RESULTS - 1
