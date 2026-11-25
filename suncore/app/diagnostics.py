# SPDX-License-Identifier: Apache-2.0
"""Builds the anonymised diagnostics document (BRIEF §5, plan WP7, PR 7-I).

Pure: :class:`DiagnosticsInputs` holds everything already read (the runtime gathers it), and
:func:`build_diagnostics` projects it onto :class:`~suncore.api.diagnostics_models.DiagnosticsOut`
through an **allowlist**: a field that is not named below is not in the export. Entity ids, device
ids and names and HA area names go through one :class:`~suncore.app.pseudonyms.Pseudonymizer`
(salted per export), so the pseudonyms agree across configuration, decisions and logs.

Configuration allowlist (:func:`redact_config`):

* numbers, booleans and ``null`` keep their value, except under a coordinate key
  (``latitude``, ``longitude``, ...), which is ``<omitted>``;
* a string keeps its value only under a key of :data:`STRING_KEYS` (enum values, template ids,
  times); ``entity_id`` / ``id`` / ``name`` / ``*entry_id`` values become pseudonyms; every other
  string is ``<omitted>``;
* a key that names a secret (password, token, key, secret, credential) is ``<masked>``; host and
  username say only ``<set>``.
"""

from __future__ import annotations

import math
import os
import platform
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from suncore.api.diagnostics_models import (
    DIAGNOSTICS_SCHEMA_VERSION,
    DiagArchitecture,
    DiagConnection,
    DiagDecision,
    DiagLogLine,
    DiagMappedEntity,
    DiagnosticsOut,
    DiagPlanner,
    DiagPlanRun,
    DiagReason,
    DiagRegistryCounts,
    DiagSolver,
    DiagTimeseries,
    DiagTimeseriesSize,
    DiagVersions,
    DiagWatchdog,
    DiagWatchdogScope,
)
from suncore.app.logbuffer import LogEntry
from suncore.app.pseudonyms import Pseudonymizer
from suncore.core.decisions import DECISION_SCHEMA_VERSION, Action, Decision, Reason
from suncore.planner.views import PlanStatusView
from suncore.storage.timeseries import TimeseriesProbe, TimeseriesSize

DECISIONS_LIMIT: Final = 200
LOG_LIMIT: Final = 500
OMITTED: Final = "<omitted>"
MASKED: Final = "<masked>"
SET: Final = "<set>"

STRING_KEYS: Final = frozenset(
    {
        "kind",
        "mode",
        "battery_mode",
        "inverter_mode",
        "template_id",
        "adapter_template_id",
        "adapter",
        "capability",
        "region_id",
        "currency",
        "language",
        "broker",
        "control",
        "role",
        "unit",
        "power_role",
        "resume_time",
        "time",
        "start",
        "end",
        "risk",
        "strategy",
        "para14a_signal_kind",
        "load_ml_mode",
        "weight_mode",
        "completed_steps",
    }
)
"""String-valued configuration fields that are kept: enum values, template ids, clock times."""
SECRET_WORDS: Final = ("password", "token", "secret", "credential", "api_key", "apikey", "auth")
PRESENCE_KEYS: Final = frozenset({"host", "username", "user", "url", "address"})
COORDINATE_KEYS: Final = frozenset(
    {"latitude", "longitude", "lat", "lon", "lng", "elevation", "altitude", "location", "gps"}
)
OPAQUE_KEYS: Final = frozenset({"id", "name"})
ID_KEYED_DICTS: Final = frozenset({"device_modes"})
"""Dictionaries whose *keys* are device ids."""
ASSET_IDS: Final = frozenset({"battery", "inverter", "site"})
"""Asset ids of the gate that are not devices; they name no installation."""
PSEUDONYM_NOTE: Final = (
    "entity ids, device names and HA area names are replaced by pseudonyms derived with a random "
    "salt that is not part of this document; the same value has the same pseudonym everywhere "
    "in this export and a different one in every other export"
)


# --- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntityFacts:
    """What the entity cache and the HA registry say about one mapped entity."""

    known: bool
    age_s: float | None
    stale: bool | None
    area_name: str | None
    device_name: str | None


@dataclass(frozen=True, slots=True)
class ScopeFacts:
    """The watchdog's view of one scope (``site`` or a device id)."""

    scope: str
    state: str
    faults: tuple[str, ...]


