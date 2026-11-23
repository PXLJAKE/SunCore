# SPDX-License-Identifier: Apache-2.0
"""Versioned configuration documents with per-section migrations and JSON export/import."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from pydantic import BaseModel, ValidationError

from suncore import __version__
from suncore.core.battery import BatterySettings
from suncore.core.config import (
    DevicesConfig,
    ForecastConfig,
    GoalsConfig,
    OperationConfig,
    RegionConfig,
    TariffConfig,
    WizardState,
    migrate_forecast_v1_to_v2,
    migrate_goals_v1_to_v2,
    migrate_tariff_v2_to_v3,
)
from suncore.core.decisions import OperatingMode
from suncore.core.devices import DeviceConfig
from suncore.core.identity import battery_identity_token, device_identity, new_device_ids
from suncore.core.site import SiteConfig, migrate_site_v1_to_v2
from suncore.storage.db import Database, Row, Transaction
from suncore.storage.errors import (
    ConfigCorruptError,
    HistoryEntryNotFoundError,
    ImportRejectedError,
    UnknownSectionError,
)
from suncore.storage.migrations import latest_version
from suncore.storage.runtime import KEY_BATTERY_MODE_IDENTITY, KEY_MODE_IDENTITIES

log = structlog.get_logger(__name__)

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


FORECAST_KEYS: Final = ("forecast_template_ids", "forecast_entities")
"""Tariff v1 fields that moved to the ``forecast`` section (tariff v2)."""


def migrate_tariff_v1_to_v2(body: Body) -> Body:
    """Tariff v2: the forecast sources are their own section (see :func:`legacy_forecast`)."""
    return {k: v for k, v in body.items() if k not in FORECAST_KEYS}


def legacy_forecast(body: Body) -> Body | None:
    """The ``forecast`` section body (schema v1) hidden in a tariff v1 body; ``None`` if it had
    no sources. Upgrade it with the ``forecast`` migrations (``_upgrade("forecast", 1, ...)``).
    """
    ids = body.get("forecast_template_ids") or []
    entities = body.get("forecast_entities") or {}
    if not ids and not entities:
        return None
    return {"forecast_template_ids": ids, "forecast_entities": entities}


def migrate_forecast_v1_to_v2_logged(body: Body) -> Body:
    """:func:`migrate_forecast_v1_to_v2` that warns when v1 entities have no template.

    v1 entities without a template id cannot be assigned to a source; the migration drops
    them (the user picks the source again in the wizard), which is logged, never silent.
    """
    ids = body.get("forecast_template_ids") or []
    entities = body.get("forecast_entities") or {}
    if not ids and isinstance(entities, dict) and entities:
        log.warning(
            "config.forecast_v1_entities_without_template",
            roles=sorted(str(role) for role in entities),
        )
    return migrate_forecast_v1_to_v2(body)


DEFAULT_SECTIONS: Final[Mapping[str, SectionSpec]] = {
    "site": SectionSpec(SiteConfig, (migrate_site_v1_to_v2,)),
    "region": SectionSpec(RegionConfig),
    "tariff": SectionSpec(TariffConfig, (migrate_tariff_v1_to_v2, migrate_tariff_v2_to_v3)),
    "forecast": SectionSpec(ForecastConfig, (migrate_forecast_v1_to_v2_logged,)),
    "devices": SectionSpec(DevicesConfig),
    "goals": SectionSpec(GoalsConfig, (migrate_goals_v1_to_v2,)),
    "operation": SectionSpec(OperationConfig),
    "wizard": SectionSpec(WizardState),
    "battery": SectionSpec(BatterySettings),
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

        The model must be the section's type at the current version. Device identity rule
        (STATUS F6/A2, ``core.identity``): putting ``devices`` removes the ``operation`` mode
        entries of devices that are new (id, template or mapped entities changed), so they
        start in shadow. Putting ``operation`` records the identity of every device that
        holds a non-shadow mode; see :meth:`restore` for the history path. Battery (ADR-0013
        decision 6): a ``battery`` or ``site`` put that changes the battery's identity token
        (``core.identity.battery_identity_token``) drops ``operation.battery_mode`` to shadow; an
        ``operation`` put with a non-shadow battery mode records the token it was granted to.
        """
        return await self._put(section, model, restoring=False)

    async def _put(self, section: str, model: BaseModel, *, restoring: bool) -> BaseModel:
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
            if isinstance(model, DevicesConfig):
                previous = await self._stored_devices(tx)
                await self._write(tx, section, model, now)
                await self._shadow_new_devices(tx, previous, model, now)
            elif isinstance(model, OperationConfig):
                devices = await self._stored_devices(tx)
                if restoring:
                    model = await self._drop_changed_identities(tx, model, devices)
                    model = await self._drop_changed_battery(tx, model)
                await self._write(tx, section, model, now)
                await self._record_identities(tx, model, devices, now)
                await self._record_battery_identity(tx, model, now)
            elif isinstance(model, BatterySettings | SiteConfig):
                before = await self._battery_token(tx)
                await self._write(tx, section, model, now)
                if await self._battery_token(tx) != before:
                    await self._shadow_battery(tx, now)
            else:
                await self._write(tx, section, model, now)
        return model

    # --- battery identity (ADR-0013 decision 6) -----------------------------------------

    async def _battery_token(self, tx: Transaction) -> str | None:
        """The stored battery's identity token (``None`` = observe-only or not configured)."""
        battery_row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'battery'"
        )
        site_row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'site'"
        )
        battery = self._try_parse("battery", battery_row)
        site = self._try_parse("site", site_row)
        return battery_identity_token(
            battery if isinstance(battery, BatterySettings) else None,
            site if isinstance(site, SiteConfig) else None,
        )

    @staticmethod
    async def _load_battery_record(tx: Transaction) -> str | None:
        row = await tx.fetchone(
            "SELECT value FROM safety_state WHERE key = ?", (KEY_BATTERY_MODE_IDENTITY,)
        )
        return None if row is None or not row[0] else str(row[0])

    @staticmethod
    async def _save_battery_record(tx: Transaction, token: str | None, now: str) -> None:
        await tx.execute(
            "INSERT INTO safety_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (KEY_BATTERY_MODE_IDENTITY, token or "", now),
        )

    async def _record_battery_identity(
        self, tx: Transaction, operation: OperationConfig, now: str
    ) -> None:
        """A non-shadow battery mode remembers the identity it was granted to (kept after a put
        back to shadow, like the device record, so a later restore of the same battery works)."""
        if operation.battery_mode is OperatingMode.SHADOW:
            return
        await self._save_battery_record(tx, await self._battery_token(tx), now)

    async def _drop_changed_battery(
        self, tx: Transaction, operation: OperationConfig
    ) -> OperationConfig:
        """Restore path: a battery mode granted to another battery identity (or never recorded,
        or to an observe-only battery) falls back to shadow."""
        if operation.battery_mode is OperatingMode.SHADOW:
            return operation
        current = await self._battery_token(tx)
        if current is None or await self._load_battery_record(tx) != current:
            return operation.without_battery_mode()
        return operation

    async def _shadow_battery(self, tx: Transaction, now: str) -> None:
        """The battery's identity changed (``battery`` or ``site`` put/restore): shadow it."""
        await self._save_battery_record(tx, None, now)
        row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'operation'"
        )
        operation = self._try_parse("operation", row)
        if not isinstance(operation, OperationConfig):
            return
        cleaned = operation.without_battery_mode()
        if cleaned is not operation:
            await self._write(tx, "operation", cleaned, now)

    async def _stored_devices(self, tx: Transaction) -> list[DeviceConfig]:
        """Stored devices; a missing or corrupt section counts as "no devices" (all new)."""
        row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'devices'"
        )
        stored = self._try_parse("devices", row)
        return list(stored.devices) if isinstance(stored, DevicesConfig) else []

    @staticmethod
    def _fingerprint(device: DeviceConfig) -> str:
        identity = device_identity(device)
        return json.dumps([identity.template_id, sorted(identity.entities)])

    @staticmethod
    async def _load_identities(tx: Transaction) -> dict[str, str]:
        row = await tx.fetchone(
            "SELECT value FROM safety_state WHERE key = ?", (KEY_MODE_IDENTITIES,)
        )
        if row is None:
            return {}
        try:
            data = json.loads(str(row[0]))
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    @staticmethod
    async def _save_identities(tx: Transaction, record: dict[str, str], now: str) -> None:
        await tx.execute(
            "INSERT INTO safety_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (KEY_MODE_IDENTITIES, json.dumps(record, sort_keys=True), now),
        )

    async def _record_identities(
        self,
        tx: Transaction,
        operation: OperationConfig,
        devices: list[DeviceConfig],
        now: str,
    ) -> None:
        """Remember under which identity each non-shadow mode was granted.

        Records of devices that still exist stay (also after a put back to shadow), so a later
        restore of an older automatic state works for devices that did not change; records of
        changed or removed devices are pruned by the ``devices`` put.
        """
        by_id = {d.id: d for d in devices}
        record = {k: v for k, v in (await self._load_identities(tx)).items() if k in by_id}
        for dev_id, mode in operation.device_modes.items():
            if mode is not OperatingMode.SHADOW and dev_id in by_id:
                record[dev_id] = self._fingerprint(by_id[dev_id])
        await self._save_identities(tx, record, now)

    async def _drop_changed_identities(
        self, tx: Transaction, operation: OperationConfig, devices: list[DeviceConfig]
    ) -> OperationConfig:
        """Restore path: a non-shadow mode of a device that is not the device it was granted
        to (identity changed, or never recorded) falls back to shadow."""
        record = await self._load_identities(tx)
        by_id = {d.id: d for d in devices}
        stale = frozenset(
            dev_id
            for dev_id, mode in operation.device_modes.items()
            if mode is not OperatingMode.SHADOW
            and dev_id in by_id
            and record.get(dev_id) != self._fingerprint(by_id[dev_id])
        )
        return operation.without_modes_of(stale)

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
        self,
        tx: Transaction,
        previous: list[DeviceConfig],
        devices: DevicesConfig,
        now: str,
    ) -> None:
        """Devices that are new by identity start in shadow: drop their stored mode."""
        new_ids = new_device_ids(previous, devices.devices)
        row = await tx.fetchone(
            "SELECT schema_version, body FROM config_doc WHERE section = 'operation'"
        )
        operation = self._try_parse("operation", row)
        record = await self._load_identities(tx)
        kept = {k: v for k, v in record.items() if k in devices.ids() and k not in new_ids}
        if kept != record:
            await self._save_identities(tx, kept, now)
        if not isinstance(operation, OperationConfig):
            return
        cleaned = operation.without_modes_of(new_ids)
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
        """Make a history entry the current body again (the current body goes to history).

        Same identity rule as :meth:`put`; additionally a restored ``operation`` loses the
        non-shadow modes of devices whose identity differs from the one the mode was granted
        to (a mode never outlives a changed device).
        """
        self._spec(section)
        row = await self._db.fetchone(
            "SELECT schema_version, body FROM config_history WHERE section = ? AND id = ?",
            (section, entry_id),
        )
        if row is None:
            msg = f"section {section!r} has no history entry {entry_id}"
            raise HistoryEntryNotFoundError(msg)
        return await self._put(section, self._parse_row(section, row), restoring=True)

    async def stored_sections(self) -> frozenset[str]:
        """Names of the sections that have a stored body."""
        rows = await self._db.fetchall("SELECT section FROM config_doc")
        return frozenset(str(r[0]) for r in rows)

    async def migrate_legacy_forecast(self) -> bool:
        """Move forecast sources out of a stored tariff v1 into the ``forecast`` section.

        Run once at start-up. Without it the v1 -> v2 document migration would drop them on
        the next read. A stored ``forecast`` section wins; the tariff is rewritten at v2
        (its old body goes to the history). Returns whether anything was moved.
        """
        if "tariff" not in self._sections or "forecast" not in self._sections:
            return False
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT schema_version, body FROM config_doc WHERE section = 'tariff'"
            )
            if row is None or int(str(row[0])) != 1:
                return False
            try:
                raw = json.loads(str(row[1]))
            except json.JSONDecodeError:
                return False
            if not isinstance(raw, dict):
                return False
            moved = legacy_forecast(raw)
            have = await tx.fetchone("SELECT 1 FROM config_doc WHERE section = 'forecast'")
            now = self._clock().isoformat()
            if moved is not None and have is None:
                try:
                    forecast: BaseModel | None = self._upgrade("forecast", 1, moved)
                except ConfigCorruptError:
                    forecast = None
                if forecast is not None:
                    await self._write(tx, "forecast", forecast, now)
            try:
                tariff = self._upgrade("tariff", 1, raw)
            except ConfigCorruptError:
                return False  # stays as it is; reads report it
            await self._write(tx, "tariff", tariff, now)
        return True

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
                if name == "tariff" and version == 1 and "forecast" not in raw_sections:
                    moved = legacy_forecast(body)
                    if moved is not None:
                        result["forecast"] = self._upgrade("forecast", 1, moved)
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
            previous = prev_devices.devices if isinstance(prev_devices, DevicesConfig) else []
            new_devices = validated.get("devices")
            operation = validated.get("operation")
            old_record = await self._load_identities(tx)
            record: dict[str, str] = {}
            if isinstance(new_devices, DevicesConfig):
                kept_ids = frozenset(d.id for d in new_devices.devices) - new_device_ids(
                    previous, new_devices.devices
                )
                record = {k: v for k, v in old_record.items() if k in kept_ids}
            if isinstance(operation, OperationConfig):
                # An import never carries trust over: the global mode is shadow afterwards, and so
                # is the battery (ADR-0013: an imported battery is never trusted to write).
                operation = operation.model_copy(
                    update={"mode": OperatingMode.SHADOW, "battery_mode": OperatingMode.SHADOW}
                )
                if isinstance(new_devices, DevicesConfig):
                    operation = operation.without_modes_of(
                        new_device_ids(previous, new_devices.devices)
                    )
                    by_id = {d.id: d for d in new_devices.devices}
                    for dev_id, mode in operation.device_modes.items():
                        if mode is not OperatingMode.SHADOW and dev_id in by_id:
                            record[dev_id] = self._fingerprint(by_id[dev_id])
                else:  # no devices imported: all are new
                    operation = operation.without_modes_of(frozenset(operation.device_modes))
                validated["operation"] = operation
            await self._save_identities(tx, record, now)
            await self._save_battery_record(tx, None, now)
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
