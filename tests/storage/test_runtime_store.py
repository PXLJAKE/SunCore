# SPDX-License-Identifier: Apache-2.0
"""WP4: SqliteRuntimeStore, PowerModelStateStore, schema 3 and decision retention."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from suncore.core.decisions import SwitchAction
from suncore.safety.runtime import DeviceRuntime, MemoryRuntimeStore, RuntimeStore
from suncore.storage import Database, DecisionLog, PowerModelStateStore, SqliteRuntimeStore
from suncore.storage.errors import SchemaTooNewError
from suncore.storage.migrations import latest_version, load_migrations, run_migrations

from .test_decision_log import T0, make_decision

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _runtime(dev: str = "a") -> DeviceRuntime:
    return DeviceRuntime(
        device_id=dev,
        last_on_ts=NOW,
        switch_times=(NOW - timedelta(minutes=5), NOW),
        last_written=SwitchAction(on=True),
        baseline=SwitchAction(on=False),
        baseline_ts=NOW,
        override=True,
        override_until=NOW + timedelta(hours=2),
    )


# --- schema 3 ---------------------------------------------------------------------------


async def test_v2_database_migrates_to_v3_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    migrations = load_migrations()[:2]
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, migrations) == 2
        await conn.execute("INSERT INTO learned VALUES ('k', '1', 't')")
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version() == 4
        assert await db.fetchone("SELECT value FROM learned") == ("1",)
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
    assert {"device_runtime", "safety_state", "power_model_state"} <= tables


async def test_reader_expecting_v2_refuses_v3(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, load_migrations()[:3]) == 3
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        with pytest.raises(SchemaTooNewError) as info:
            await run_migrations(conn, load_migrations()[:2])
    assert (info.value.db_version, info.value.code_version) == (3, 2)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (3,)
    con.close()


# --- SqliteRuntimeStore -----------------------------------------------------------------


async def test_store_satisfies_protocol_and_round_trips(db: Database) -> None:
    store: RuntimeStore = SqliteRuntimeStore(db, clock=lambda: NOW)
    assert await store.load_runtime("a") is None
    await store.save_runtime(_runtime("a"))
    await store.save_runtime(_runtime("b"))
    await store.save_runtime(_runtime("a").model_copy(update={"override": False}))
    assert (await store.load_runtime("a")) == _runtime("a").model_copy(update={"override": False})
    assert [r.device_id for r in await store.all_runtimes()] == ["a", "b"]


async def test_store_matches_memory_store_behaviour(db: Database) -> None:
    sql, mem = SqliteRuntimeStore(db), MemoryRuntimeStore()
    for s in (sql, mem):
        await s.save_runtime(_runtime("x"))
        await s.save_emergency_stop(True)
    assert await sql.load_runtime("x") == await mem.load_runtime("x")
    assert await sql.load_emergency_stop() is await mem.load_emergency_stop() is True


async def test_runtime_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    async with await Database.open(path) as db:
        store = SqliteRuntimeStore(db)
        await store.save_runtime(_runtime())
        await store.save_emergency_stop(True)
    async with await Database.open(path) as db:
        store = SqliteRuntimeStore(db)
        assert await store.load_runtime("a") == _runtime()
        assert await store.load_emergency_stop() is True
        await store.save_emergency_stop(False)
        assert await store.load_emergency_stop() is False


async def test_unparseable_payload_counts_as_missing(db: Database) -> None:
    store = SqliteRuntimeStore(db)
    await store.save_runtime(_runtime("good"))
    await db.execute("INSERT INTO device_runtime VALUES ('bad', '{\"nope\": 1}', 't')")
    await db.execute(
        "INSERT INTO device_runtime VALUES ('mismatch', ?, 't')",
        (_runtime("other").model_dump_json(),),
    )
    assert await store.load_runtime("bad") is None
    assert await store.load_runtime("mismatch") is None
    assert [r.device_id for r in await store.all_runtimes()] == ["good"]


async def test_emergency_stop_defaults_off_and_unreadable_fails_safe(db: Database) -> None:
    store = SqliteRuntimeStore(db)
    assert await store.load_emergency_stop() is False
    await db.execute("INSERT INTO safety_state VALUES ('emergency_stop', 'garbage', 't')")
    assert await store.load_emergency_stop() is True


async def test_delete_runtime(db: Database) -> None:
    store = SqliteRuntimeStore(db)
    await store.save_runtime(_runtime())
    await store.delete_runtime("a")
    assert await store.load_runtime("a") is None


# --- PowerModelStateStore ---------------------------------------------------------------


async def test_power_model_state_round_trip_and_tolerance(db: Database) -> None:
    store = PowerModelStateStore(db, clock=lambda: NOW)
    assert await store.get("ac") is None
    await store.put("ac", {"theta": [1.0, 2.5], "n": 3})
    await store.put("ac", {"theta": [2.0], "n": 4})
    assert await store.get("ac") == {"theta": [2.0], "n": 4}
    await db.execute("INSERT INTO power_model_state VALUES ('arr', '[1]', 't')")
    assert await store.get("arr") is None
    with pytest.raises(ValueError, match="Out of range"):
        await store.put("bad", {"x": float("nan")})
    await store.delete("ac")
    assert await store.get("ac") is None


# --- decision retention -----------------------------------------------------------------


async def test_prune_by_age(db: Database) -> None:
    log = DecisionLog(db)
    for n in range(5):
        await log.append(make_decision(n))  # all at T0 + n s
    await log.append(make_decision(0).model_copy(update={"ts": T0 + timedelta(days=40)}))
    now = T0 + timedelta(days=45)
    assert await log.prune(now) == 5  # 45 days old; the 40-day-old-at-T0 row is 5 days old
    assert await log.count() == 1
    assert await log.prune(now) == 0


async def test_prune_by_row_cap_keeps_newest(db: Database) -> None:
    log = DecisionLog(db)
    for n in range(10):
        await log.append(make_decision(n))
    assert await log.prune(T0, max_rows=4) == 6
    assert [d.ts for d in await log.latest(10)] == [T0 + timedelta(seconds=n) for n in (9, 8, 7, 6)]
    assert await log.prune(T0, max_rows=4) == 0
    assert await log.prune(T0, max_rows=100) == 0


async def test_prune_boundary_exactly_max_age_is_kept(db: Database) -> None:
    log = DecisionLog(db)
    await log.append(make_decision(0))
    assert await log.prune(T0 + timedelta(days=30)) == 0
    assert await log.prune(T0 + timedelta(days=30, seconds=1)) == 1
