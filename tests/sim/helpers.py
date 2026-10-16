# SPDX-License-Identifier: Apache-2.0
"""Shared helpers of the simulator tests: catalogue, cached scenario runs, a time pump."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from suncore.core.decisions import OperatingMode
from suncore.ha.connection import RateLimiter, ServiceResult
from suncore.sim.fake_ha import SIM_ENTITY_IDS, FakeHAConnection
from suncore.sim.runner import RunResult, ScenarioRunner, VirtualTime
from suncore.sim.scenario import Scenario, load_scenario, parse_scenario
from suncore.templates import TemplateCatalog, load_templates

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


@functools.cache
def catalog() -> TemplateCatalog:
    return load_templates(TEMPLATES)


def run_scenario(
    scenario: Scenario,
    *,
    mode: OperatingMode = OperatingMode.AUTOMATIC,
    duration_s: float | None = None,
    setup: Callable[[ScenarioRunner], None] | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[RunResult, ScenarioRunner]:
    """Play ``scenario`` closed-loop (no wall-clock time passes); returns evidence and runner."""

    async def go() -> tuple[RunResult, ScenarioRunner]:
        runner = ScenarioRunner(scenario, catalog(), global_mode=mode, settings=settings)
        if setup is not None:
            setup(runner)
        try:
            result = await runner.run(duration_s)
        finally:
            await runner.close()
        return result, runner

    return asyncio.run(go())


@functools.cache
def run_named(name: str, *, shadow: bool = False) -> tuple[RunResult, ScenarioRunner]:
    """A bundled scenario in automatic (default) or shadow mode, cached across tests.

    Read-only: the runner is returned closed, for inspecting the gate's runtime afterwards.
    """
    mode = OperatingMode.SHADOW if shadow else OperatingMode.AUTOMATIC
    return run_scenario(load_scenario(name), mode=mode)


def caused_import_wh(name: str) -> tuple[float, float]:
    """``(Wh of import SunCore caused, Wh it moved to the devices)`` against the shadow run.

    The shadow run (same house, same user, nothing written) is the counterfactual: whatever
    import the controlled run has on top of it is attributable to SunCore.
    """
    auto, _ = run_named(name)
    shadow, _ = run_named(name, shadow=True)
    moved = sum(auto.device_wh(d) - shadow.device_wh(d) for d in auto.configs)
    return auto.import_wh() - shadow.import_wh(), moved


def scenario_from(text: str) -> Scenario:
    return parse_scenario(text)


class Pump:
    """A virtual clock plus a fake HA, for tests that call services directly."""

    def __init__(self, scenario: Scenario, **kwargs: Any) -> None:
        self.vt = VirtualTime(scenario.start)
        self.conn = FakeHAConnection(
            scenario,
            clock=self.vt.clock,
            sleep=self.vt.sleep,
            sim_clock=self.vt.clock,
            autorun=False,
            write_limiter=RateLimiter(0.0, clock=self.vt.monotonic, sleep=self.vt.sleep),
            **kwargs,
        )
        self.caller = self.conn.service_caller()

    async def start(self) -> Pump:
        await self.conn.connect()
        await self.conn.subscribe_entities(
            [
                *SIM_ENTITY_IDS,
                *(e for d in self.conn.demo.devices.devices.values() for e in d.entity_ids()),
            ]
        )
        return self

    async def advance(self, seconds: float) -> None:
        await self.vt.advance_to(
            self.vt.now + timedelta(seconds=seconds),
            events=self.conn.next_event_time,
            run_events=self.conn.run_events_until,
        )

    async def call(
        self, domain: str, service: str, entity_id: str | list[str], **data: Any
    ) -> tuple[ServiceResult, float]:
        """One write; returns the result and how long (simulated seconds) it blocked."""
        start = self.vt.now
        task = asyncio.ensure_future(
            self.caller.write(
                domain, service, service_data=data or None, target={"entity_id": entity_id}
            )
        )
        for _ in range(400):
            await self.advance(0.1)
            if task.done():
                break
        result = await task
        return result, (self.vt.now - start).total_seconds()

    def state(self, entity_id: str) -> Any:
        return self.conn._published[entity_id]

    @property
    def now(self) -> datetime:
        return self.vt.now
