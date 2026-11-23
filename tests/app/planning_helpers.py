# SPDX-License-Identifier: Apache-2.0
"""Helpers for the planner runtime tests: a fake clock, a collector on the sim scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from suncore.app.planning import LiveReading, PlanningService, PlanningSettings, PlanningSnapshot
from suncore.core.control import Plan
from suncore.planner.run import PlannerRun
from suncore.sim.planner_scenarios import SUMMER, TZ, situation, situation_inputs

START = datetime(SUMMER.year, SUMMER.month, SUMMER.day, 6, 0, tzinfo=TZ)


@dataclass
class Clock:
    now: datetime = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class World:
    """What the collector and the live reading see; tests change it between ticks."""

    clock: Clock = field(default_factory=Clock)
    soc_pct: float = 30.0
    grid_w: float = 0.0
    complete: bool = True
    ready: bool = True
    collect_calls: int = 0
    collect_error: Exception | None = None

    async def collect(self, now: datetime, previous: Plan | None) -> PlanningSnapshot:
        self.collect_calls += 1
        if self.collect_error is not None:
            raise self.collect_error
        if not self.ready:
            return PlanningSnapshot(None)
        base = replace(situation("sunny_day"), now=now, soc_pct=self.soc_pct)
        inputs = situation_inputs(base)
        if previous is not None:
            inputs = replace(inputs, previous=previous)
        return PlanningSnapshot(inputs, complete=self.complete)

    def live(self) -> LiveReading:
        return LiveReading(soc_pct=self.soc_pct, grid_w=self.grid_w)


def service(
    world: World,
    *,
    settings: PlanningSettings | None = None,
    **kw: object,
) -> PlanningService:
    args: dict[str, object] = {"clock": world.clock, "settings": settings or PlanningSettings()}
    args.update(kw)
    return PlanningService(world.collect, world.live, **args)  # type: ignore[arg-type]


def broken_solver(*_a: object) -> PlannerRun:
    msg = "boom"
    raise RuntimeError(msg)
