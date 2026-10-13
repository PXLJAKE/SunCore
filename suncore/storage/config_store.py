# SPDX-License-Identifier: Apache-2.0
"""Versioned configuration documents with per-section migrations and JSON export/import."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from suncore import __version__
from suncore.core.config import (
    DevicesConfig,
    GoalsConfig,
    OperationConfig,
    RegionConfig,
    TariffConfig,
    WizardState,
)
from suncore.core.site import SiteConfig, migrate_site_v1_to_v2
from suncore.storage.db import Database, Row, Transaction
from suncore.storage.errors import (
    ConfigCorruptError,
    HistoryEntryNotFoundError,
    ImportRejectedError,
    UnknownSectionError,
)
from suncore.storage.migrations import latest_version

EXPORT_FORMAT: Final = 1
"""Value of the ``suncore_export`` header field."""

HISTORY_LIMIT: Final = 20
"""Previous bodies kept per section."""

Body = dict[str, Any]
DocMigration = Callable[[Body], Body]
"""Upgrades a body dict by exactly one schema version."""


@dataclass(frozen=True)
class SectionSpec:
    """A configuration section: its model and the migrations from version 1 upwards.

    ``migrations[i]`` upgrades version ``i + 1`` to ``i + 2``; the current
    version is therefore ``len(migrations) + 1``.
    """

    model: type[BaseModel]
    migrations: tuple[DocMigration, ...] = field(default=())

    @property
    def current_version(self) -> int:
        return len(self.migrations) + 1


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A replaced section body."""

    id: int
    section: str
    schema_version: int
    replaced_at: str
    body: Body


DEFAULT_SECTIONS: Final[Mapping[str, SectionSpec]] = {
    "site": SectionSpec(SiteConfig, (migrate_site_v1_to_v2,)),
    "region": SectionSpec(RegionConfig),
    "tariff": SectionSpec(TariffConfig),
    "devices": SectionSpec(DevicesConfig),
    "goals": SectionSpec(GoalsConfig),
    "operation": SectionSpec(OperationConfig),
    "wizard": SectionSpec(WizardState),
}


def _now() -> datetime:
    return datetime.now(UTC)


