# SPDX-License-Identifier: Apache-2.0
"""Seven simulated days without oscillation (Phase 3 plan section 4).

A week of mixed weather - clear, hazy, overcast, flickering and random-walk days, seeded - with a
10 kWh battery and four devices of different control kinds under the controller (60 s tick, the
largest the settings allow; the 5 s plan default is covered by the single-day scenarios).
The run takes simulated time only; it is marked ``slow`` because it is the longest
simulator test, and runs in CI.

What must hold over the whole week (same criteria as the single-day scenarios, now every day):

* no device exceeds its switches per hour, nothing violates min run / min pause, no on/off cycle is
  shorter than ``min_run_s + min_pause_s``;
* the number of set-point writes per hour stays bounded (no hunting of the air conditioner);
* the user's base set-point does not drift: every night the unit is back at 24;
* the controller does something every day it is sunny, the gate refuses nothing it proposed and
  SunCore causes (almost) no import.
"""

from __future__ import annotations

import itertools
import random
from typing import Any

import pytest

from suncore.core.decisions import DecisionKind, OperatingMode, ReleaseAction, SetpointAction
from suncore.sim.scenario import Scenario
from tests.sim.helpers import run_scenario

pytestmark = [pytest.mark.simulator, pytest.mark.slow]

DAY_S = 86_400
STEP_S = 60
KINDS = ("clear", "hazy", "overcast", "flicker", "mixed", "clear", "hazy")


def clouds(seed: int, days: int) -> list[dict[str, float]]:
    """Cloud factor changes for ``days`` days: a seeded weather generator."""
    rng = random.Random(seed)
    points: list[tuple[int, float]] = []
    for day in range(days):
        kind = KINDS[day % len(KINDS)] if day < len(KINDS) else rng.choice(KINDS)
        base = day * DAY_S
        t = base + 5 * 3600
        end = base + 21 * 3600
        if kind == "clear":
            points.append((t, 1.0))
        elif kind == "hazy":
            while t < end:
                points.append((t, round(rng.uniform(0.5, 0.75), 2)))
                t += rng.randint(1200, 3600)
        elif kind == "overcast":
            while t < end:
                points.append((t, round(rng.uniform(0.1, 0.25), 2)))
                t += rng.randint(1800, 5400)
        elif kind == "flicker":
            sunny = True
            while t < end:
                points.append((t, 0.9 if sunny else 0.25))
                sunny = not sunny
                t += rng.randint(15, 120)
        else:
            while t < end:
                points.append((t, round(rng.choice([0.15, 0.3, 0.5, 0.8, 1.0, 1.0]), 2)))
                t += rng.randint(120, 900)
        points.append((end, 1.0))
    seen: dict[int, float] = {}
    for at, factor in points:
        seen[at] = factor
    ordered = sorted(seen.items())
    return [{"at_s": 0, "factor": 1.0}, *({"at_s": a, "factor": f} for a, f in ordered if a > 0)]


def week(seed: int = 7, days: int = 7) -> Scenario:
    spec: dict[str, Any] = {
        "name": "week",
        "start": "2026-07-13T00:00:00+02:00",
        "duration_s": days * DAY_S,
        "step_s": STEP_S,
        "house": {
            "seed": seed,
            "pv": {"kwp": 8.0},
            "load": {"noise_fraction": 0.05},
            "battery": {"capacity_wh": 10000, "initial_soc_pct": 40},
        },
        "outdoor": {"mean_c": 22.0, "amplitude_c": 7.0},
        "clouds": clouds(seed, days),
        "devices": [
            {
                "id": "pool",
                "template": "generic.switch",
                "physics": {"kind": "switch", "nominal_w": 750, "power_sensor": True},
                "control": {
                    "priority": 10,
                    "min_run_s": 600,
                    "min_pause_s": 300,
                    "max_switches_per_hour": 4,
                    "power_model": {"kind": "measured", "power_role": "power", "nominal_w": 750},
                },
            },
            {
                "id": "ac",
                "template": "lg.thinq_climate",
                "physics": {
                    "kind": "climate",
                    "initial_mode": "cool",
                    "initial_setpoint_c": 24,
                    "initial_temp_c": 24.5,
                    "default_on_mode": "cool",
                    "ua_w_per_k": 250,
                    "internal_gain_w": 400,
                    "power_sensor": True,
                    "power_poll_s": 30,
                },
                "control": {
                    "priority": 20,
                    "comfort": {"min_c": 21, "max_c": 27},
                    "power_model": {"kind": "measured", "power_role": "power", "nominal_w": 900},
                },
            },
            {
                "id": "boiler",
                "template": "generic.water_heater_steps",
                "physics": {
                    "kind": "water_heater",
                    "variant": "select",
                    "initial_temp_c": 38,
                    "max_temp_c": 60,
                    "power_sensor": True,
                },
                "control": {
                    "priority": 30,
                    "comfort": {"min_c": 30, "max_c": 62},
                    "power_model": {"kind": "measured", "power_role": "power", "nominal_w": 3000},
                },
            },
            {
                "id": "rod",
                "template": "generic.number_load",
                "physics": {"kind": "number", "max": 3000, "step": 100, "power_sensor": True},
                "control": {
                    "priority": 40,
                    "power_model": {"kind": "measured", "power_role": "power", "nominal_w": 3000},
                },
            },
        ],
    }
    return Scenario.model_validate(spec)