@dataclass(slots=True)
class DiagnosticsInputs:
    """Everything the document is made of, already read; ``None`` = not available."""

    now: datetime
    app_version: str
    ha_core: str | None
    supervised: bool
    demo: bool
    operating_mode: str
    config_export: Mapping[str, Any]
    """``ConfigStore.export_json()``: the stored sections with the MQTT password masked."""
    secrets: Collection[str] = ()
    """Literal secret values to mask in free text (MQTT password, user name, host, ...)."""
    coordinates: Collection[float] = ()
    """The home location, masked wherever it appears in text; never exported."""
    device_names: Mapping[str, str] = field(default_factory=dict)
    """Configured device id to its name."""
    ha_device_names: Collection[str] = ()
    ha_area_names: Collection[str] = ()
    registry_counts: tuple[int, int, int] | None = None
    entity_facts: Callable[[str], EntityFacts] | None = None
    ha_state: str = "unknown"
    last_update_age_s: float | None = None
    db_ok: bool = True
    mqtt_state: str | None = None
    mqtt_reason_key: str | None = None
    control_active: bool | None = None
    emergency_stop: bool | None = None
    watchdog_connected: bool | None = None
    watchdog_scopes: Sequence[ScopeFacts] = ()
    plan_status: PlanStatusView | None = None
    timeseries: TimeseriesProbe | None = None
    timeseries_size: TimeseriesSize | None = None
    timeseries_open: bool = False
    decisions: Sequence[Decision] = ()
    logs: Sequence[LogEntry] = ()
    salt: bytes | None = None


# --- configuration allowlist -----------------------------------------------------------------


