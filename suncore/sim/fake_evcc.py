# SPDX-License-Identifier: Apache-2.0
"""A fake evcc for tests, the simulator and the demo (PR 6-J, plan WP10).

:class:`FakeEvccBus` is the in-memory counterpart of ``suncore.app.evcc_link.EvccLink``: the same
narrow bus the evcc driver sees (``value``, ``alive``, ``publish_set`` - :class:`~suncore.devices.
evcc.EvccBus`) plus the listener feed (:class:`~suncore.devices.evcc.EvccEvents`), without a broker.
The simulated loadpoints (:class:`suncore.sim.devices.SimLoadpoint`, evcc's own regulation and the
car) publish their retained values here and receive the ``<topic>/set`` messages as evcc's MQTT
setter would (``server/mqtt_setter.go``: a written value is applied and published back).

What it models, from the evcc sources read on 2026-11-24 (see ``suncore.devices.evcc``):

* ``<prefix>/loadpoints/<id>/<key>`` values as text (booleans ``true``/``false``, floats with up to
  three decimals) and the heartbeat ``<prefix>/updated`` (Unix seconds);
* ``publish_set`` accepts only the three keys SunCore may write; anything else (notably
  ``<prefix>/site/*``) is **recorded and refused**, so a test can prove nothing else was sent;
* a ``silent`` evcc stops publishing and refuses writes (``alive`` turns false after the age
  limit, like a dead evcc whose retained values stay on the broker).

Everything is deterministic; time is injected.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from suncore.devices.evcc import HEARTBEAT_MAX_AGE_S, EvccTopicListener

WRITABLE_KEYS: Final = ("mode", "maxCurrent", "limitSoc")
"""What SunCore may set on a loadpoint (``suncore.devices.evcc``)."""

SetHandler = Callable[[str, str], bool]
"""``(key, payload) -> accepted`` of one loadpoint."""


class FakeEvccBus:
    """See the module docstring; implements ``EvccBus`` and ``EvccEvents``."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._values: dict[str, str] = {}
        self._beats: dict[str, datetime] = {}
        self._handlers: dict[str, SetHandler] = {}
        self._listeners: list[EvccTopicListener] = []
        self.silent = False
        """evcc is down: nothing is published, writes are refused (a test or fault sets it)."""
        self.sent: list[tuple[str, str]] = []
        """Every ``publish_set`` that was accepted for delivery, in order."""
        self.refused: list[tuple[str, str]] = []
        """Every ``publish_set`` the fake refused (silent evcc, unknown topic, bad key)."""

    # --- the evcc side (the simulated loadpoints) -----------------------------------------

    def register(self, prefix: str, loadpoint_id: int, handler: SetHandler) -> None:
        self._handlers[f"{prefix}/loadpoints/{loadpoint_id}"] = handler

    def publish(self, prefix: str, loadpoint_id: int, key: str, payload: str) -> None:
        """evcc publishes one retained value (nothing while it is silent)."""
        if self.silent:
            return
        topic = f"{prefix}/loadpoints/{loadpoint_id}/{key}"
        if self._values.get(topic) == payload:
            return
        self._values[topic] = payload
        for listener in list(self._listeners):
            listener(topic)

    def beat(self, prefix: str) -> None:
        """evcc's heartbeat ``<prefix>/updated`` (the time of its latest publication)."""
        if not self.silent:
            self._beats[prefix] = self._clock()

    # --- EvccBus ----------------------------------------------------------------------------

    def value(self, topic: str) -> str | None:
        return self._values.get(topic)

    def alive(self, prefix: str) -> bool:
        beat = self._beats.get(prefix)
        if beat is None:
            return False
        return (self._clock() - beat).total_seconds() <= HEARTBEAT_MAX_AGE_S

    async def publish_set(self, topic: str, payload: str) -> bool:
        if self.silent or not topic.endswith("/set"):
            self.refused.append((topic, payload))
            return False
        base, _, key = topic.removesuffix("/set").rpartition("/")
        handler = self._handlers.get(base)
        if handler is None or key not in WRITABLE_KEYS:
            self.refused.append((topic, payload))
            return False
        self.sent.append((topic, payload))
        handler(key, payload)  # evcc may ignore a bad value (it logs an error), like the real one
        return True

    # --- EvccEvents -------------------------------------------------------------------------

    def add_listener(self, listener: EvccTopicListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove


__all__ = ["WRITABLE_KEYS", "FakeEvccBus", "SetHandler"]