def test_the_weather_generator_is_deterministic_and_valid() -> None:
    assert clouds(7, 7) == clouds(7, 7)
    assert clouds(7, 7) != clouds(8, 7)
    week(7, 7)  # validates: strictly ascending clouds, devices


def test_a_week_of_mixed_weather_runs_without_oscillation() -> None:
    result, runner = run_scenario(week())
    assert len(result.steps) == 7 * DAY_S // STEP_S
    assert result.skipped_ticks == 0
    for device_id, config in result.configs.items():
        limit = config.max_switches_per_hour or 6
        assert result.switches_in_window(device_id) <= limit, device_id
        violations = result.hold_violations(
            device_id,
            min_run_s=config.min_run_s,
            min_pause_s=config.min_pause_s,
            tol_s=float(config.settle_s or 10) + 2 * STEP_S,
            exempt=[
                d.ts
                for d in result.accepted(device_id)
                if isinstance(d.intent.action, ReleaseAction)
            ],
        )
        assert violations == [], (device_id, violations[:3])
        ons = [i * STEP_S for i, on in result.transitions(device_id) if on]
        for a, b in itertools.pairwise(ons):
            if config.min_run_s and config.min_pause_s:
                assert b - a >= config.min_run_s + config.min_pause_s - 2 * (STEP_S + 10)
    # no hunting: set-point writes per hour of the air conditioner stay bounded
    stamps = sorted(
        (d.ts - result.scenario.start).total_seconds()
        for d in result.accepted("ac")
        if isinstance(d.intent.action, SetpointAction)
    )
    worst = max(sum(1 for t in stamps if s <= t < s + 3600) for s in stamps)
    assert worst <= 20
    # the base set-point does not drift: every night the unit is back at the user's 24
    ac = result.devices["ac"].setpoint_c
    for day in range(7):
        at = (day * DAY_S + 3 * 3600) // STEP_S
        assert ac[at] in (None, 24.0), (day, ac[at])
    bases = {
        d.intent.action.base_c
        for d in result.accepted("ac")
        if isinstance(d.intent.action, SetpointAction)
    }
    assert bases == {24.0}
    # it does its job on every clear day, and the gate refuses nothing it proposed
    for day in (0, 5):  # the two clear days
        wh = sum(
            sum(log.power_w[(day * DAY_S) // STEP_S : ((day + 1) * DAY_S) // STEP_S])
            for log in result.devices.values()
        )
        assert wh * STEP_S / 3600 > 3000
    assert result.of_kind(DecisionKind.FAILED) == []
    assert [
        d
        for d in result.of_kind(DecisionKind.REJECTED)
        if d.reasons[0].key not in ("safety.min_pause", "safety.min_run")
    ] == []
    # (almost) no import is caused by SunCore: against the shadow run of the first two days (nothing
    # written, same weather) the controlled run's import is within 3 % of the energy it moved
    horizon = 2 * DAY_S
    shadow, _ = run_scenario(week(), mode=OperatingMode.SHADOW, duration_s=horizon)
    moved = sum(
        sum(result.devices[d].power_w[: horizon // STEP_S]) * STEP_S / 3600
        - sum(shadow.devices[d].power_w) * STEP_S / 3600
        for d in result.configs
    )
    assert moved > 5_000  # two days of real work: kWh moved to the devices
    assert result.import_wh(0, horizon) - shadow.import_wh() <= 0.03 * moved
    assert runner.stack is not None
