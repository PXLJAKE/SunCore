# SPDX-License-Identifier: Apache-2.0
"""SunCore storage: SQLite (WAL) via aiosqlite, ADR-0004."""

from __future__ import annotations

from suncore.storage.config_store import ConfigStore, HistoryEntry, SectionSpec
from suncore.storage.db import Database
from suncore.storage.decisions import DecisionLog
from suncore.storage.errors import (
    ConfigCorruptError,
    HistoryEntryNotFoundError,
    ImportRejectedError,
    MigrationError,
    SchemaTooNewError,
    StorageError,
    UnknownSectionError,
)
from suncore.storage.runtime import PowerModelStateStore, SqliteRuntimeStore

__all__ = [
    "ConfigCorruptError",
    "ConfigStore",
    "Database",
    "DecisionLog",
    "HistoryEntry",
    "HistoryEntryNotFoundError",
    "ImportRejectedError",
    "MigrationError",
    "PowerModelStateStore",
    "SchemaTooNewError",
    "SectionSpec",
    "SqliteRuntimeStore",
    "StorageError",
    "UnknownSectionError",
]
