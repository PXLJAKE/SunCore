# SPDX-License-Identifier: Apache-2.0
"""Wiring the actuators: where the write capability of a connection meets the drivers.

Boundary rule 3 keeps the Home Assistant write capability (``ServiceCaller``) inside
``suncore.ha`` and ``suncore.safety``. The composition root (``suncore.app``) therefore never
names it: it passes the *connection* to :func:`build_actuators` together with a driver factory
(``suncore.devices.drivers.create_driver``), and this module - which is part of ``safety`` - takes
the narrow ``write`` callable out of the connection and hands it to the factory. The resulting
actuators go straight into :class:`~suncore.safety.gate.SafetyGate`, the only object that may call
``apply()``/``release()``.

``safety`` imports only ``core`` (boundary rule 4): the connection is recognised structurally
(:class:`WriteSource`), the driver is injected. A connection without the capability (a read-only
test double) yields actuators whose writes fail loudly with :class:`NoWriteCapabilityError`; the
gate records that as a failed write and trips the device after three of them. Shadow and assisted
mode never write, so they work either way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

import structlog

from suncore.core.control import DeviceState
from suncore.core.devices import DeviceConfig
from suncore.safety.actuator import Actuator

log = structlog.get_logger(__name__)


class WriteOutcome(Protocol):
    """The part of a service-call result the drivers use (structurally ``ha.ServiceResult``)."""

    @property
    def context_id(self) -> str | None: ...


class WriterFn(Protocol):
    """``ServiceCaller.write``: one rate-limited service call returning its context id."""

    async def __call__(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
    ) -> WriteOutcome: ...


class NoWriteCapabilityError(RuntimeError):
    """The connection offers no write capability; nothing was sent."""


class _Caller(Protocol):
    async def write(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
    ) -> WriteOutcome: ...


@runtime_checkable
class WriteSource(Protocol):
    """A connection that hands out the write capability (structurally ``ServiceCallerProvider``)."""

    def service_caller(self) -> _Caller: ...


class StatefulActuator(Actuator, Protocol):
    """An :class:`Actuator` that also reports its device state from the entity cache.

    The drivers of ``suncore.devices.drivers`` satisfy it; the controller's tick reads
    :meth:`device_state` (reads only - writes stay with the gate).
    """

    def device_state(self) -> DeviceState: ...


async def _no_capability(
    domain: str,  # noqa: ARG001
    service: str,  # noqa: ARG001
    *,
    service_data: Mapping[str, Any] | None = None,  # noqa: ARG001
    target: Mapping[str, Any] | None = None,  # noqa: ARG001
) -> WriteOutcome:
    msg = "the connection has no write capability"
    raise NoWriteCapabilityError(msg)


def writer_of(source: object) -> WriterFn:
    """The ``write`` callable of ``source`` (an inert failing one without the capability)."""
    if isinstance(source, WriteSource):
        return source.service_caller().write
    log.warning("safety.no_write_capability", source=type(source).__name__)
    return _no_capability


def build_actuators[A: Actuator](
    source: object,
    devices: Iterable[DeviceConfig],
    make: Callable[[DeviceConfig, WriterFn], A],
) -> dict[str, A]:
    """One actuator per device, built by ``make(device, write)`` with the connection's writer."""
    write = writer_of(source)
    return {device.id: make(device, write) for device in devices}


__all__ = [
    "NoWriteCapabilityError",
    "StatefulActuator",
    "WriteOutcome",
    "WriteSource",
    "WriterFn",
    "build_actuators",
    "writer_of",
]
