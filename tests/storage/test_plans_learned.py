# SPDX-License-Identifier: Apache-2.0
"""Schema 4 of ``suncore.db``: plan snapshots and learned parameters."""

from __future__ import annotations

import sqlite3
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from suncore.storage import (
    Database,
    LearnedParamStore,
    PlanSnapshotStore,
    SchemaTooNewError,
)
from suncore.storage.learned import NS_BLEND_WEIGHTS, NS_FORECAST_BIAS, NS_ML_STATE
from suncore.storage.migrations import latest_version, load_migrations, run_migrations
from suncore.storage.plans import decode_payload, encode_payload

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


# --- migration 0004 ---------------------------------------------------------------------


async def test_v3_database_upgrades_to_v4_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, load_migrations()[:3]) == 3
        await conn.execute("INSERT INTO learned VALUES ('k', '1', 't')")
        await conn.execute("INSERT INTO safety_state VALUES ('emergency_stop', '0', 't')")
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version() == 4
        assert await db.fetchone("SELECT value FROM learned") == ("1",)
        assert await db.fetchone("SELECT value FROM safety_state") == ("0",)
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
    assert {"plan_snapshot", "learned_param"} <= tables


async def test_fresh_database_has_schema_4(tmp_path: Path) -> None:
    async with await Database.open(tmp_path / "s.db") as db:
        assert await db.user_version() == 4


