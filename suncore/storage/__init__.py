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
from suncore.storage.learned import LearnedParam, LearnedParamStore
from suncore.storage.plans import PlanSnapshot, PlanSnapshotMeta, PlanSnapshotStore
from suncore.storage.runtime import PowerModelStateStore, SqliteRuntimeStore
from suncore.storage.timeseries import (
    TimeseriesStore,
    open_timeseries,
    open_timeseries_or_recover,
)

__all__ = [
    "ConfigCorruptError",
    "ConfigStore",
    "Database",
    "DecisionLog",
    "HistoryEntry",
    "HistoryEntryNotFoundError",
    "ImportRejectedError",
    "LearnedParam",
    "LearnedParamStore",
    "MigrationError",
    "PlanSnapshot",
    "PlanSnapshotMeta",
    "PlanSnapshotStore",
    "PowerModelStateStore",
    "SchemaTooNewError",
    "SectionSpec",
    "SqliteRuntimeStore",
    "StorageError",
    "TimeseriesStore",
    "UnknownSectionError",
    "open_timeseries",
    "open_timeseries_or_recover",
]
