# SPDX-License-Identifier: Apache-2.0
"""The actuator protocol: the only way hardware is ever written.

ADR-0011: actuators are dumb - write, read back, release, report. Implementations
(device drivers, ``suncore/devices/drivers``, Phase 3 WP6) satisfy the protocol
structurally and are handed to the :class:`~suncore.safety.gate.SafetyGate`, which is
the only object that may hold them and the only module that may call
:meth:`Actuator.apply` and :meth:`Actuator.release` (enforced by
``tests/safety/test_boundary.py``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from suncore.core.decisions import Action, Intent


@runtime_checkable
class Actuator(Protocol):
    """Write access to one asset.

    Implementations must be idempotent, must never block the event loop and never
    decide - the gate does. They read the device state from the entity cache (never
    ``get_states``) and map it to the :class:`Action` it corresponds to.
    """

    @property
    def asset_id(self) -> str:
        """The asset this actuator writes to."""
        ...

    async def apply(self, intent: Intent) -> str | None:
        """Write the intent's action to the device; return the HA context id if known.

        Only ``suncore.safety.gate`` may call this. Raise on failure; the gate records a
        ``failed`` decision. A successful return proves nothing (HA accepts calls for
        unavailable entities): the gate always reads back after the settle time. The
        context id is positive evidence for "this change was ours" in override detection.
        """
        ...

    async def read_back(self) -> Action | None:
        """The device's current state as the action it corresponds to.

        ``None`` means unknown (unavailable or never seen). The gate compares it with
        the applied action after every write and uses it as the takeover baseline.
        """
        ...

    async def release(self, baseline: Action | None) -> None:
        """Give the device back to the user: restore ``baseline`` if known, stop any
        heartbeat or limit SunCore set. Called on emergency stop, shutdown, mode change
        to shadow, watchdog safe state and crash recovery; must be safe to repeat.
        """
        ...


__all__ = ["Actuator"]
