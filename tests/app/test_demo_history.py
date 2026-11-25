# SPDX-License-Identifier: Apache-2.0
"""The deterministic demo history (Phase 5 WP3, plan decision 9)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from suncore.app.main import build_runtime
from suncore.core.measurements import SITE_PV_W, SITE_SOC_PCT, device_on_key
from suncore.core.series import TimeRange
from suncore.sim.demo_history import (
    PRICE_SOURCE,
    DemoSeedError,
    seed_demo_decisions,
    seed_demo_history,
    seed_demo_plans,
)
from suncore.storage import Database, open_timeseries
from suncore.storage.decisions import DecisionLog
from suncore.storage.plans import PlanSnapshotStore
from suncore.storage.timeseries import TimeseriesStore
from tests.app.conftest import demo_settings

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TimeseriesStore]:
    async with await open_timeseries(tmp_path / "t.db") as db:
        yield TimeseriesStore(db)


async def dump(store: TimeseriesStore) -> dict[str, list[tuple[object, ...]]]:
    out: dict[str, list[tuple[object, ...]]] = {}
    for table, order in (
        ("measurement_15m", "key, ts"),
        ("price", "source_id, slot_start"),
        ("forecast", "source_id, kind, issued_at, slot_start"),
        ("day_summary", "day"),
        ("accuracy_day", "source_id, kind, day, horizon"),
    ):
        rows = await store.database.fetchall(f"SELECT * FROM {table} ORDER BY {order}")
        out[table] = [tuple(r) for r in rows]
    return out


async def test_ninety_days_of_history_are_seeded(store: TimeseriesStore) -> None:
    assert await seed_demo_history(store, now=NOW) == 90
    sizes = (await store.size_report()).rows
    assert sizes["day_summary"] == 90
    assert sizes["accuracy_day"] > 40
    assert sizes["price"] > 8000
    rows = await store.day_summaries.between(date(2026, 1, 1), date(2026, 12, 31))
    assert [r.day for r in rows] == [
        date(2026, 11, 22) - timedelta(days=i) for i in range(89, -1, -1)
    ]
    assert all(r.complete and r.priced_slots == r.slots for r in rows)
    assert all(r.cost_net is not None and r.tariff_fingerprint is None for r in rows)
    assert all(0.0 <= (r.autarky or 0.0) <= 1.0 for r in rows)
    assert any((r.equivalent_full_cycles or 0.0) > 0.2 for r in rows)
    # older than 28 days: derived (hourly step-hold), newer: measured
    assert rows[0].derived_slots == rows[0].slots
    assert rows[-1].derived_slots == 0
    # the DST day of the season is 25 hours long (Europe/Berlin, 2026-10-25)
    dst = next(r for r in rows if r.day == date(2026, 10, 25))
    assert dst.slots == 100
    # forecasts only for the retention window
    first_issue = await store.database.fetchone("SELECT MIN(issued_at) FROM forecast")
    assert first_issue is not None
    assert datetime.fromtimestamp(float(str(first_issue[0])), UTC) >= NOW - timedelta(days=41)
    # a controlled device's on/off state is there for the run-time chart
    on = await store.database.fetchone(
        "SELECT COUNT(*) FROM measurement_15m WHERE key = ? AND mean > 0",
        (device_on_key("pool_pump"),),
    )
    assert on is not None
    assert int(str(on[0])) > 100
    window = TimeRange(start=NOW - timedelta(days=90), end=NOW)
    assert PRICE_SOURCE in await store.price_source_ids(window)


async def test_seeding_is_deterministic(tmp_path: Path) -> None:
    dumps = []
    for name in ("a", "b"):
        async with await open_timeseries(tmp_path / f"{name}.db") as db:
            store = TimeseriesStore(db)
            await seed_demo_history(store, now=NOW, days=30)
            dumps.append(await dump(store))
    assert dumps[0] == dumps[1]
    assert dumps[0]["day_summary"]
    # a different day gives different weather
    async with await open_timeseries(tmp_path / "c.db") as db:
        other = TimeseriesStore(db)
        await seed_demo_history(other, now=NOW + timedelta(days=3), days=30)
        assert (await dump(other))["measurement_15m"] != dumps[0]["measurement_15m"]


async def test_seeding_is_repeatable_on_the_same_file(store: TimeseriesStore) -> None:
    await seed_demo_history(store, now=NOW, days=10)
    before = await dump(store)
    await seed_demo_history(store, now=NOW, days=10)
    assert await dump(store) == before


async def test_battery_stays_in_its_corridor_and_balance_holds(store: TimeseriesStore) -> None:
    await seed_demo_history(store, now=NOW, days=20)
    soc = await store.database.fetchone(
        "SELECT MIN(mean), MAX(mean) FROM measurement_15m WHERE key = ?", (SITE_SOC_PCT,)
    )
    assert soc is not None
    assert float(str(soc[0])) >= 10.0
    assert float(str(soc[1])) <= 100.0
    rows = await store.day_summaries.between(date(2026, 1, 1), date(2026, 12, 31))
    for r in rows:
        # the seeded measurements are consistent: the attribution leaves no residual
        assert abs(r.residual_wh) < 1.0
        assert r.pv_wh > 0 or r.slots == 0
    pv = await store.database.fetchone(
        "SELECT COUNT(*) FROM measurement_15m WHERE key = ?", (SITE_PV_W,)
    )
    assert pv is not None
    assert int(str(pv[0])) >= 20 * 92


async def test_zero_days_seed_nothing(store: TimeseriesStore) -> None:
    assert await seed_demo_history(store, now=NOW, days=0) == 0
    assert (await store.size_report()).rows["day_summary"] == 0


async def test_seeding_refuses_the_production_data_directory(tmp_path: Path) -> None:
    class FakeDb:
        path = "/data/timeseries.db"

    class FakeStore:
        database = FakeDb()

    with pytest.raises(DemoSeedError):
        await seed_demo_history(FakeStore(), now=NOW)  # type: ignore[arg-type]


async def test_demo_decisions_and_plans(tmp_path: Path) -> None:
    async with await Database.open(tmp_path / "s.db") as db:
        log = DecisionLog(db)
        written = await seed_demo_decisions(log, now=NOW)
        assert written > 4
        assert written % 2 == 0
        latest = await log.latest(limit=100)
        assert len(latest) == written
        assert {d.intent.asset_id for d in latest} == {"pool_pump"}
        plans = PlanSnapshotStore(db, clock=lambda: NOW)
        assert await seed_demo_plans(plans, now=NOW, count=2) == 2
        assert await plans.count() == 2
        newest = await plans.latest()
        assert newest is not None
        assert newest.payload["intervals"]
        # The stored plans name the demo's own air conditioner, a device the demo configures.
        planned = {d for i in newest.payload["intervals"] for d in (i.get("devices") or {})}
        assert planned == {"living_room_ac"}


async def test_the_demo_runtime_seeds_its_throw_away_database(tmp_path: Path) -> None:
    runtime = await build_runtime(demo_settings(tmp_path, demo_history_days=5), env={})
    try:
        assert runtime.timeseries is not None
        assert (await runtime.timeseries.day_summaries.count()) == 5
        assert runtime.plans is not None
        assert await runtime.plans.count() >= 1
        assert not (tmp_path / "data").exists()  # the demo never writes the data directory
    finally:
        await runtime.stop()


async def test_the_demo_runtime_without_history_stays_empty(tmp_path: Path) -> None:
    runtime = await build_runtime(demo_settings(tmp_path), env={})
    try:
        assert runtime.timeseries is not None
        assert (await runtime.timeseries.day_summaries.count()) == 0
    finally:
        await runtime.stop()