class _Walk:
    """State of one :func:`redact_config` run."""

    def __init__(self, pseudo: Pseudonymizer) -> None:
        self.pseudo = pseudo
        self.entities: dict[str, str] = {}
        """Real entity id to pseudonym, in the order of appearance."""

    def value(self, value: Any, key: str) -> Any:
        if isinstance(value, Mapping):
            return self.mapping(value, key)
        if isinstance(value, list | tuple):
            return [self.value(item, key) for item in value]
        return self.scalar(value, key)

    def mapping(self, data: Mapping[str, Any], parent: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for raw_key, item in data.items():
            name = str(raw_key)
            new_key = self._dict_key(name, parent)
            out[new_key] = self.value(item, name)
        return out

    def _dict_key(self, name: str, parent: str) -> str:
        if parent in ID_KEYED_DICTS:
            return self.pseudo.register_device(name)
        return name

    def scalar(self, value: Any, key: str) -> Any:
        """The allowlisted form of one value. Whatever is hidden here is hidden in the decisions
        and the logs too: a hidden string becomes a secret of the scrubber, an opaque id or name
        a registered pseudonym."""
        result = self._rule(value, key)
        if isinstance(value, str):
            if result in {OMITTED, MASKED, SET}:
                self.pseudo.register_secret(value)
            elif result != value and key.lower() in OPAQUE_KEYS:
                self.pseudo.register_literal(value, result)
        return result

    def _rule(self, value: Any, key: str) -> Any:  # noqa: PLR0911 - one rule per branch
        lowered = key.lower()
        if value is None:
            return None
        if any(word in lowered for word in SECRET_WORDS):
            return MASKED
        if lowered in PRESENCE_KEYS:
            return SET
        if lowered in COORDINATE_KEYS:
            return OMITTED
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value if math.isfinite(value) else None
        if not isinstance(value, str):
            return OMITTED
        if lowered == "entity_id":
            self.entities.setdefault(value, self.pseudo.register_entity(value))
            return self.entities[value]
        if lowered == "id":
            return self.pseudo.known_pseudonym(value) or self.pseudo.opaque(value, "id")
        if lowered == "name":
            return self.pseudo.known_pseudonym(value) or self.pseudo.opaque(value, "name")
        if lowered.endswith("entry_id"):
            alias = self.pseudo.opaque(value, "entry")
            self.pseudo.register_literal(value, alias)
            return alias
        if lowered in STRING_KEYS:
            return self.pseudo.text(value)
        return OMITTED


def redact_config(
    sections: Mapping[str, Any], pseudo: Pseudonymizer
) -> tuple[dict[str, Any], dict[str, str]]:
    """The allowlisted configuration and the entity ids found in it (real to pseudonym)."""
    walk = _Walk(pseudo)
    out = {name: walk.value(doc, name) for name, doc in sorted(sections.items())}
    return out, walk.entities


# --- decisions and logs ------------------------------------------------------------------------


def _reason(reason: Reason, pseudo: Pseudonymizer) -> DiagReason:
    args: dict[str, str | int | float | bool] = {}
    for key, value in reason.args.items():
        args[key] = pseudo.text(value) if isinstance(value, str) else value
    return DiagReason(key=reason.key, args=args)


def _action(action: Action | None, pseudo: Pseudonymizer) -> dict[str, Any] | None:
    if action is None:
        return None
    out: dict[str, Any] = {}
    for key, value in action.model_dump(mode="json").items():
        if value is None or isinstance(value, bool | int | float):
            out[key] = value
        elif key in {"kind", "mode", "plan_time"} and isinstance(value, str):
            out[key] = pseudo.text(value)
    return out


def _asset(asset_id: str, pseudo: Pseudonymizer) -> str:
    if asset_id in ASSET_IDS:
        return asset_id
    return pseudo.known_pseudonym(asset_id) or pseudo.register_device(asset_id)


def _decision(decision: Decision, pseudo: Pseudonymizer) -> DiagDecision:
    return DiagDecision(
        ts=decision.ts,
        kind=decision.kind.value,
        mode=decision.mode.value,
        origin=decision.intent.origin.value,
        asset=_asset(decision.intent.asset_id, pseudo),
        action=_action(decision.intent.action, pseudo) or {},
        observed=_action(decision.observed, pseudo),
        reasons=[_reason(r, pseudo) for r in decision.reasons],
        intent_reasons=[_reason(r, pseudo) for r in decision.intent.reasons],
        expires_at=decision.expires_at,
        held_until=decision.held_until,
    )


def _log_field(key: str, value: str | int | float | bool | None, pseudo: Pseudonymizer) -> Any:
    lowered = key.lower()
    if value is None:
        return None
    if any(word in lowered for word in SECRET_WORDS) or lowered in PRESENCE_KEYS:
        return MASKED
    if lowered in COORDINATE_KEYS:
        return OMITTED
    if isinstance(value, str):
        return pseudo.text(value)
    return value


def _log_line(entry: LogEntry, pseudo: Pseudonymizer) -> DiagLogLine:
    return DiagLogLine(
        ts=entry.ts,
        level=entry.level,
        logger=entry.logger,
        event=pseudo.text(entry.event),
        fields={k: _log_field(k, v, pseudo) for k, v in entry.fields.items()},
        exception=None if entry.exception is None else pseudo.text(entry.exception),
    )


# --- the document -----------------------------------------------------------------------------


def _timeseries(inputs: DiagnosticsInputs) -> DiagTimeseries:
    probe = inputs.timeseries
    size = inputs.timeseries_size
    if probe is None:
        state = "ok" if inputs.timeseries_open else "unavailable"
        return DiagTimeseries(
            state=state, file_schema_version=None, code_schema_version=None, size=None
        )
    state = probe.state
    if (state == "ok" and not inputs.timeseries_open) or state == "absent":
        state = "unavailable"
    return DiagTimeseries(
        state=state,
        file_schema_version=probe.file_version,
        code_schema_version=probe.code_version,
        size=None
        if size is None
        else DiagTimeseriesSize(
            file_bytes=size.file_bytes,
            wal_bytes=size.wal_bytes,
            free_bytes=size.free_bytes,
            total_bytes=size.total_bytes,
            rows=dict(size.rows),
        ),
    )


def _planner(status: PlanStatusView | None, pseudo: Pseudonymizer) -> DiagPlanner:
    if status is None:
        return DiagPlanner(run=None, solver=None)
    run = DiagPlanRun(
        last_trigger=status.last_trigger,
        last_outcome=status.last_outcome,
        last_started_at=status.last_started_at,
        last_finished_at=status.last_finished_at,
        last_ok_at=status.last_ok_at,
        consecutive_failures=status.consecutive_failures,
        running=status.running,
        pending=[str(t) for t in status.pending],
        next_scheduled_at=status.next_scheduled_at,
        reason=None if status.reason is None else _reason(status.reason, pseudo),
    )
    plan = status.plan
    solver = DiagSolver(
        source=str(plan.source),
        status=plan.status,
        solve_time_s=plan.solve_time_s,
        last_duration_s=status.last_duration_s,
        intervals=plan.intervals,
        stale=plan.stale,
        in_use=plan.in_use,
        created_at=plan.created_at,
        reasons=[_reason(r, pseudo) for r in plan.reasons],
    )
    return DiagPlanner(run=run, solver=solver)


def _section_versions(export: Mapping[str, Any]) -> dict[str, int | None]:
    sections = export.get("sections")
    out: dict[str, int | None] = {}
    if isinstance(sections, Mapping):
        for name, doc in sorted(sections.items()):
            version = doc.get("schema_version") if isinstance(doc, Mapping) else None
            out[str(name)] = version if isinstance(version, int) else None
    return out


def _unavailable(out: DiagnosticsOut) -> list[str]:
    missing: list[str] = []
    if out.versions.ha_core is None:
        missing.append("versions.ha_core")
    if out.connection.registry is None:
        missing.append("connection.registry")
    if out.connection.mqtt_state is None:
        missing.append("connection.mqtt_state")
    if out.watchdog.connected is None:
        missing.append("watchdog")
    if out.planner.run is None:
        missing.append("planner")
    if out.timeseries.size is None:
        missing.append("timeseries.size")
    if out.timeseries.file_schema_version is None:
        missing.append("timeseries.file_schema_version")
    # The planner view carries no MIP gap or objective value; they are not exported at all.
    missing.append("planner.solver.mip_gap (not exposed by the planner)")
    return missing


def build_diagnostics(inputs: DiagnosticsInputs) -> DiagnosticsOut:
    """Project ``inputs`` onto the export document through the allowlist."""
    pseudo = Pseudonymizer(inputs.salt, secrets=inputs.secrets, coordinates=inputs.coordinates)
    for device_id, name in inputs.device_names.items():
        pseudo.register_device(device_id, name)
    for name in inputs.ha_device_names:
        pseudo.register_ha_device(name)
    for name in inputs.ha_area_names:
        pseudo.register_area(name)

    sections = inputs.config_export.get("sections")
    configuration, entities = redact_config(
        sections if isinstance(sections, Mapping) else {}, pseudo
    )
    mapped: list[DiagMappedEntity] = []
    for real, alias in sorted(entities.items(), key=lambda kv: kv[1]):
        facts = None if inputs.entity_facts is None else inputs.entity_facts(real)
        mapped.append(
            DiagMappedEntity(
                entity=alias,
                domain=real.partition(".")[0],
                area=None
                if facts is None or facts.area_name is None
                else pseudo.register_area(facts.area_name),
                device=None
                if facts is None or facts.device_name is None
                else pseudo.register_ha_device(facts.device_name),
                known=False if facts is None else facts.known,
                age_s=None if facts is None else facts.age_s,
                stale=None if facts is None else facts.stale,
            )
        )

    db_version = inputs.config_export.get("db_schema_version")
    probe = inputs.timeseries
    versions = DiagVersions(
        app=inputs.app_version,
        ha_core=inputs.ha_core,
        python=platform.python_version(),
        schemas={
            "suncore_db": db_version if isinstance(db_version, int) else None,
            "timeseries_db_file": None if probe is None else probe.file_version,
            "timeseries_db_code": None if probe is None else probe.code_version,
            "decision": DECISION_SCHEMA_VERSION,
            "diagnostics": DIAGNOSTICS_SCHEMA_VERSION,
        },
        config_sections=_section_versions(inputs.config_export),
    )
    counts = inputs.registry_counts
    connection = DiagConnection(
        ha_state=inputs.ha_state,
        last_update_age_s=inputs.last_update_age_s,
        db_ok=inputs.db_ok,
        registry=None
        if counts is None
        else DiagRegistryCounts(entities=counts[0], devices=counts[1], areas=counts[2]),
        mqtt_state=inputs.mqtt_state,
        mqtt_reason_key=inputs.mqtt_reason_key,
    )
    watchdog = DiagWatchdog(
        connected=inputs.watchdog_connected,
        control_active=inputs.control_active,
        emergency_stop=inputs.emergency_stop,
        scopes=[
            DiagWatchdogScope(
                scope=s.scope if s.scope == "site" else _asset(s.scope, pseudo),
                state=s.state,
                faults=list(s.faults),
            )
            for s in inputs.watchdog_scopes
        ],
    )
    decisions = [_decision(d, pseudo) for d in inputs.decisions[:DECISIONS_LIMIT]]
    logs = [_log_line(e, pseudo) for e in inputs.logs[-LOG_LIMIT:]]
    out = DiagnosticsOut(
        schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        generated_at=inputs.now,
        versions=versions,
        architecture=DiagArchitecture(
            machine=platform.machine() or "unknown",
            system=platform.system() or sys.platform,
            cpu_count=os.cpu_count(),
            supervised=inputs.supervised,
            demo=inputs.demo,
            operating_mode=inputs.operating_mode,
        ),
        configuration=configuration,
        mapped_entities=mapped,
        connection=connection,
        watchdog=watchdog,
        planner=_planner(inputs.plan_status, pseudo),
        timeseries=_timeseries(inputs),
        decisions=decisions,
        logs=logs,
        pseudonyms=PSEUDONYM_NOTE,
        unavailable=[],
    )
    return out.model_copy(update={"unavailable": _unavailable(out)})
