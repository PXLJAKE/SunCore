# SPDX-License-Identifier: Apache-2.0
"""Typed storage errors."""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all storage errors."""


class SchemaTooNewError(StorageError):
    """The database was written by a newer SunCore than this one."""

    def __init__(self, db_version: int, code_version: int) -> None:
        super().__init__(
            f"database schema version {db_version} is newer than the supported {code_version}"
        )
        self.db_version = db_version
        self.code_version = code_version


class MigrationError(StorageError):
    """A migration failed or the migration set is inconsistent."""


class UnknownSectionError(StorageError):
    """The configuration section is not registered."""


class ConfigCorruptError(StorageError):
    """A stored configuration document does not validate."""


class ImportRejectedError(StorageError):
    """An import file was rejected; nothing was changed."""
