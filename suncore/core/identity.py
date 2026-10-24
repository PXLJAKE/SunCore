# SPDX-License-Identifier: Apache-2.0
"""Device and battery identity: when does a stored asset count as "new" (STATUS F6/A2, ADR-0013)?

A device that is new starts in shadow mode. Comparing ids alone is not enough: a user
who re-uses an id for another template or re-maps its entities has, for the safety
layer, created a new device. The rule is pure and used by storage (put/import/restore
of ``devices`` and ``operation``, Phase 3 WP4) and by the app.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from suncore.core.battery import BATTERY_ASSET_ID, BatterySettings
from suncore.core.devices import DeviceConfig
from suncore.core.site import SiteConfig


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """What makes a device the same device: id, template and the mapped entities."""

    id: str
    template_id: str
    entities: frozenset[tuple[str, str]]
    """``(role, entity_id)`` pairs; ``invert_sign`` is a setting, not identity."""


def device_identity(device: DeviceConfig) -> DeviceIdentity:
    return DeviceIdentity(
        id=device.id,
        template_id=device.template_id,
        entities=frozenset((role, ref.entity_id) for role, ref in device.entities.items()),
    )


def identity_token(device: DeviceConfig) -> str:
    """A short opaque token of the device's identity (id, template, mapped entities).

    The control state carries it; a request that raises a device's operating mode must send it
    back, so a stale UI cannot grant a mode to a device that was re-mapped meanwhile.
    """
    identity = device_identity(device)
    payload = json.dumps(
        [identity.id, identity.template_id, sorted(identity.entities)], separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def is_new_device(device: DeviceConfig, previous: DeviceConfig | None) -> bool:
    """``True`` if ``device`` is not the same device as ``previous`` (or there is none)."""
    return previous is None or device_identity(device) != device_identity(previous)


def new_device_ids(
    previous: Iterable[DeviceConfig], current: Iterable[DeviceConfig]
) -> frozenset[str]:
    """Ids in ``current`` that are new relative to ``previous`` (shadow fallback set)."""
    before = {d.id: d for d in previous}
    return frozenset(d.id for d in current if is_new_device(d, before.get(d.id)))


@dataclass(frozen=True, slots=True)
class BatteryIdentity:
    """What makes the battery the same controlled asset (ADR-0013 decision 6).

    The reserved asset id, the adapter template, the mapped role entities, the site's SoC and
    battery power entities (what the gate judges the battery by) and, for the ``kostal_modbus``
    adapter, the modbus hub and unit id (another unit id writes to another inverter). Settings
    (model, strategy, policy, byte order, timers) are not identity.
    """

    asset_id: str
    template_id: str
    entities: frozenset[tuple[str, str]]
    soc_entity: str | None
    power_entity: str | None
    modbus: tuple[str, int] | None


def battery_identity(
    settings: BatterySettings | None, site: SiteConfig | None
) -> BatteryIdentity | None:
    """The battery's identity, ``None`` when it is observe-only (no adapter, nothing to write)."""
    if settings is None or settings.adapter_template_id is None:
        return None
    site_battery = None if site is None else site.battery
    return BatteryIdentity(
        asset_id=BATTERY_ASSET_ID,
        template_id=settings.adapter_template_id,
        entities=frozenset((role, ref.entity_id) for role, ref in settings.entities.items()),
        soc_entity=None if site_battery is None else site_battery.soc.entity_id,
        power_entity=None if site_battery is None else site_battery.power.entity_id,
        modbus=None if settings.modbus is None else (settings.modbus.hub, settings.modbus.slave),
    )


def battery_identity_token(settings: BatterySettings | None, site: SiteConfig | None) -> str | None:
    """Opaque token of :func:`battery_identity` (``None`` = observe-only, no mode above shadow).

    Raising the battery's mode needs it (``PUT /api/v1/operation/battery``); the store records it
    when the mode is granted and drops the mode when it changes.
    """
    identity = battery_identity(settings, site)
    if identity is None:
        return None
    payload = json.dumps(
        [
            identity.asset_id,
            identity.template_id,
            sorted(identity.entities),
            identity.soc_entity,
            identity.power_entity,
            list(identity.modbus) if identity.modbus is not None else None,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


__all__ = [
    "BatteryIdentity",
    "DeviceIdentity",
    "battery_identity",
    "battery_identity_token",
    "device_identity",
    "identity_token",
    "is_new_device",
    "new_device_ids",
]
