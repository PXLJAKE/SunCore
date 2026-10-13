# SPDX-License-Identifier: Apache-2.0
"""Deterministic virtual house for the simulator (BRIEF 4.14).

Fixed time step, time injected (the house never reads a wall clock), seeded
noise. Sign convention as in ``core``: grid + = import, battery + = charging,
PV + = generation. The energy balance ``pv + grid == load + battery`` closes
every step by construction: the grid is the balance.

The battery power is the AC terminal power. Round-trip efficiency is split
evenly between charging and discharging (``sqrt(eta)`` each way), so losses
show up in the SoC, not in the balance.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_DEFAULT_LOAD_PROFILE_W: tuple[float, ...] = (
    250, 220, 210, 210, 220, 300, 550, 700, 500, 400, 380, 450,
    600, 500, 380, 380, 450, 700, 1000, 1100, 900, 650, 450, 320,
)  # fmt: skip


class PVParams(BaseModel):
    """PV array: peak power, location and performance ratio."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kwp: float = Field(default=8.0, ge=0, le=1000)
    latitude: float = Field(default=48.0, ge=-66, le=66)
    longitude: float = Field(default=11.0, ge=-180, le=180)
    performance_ratio: float = Field(default=0.85, gt=0, le=1)


class LoadParams(BaseModel):
    """Base load: 24 hourly values (local clock of the start time) plus noise."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hourly_w: tuple[float, ...] = Field(
        default=_DEFAULT_LOAD_PROFILE_W, min_length=24, max_length=24
    )
    noise_fraction: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _non_negative(self) -> LoadParams:
        if any(v < 0 for v in self.hourly_w):
            msg = "hourly_w must not contain negative values"
            raise ValueError(msg)
        return self


class BatteryParams(BaseModel):
    """Battery; ``capacity_wh == 0`` means no battery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capacity_wh: float = Field(default=10_000.0, ge=0)
    max_charge_w: float = Field(default=5_000.0, ge=0)
    max_discharge_w: float = Field(default=5_000.0, ge=0)
    round_trip_efficiency: float = Field(default=0.92, gt=0, le=1)
    soc_min_pct: float = Field(default=10.0, ge=0, le=100)
    soc_max_pct: float = Field(default=100.0, ge=0, le=100)
    initial_soc_pct: float = Field(default=50.0, ge=0, le=100)

    @model_validator(mode="after")
    def _bounds(self) -> BatteryParams:
        if self.soc_min_pct > self.soc_max_pct:
            msg = "soc_min_pct must be <= soc_max_pct"
            raise ValueError(msg)
        if not self.soc_min_pct <= self.initial_soc_pct <= self.soc_max_pct:
            msg = "initial_soc_pct must lie within [soc_min_pct, soc_max_pct]"
            raise ValueError(msg)
        return self


