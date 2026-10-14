# SPDX-License-Identifier: Apache-2.0
"""Thin async SQLite wrapper (ADR-0004): WAL, foreign keys, explicit transactions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from suncore.storage.migrations import run_migrations

SqlParam = str | int | float | bytes | None
Row = tuple[object, ...]


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

    async def fetchall(self, sql: str, params: Sequence[SqlParam] = ()) -> list[Row]:
        async with self._conn.execute(sql, params) as cur:
            return [tuple(r) for r in await cur.fetchall()]

    async def fetchone(self, sql: str, params: Sequence[SqlParam] = ()) -> Row | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None


class Database:
    """One connection to ``suncore.db``; all access is serialised by a lock."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path | str) -> Self:
        """Open (creating if needed), configure, and migrate the database."""
        db = cls(path)
        await db._connect()
        try:
            await run_migrations(db._connection())
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
            for row in rows:
                await tx.execute(sql, row)

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
                # A cancelled caller may leave the BEGIN running in the worker thread.
                if conn.in_transaction:
                    await conn.execute("ROLLBACK")
                raise
            try:
                yield Transaction(conn)
            except BaseException:
                await conn.execute("ROLLBACK")
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
