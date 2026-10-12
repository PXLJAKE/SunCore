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
from suncore.core.site import SiteConfig
from suncore.storage.db import Database
from suncore.storage.errors import (
    ConfigCorruptError,
    ImportRejectedError,
    UnknownSectionError,
)
from suncore.storage.migrations import latest_version

EXPORT_FORMAT: Final = 1
"""Value of the ``suncore_export`` header field."""

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


DEFAULT_SECTIONS: Final[Mapping[str, SectionSpec]] = {"site": SectionSpec(SiteConfig)}


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
        return model.model_dump_json()

    async def get(self, section: str) -> BaseModel | None:
        """Return the validated (and migrated) section, or ``None`` if never stored."""
        self._spec(section)
        row = await self._db.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = ?", (section,)
        )
        if row is None:
            return None
        return self._parse_row(section, row)

    def _parse_row(self, section: str, row: tuple[object, ...]) -> BaseModel:
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

    async def put(self, section: str, model: BaseModel) -> None:
        """Store a section; the model must be the section's type at the current version."""
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
        await self._db.execute(
            "INSERT INTO config_doc (section, schema_version, body, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(section) DO UPDATE SET "
            "schema_version = excluded.schema_version, body = excluded.body, "
            "updated_at = excluded.updated_at",
            (section, spec.current_version, self._dump(model), self._clock().isoformat()),
        )

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
            await tx.execute("DELETE FROM config_doc")
            for name, model in validated.items():
                await tx.execute(
                    "INSERT INTO config_doc (section, schema_version, body, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (name, self._spec(name).current_version, self._dump(model), now),
                )
        return sorted(validated)
