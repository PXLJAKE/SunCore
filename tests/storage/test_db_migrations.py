# SPDX-License-Identifier: Apache-2.0
"""Database opening and migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from suncore.storage import Database, MigrationError, SchemaTooNewError
from suncore.storage.migrations import Migration, latest_version, load_migrations, run_migrations


async def test_fresh_db_is_migrated_wal_and_fk(tmp_path: Path) -> None:
    async with await Database.open(tmp_path / "s.db") as db:
        assert await db.user_version() == latest_version() >= 1
        mode = await db.fetchone("PRAGMA journal_mode")
        fk = await db.fetchone("PRAGMA foreign_keys")
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
    assert mode == ("wal",)
    assert fk == (1,)
    assert {"config_doc", "decision", "learned"} <= tables


async def test_migration_from_v0_empty_fixture(tmp_path: Path) -> None:
    path = tmp_path / "v0.db"
    sqlite3.connect(path).close()  # empty file, user_version 0
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version()
        assert await db.fetchone("SELECT COUNT(*) FROM config_doc") == (0,)


async def test_reopen_keeps_data_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    async with await Database.open(path) as db:
        await db.execute("INSERT INTO learned VALUES ('k', '1', 't')")
    async with await Database.open(path) as db:
        assert await db.fetchone("SELECT value FROM learned") == ("1",)


async def test_newer_db_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {latest_version() + 1}")
    con.close()
    with pytest.raises(SchemaTooNewError) as info:
        await Database.open(path)
    assert info.value.db_version == latest_version() + 1
    # untouched
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (latest_version() + 1,)
    con.close()


async def test_failing_migration_rolls_back(tmp_path: Path) -> None:
    bad = [
        Migration(1, "0001_a.sql", "CREATE TABLE a (x INTEGER);"),
        Migration(2, "0002_b.sql", "CREATE TABLE b (x INTEGER); SELECT nonsense FROM nowhere;"),
    ]
    async with aiosqlite.connect(tmp_path / "x.db", isolation_level=None) as conn:
        with pytest.raises(MigrationError):
            await run_migrations(conn, bad)
        async with conn.execute("PRAGMA user_version") as cur:
            assert await cur.fetchone() == (1,)
        async with conn.execute("SELECT name FROM sqlite_master WHERE name='b'") as cur:
            assert await cur.fetchone() is None


def test_shipped_migrations_are_contiguous() -> None:
    assert [m.version for m in load_migrations()] == list(range(1, latest_version() + 1))


async def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    async with await Database.open(tmp_path / "s.db") as db:
        with pytest.raises(RuntimeError):
            await _failing_tx(db)
        assert await db.fetchone("SELECT COUNT(*) FROM learned") == (0,)
        assert await db.check()
    assert not await db.check()


async def _failing_tx(db: Database) -> None:
    async with db.transaction() as tx:
        await tx.execute("INSERT INTO learned VALUES ('k', '1', 't')")
        raise RuntimeError