class ConfigStore:
    """Typed access to ``config_doc``."""

    def __init__(
        self,
        db: Database,
        sections: Mapping[str, SectionSpec] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._db = db
        self._sections = dict(sections if sections is not None else DEFAULT_SECTIONS)
        self._clock = clock

    def _spec(self, section: str) -> SectionSpec:
        try:
            return self._sections[section]
        except KeyError:
            msg = f"unknown config section {section!r}"
            raise UnknownSectionError(msg) from None

    def _upgrade(self, section: str, version: int, body: Body) -> BaseModel:
        """Migrate ``body`` from ``version`` to current and validate it."""
        spec = self._spec(section)
        if version < 1 or version > spec.current_version:
            msg = (
                f"section {section!r}: unsupported schema_version {version} "
                f"(supported 1..{spec.current_version})"
            )
            raise ConfigCorruptError(msg)
        try:
            for step in spec.migrations[version - 1 :]:
                body = step(dict(body))
            body = {**body, "schema_version": spec.current_version}
            return spec.model.model_validate(body)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            msg = f"section {section!r} is invalid: {exc}"
            raise ConfigCorruptError(msg) from exc

    @staticmethod
    def _dump(model: BaseModel) -> str:
        return model.model_dump_json(by_alias=True)

    async def get(self, section: str) -> BaseModel | None:
        """Return the validated (and migrated) section, or ``None`` if never stored."""
        self._spec(section)
        row = await self._db.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = ?", (section,)
        )
        if row is None:
            return None
        return self._parse_row(section, row)

    def _parse_row(self, section: str, row: Row) -> BaseModel:
        version, raw = row
        try:
            body = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            msg = f"section {section!r}: stored body is not JSON"
            raise ConfigCorruptError(msg) from exc
        if not isinstance(body, dict):
            msg = f"section {section!r}: stored body is not an object"
            raise ConfigCorruptError(msg)
        return self._upgrade(section, int(str(version)), body)

    def _try_parse(self, section: str, row: Row | None) -> BaseModel | None:
        """Parse a stored row; a corrupt one counts as absent (it still goes to the history)."""
        if row is None:
            return None
        try:
            return self._parse_row(section, row)
        except ConfigCorruptError:
            return None

    async def put(self, section: str, model: BaseModel) -> BaseModel:
        """Store a section and keep the previous body in the history.

        The model must be the section's type at the current version. Putting ``devices`` also
        removes the ``operation`` mode entries of devices that are new (shadow).
        """
        spec = self._spec(section)
        if not isinstance(model, spec.model):
            msg = f"section {section!r} expects {spec.model.__name__}, got {type(model).__name__}"
            raise TypeError(msg)
        version = getattr(model, "schema_version", spec.current_version)
        if version != spec.current_version:
            msg = (
                f"section {section!r}: model schema_version {version} "
                f"!= current {spec.current_version}"
            )
            raise ValueError(msg)
        async with self._db.transaction() as tx:
            now = self._clock().isoformat()
            previous_ids = (
                await self._stored_device_ids(tx) if isinstance(model, DevicesConfig) else None
            )
            await self._write(tx, section, model, now)
            if isinstance(model, DevicesConfig) and previous_ids is not None:
                await self._shadow_new_devices(tx, model, previous_ids, now)
        return model

    async def _stored_device_ids(self, tx: Transaction) -> frozenset[str]:
        row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'devices'"
        )
        stored = self._try_parse("devices", row)
        return stored.ids() if isinstance(stored, DevicesConfig) else frozenset()

    async def _write(self, tx: Transaction, section: str, model: BaseModel, now: str) -> None:
        spec = self._spec(section)
        old = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = ?", (section,)
        )
        body = self._dump(model)
        if old is not None and (int(str(old[0])), str(old[1])) != (spec.current_version, body):
            await self._record(tx, section, old, now)
        await tx.execute(
            "INSERT INTO config_doc (section, schema_version, body, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(section) DO UPDATE SET "
            "schema_version = excluded.schema_version, body = excluded.body, "
            "updated_at = excluded.updated_at",
            (section, spec.current_version, body, now),
        )

    async def _shadow_new_devices(
        self, tx: Transaction, devices: DevicesConfig, previous_ids: frozenset[str], now: str
    ) -> None:
        """Devices whose id did not exist before start in shadow: drop their stored mode."""
        row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'operation'"
        )
        operation = self._try_parse("operation", row)
        if not isinstance(operation, OperationConfig):
            return
        cleaned = operation.without_modes_of(devices.ids() - previous_ids)
        if cleaned is not operation:
            await self._write(tx, "operation", cleaned, now)

    @staticmethod
    async def _record(tx: Transaction, section: str, row: Row, now: str) -> None:
        """Move a replaced ``(schema_version, body)`` into the history and prune it."""
        await tx.execute(
            "INSERT INTO config_history (section, schema_version, body, replaced_at) "
            "VALUES (?, ?, ?, ?)",
            (section, int(str(row[0])), str(row[1]), now),
        )
        await tx.execute(
            "DELETE FROM config_history WHERE section = ? AND id NOT IN ("
            "SELECT id FROM config_history WHERE section = ? ORDER BY id DESC LIMIT ?)",
            (section, section, HISTORY_LIMIT),
        )

    async def history(self, section: str) -> list[HistoryEntry]:
        """Replaced bodies of ``section``, newest first (at most :data:`HISTORY_LIMIT`)."""
        self._spec(section)
        rows = await self._db.fetchall(
            "SELECT id, schema_version, body, replaced_at FROM config_history "
            "WHERE section = ? ORDER BY id DESC",
            (section,),
        )
        return [
            HistoryEntry(int(str(i)), section, int(str(v)), str(t), json.loads(str(b)))
            for i, v, b, t in rows
        ]

    async def restore(self, section: str, entry_id: int) -> BaseModel:
        """Make a history entry the current body again (the current body goes to history)."""
        self._spec(section)
        row = await self._db.fetchone(
            "SELECT schema_version, body FROM config_history WHERE section = ? AND id = ?",
            (section, entry_id),
        )
        if row is None:
            msg = f"section {section!r} has no history entry {entry_id}"
            raise HistoryEntryNotFoundError(msg)
        return await self.put(section, self._parse_row(section, row))

    async def stored_sections(self) -> frozenset[str]:
        """Names of the sections that have a stored body."""
        rows = await self._db.fetchall("SELECT section FROM config_doc")
        return frozenset(str(r[0]) for r in rows)

    async def get_site(self) -> SiteConfig | None:
        model = await self.get("site")
        assert model is None or isinstance(model, SiteConfig)
        return model

    async def put_site(self, site: SiteConfig) -> None:
        await self.put("site", site)

    async def export_json(self) -> dict[str, Any]:
        """Return all stored sections in the export format (JSON-serialisable)."""
        rows = await self._db.fetchall(
            "SELECT section, schema_version, body FROM config_doc ORDER BY section"
        )
        sections: dict[str, Any] = {}
        for section, version, raw in rows:
            sections[str(section)] = {
                "schema_version": int(str(version)),
                "body": json.loads(str(raw)),
            }
        return {
            "suncore_export": EXPORT_FORMAT,
            "app_version": __version__,
            "db_schema_version": latest_version(),
            "sections": sections,
        }

    def _validate_import(self, data: object) -> dict[str, BaseModel]:
        if not isinstance(data, dict):
            msg = "import: top level must be an object"
            raise ImportRejectedError(msg)
        fmt = data.get("suncore_export")
        if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt != EXPORT_FORMAT:
            msg = f"import: unsupported or missing suncore_export header: {fmt!r}"
            raise ImportRejectedError(msg)
        db_ver = data.get("db_schema_version")
        if not isinstance(db_ver, int) or isinstance(db_ver, bool):
            msg = "import: db_schema_version missing or not an integer"
            raise ImportRejectedError(msg)
        if db_ver > latest_version():
            msg = f"import: db_schema_version {db_ver} is newer than supported {latest_version()}"
            raise ImportRejectedError(msg)
        raw_sections = data.get("sections")
        if not isinstance(raw_sections, dict):
            msg = "import: `sections` must be an object"
            raise ImportRejectedError(msg)
        result: dict[str, BaseModel] = {}
        for name, doc in raw_sections.items():
            try:
                self._spec(name)
                if not isinstance(doc, dict):
                    msg = f"section {name!r} must be an object"
                    raise ImportRejectedError(msg)
                version, body = doc.get("schema_version"), doc.get("body")
                if not isinstance(version, int) or isinstance(version, bool):
                    msg = f"section {name!r}: schema_version missing or not an integer"
                    raise ImportRejectedError(msg)
                if not isinstance(body, dict):
                    msg = f"section {name!r}: body must be an object"
                    raise ImportRejectedError(msg)
                result[name] = self._upgrade(name, version, body)
            except (UnknownSectionError, ConfigCorruptError) as exc:
                msg = f"import: {exc}"
                raise ImportRejectedError(msg) from exc
        return result

    async def import_json(self, data: object) -> list[str]:
        """Validate everything, then replace all sections atomically.

        Returns the imported section names. Any problem raises
        :class:`ImportRejectedError` and leaves the database untouched.
        """
        validated = self._validate_import(data)
        now = self._clock().isoformat()
        async with self._db.transaction() as tx:
            old_rows = await tx.fetchall("SELECT section, schema_version, body FROM config_doc")
            old = {str(r[0]): (r[1], r[2]) for r in old_rows}
            prev_devices = self._try_parse("devices", old.get("devices"))
            previous_ids = (
                prev_devices.ids() if isinstance(prev_devices, DevicesConfig) else frozenset()
            )
            new_devices = validated.get("devices")
            operation = validated.get("operation")
            if isinstance(new_devices, DevicesConfig) and isinstance(operation, OperationConfig):
                validated["operation"] = operation.without_modes_of(
                    new_devices.ids() - previous_ids
                )
            elif isinstance(operation, OperationConfig):  # no devices imported: all are new
                validated["operation"] = operation.without_modes_of(
                    frozenset(operation.device_modes)
                )
            stored = {name: self._dump(model) for name, model in validated.items()}
            for name, row in old.items():
                if (int(str(row[0])), str(row[1])) != (
                    self._spec(name).current_version,
                    stored.get(name),
                ):
                    await self._record(tx, name, row, now)
            await tx.execute("DELETE FROM config_doc")
            for name, body in stored.items():
                await tx.execute(
                    "INSERT INTO config_doc (section, schema_version, body, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (name, self._spec(name).current_version, body, now),
                )
        return sorted(validated)
