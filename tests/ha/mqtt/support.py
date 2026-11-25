# SPDX-License-Identifier: Apache-2.0
"""Shared fakes for the MQTT tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from suncore.core.devices import DeviceKind
from suncore.ha.mqtt import (
    ALL_ENTITIES,
    DeviceInfo,
    InstallRecord,
    SiteEvent,
    Topology,
)

SUPPORTED_READ_ONLY = frozenset(s.key for s in ALL_ENTITIES if not s.command)

DEVICES = (
    DeviceInfo("heater", "Heater", DeviceKind.SWITCH),
    DeviceInfo("car", "Car charger", DeviceKind.WALLBOX),
    DeviceInfo("washer", "Washer", DeviceKind.PROGRAM),
    DeviceInfo("ac", "Air conditioner", DeviceKind.CLIMATE),
)


def topology(**kw: Any) -> Topology:
    base: dict[str, Any] = {
        "devices": DEVICES,
        "has_battery": True,
        "supported": SUPPORTED_READ_ONLY,
        "currency": "EUR",
        "version": "9.9.9",
    }
    base.update(kw)
    return Topology(**base)


class FakeSource:
    """An ``EntitySource`` with settable values."""

    def __init__(self, topo: Topology | None = None, language: str = "en") -> None:
        self.topo = topo if topo is not None else topology()
        self.lang = language
        self.data: dict[str, object] = {}
        self.events: list[SiteEvent] = []

    def topology(self) -> Topology:
        return self.topo

    def language(self) -> str:
        return self.lang

    def values(self) -> Mapping[str, object]:
        return dict(self.data)

    def drain_events(self) -> list[SiteEvent]:
        out, self.events = self.events, []
        return out


class MemoryStore:
    def __init__(self, record: InstallRecord | None = None) -> None:
        self.record = record
        self.saves = 0

    async def load(self) -> InstallRecord | None:
        return self.record

    async def save(self, record: InstallRecord) -> None:
        self.record = record
        self.saves += 1
