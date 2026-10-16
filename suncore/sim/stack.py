# SPDX-License-Identifier: Apache-2.0
"""The full control stack on a fake Home Assistant: the reference composition (Phase 3 WP8).

The composition itself lives in :class:`suncore.app.control.ControlStack` since Phase 3 WP9: the
production runtime and the simulator run the *same* class, so the closed-loop scenarios test what
ships. This module keeps the simulator's names (``ControlStack``, ``SimConnection``,
``DeviceActuator``) and :func:`demo_stack`, the helper that wires the stack on a connected
``FakeHAConnection`` and its simulated house.

Time is injected twice: ``clock`` (the simulated now) and ``sleep`` (the gate's settle wait and
the tick period); nothing here sleeps on its own. ``await stack.tick()`` is one controller tick; a
tick may *block* on simulated time (a release waits for the device's service call), so the owner
runs it as a task and keeps the clock moving - ``suncore.sim.runner`` does exactly that, the demo
(``suncore.app.main``) uses :meth:`ControlStack.run`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Final, Protocol

from suncore.app.control import ControlStack, DeviceActuator
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings
from suncore.core.devices import DeviceConfig
from suncore.ha.connection import HAConnection, ServiceCallerProvider
from suncore.safety.modes import ModeProvider
from suncore.safety.runtime import RuntimeStore

DEFAULT_SLOW_CADENCE_S: Final = 300.0


class SimConnection(HAConnection, ServiceCallerProvider, Protocol):
    """A connection the stack can run on: reads, entity updates and the write capability."""


def demo_stack(
    connection: SimConnection,
    devices: Sequence[DeviceConfig],
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
    modes: ModeProvider,
    settings: ControlSettings | None = None,
    store: RuntimeStore | None = None,
    goals: GoalsConfig | None = None,
) -> ControlStack:
    """The stack on a connected ``FakeHAConnection`` and its simulated house (demo mode, WP9).

    The site is the fake's own (``demo_site_config``), the time zone and the slow-sensor cadences
    come from the connection's scenario and devices. ``clock`` must be the *simulated* now
    (``connection.sim_time``, or an accelerated clock), ``sleep`` the gate's settle wait in the same
    time base. Drive it with ``await stack.tick()`` every ``settings.tick_s`` of simulated time;
    ``modes`` decides which device writes (shadow = "would have" entries only), nothing here
    reaches Home Assistant - the fake executes the writes against the simulated house.
    """
    from suncore.sim.fake_ha import FakeHAConnection, demo_site_config  # noqa: PLC0415

    assert isinstance(connection, FakeHAConnection), "demo_stack runs on the simulator only"
    tz = connection.scenario.start.tzinfo
    assert tz is not None
    return ControlStack(
        connection,
        site=demo_site_config(),
        devices=devices,
        clock=clock,
        sleep=sleep,
        settings=settings if settings is not None else ControlSettings(),
        modes=modes,
        store=store,
        tz=tz,
        goals=goals,
        cadences=connection.demo.devices.heartbeats(),
    )


__all__ = [
    "DEFAULT_SLOW_CADENCE_S",
    "ControlStack",
    "DeviceActuator",
    "SimConnection",
    "demo_stack",
]
