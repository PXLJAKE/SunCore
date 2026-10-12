# SPDX-License-Identifier: Apache-2.0
"""Simulator scenarios as data files (BRIEF 4.14).

A scenario bundles house parameters, a start time, a duration, a step, a cloud
series and a fault list. Faults are data only here; the fake HA connection
applies them later. Scenario files live in ``suncore/sim/scenarios`` as YAML.
"""

from __future__ import annotations

from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from suncore.sim.house import House, HouseParams

SimSignal = Literal["grid_power", "pv_power", "battery_power", "battery_soc", "load_power"]
FaultKind = Literal["unavailable", "stale", "disconnect"]


class CloudPoint(BaseModel):
    """Cloud factor (1 = clear, 0 = fully overcast) valid from ``at_s`` seconds after start."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    at_s: int = Field(ge=0)
    factor: float = Field(ge=0, le=1)


class Fault(BaseModel):
    """A fault window ``[from_s, to_s)`` in seconds after the scenario start.

    ``target`` is ignored for ``disconnect`` (the whole connection drops).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FaultKind
    target: SimSignal | None = None
    from_s: int = Field(ge=0)
    to_s: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> Fault:
        if self.to_s <= self.from_s:
            msg = "to_s must be greater than from_s"
            raise ValueError(msg)
        if self.kind != "disconnect" and self.target is None:
            msg = f"fault kind {self.kind!r} needs a target"
            raise ValueError(msg)
        return self


class Scenario(BaseModel):
    """A complete simulator scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    start: AwareDatetime
    duration_s: int = Field(gt=0)
    step_s: int = Field(default=60, gt=0)
    house: HouseParams = HouseParams()
    clouds: tuple[CloudPoint, ...] = ()
    faults: tuple[Fault, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Scenario:
        if self.duration_s < self.step_s:
            msg = "duration_s must be at least one step"
            raise ValueError(msg)
        times = [c.at_s for c in self.clouds]
        if times != sorted(set(times)):
            msg = "clouds must be strictly ascending by at_s"
            raise ValueError(msg)
        return self

    @property
    def steps(self) -> int:
        """Number of whole steps in the scenario."""
        return self.duration_s // self.step_s

    def cloud_series(self) -> list[float]:
        """Cloud factor per step (piecewise constant, 1.0 before the first point)."""
        series: list[float] = []
        pending = list(self.clouds)
        current = 1.0
        for i in range(self.steps):
            t = i * self.step_s
            while pending and pending[0].at_s <= t:
                current = pending.pop(0).factor
            series.append(current)
        return series

    def build_house(self) -> House:
        """A fresh :class:`House` for this scenario."""
        return House(self.house, self.start, self.step_s, self.cloud_series())

    def active_faults(self, offset_s: int) -> list[Fault]:
        """Faults active ``offset_s`` seconds after the scenario start."""
        return [f for f in self.faults if f.from_s <= offset_s < f.to_s]


_PACKAGE = "suncore.sim.scenarios"


def list_scenarios() -> list[str]:
    """Names of the bundled scenarios (file stems), sorted."""
    return sorted(
        p.name.removesuffix(".yaml")
        for p in resources.files(_PACKAGE).iterdir()
        if p.name.endswith(".yaml")
    )


def parse_scenario(text: str) -> Scenario:
    """Parse scenario YAML text (safe loader)."""
    data: Any = yaml.safe_load(text)
    return Scenario.model_validate(data)


def load_scenario(name: str) -> Scenario:
    """Load a bundled scenario by name (without extension).

    Raises:
        FileNotFoundError: unknown scenario name.
    """
    if not name.replace("_", "").isalnum():
        msg = f"invalid scenario name: {name!r}"
        raise FileNotFoundError(msg)
    res = resources.files(_PACKAGE).joinpath(f"{name}.yaml")
    if not res.is_file():
        msg = f"unknown scenario: {name!r}"
        raise FileNotFoundError(msg)
    return parse_scenario(res.read_text(encoding="utf-8"))