async def test_reader_of_schema_3_refuses_schema_4(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    async with await Database.open(path):
        pass
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        with pytest.raises(SchemaTooNewError) as info:
            await run_migrations(conn, load_migrations()[:3])
    assert (info.value.db_version, info.value.code_version) == (4, 3)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (4,)
    con.close()


# --- plan snapshots -----------------------------------------------------------------------


@pytest.fixture
def plans(db: Database) -> PlanSnapshotStore:
    return PlanSnapshotStore(db, clock=lambda: NOW)


async def test_snapshot_round_trip_and_compression(db: Database, plans: PlanSnapshotStore) -> None:
    payload = {"intervals": [{"t": i, "w": i * 1.5} for i in range(500)], "name": "plän"}
    snapshot_id = await plans.save(payload, source="planner", schema_version=2)
    got = await plans.get(snapshot_id)
    assert got is not None
    assert got.payload == payload
    assert (got.source, got.schema_version, got.created_at) == ("planner", 2, NOW)
    row = await db.fetchone("SELECT length(payload) FROM plan_snapshot")
    assert row is not None
    assert int(str(row[0])) < len(str(payload)) / 3  # compressed
    assert await plans.get(snapshot_id + 1) is None


async def test_payload_codec_is_strict() -> None:
    assert decode_payload(encode_payload({"a": 1})) == {"a": 1}
    with pytest.raises(ValueError, match="not an object"):
        decode_payload(zlib.compress(b"[1, 2]"))
    with pytest.raises(ValueError, match="unreadable"):
        decode_payload(b"garbage")
    with pytest.raises(ValueError, match="unreadable"):
        decode_payload(zlib.compress(b"{not json"))
    with pytest.raises(ValueError):  # noqa: PT011 - json refuses NaN with allow_nan=False
        encode_payload({"x": float("nan")})


async def test_latest_headers_and_unreadable_rows(db: Database, plans: PlanSnapshotStore) -> None:
    first = await plans.save({"n": 1}, source="a", schema_version=1, created_at=NOW)
    second = await plans.save({"n": 2}, source="b", schema_version=2, created_at=NOW)
    await db.execute(
        "INSERT INTO plan_snapshot (created_at, source, schema_version, payload) "
        "VALUES (?, 'bad', 1, ?)",
        (NOW.isoformat(), b"not zlib"),
    )
    latest = await plans.latest()
    assert latest is not None
    assert latest.id == second  # the unreadable newest row is skipped
    headers = await plans.headers()
    assert [h.source for h in headers] == ["bad", "b", "a"]
    assert [h.id for h in await plans.headers(limit=1)] == [headers[0].id]
    assert await plans.count() == 3
    assert first < second


async def test_latest_is_none_without_readable_snapshots(plans: PlanSnapshotStore) -> None:
    assert await plans.latest() is None


async def test_prune_keeps_at_most_n_and_14_days(plans: PlanSnapshotStore) -> None:
    for i in range(10):
        await plans.save(
            {"i": i}, source="p", schema_version=1, created_at=NOW - timedelta(hours=i)
        )
    assert await plans.prune(NOW, keep_last=4) == 6
    assert [h.id for h in await plans.headers()] == [10, 9, 8, 7]
    await plans.save({"old": 1}, source="p", schema_version=1, created_at=NOW - timedelta(days=20))
    # the newest id is the old one: the age rule removes it, the others stay
    assert await plans.prune(NOW) == 1
    assert await plans.count() == 4


async def test_prune_defaults_are_200_and_14_days(plans: PlanSnapshotStore) -> None:
    from suncore.storage.plans import KEEP_DAYS, KEEP_LAST  # noqa: PLC0415

    assert (KEEP_LAST, KEEP_DAYS) == (200, 14)
    assert await plans.prune(NOW) == 0


async def test_prune_by_age(plans: PlanSnapshotStore) -> None:
    await plans.save({"a": 1}, source="p", schema_version=1, created_at=NOW - timedelta(days=15))
    await plans.save({"b": 1}, source="p", schema_version=1, created_at=NOW - timedelta(days=13))
    assert await plans.prune(NOW) == 1
    remaining = await plans.latest()
    assert remaining is not None
    assert remaining.payload == {"b": 1}


# --- learned parameters --------------------------------------------------------------------


@pytest.fixture
def learned(db: Database) -> LearnedParamStore:
    return LearnedParamStore(db, clock=lambda: NOW)


async def test_learned_round_trip_and_replace(learned: LearnedParamStore) -> None:
    await learned.put(NS_FORECAST_BIAS, "solcast", {"hour_12": 1.05}, schema_version=2)
    got = await learned.get(NS_FORECAST_BIAS, "solcast")
    assert got is not None
    assert (got.payload, got.schema_version, got.updated_at) == ({"hour_12": 1.05}, 2, NOW)
    await learned.put(NS_FORECAST_BIAS, "solcast", {"hour_12": 1.1})
    replaced = await learned.get(NS_FORECAST_BIAS, "solcast")
    assert replaced is not None
    assert (replaced.payload, replaced.schema_version) == ({"hour_12": 1.1}, 1)
    assert await learned.get(NS_FORECAST_BIAS, "other") is None


async def test_learned_namespaces_are_separate(learned: LearnedParamStore) -> None:
    await learned.put(NS_FORECAST_BIAS, "b", {"x": 1})
    await learned.put(NS_FORECAST_BIAS, "a", {"x": 2})
    await learned.put(NS_BLEND_WEIGHTS, "a", {"w": 0.5})
    listed = await learned.in_namespace(NS_FORECAST_BIAS)
    assert [p.key for p in listed] == ["a", "b"]
    assert await learned.in_namespace(NS_ML_STATE) == []
    assert await learned.delete(NS_FORECAST_BIAS, "a") is True
    assert await learned.delete(NS_FORECAST_BIAS, "a") is False
    assert await learned.delete_namespace(NS_FORECAST_BIAS) == 1
    assert len(await learned.in_namespace(NS_BLEND_WEIGHTS)) == 1


async def test_learned_unreadable_payload_is_missing(
    db: Database, learned: LearnedParamStore
) -> None:
    await db.execute(
        "INSERT INTO learned_param VALUES ('net_error', 'list', '[1, 2]', 1, ?)", (NOW.isoformat(),)
    )
    await learned.put("net_error", "ok", {"q": 1})
    assert await learned.get("net_error", "list") is None
    assert [p.key for p in await learned.in_namespace("net_error")] == ["ok"]


async def test_learned_rejects_bad_names_and_non_json(
    db: Database, learned: LearnedParamStore
) -> None:
    with pytest.raises(ValueError, match="namespace"):
        await learned.put("Bad Name", "k", {})
    with pytest.raises(ValueError, match="key"):
        await learned.put(NS_ML_STATE, "", {})
    with pytest.raises(ValueError, match="key"):
        await learned.get(NS_ML_STATE, "x" * 201)
    with pytest.raises(ValueError, match="namespace"):
        await learned.in_namespace("../x")
    with pytest.raises(ValueError, match=r"NaN|Out of range"):
        await learned.put(NS_ML_STATE, "k", {"x": float("nan")})
    assert await db.fetchone("SELECT COUNT(*) FROM learned_param") == (0,)
