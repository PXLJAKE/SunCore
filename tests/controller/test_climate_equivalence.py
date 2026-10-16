# SPDX-License-Identifier: Apache-2.0
"""One climate unit, measured power vs. an hourly energy counter only (plan §1.3, R2).

Both runs use the real power models (``create_power_model``) behind the real gate. The
counter is the LG ThinQ shape: integer Wh, published once per hour. The counter-derived
estimate is old and uncertain (``uncertainty`` grows to the full span within one update
period), so the controller widens its band by that uncertainty and the gate counts a
shift away from the base as ``nominal - estimate.lower``.

Documented tolerance (``docs`` in the PR):

* ample surplus (``free >= nominal + span + hysteresis``, here PV 2500 W): the same
  behaviour - the hourly mean shift differs by at most ``EQUIV_SHIFT_C`` in every hour,
  the unit is back at its base within ``BACKOFF_S`` of the cloud in both runs, and
  neither run gets a rejection from the gate;
* marginal surplus (PV 1700 W): the counter-only unit may shift *less* (never more) than
  the measured one and never causes more grid import.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.decisions import OperatingMode
from suncore.core.devices import DeviceConfig, PowerModel, PowerModelKind
from suncore.core.signals import Sample
from suncore.core.site import EntityRef
from suncore.core.units import Unit
from suncore.devices.power import create_power_model
from tests.controller.sim import Sim, build_sim
from tests.safety.helpers import T0, climate_device

BASE_C = 21.0
NOMINAL_W = 800.0
HOURS = 6
CLOUD = (2, 3)
"""Hours [start, end) with almost no PV."""
EQUIV_SHIFT_C = 0.5
BACKOFF_S = 300


def device(kind: str) -> DeviceConfig:
    base = climate_device("ac", nominal_w=NOMINAL_W)
    if kind == "measured":
        pm = PowerModel(kind=PowerModelKind.MEASURED, power_role="power", nominal_w=NOMINAL_W)
        entities = {**base.entities, "power": EntityRef(entity_id="sensor.ac_power")}
    else:
        pm = PowerModel(
            kind=PowerModelKind.COUNTER_DERIVED,
            energy_role="energy",
            nominal_w=NOMINAL_W,
            counter_update_s=3600,
        )
        entities = {**base.entities, "energy": EntityRef(entity_id="sensor.ac_energy")}
    return base.model_copy(update={"power_model": pm, "entities": entities})


@dataclass
class ModelFeed:
    """Feeds the real power model the way the composition root will (observe, estimate)."""

    config: DeviceConfig
    counter: bool
    energy_wh: float = 0.0
    published_wh: float = 0.0
    published_ts: datetime = T0
    last: datetime | None = None
    model: object = field(init=False)

    def __post_init__(self) -> None:
        self.model = create_power_model(self.config, power_cadence_s=10)

    def __call__(self, _: str, now: datetime, power_w: float, state: DeviceState) -> PowerEstimate:
        if self.last is not None:
            self.energy_wh += power_w * (now - self.last).total_seconds() / 3600.0
        self.last = now
        if self.counter:
            if now >= self.published_ts + timedelta(hours=1):
                self.published_wh = float(int(self.energy_wh))  # LG: integer Wh, hourly
                self.published_ts = now
            samples = {"energy": Sample(self.published_wh, Unit.WH, self.published_ts)}
        else:
            samples = {"power": Sample(power_w, Unit.W, now)}
        model = self.model
        model.observe(now, state, samples)  # type: ignore[attr-defined]
        return model.estimate(now, state, samples)  # type: ignore[attr-defined, no-any-return]


def pv_profile(clear_w: float) -> Callable[[datetime], float]:
    def pv(now: datetime) -> float:
        hour = (now - T0).total_seconds() / 3600.0
        return 500.0 if CLOUD[0] <= hour < CLOUD[1] else clear_w

    return pv


@dataclass
class Run:
    sim: Sim
    shift_by_hour: dict[int, float]
    """Time-weighted mean shift above the base in °C per hour."""
    back_at_base_s: float | None
    """Seconds after the cloud started until the unit was at its base."""
    import_wh: float


def run(kind: str, clear_w: float, mode: OperatingMode = OperatingMode.AUTOMATIC) -> Run:
    config = device(kind)
    sim = build_sim(
        [config],
        pv=pv_profile(clear_w),
        estimate=ModelFeed(config, counter=kind == "counter"),
        states={"ac": DeviceState(on=True, mode="heat", setpoint_c=BASE_C)},
        mode=mode,
    )
    shift: dict[int, float] = {}
    back: float | None = None
    dt = sim.settings.tick_s
    ticks_per_hour = round(3600 / dt)

    async def go() -> None:
        nonlocal back
        for _ in range(HOURS * ticks_per_hour):
            await sim.tick()
            elapsed = (sim.now - T0).total_seconds()
            hour = int(elapsed // 3600)
            setpoint = sim.devices["ac"].state.setpoint_c or BASE_C
            shift[hour] = shift.get(hour, 0.0) + (setpoint - BASE_C) / ticks_per_hour
            cloud_s = elapsed - CLOUD[0] * 3600
            if back is None and cloud_s >= 0 and setpoint == BASE_C:
                back = cloud_s
            sim.now += timedelta(seconds=dt)

    asyncio.run(go())
    imported = sum(max(0.0, g) * dt / 3600.0 for _, g in sim.grid_log)
    return Run(sim, shift, back, imported)


@pytest.mark.simulator
def test_ample_surplus_behaves_the_same_with_a_counter_only() -> None:
    measured = run("measured", 2500.0)
    counter = run("counter", 2500.0)
    for r in (measured, counter):
        assert r.sim.rejected() == []
        assert r.back_at_base_s is not None
        assert r.back_at_base_s <= BACKOFF_S
        assert r.shift_by_hour[CLOUD[0] + 1 - 1] < 0.1  # the cloudy hour stays at the base
    for hour in range(HOURS):
        diff = abs(measured.shift_by_hour[hour] - counter.shift_by_hour[hour])
        assert diff <= EQUIV_SHIFT_C, (
            f"hour {hour}: {measured.shift_by_hour} vs {counter.shift_by_hour}"
        )


@pytest.mark.simulator
def test_marginal_surplus_counter_only_is_never_more_aggressive() -> None:
    measured = run("measured", 1700.0)
    counter = run("counter", 1700.0)
    assert counter.sim.rejected() == []
    assert measured.sim.rejected() == []
    assert sum(counter.shift_by_hour.values()) <= sum(measured.shift_by_hour.values()) + 1e-9
    assert counter.import_wh <= measured.import_wh + 1e-6


@pytest.mark.simulator
@pytest.mark.slow
@pytest.mark.parametrize("kind", ["measured", "counter"])
def test_import_caused_by_the_shift_is_bounded(kind: str) -> None:
    """Against the shadow run (nothing written): what the shift costs at the cloud edge."""
    controlled = run(kind, 2500.0)
    baseline = run(kind, 2500.0, mode=OperatingMode.SHADOW)
    assert baseline.sim.accepted() == []
    caused = controlled.import_wh - baseline.import_wh
    # Delay (30 s) plus four set-point steps (settle + tick each) at <= nominal power.
    assert caused <= NOMINAL_W * (30 + 4 * 15 + 30) / 3600.0
