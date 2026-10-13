# SPDX-License-Identifier: Apache-2.0
"""The actuator protocol: the only way hardware is ever written.

ADR-0011: actuators are dumb - write, read back, report. Implementations
live in ``suncore/devices`` (Phase 3) and are handed to the
:class:`~suncore.safety.gate.SafetyGate`, which is the only object that may
hold them and the only module that may call :meth:`Actuator.apply`
(enforced by ``tests/safety/test_boundary.py``). Phase 1 ships no
implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from suncore.core.decisions import Action, Intent


@runtime_checkable
class Actuator(Protocol):
    """Write access to one asset.

    Implementations must be idempotent (write only on change), rate limited
    and must never block the event loop. They never decide - the gate does.
    """

    @property
    def asset_id(self) -> str:
        """The asset this actuator writes to."""
        ...

    async def apply(self, intent: Intent) -> None:
        """Write the intent's action to the device.

        Only ``suncore.safety`` may call this. Raise on failure; the gate
        records the failure and transitions the asset into its safe state.
        """
        ...

    async def read_back(self) -> Action | None:
        """Return the device's current state as the action it corresponds to.

        ``None`` means the state is unknown (stale or unavailable). The gate
        compares it with the last applied action after every write.
        """
        ...


__all__ = ["Actuator"]
