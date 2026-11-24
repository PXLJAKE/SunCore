# SPDX-License-Identifier: Apache-2.0
"""Composition of the HA entity surface: what SunCore offers to ``suncore.ha.mqtt`` (WP3).

Read only: the source reads the control state, the flows and the configuration; the install
record lives in ``learned_param`` (namespace ``ha_entities``). Nothing here touches the gate,
the controller or a device.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final

from suncore import __version__
from suncore.core.config import MqttConfig
from suncore.core.decisions import DecisionKind
from suncore.ha.connection import ConnectionState
from suncore.ha.mqtt import (
    DeviceInfo,
    EntitySource,
    HaEntityService,
    InstallRecord,
    SiteEvent,
    Topology,
    supervisor_services_from_env,
)
from suncore.i18n import resolve_language
from suncore.storage.learned import LearnedParamStore

if TYPE_CHECKING:
    from suncore.app.runtime import Runtime

NAMESPACE: Final = "ha_entities"
KEY_INSTALL: Final = "install"

SUPPORTED: Final = frozenset(
    {
        "status",
        "mode",
        "available_surplus",
        "healthy",
        "shadow_mode",
        "emergency_stop",
        "events",
        "device_mode",
        "device_power",
        "device_last_decision",
    }
)
"""Catalogue keys SunCore can fill today. The others (costs, savings, autarky, forecast error,
month peak, grid state, §14a, island, battery action and lease, EV and program entities)
follow with the PRs that produce their values (6-F onwards) and are not announced before."""


class SqliteInstallStore:
    """The install id and the announced device ids, in ``learned_param``."""

    def __init__(self, store: LearnedParamStore) -> None:
        self._store = store

    async def load(self) -> InstallRecord | None:
        row = await self._store.get(NAMESPACE, KEY_INSTALL)
        if row is None:
            return None
        iid = row.payload.get("iid")
        published = row.payload.get("published", [])
        if not isinstance(iid, str) or len(iid) != 8 or not isinstance(published, list):
            return None
        return InstallRecord(iid, tuple(str(p) for p in published))

    async def save(self, record: InstallRecord) -> None:
        await self._store.put(
            NAMESPACE, KEY_INSTALL, {"iid": record.iid, "published": list(record.published)}
        )


class RuntimeEntitySource:
    """``EntitySource`` over the running application."""

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime
        self._seen: dict[str, datetime] = {}
        self._baseline = False

    def topology(self) -> Topology:
        rt = self._rt
        devices = tuple(
            DeviceInfo(d.id, d.name, d.kind) for d in (rt.devices.devices if rt.devices else ())
        )
        return Topology(
            devices=devices,
            has_battery=False,  # battery entities follow in a later PR (see SUPPORTED)
            supported=SUPPORTED,
            currency=rt.region.currency if rt.region is not None else None,
            version=__version__,
        )

    def language(self) -> str:
        rt = self._rt
        if rt.region is not None and rt.region.language != "auto":
            return resolve_language(rt.region.language)
        return resolve_language(rt.ha_language)

    def values(self) -> Mapping[str, object]:
        rt = self._rt
        state = rt.control_state()
        ha_ok = rt.connection.state is ConnectionState.CONNECTED
        if not ha_ok:
            status = "ha_unavailable"
        elif state.emergency_stop:
            status = "emergency_stop"
        elif not state.active:
            status = "starting"
        else:
            status = "running"
        out: dict[str, object] = {
            "status": status,
            "mode": state.global_mode.value,
            "healthy": ha_ok and state.active,
            "shadow_mode": state.global_mode.value == "shadow",
            "emergency_stop": state.emergency_stop,
        }
        if state.site is not None and state.site.available_w is not None:
            out["available_surplus"] = state.site.available_w
        for device in state.devices:
            out[f"{device.device_id}_mode"] = device.effective_mode.value
            if device.estimate is not None:
                out[f"{device.device_id}_power"] = device.estimate.value_w
            last = device.last_decision
            out[f"{device.device_id}_last_decision"] = "none" if last is None else last.kind.value
        return out

    def drain_events(self) -> list[SiteEvent]:
        """``device_switched``, ``proposal`` and ``error`` from the gate's newest decisions."""
        events: list[SiteEvent] = []
        for device in self._rt.control_state().devices:
            last = device.last_decision
            if last is None:
                continue
            known = self._seen.get(device.device_id)
            self._seen[device.device_id] = last.ts
            if not self._baseline or (known is not None and last.ts <= known):
                continue
            kind = {
                DecisionKind.ACCEPTED: "device_switched",
                DecisionKind.PROPOSED: "proposal",
                DecisionKind.FAILED: "error",
            }.get(last.kind)
            if kind is not None:
                events.append(SiteEvent(kind, {"device": device.device_id, "name": device.name}))
        self._baseline = True
        return events


def build_ha_entities(runtime: Runtime) -> HaEntityService | None:
    """The service for ``runtime`` (``None`` in demo mode or without the learned store)."""
    if runtime.demo or runtime.learned is None:
        return None

    async def load_config() -> MqttConfig | None:
        stored = await runtime.config.get("mqtt")
        return stored if isinstance(stored, MqttConfig) else None

    source: EntitySource = RuntimeEntitySource(runtime)
    return HaEntityService(
        source=source,
        load_config=load_config,
        store=SqliteInstallStore(runtime.learned),
        services=supervisor_services_from_env(),
    )
