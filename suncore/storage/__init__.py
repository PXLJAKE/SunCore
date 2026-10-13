# SPDX-License-Identifier: Apache-2.0
"""SunCore storage: SQLite (WAL) via aiosqlite, ADR-0004."""

from __future__ import annotations

from suncore.storage.config_store import ConfigStore, SectionSpec
from suncore.storage.db import Database
from suncore.storage.decisions import DecisionLog
from suncore.storage.errors import (
    ConfigCorruptError,
    ImportRejectedError,
    MigrationError,
    SchemaTooNewError,
    StorageError,
    UnknownSectionError,
)

__all__ = [
    "ConfigCorruptError",
    "ConfigStore",
    "Database",
    "DecisionLog",
    "ImportRejectedError",
    "MigrationError",
    "SchemaTooNewError",
    "SectionSpec",
    "StorageError",
    "UnknownSectionError",
]
