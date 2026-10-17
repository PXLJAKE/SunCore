# SPDX-License-Identifier: Apache-2.0
"""Ordered SQL migrations of ``timeseries.db`` (same file format as ``storage.migrations``).

``timeseries.db`` has its own ``PRAGMA user_version``; it is excluded from backups and can be
rebuilt, so a database that is newer than the code is left untouched and not opened.
"""
