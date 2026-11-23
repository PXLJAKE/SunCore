# SPDX-License-Identifier: Apache-2.0
"""A cancelled writer must never leave a transaction open on the shared connection."""

from __future__ import annotations

import asyncio

import pytest

from suncore.storage import Database


@pytest.mark.parametrize("turns", range(8))
async def test_cancelling_a_transaction_leaves_the_database_usable(
    db: Database, turns: int
) -> None:
    await db.execute("CREATE TABLE t (x INTEGER)")
    rows = [(i,) for i in range(5000)]
    task = asyncio.create_task(db.executemany("INSERT INTO t VALUES (?)", rows))
    for _ in range(turns):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # the next writer starts a transaction without "within a transaction" and sees either
    # none or all of the cancelled batch (it was rolled back or committed as a whole)
    await db.executemany("INSERT INTO t VALUES (?)", [(-1,)])
    row = await db.fetchone("SELECT COUNT(*) FROM t")
    assert row in {(1,), (5001,)}


async def test_cancelled_while_waiting_for_the_lock_is_harmless(db: Database) -> None:
    await db.execute("CREATE TABLE t (x INTEGER)")
    async with db.transaction() as tx:
        waiter = asyncio.create_task(db.executemany("INSERT INTO t VALUES (?)", [(1,)]))
        await asyncio.sleep(0)
        waiter.cancel()
        await tx.execute("INSERT INTO t VALUES (7)")
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert await db.fetchone("SELECT COUNT(*) FROM t") == (1,)
