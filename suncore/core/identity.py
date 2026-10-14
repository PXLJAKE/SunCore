# SPDX-License-Identifier: Apache-2.0
"""Device identity: when does a stored device count as "new" (STATUS follow-up F6/A2)?

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

from suncore.core.devices import DeviceConfig


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


__all__ = [
    "DeviceIdentity",
    "device_identity",
    "identity_token",
    "is_new_device",
    "new_device_ids",
]
