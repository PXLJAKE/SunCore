# SPDX-License-Identifier: Apache-2.0
"""The wire model of ``GET /api/v1/diagnostics``, the anonymised bug-report export.

BRIEF §5 and plan WP7.

The document is built from an allowlist of fields (``suncore.app.diagnostics``): entity ids,
device names and HA area names are replaced by pseudonyms that are salted per export, secrets
are masked, and coordinates, tokens, passwords and MQTT credentials never appear. A value that
cannot be read is ``null`` and its path is named in ``unavailable``.
"""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

DIAGNOSTICS_SCHEMA_VERSION = 1
"""Bumped when a field changes meaning; added fields are not a bump."""


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagVersions(_Out):
    app: str
    ha_core: str | None
    """Home Assistant core version (``get_config`` key ``version``); ``null`` if not read yet."""
    python: str
    schemas: dict[str, int | None]
    """``suncore.db``, ``timeseries.db`` (file and code), decision and diagnostics schema."""
    config_sections: dict[str, int | None]
    """``schema_version`` of every stored configuration section (``null`` = none stored)."""


class DiagArchitecture(_Out):
    machine: str
    """CPU architecture (``platform.machine()``, e.g. ``aarch64``)."""
    system: str
    cpu_count: int | None
    supervised: bool
    demo: bool
    operating_mode: str


class DiagReason(_Out):
    key: str
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)


class DiagRegistryCounts(_Out):
    entities: int
    devices: int
    areas: int


class DiagConnection(_Out):
    ha_state: str
    last_update_age_s: float | None
    db_ok: bool
    registry: DiagRegistryCounts | None
    """Entries of the Home Assistant registries SunCore read; ``null`` before the first read."""
    mqtt_state: str | None
    """State of the MQTT entity surface; ``null`` in demo mode."""
    mqtt_reason_key: str | None


class DiagMappedEntity(_Out):
    entity: str
    """Pseudonym (the domain is kept)."""
    domain: str
    area: str | None
    device: str | None
    known: bool
    """The entity has a state in the cache."""
    age_s: float | None
    stale: bool | None


class DiagWatchdogScope(_Out):
    scope: str
    """``site`` or a device pseudonym."""
    state: str
    faults: list[str]
    """Fault kinds (``stale``, ``unavailable``, ``jump`` ...), no entity ids."""


class DiagWatchdog(_Out):
    connected: bool | None
    control_active: bool | None
    emergency_stop: bool | None
    scopes: list[DiagWatchdogScope]


class DiagPlanRun(_Out):
    last_trigger: str | None
    last_outcome: str | None
    last_started_at: AwareDatetime | None
    last_finished_at: AwareDatetime | None
    last_ok_at: AwareDatetime | None
    consecutive_failures: int
    running: bool
    pending: list[str]
    next_scheduled_at: AwareDatetime | None
    reason: DiagReason | None


class DiagSolver(_Out):
    source: str | None
    """Who made the current plan (``lp``, ``heuristic`` ...)."""
    status: str | None
    solve_time_s: float | None
    last_duration_s: float | None
    intervals: int
    stale: bool
    in_use: bool
    created_at: AwareDatetime | None
    reasons: list[DiagReason]


class DiagPlanner(_Out):
    run: DiagPlanRun | None
    solver: DiagSolver | None


class DiagTimeseriesSize(_Out):
    file_bytes: int
    wal_bytes: int
    free_bytes: int
    total_bytes: int
    rows: dict[str, int]


class DiagTimeseries(_Out):
    state: str
    """``ok``, ``too_new`` (written by a newer SunCore, left untouched), ``unavailable``
    (absent or unopenable) or ``unreadable``."""
    file_schema_version: int | None
    code_schema_version: int | None
    size: DiagTimeseriesSize | None


class DiagDecision(_Out):
    ts: AwareDatetime
    kind: str
    mode: str
    origin: str
    asset: str
    """Device pseudonym, or ``battery`` / ``inverter``."""
    action: dict[str, Any]
    observed: dict[str, Any] | None
    reasons: list[DiagReason]
    intent_reasons: list[DiagReason]
    expires_at: AwareDatetime | None
    held_until: AwareDatetime | None


class DiagLogLine(_Out):
    ts: AwareDatetime
    level: str
    logger: str
    event: str
    fields: dict[str, str | int | float | bool | None]
    exception: str | None


class DiagnosticsOut(_Out):
    """The whole export. Share it in a bug report; nothing in it names the installation."""

    schema_version: int
    generated_at: AwareDatetime
    versions: DiagVersions
    architecture: DiagArchitecture
    configuration: dict[str, Any]
    """Every stored section through the field allowlist: numbers and flags as they are, enum
    values and template ids kept, entity ids, device ids and names replaced, everything else
    ``<omitted>``; secrets ``<masked>``."""
    mapped_entities: list[DiagMappedEntity]
    connection: DiagConnection
    watchdog: DiagWatchdog
    planner: DiagPlanner
    timeseries: DiagTimeseries
    decisions: list[DiagDecision]
    """The newest 200 decisions, newest first."""
    logs: list[DiagLogLine]
    """The newest 500 warning and error lines, oldest first, redacted."""
    pseudonyms: str
    """How to read the pseudonyms (fixed text, no value)."""
    unavailable: list[str]
    """Paths whose value is ``null`` because no source provides it."""
