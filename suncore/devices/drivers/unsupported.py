# SPDX-License-Identifier: Apache-2.0
"""Placeholder actuator for the Phase 6 control variants before their drivers exist (PR 6-B).

A device with a ``charger`` or ``loadpoint`` control can be configured from PR 6-B on
(contracts first), but its driver arrives with 6-I and 6-J (``sg_ready`` has its own since 6-K).
The safety gate rejects every action but a release for such a device before mode handling;
this driver is the second
line of defence: it never writes (``apply`` raises before any service call), reports no state
it could be judged by. Its release is a no-op only without a baseline (SunCore never took the
device over); with a baseline it raises, so the gate reports the release ``failed`` and keeps the
runtime (and its baseline) instead of marking a device released that may still be in the state
SunCore last wrote - e.g. after its control kind changed with the same entities while a
handover release failed (control-safety review of 6-B, A1).
"""

from __future__ import annotations

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent
from suncore.core.signals import Quality
from suncore.devices.drivers.base import ActionNotSupportedError, EntityDriver


class UnsupportedDriver(EntityDriver):
    """Writes nothing; ``read_back`` is unknown; ``release`` fails when there is a baseline."""

    async def apply(self, intent: Intent) -> str | None:
        raise self.unsupported(intent)

    async def read_back(self) -> Action | None:
        return None

    def device_state(self) -> DeviceState:
        return DeviceState(quality=Quality.UNAVAILABLE)

    async def release(self, baseline: Action | None) -> None:
        if baseline is None:
            return
        msg = (
            f"{type(self).__name__} cannot restore the {baseline.kind.value} baseline "
            f"of {self.asset_id}"
        )
        raise ActionNotSupportedError(msg)


__all__ = ["UnsupportedDriver"]
