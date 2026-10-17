# SPDX-License-Identifier: Apache-2.0
"""Thin async SQLite wrapper (ADR-0004): WAL, foreign keys, explicit transactions."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from suncore.storage.migrations import Migration, run_migrations

SqlParam = str | int | float | bytes | None
Row = tuple[object, ...]


async def _rollback(conn: aiosqlite.Connection) -> None:
    """``ROLLBACK`` that completes even if the awaiting task is cancelled meanwhile.

    Statements of a cancelled caller are still executed by the connection's worker thread, so
    the rollback is queued behind them and shielded; "no transaction is active" is fine.
    """

    async def run() -> None:
        with contextlib.suppress(sqlite3.OperationalError):
            await conn.execute("ROLLBACK")

    task = asyncio.ensure_future(run())
    # Retrieve the outcome even if this await is cancelled: no "exception never retrieved".
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    await asyncio.shield(task)


class Transaction:
    """Statements inside one ``BEGIN IMMEDIATE`` block; obtained from ``Database.transaction``."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: Sequence[SqlParam] = ()) -> int:
        """Run a statement, return the affected row count."""
        cur = await self._conn.execute(sql, params)
        try:
            return cur.rowcount
        finally:
            await cur.close()

    async def executemany(self, sql: str, rows: Iterable[Sequence[SqlParam]]) -> None:
        """Run a statement for every parameter row (one round trip to the SQLite thread)."""
        cur = await self._conn.executemany(sql, rows)
        await asyncio.shield(cur.close())  # closed even if this task is cancelled meanwhile

    async def fetchall(self, sql: str, params: Sequence[SqlParam] = ()) -> list[Row]:
        async with self._conn.execute(sql, params) as cur:
            return [tuple(r) for r in await cur.fetchall()]

    async def fetchone(self, sql: str, params: Sequence[SqlParam] = ()) -> Row | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None


class Database:
    """One connection to a SQLite file (``suncore.db``, ``timeseries.db``), serialised by a lock."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path | str, *, migrations: list[Migration] | None = None) -> Self:
        """Open (creating if needed), configure, and migrate the database.

        ``migrations`` selects the migration set (default: ``suncore.db``'s; the time-series
        database passes its own). A database newer than the set raises ``SchemaTooNewError``.
        """
        db = cls(path)
        await db._connect()
        try:
            await run_migrations(db._connection(), migrations)
        except BaseException:
            await db.close()
            raise
        return db

    async def _connect(self) -> None:
        # isolation_level=None: autocommit; we issue BEGIN/COMMIT ourselves.
        conn = await aiosqlite.connect(self._path, isolation_level=None)
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA busy_timeout=5000")
        except BaseException:
            await conn.close()
            raise
        self._conn = conn

    def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            msg = "database is closed"
            raise RuntimeError(msg)
        return self._conn

    @property
    def path(self) -> str:
        return self._path

    async def close(self) -> None:
        """Close the connection (idempotent)."""
        async with self._lock:
            conn, self._conn = self._conn, None
            if conn is not None:
                await conn.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def execute(self, sql: str, params: Sequence[SqlParam] = ()) -> int:
        async with self._lock:
            return await Transaction(self._connection()).execute(sql, params)

    async def fetchall(self, sql: str, params: Sequence[SqlParam] = ()) -> list[Row]:
        async with self._lock:
            return await Transaction(self._connection()).fetchall(sql, params)

    async def fetchone(self, sql: str, params: Sequence[SqlParam] = ()) -> Row | None:
        async with self._lock:
            return await Transaction(self._connection()).fetchone(sql, params)

    async def executemany(self, sql: str, rows: Iterable[Sequence[SqlParam]]) -> None:
        async with self.transaction() as tx:
            await tx.executemany(sql, rows)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Atomic block: commit on success, roll back on any exception.

        Use only the yielded ``Transaction`` inside the block: the lock is not
        reentrant, so calling ``Database`` methods here would deadlock.
        """
        async with self._lock:
            conn = self._connection()
            try:
                await conn.execute("BEGIN IMMEDIATE")
            except BaseException:
                # A cancelled caller may leave the BEGIN queued in the worker thread: the
                # rollback queues behind it, so it always sees the transaction (if any).
                await _rollback(conn)
                raise
            try:
                yield Transaction(conn)
            except BaseException:
                await _rollback(conn)
                raise
            else:
                await conn.execute("COMMIT")

    async def user_version(self) -> int:
        row = await self.fetchone("PRAGMA user_version")
        return int(row[0]) if row else 0  # type: ignore[call-overload]

    async def check(self) -> bool:
        """Cheap liveness probe for ``/health``."""
        try:
            await self.fetchone("SELECT 1")
        except Exception:
            return False
        return True
