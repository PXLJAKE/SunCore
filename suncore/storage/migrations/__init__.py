# SPDX-License-Identifier: Apache-2.0
"""Ordered SQL migrations; the schema version is ``PRAGMA user_version``.

Files are named ``NNNN_description.sql`` (contiguous from 0001). Each file
runs in one transaction together with the version bump.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable

import aiosqlite

from suncore.storage.errors import MigrationError, SchemaTooNewError

_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations(package: Traversable | None = None) -> list[Migration]:
    """Read the migration files, validating numbering."""
    root = package if package is not None else resources.files(__package__)
    found: list[Migration] = []
    for entry in root.iterdir():
        m = _NAME_RE.match(entry.name)
        if m:
            found.append(Migration(int(m.group(1)), entry.name, entry.read_text("utf-8")))
    found.sort(key=lambda mig: mig.version)
    for expected, mig in enumerate(found, start=1):
        if mig.version != expected:
            msg = f"migration numbering gap or duplicate at {mig.name}"
            raise MigrationError(msg)
    return found


def latest_version() -> int:
    """Schema version this code produces."""
    migs = load_migrations()
    return migs[-1].version if migs else 0


async def run_migrations(
    conn: aiosqlite.Connection, migrations: list[Migration] | None = None
) -> int:
    """Apply pending migrations; return the resulting version.

    Raises :class:`SchemaTooNewError` if the database is newer than the code.
    The connection must be in autocommit mode (``isolation_level=None``).
    """
    migs = migrations if migrations is not None else load_migrations()
    target = migs[-1].version if migs else 0
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row else 0
    if current > target:
        raise SchemaTooNewError(current, target)
    for mig in migs:
        if mig.version <= current:
            continue
        script = f"BEGIN IMMEDIATE;\n{mig.sql}\nPRAGMA user_version = {int(mig.version)};\nCOMMIT;"
        try:
            await conn.executescript(script)
        except BaseException as exc:
            if conn.in_transaction:
                await conn.execute("ROLLBACK")
            if not isinstance(exc, Exception):
                raise
            msg = f"migration {mig.name} failed: {exc}"
            raise MigrationError(msg) from exc
    return target