class HouseParams(BaseModel):
    """All parameters of the virtual house."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pv: PVParams = PVParams()
    load: LoadParams = LoadParams()
    battery: BatteryParams = BatteryParams()
    seed: int = 0


class HouseState(BaseModel):
    """One simulated step (state at ``time``, constant over the step)."""

    model_config = ConfigDict(frozen=True)

    time: AwareDatetime
    pv_w: float
    load_w: float
    battery_w: float
    soc_pct: float
    grid_w: float
    cloud_factor: Annotated[float, Field(ge=0, le=1)]


def sin_elevation(when: datetime, latitude: float, longitude: float) -> float:
    """Sine of the solar elevation (simple model: no equation of time, no refraction)."""
    utc = when.astimezone(UTC)
    day_of_year = utc.timetuple().tm_yday
    declination = math.radians(23.44) * math.sin(2 * math.pi * (284 + day_of_year) / 365)
    solar_hours = (
        utc.hour + utc.minute / 60 + (utc.second + utc.microsecond / 1e6) / 3600 + longitude / 15
    )
    hour_angle = math.radians(15 * (solar_hours - 12))
    lat = math.radians(latitude)
    return math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(
        hour_angle
    )


def clear_sky_w(when: datetime, pv: PVParams) -> float:
    """Clear-sky PV power in W: ``kWp * PR * max(0, sin(elevation))``."""
    return (
        pv.kwp
        * 1000
        * pv.performance_ratio
        * max(0.0, sin_elevation(when, pv.latitude, pv.longitude))
    )


class House:
    """Stateful virtual house; call :meth:`step` once per time step.

    Args:
        params: house parameters (including the noise seed).
        start: tz-aware time of the first step.
        step_s: fixed step length in seconds.
        cloud: cloud factor per step (1.0 = clear sky); the last value is
            held beyond the end, an empty sequence means clear sky.
    """

    def __init__(
        self,
        params: HouseParams,
        start: datetime,
        step_s: int,
        cloud: Sequence[float] = (),
    ) -> None:
        if start.tzinfo is None or start.utcoffset() is None:
            msg = "start must be timezone-aware"
            raise ValueError(msg)
        if step_s <= 0:
            msg = "step_s must be positive"
            raise ValueError(msg)
        if any(not 0.0 <= c <= 1.0 for c in cloud):
            msg = "cloud factors must lie in [0, 1]"
            raise ValueError(msg)
        self.params = params
        self.start = start
        self.step_s = step_s
        self._cloud = tuple(cloud)
        self._rng = random.Random(params.seed)
        self._index = 0
        self._soc_pct = params.battery.initial_soc_pct

    @property
    def index(self) -> int:
        """Index of the next step to be simulated."""
        return self._index

    @property
    def time(self) -> datetime:
        """Time of the next step (injected start + index * step)."""
        return self.start + timedelta(seconds=self._index * self.step_s)

    def _cloud_at(self, index: int) -> float:
        if not self._cloud:
            return 1.0
        return self._cloud[min(index, len(self._cloud) - 1)]

    def _load_w(self, when: datetime) -> float:
        p = self.params.load
        base = p.hourly_w[when.hour]
        if p.noise_fraction == 0:
            return base
        return max(0.0, base * (1 + self._rng.uniform(-p.noise_fraction, p.noise_fraction)))

    def _dispatch(self, surplus_w: float) -> float:
        """Battery AC power (+ = charging) absorbing surplus / covering deficit."""
        b = self.params.battery
        if b.capacity_wh <= 0:
            return 0.0
        hours = self.step_s / 3600
        leg = math.sqrt(b.round_trip_efficiency)
        if surplus_w >= 0:
            room_wh = (b.soc_max_pct - self._soc_pct) / 100 * b.capacity_wh
            limit = min(b.max_charge_w, room_wh / (hours * leg))
            power = min(surplus_w, max(0.0, limit))
            self._soc_pct += power * hours * leg / b.capacity_wh * 100
        else:
            avail_wh = (self._soc_pct - b.soc_min_pct) / 100 * b.capacity_wh
            limit = min(b.max_discharge_w, avail_wh * leg / hours)
            power = -min(-surplus_w, max(0.0, limit))
            self._soc_pct += power * hours / leg / b.capacity_wh * 100
        self._soc_pct = min(b.soc_max_pct, max(b.soc_min_pct, self._soc_pct))
        return power

    def step(self) -> HouseState:
        """Simulate one step and advance the clock."""
        when = self.time
        cloud = self._cloud_at(self._index)
        pv_w = clear_sky_w(when, self.params.pv) * cloud
        load_w = self._load_w(when)
        battery_w = self._dispatch(pv_w - load_w)
        state = HouseState(
            time=when,
            pv_w=pv_w,
            load_w=load_w,
            battery_w=battery_w,
            soc_pct=self._soc_pct,
            grid_w=load_w + battery_w - pv_w,
            cloud_factor=cloud,
        )
        self._index += 1
        return state

    def run(self, steps: int) -> Iterator[HouseState]:
        """Yield ``steps`` consecutive states."""
        for _ in range(steps):
            yield self.step()
