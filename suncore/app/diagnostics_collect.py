# SPDX-License-Identifier: Apache-2.0
"""Reads the live runtime into :class:`DiagnosticsInputs` and builds the export (PR 7-I).

Every read is guarded: a source that fails leaves its part ``null`` (named in the document's
``unavailable`` list); the export itself never fails because one piece is missing. Nothing here
writes to Home Assistant, the gate or a database.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from suncore.api.diagnostics_models import DiagnosticsOut
from suncore.app.diagnostics import (
    DECISIONS_LIMIT,
    LOG_LIMIT,
    DiagnosticsInputs,
    EntityFacts,
    ScopeFacts,
    build_diagnostics,
)
from suncore.app.logbuffer import LOG_RING
from suncore.core.config import MqttConfig
from suncore.core.decisions import Decision
from suncore.planner.views import PlanStatusView
from suncore.safety.watchdog import SITE_SCOPE
from suncore.storage.timeseries import (
    TS_FILENAME,
    TimeseriesProbe,
    TimeseriesSize,
    probe_timeseries_file,
)

if TYPE_CHECKING:
    from suncore.app.runtime import Runtime

log = structlog.get_logger(__name__)


async def collect_diagnostics(runtime: Runtime) -> DiagnosticsOut:
    """The anonymised diagnostics document of ``runtime`` as of now."""
    inputs = await _inputs(runtime)
    return build_diagnostics(inputs)


async def _inputs(runtime: Runtime) -> DiagnosticsInputs:
    health = await runtime.health()
    control = runtime.control_state()
    secrets, coordinates = await _secrets(runtime)
    snapshot = runtime.catalogue.snapshot
    counts = (len(snapshot.entities), len(snapshot.devices), len(snapshot.areas))
    status = runtime.ha_entities_status()
    scopes, watchdog_connected = _watchdog(runtime)
    probe, size = await _timeseries(runtime)
    return DiagnosticsInputs(
        now=runtime.real_clock(),
        app_version=runtime.version,
        ha_core=runtime.ha_core_version,
        supervised=runtime.supervised,
        demo=runtime.demo,
        operating_mode=runtime.mode,
        config_export=await runtime.config.export_json(),
        secrets=secrets,
        coordinates=coordinates,
        device_names=(
            {} if runtime.devices is None else {d.id: d.name for d in runtime.devices.devices}
        ),
        ha_device_names=[d.name for d in snapshot.devices.values() if d.name],
        ha_area_names=list(snapshot.areas.values()),
        registry_counts=counts if any(counts) else None,
        entity_facts=lambda entity_id: _entity_facts(runtime, entity_id),
        ha_state=health.ha_state,
        last_update_age_s=health.ha_last_update_age_s,
        db_ok=health.db_ok,
        mqtt_state=status.state.value,
        mqtt_reason_key=status.reason_key,
        control_active=control.active,
        emergency_stop=control.emergency_stop,
        watchdog_connected=watchdog_connected,
        watchdog_scopes=scopes,
        plan_status=await _plan_status(runtime),
        timeseries=probe,
        timeseries_size=size,
        timeseries_open=runtime.timeseries is not None,
        decisions=await _decisions(runtime),
        logs=LOG_RING.entries(LOG_LIMIT),
    )


async def _secrets(runtime: Runtime) -> tuple[list[str], list[float]]:
    """The literal values to mask in free text, and the home location (never exported)."""
    secrets: list[str] = []
    try:
        mqtt = await runtime.get_section("mqtt")
    except Exception:
        log.warning("diagnostics.secrets_unreadable")
        mqtt = None
    if isinstance(mqtt, MqttConfig):
        if mqtt.password is not None:
            secrets.append(mqtt.password.get_secret_value())
        secrets.extend(v for v in (mqtt.username, mqtt.host) if v)
    coordinates = [] if runtime.ha_location is None else list(runtime.ha_location)
    return secrets, coordinates


def _entity_facts(runtime: Runtime, entity_id: str) -> EntityFacts:
    cached = runtime.cache.get(entity_id)
    snapshot = runtime.catalogue.snapshot
    entity = snapshot.entities.get(entity_id)
    device = None if entity is None else runtime.catalogue.device(entity.device_id)
    area_id = None if entity is None else entity.area_id
    if area_id is None and device is not None:
        area_id = device.area_id
    return EntityFacts(
        known=cached is not None,
        age_s=runtime.cache.age_s(entity_id),
        stale=None if cached is None else runtime.cache.is_stale(entity_id),
        area_name=runtime.catalogue.area_name(area_id),
        device_name=None if device is None else device.name,
    )


def _watchdog(runtime: Runtime) -> tuple[list[ScopeFacts], bool | None]:
    stack = runtime.stack
    if stack is None:
        return [], None
    now = runtime.clock()
    watchdog = stack.watchdog
    scope_ids = [SITE_SCOPE] + (
        [] if runtime.devices is None else [d.id for d in runtime.devices.devices]
    )
    scopes = [
        ScopeFacts(
            scope=scope,
            state=watchdog.state(scope, now).value,
            faults=tuple(sorted({f.kind.value for f in watchdog.faults(scope)})),
        )
        for scope in scope_ids
    ]
    return scopes, watchdog.connected


async def _plan_status(runtime: Runtime) -> PlanStatusView | None:
    try:
        return await runtime.plan_status()
    except Exception:
        log.warning("diagnostics.plan_status_unavailable")
        return None


async def _decisions(runtime: Runtime) -> list[Decision]:
    try:
        return await runtime.decisions(DECISIONS_LIMIT)
    except Exception:
        log.warning("diagnostics.decisions_unavailable")
        return []


async def _timeseries(runtime: Runtime) -> tuple[TimeseriesProbe | None, TimeseriesSize | None]:
    """The file's state (read-only probe, also for a file this process could not open) and the
    size report of the open database."""
    db = runtime.timeseries_db
    path = db.path if db is not None else runtime.settings.data_dir / TS_FILENAME
    probe: TimeseriesProbe | None = None
    with contextlib.suppress(Exception):
        probe = await asyncio.to_thread(probe_timeseries_file, path)
    size: TimeseriesSize | None = None
    if runtime.timeseries is not None:
        with contextlib.suppress(Exception):
            size = await runtime.timeseries.size_report()
    return probe, size
