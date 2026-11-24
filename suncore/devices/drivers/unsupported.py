# SPDX-License-Identifier: Apache-2.0
"""Placeholder actuator for the Phase 6 control variants before their drivers exist (PR 6-B).

A device with a ``charger``, ``sg_ready`` or ``loadpoint`` control can be configured from PR 6-B
on (contracts first), but its driver arrives with 6-I, 6-K and 6-J. The safety gate rejects
every action but a release for such a device before mode handling; this driver is the second
line of defence: it never writes (``apply`` raises before any service call), reports no state
it could be judged by, and its release is a no-op (SunCore never took the device over).
"""

from __future__ import annotations

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent
from suncore.core.signals import Quality
from suncore.devices.drivers.base import EntityDriver


class UnsupportedDriver(EntityDriver):
    """Writes nothing; ``read_back`` is unknown; ``release`` does nothing."""

    async def apply(self, intent: Intent) -> str | None:
        raise self.unsupported(intent)

    async def read_back(self) -> Action | None:
        return None

    def device_state(self) -> DeviceState:
        return DeviceState(quality=Quality.UNAVAILABLE)

    async def release(self, baseline: Action | None) -> None:  # noqa: ARG002 - protocol
        return None


__all__ = ["UnsupportedDriver"]
