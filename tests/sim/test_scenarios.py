# SPDX-License-Identifier: Apache-2.0
"""The bundled scenarios, closed-loop: real controller, real gate, real drivers, fake HA.

Every scenario runs the full stack in simulated time (no sleeps, no network) and is judged by
*physical* evidence - the simulated devices' state and the house balance - not by the gate's own
opinion. Common to all of them:

* every device stays within its ``max_switches_per_hour`` (physical on/off changes, any hour);
* min run / min pause hold, except for transitions a *release* causes (emergency stop, watchdog,
  shutdown: the safe direction bypasses the protection times by design);
* the gate refuses nothing the controller proposed (no ``rejected``), and the only ``failed``
  decisions are release attempts that could not reach an unavailable device or connection;
* SunCore-caused grid import is bounded (:data:`CAUSED_IMPORT_SHARE` of the energy it moved to
  the devices, plan section 6: the battery buffers short deficits, then SunCore backs off; these
  scenarios have no battery unless stated, so every deficit is import until the back-off).
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest

from suncore.core.control import ControlSettings
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    PowerSetpointAction,
    ReleaseAction,
    SetpointAction,
)
from suncore.sim.runner import RunResult
from tests.sim.helpers import caused_import_wh, run_named

pytestmark = pytest.mark.simulator

SETTINGS = ControlSettings()
CAUSED_IMPORT_SHARE = 0.03
"""At most 3 % of the energy SunCore moved to the devices may be import it caused."""

BACKOFF_S = 600
"""Climate with a 5 minute power sensor: one poll period of lag plus two back-off steps (each
``deficit_delay_s`` + settle + a tick); the counter-only unit is faster (it assumes its nominal)."""

CONTROLLED = [
    "cloud_flicker",
    "climate_min_run",
    "climate_counter_only",
    "climate_measured_slow",
    "sensor_dropout",
    "inverter_hang",
    "ha_disconnect",
    "manual_override",
    "water_heater_steps",
    "grid_limit",
]


def seconds(result: RunResult, when: datetime) -> float:
    return (when - result.scenario.start).total_seconds()


def writes(
    result: RunResult, device_id: str, start_s: float = 0.0, end_s: float = 1e12
) -> list[Decision]:
    """Accepted writes of the controller (not releases) in ``[start_s, end_s)``."""
    return [
        d
        for d in result.accepted(device_id)
        if not isinstance(d.intent.action, ReleaseAction)
        and start_s <= seconds(result, d.intent.ts) < end_s
    ]


def releases(result: RunResult, device_id: str, start_s: float, end_s: float) -> list[Decision]:
    return [
        d
        for d in result.accepted(device_id)
        if isinstance(d.intent.action, ReleaseAction) and start_s <= seconds(result, d.ts) < end_s
    ]


def index(result: RunResult, at_s: float) -> int:
    return int(at_s // result.step_s)


def cycles(result: RunResult, device_id: str) -> list[float]:
    """Seconds from every physical switch-on to the next switch-on (full on/off cycles)."""
    ons = [i * result.step_s for i, on in result.transitions(device_id) if on]
    return [b - a for a, b in itertools.pairwise(ons)]


# --- what holds in every scenario ------------------------------------------------------


@pytest.mark.parametrize("name", CONTROLLED)
def test_switching_stays_within_the_limits(name: str) -> None:
    result, _ = run_named(name)
    for device_id, config in result.configs.items():
        limit = config.max_switches_per_hour or SETTINGS.default_max_switches_per_hour
        assert result.switches_in_window(device_id) <= limit, device_id
        settle = float(config.settle_s if config.settle_s is not None else 10)
        violations = result.hold_violations(
            device_id,
            min_run_s=config.min_run_s,
            min_pause_s=config.min_pause_s,
            tol_s=settle + 2 * result.step_s,
            exempt=[
                d.ts
                for d in result.accepted(device_id)
                if isinstance(d.intent.action, ReleaseAction)
            ],
        )
        assert violations == [], (device_id, violations)
        for gap in cycles(result, device_id):
            if config.min_run_s and config.min_pause_s:
                assert gap >= config.min_run_s + config.min_pause_s - settle - 2 * result.step_s


@pytest.mark.parametrize("name", CONTROLLED)
def test_the_gate_refuses_nothing_the_controller_proposed(name: str) -> None:
    result, _ = run_named(name)
    rejected = result.of_kind(DecisionKind.REJECTED)
    # one benign exception: a gate release counts as a switch in the protection history, which
    # the controller does not see, so the first intent after a release can meet min pause
    unexpected = [
        d
        for d in rejected
        if [r.key for r in d.reasons] not in (["safety.min_pause"], ["safety.min_run"])
    ]
    assert unexpected == [], [
        (d.ts, d.intent.asset_id, [r.key for r in d.reasons]) for d in unexpected
    ]
    failed = result.of_kind(DecisionKind.FAILED)
    assert all(isinstance(d.intent.action, ReleaseAction) for d in failed)
    assert result.skipped_ticks == 0


@pytest.mark.parametrize("name", CONTROLLED)
def test_no_import_beyond_the_documented_bound_is_caused_by_suncore(name: str) -> None:
    caused, moved = caused_import_wh(name)
    assert caused <= CAUSED_IMPORT_SHARE * max(moved, 1.0) + 2.0, (caused, moved)


@pytest.mark.parametrize("name", CONTROLLED)
def test_the_controller_actually_does_something(name: str) -> None:
    """Guards the guards: a scenario that never writes would pass every bound above."""
    result, _ = run_named(name)
    assert sum(len(writes(result, d)) for d in result.configs) >= 2


# --- cloud flicker ---------------------------------------------------------------------


def test_cloud_flicker_is_smoothed_into_few_switches() -> None:
    result, _ = run_named("cloud_flicker")
    edges = len(result.scenario.clouds)
    assert edges > 100  # a hundred sun/cloud edges over four hours ...
    assert len(result.transitions("pool")) <= 8  # ... and a handful of switch operations
    assert result.switches_in_window("pool") <= 4  # the template's configured limit
    ac = result.devices["ac"]
    comfort = result.configs["ac"].comfort
    assert comfort is not None
    shifted = [s for s in ac.setpoint_c if s is not None]
    assert min(shifted) >= comfort.min_c
    assert max(shifted) <= 24.0  # never above the user's base while cooling
    # a flicker episode (15-40 min in) does not make the pump follow every cloud
    flicker = result.transitions("pool")
    in_episode = [i for i, _ in flicker if 1500 <= i * result.step_s < 2400]
    assert len(in_episode) <= 1


# --- climate: protection times and the base set-point ---------------------------------


def test_climate_min_run_and_min_pause_hold_through_clouds() -> None:
    result, _ = run_named("climate_min_run")
    moves = result.transitions("ac")
    assert len(moves) >= 4  # on, off, on, off ... the protection is really exercised
    for (i, on), (j, _) in itertools.pairwise(moves):
        held = (j - i) * result.step_s
        assert held >= (600 if on else 300) - 15, (i, on, held)
    # the first switch-on is followed by a deep cloud inside min_run_s: the unit stays on
    first_on, first_off = moves[0][0], moves[1][0]
    assert first_off * result.step_s - first_on * result.step_s >= 600 - 15
    assert 900 < first_off * result.step_s < 3600  # and goes off during the cloud, not before


def test_a_shifted_unit_is_restored_before_it_is_switched_off_and_the_base_never_drifts() -> None:
    """Regression: the unit's own set-point must come back with every off/on cycle (the base
    ratcheted down 24 -> 21 before the shift was undone ahead of the switch-off)."""
    result, _ = run_named("climate_min_run")
    bases = {
        d.intent.action.base_c
        for d in result.accepted("ac")
        if isinstance(d.intent.action, SetpointAction)
    }
    assert bases == {24.0}
    ac = result.devices["ac"]
    for i, on in result.transitions("ac"):
        if not on and i > 0:  # the set-point just before every switch-off is the base
            assert ac.setpoint_c[i - 2] == 24.0


# --- slow measured power and counter-only power, the same unit and day ------------


def hourly_shift(result: RunResult, base: float = 24.0) -> dict[int, float]:
    """Mean set-point shift below the base (cooling) per hour of the run."""
    series = result.devices["ac"].setpoint_c
    per_hour: dict[int, list[float]] = {}
    for i, setpoint in enumerate(series):
        hour = int(i * result.step_s // 3600)
        per_hour.setdefault(hour, []).append(0.0 if setpoint is None else base - setpoint)
    return {h: sum(v) / len(v) for h, v in per_hour.items()}


def test_counter_only_climate_behaves_like_the_slow_measured_one_at_full_stack() -> None:
    measured, _ = run_named("climate_measured_slow")
    counter, _ = run_named("climate_counter_only")
    kinds = {
        k: v.power_model.kind.value
        for k, v in {**measured.configs, "c": counter.configs["ac"]}.items()
    }
    assert kinds == {"ac": "measured", "c": "counter_derived"}
    for result in (measured, counter):
        assert result.of_kind(DecisionKind.REJECTED) == []
        assert result.of_kind(DecisionKind.FAILED) == []
    # (1) the same behaviour: the hourly mean shift differs by at most 0.5 degrees
    a, b = hourly_shift(measured), hourly_shift(counter)
    assert a.keys() == b.keys()
    for hour in a:
        assert abs(a[hour] - b[hour]) <= 0.5, (hour, a, b)
    # (2) the cloud (2 h to 2.5 h): both are back at the base within BACKOFF_S of its edge
    for result in (measured, counter):
        series = result.devices["ac"].setpoint_c
        edge = index(result, 7200)
        back = next(i for i in range(edge, len(series)) if series[i] == 24.0)
        assert (back - edge) * result.step_s <= BACKOFF_S
        assert all(s in (None, 24.0) for s in series[back : index(result, 9000)])
    # (3) the counter-only unit is never more aggressive and never causes more import
    assert sum(b.values()) <= sum(a.values()) + 1e-9
    assert counter.import_wh() <= measured.import_wh() + 5.0
    # (4) what differs is the estimate: stale at first, then old and wide (the counter is hourly)
    est_counter = counter.devices["ac"].estimate_uncertainty_w
    est_measured = measured.devices["ac"].estimate_uncertainty_w
    late = slice(index(counter, 7000), index(counter, 7100))
    assert min(u for u in est_counter[late] if u is not None) > 3 * max(
        u for u in est_measured[late] if u is not None
    )
    # a measured 5 minute sensor lags by up to one poll: usually close, off by a shift at most
    truth = measured.devices["ac"].power_w
    est = measured.devices["ac"].estimate_w
    errors = [abs(e - t) for e, t in zip(est, truth, strict=False) if e is not None and t > 100]
    assert sorted(errors)[len(errors) // 2] <= 0.1 * 900  # the median error: 10 % of nominal


# --- sensor dropout --------------------------------------------------------------------


def test_sensor_dropout_pauses_and_releases_the_devices() -> None:
    result, runner = run_named("sensor_dropout")
    cooldown = SETTINGS.safe_state_cooldown_s
    # grid_power unavailable 08:00-08:30: nothing was under control yet, nothing is written
    assert writes(result, "pool", 7200, 9000 + cooldown) == []
    assert writes(result, "ac", 7200, 9000 + cooldown) == []
    # grid meter silent (stale) 10:00-11:00: it is the site's heartbeat, so after the cache's
    # 120 s the site trips and releases both
    for device_id in ("pool", "ac"):
        rel = releases(result, device_id, 14400, 14400 + 300)
        assert len(rel) == 1, device_id
        assert "safety.watchdog_stale" in [r.key for r in rel[0].reasons]
        assert writes(result, device_id, 14400 + 240, 18000 + cooldown) == []
    # ... and the devices are back at the state they were in before SunCore: pool off, AC 21
    i = index(result, 16200)
    assert result.devices["pool"].on[i] is False
    assert result.devices["ac"].setpoint_c[i] == 21.0
    # an unchanged battery SoC 12:00-13:00 is normal (the integration only reports changes):
    # nothing trips on it
    for device_id in ("pool", "ac"):
        silent_soc = releases(result, device_id, 21600, 25200)
        assert all("safety.watchdog_stale" not in [r.key for r in d.reasons] for d in silent_soc)
    # the plug that is unavailable 16:00-16:15 cannot be written, and nothing reached it
    window = (result.scenario.start.timestamp() + 36000, result.scenario.start.timestamp() + 36900)
    reached = [
        c
        for c in runner.conn.call_log
        if "switch.sim_pool" in c.entity_ids and window[0] <= c.ts.timestamp() < window[1]
    ]
    assert all(c.reached == () for c in reached)
    # SunCore takes over again after every fault (the controller is not stuck in safe state)
    assert len(writes(result, "pool", 18000 + cooldown)) >= 1
    assert len(writes(result, "ac", 18000 + cooldown)) >= 1


def test_sensor_dropout_connection_loss_releases_on_restore() -> None:
    result, runner = run_named("sensor_dropout")
    restore = 29100
    assert writes(result, "pool", 28800, restore) == []
    for device_id in ("pool", "ac"):
        rel = releases(result, device_id, restore, restore + 60)
        assert len(rel) == 1, device_id
        assert "safety.connection_lost" in [r.key for r in rel[0].reasons]
    # Nothing is sent - and nothing logged - while the connection is lost: the stack skips its
    # ticks (a release cannot succeed without Home Assistant; WP8 found one failed release
    # decision per device per tick, 230 entries in 10 minutes).
    assert [c for c in runner.conn.call_log if c.error == "not_connected"] == []
    outage = [
        d
        for d in result.decisions
        if 28800 <= (d.ts - result.scenario.start).total_seconds() < restore
    ]
    assert [d for d in outage if d.kind is DecisionKind.FAILED] == []
    i = index(result, restore + 90)
    assert result.devices["pool"].on[i] is False
    assert result.devices["ac"].setpoint_c[i] == 21.0


# --- inverter hang ---------------------------------------------------------------------


def test_a_hung_inverter_is_detected_and_the_devices_are_released() -> None:
    result, _ = run_named("inverter_hang")
    start, end = 7200, 10800
    frozen_s = SETTINGS.frozen_pv_s
    # the first release comes from the balance check (a cloud while the PV value is frozen)
    # or at the latest from the frozen-value check after frozen_pv_s: within 15 minutes + a tick
    for device_id in ("pool", "ac"):
        first = releases(result, device_id, start, end)[0]
        assert seconds(result, first.ts) <= start + frozen_s + 2 * result.step_s
        assert first.reasons[-1].key in ("safety.watchdog_frozen", "safety.watchdog_balance")
    keys = {r.key for d in releases(result, "pool", start, end) for r in d.reasons}
    assert "safety.watchdog_frozen" in keys
    # after the last release no write until the inverter recovers, plus the cool-down
    last = max(seconds(result, d.ts) for d in releases(result, "ac", start, end))
    assert writes(result, "ac", last, end) == []
    assert result.devices["pool"].on[index(result, last + 60)] is False
    assert result.devices["ac"].setpoint_c[index(result, last + 60)] == 24.0
    # the blind window (frozen PV, balanced by the live grid meter) caused no import
    caused, _ = caused_import_wh("inverter_hang")
    assert caused <= 2.0
    assert result.import_wh(start, end) <= 2.0
    # recovery: control resumes
    assert len(writes(result, "pool", end)) >= 1


# --- Home Assistant disconnect ---------------------------------------------------------


def test_disconnect_releases_on_restore_and_takes_over_only_afterwards() -> None:
    result, runner = run_named("ha_disconnect")
    start, end = 10800, 11400
    settle = SETTINGS.safe_state_cooldown_s
    assert writes(result, "pool", start, end) == []
    assert writes(result, "ac", start, end) == []
    for device_id in ("pool", "ac"):
        rel = releases(result, device_id, end, end + 60)
        assert len(rel) == 1
        assert "safety.connection_lost" in [r.key for r in rel[0].reasons]
        # baseline right after: pool off, AC at the user's 24
    i = index(result, end + 60)
    assert result.devices["pool"].on[i] is False
    assert result.devices["ac"].setpoint_c[i] == 24.0
    # no service call was attempted during the outage: the stack skips its ticks (a release
    # cannot succeed without Home Assistant, WP8 logged a failed one per tick)
    outage = [
        c
        for c in runner.conn.call_log
        if start <= (c.ts - result.scenario.start).total_seconds() < end
    ]
    assert outage == []
    # the next takeover waits for the cool-down and for the pump's min pause
    first_again = min(seconds(result, d.ts) for d in writes(result, "pool", end))
    assert first_again >= end + settle
    assert first_again >= end + result.configs["pool"].min_pause_s - 15


# --- manual override -------------------------------------------------------------------


def test_a_manual_switch_off_stays_off_until_four_the_next_morning() -> None:
    result, _ = run_named("manual_override")
    off_at, resume = 9000, 72000  # 10:30 and 04:00 the next day (the scenario starts at 08:00)
    assert [on for _, on in result.transitions("pool")][:2] == [True, False]
    assert result.transitions("pool")[1][0] * result.step_s == off_at  # the user's remote
    # nothing SunCore did between the override and 04:00 - not one write, not one release
    assert writes(result, "pool", off_at, resume) == []
    assert releases(result, "pool", off_at, resume) == []
    # the controller does not even propose while the gate holds the device
    assert [
        d
        for d in result.decisions
        if d.intent.asset_id == "pool" and off_at < seconds(result, d.ts) < resume
    ] == []
    # the pool pump stays off for the whole sunny afternoon ...
    series = result.devices["pool"].on
    assert not any(series[index(result, off_at + 30) : index(result, resume)])
    # ... and SunCore takes over again after 04:00, when there is surplus
    nxt = [
        i * result.step_s
        for i, on in result.transitions("pool")
        if on and i * result.step_s > off_at
    ]
    assert nxt
    assert nxt[0] >= resume
    # the gate's own view: held from the detection until exactly 04:00 the next morning
    held = result.devices["pool"].held_until
    resume_at = result.scenario.start + timedelta(seconds=resume)
    flagged = [i for i, until in enumerate(held) if until is not None]
    assert {held[i] for i in flagged} == {resume_at}
    assert off_at <= flagged[0] * result.step_s <= off_at + 60  # detected within a minute
    assert flagged[-1] * result.step_s < resume <= (flagged[-1] + 2) * result.step_s


def test_a_manual_temperature_becomes_the_new_base_set_point() -> None:
    result, _ = run_named("manual_override")
    changed_at, resume = 10800, 72000
    # held until 04:00: SunCore does not touch the unit, the user's 25 stays
    assert writes(result, "ac", changed_at, resume) == []
    ac = result.devices["ac"].setpoint_c
    assert {
        s for s in ac[index(result, changed_at + 30) : index(result, resume)] if s is not None
    } == {25.0}
    # afterwards every shift is relative to 25, never to the old 24 (and never below the 2 K reach)
    later = [d.intent.action for d in writes(result, "ac", resume)]
    assert later
    assert all(isinstance(a, SetpointAction) and a.base_c == 25.0 for a in later)
    assert all(23.0 <= a.setpoint_c <= 25.0 for a in later if isinstance(a, SetpointAction))
    before = [d.intent.action for d in writes(result, "ac", 0, changed_at)]
    assert before
    assert all(isinstance(a, SetpointAction) and a.base_c == 24.0 for a in before)


# --- water heater ----------------------------------------------------------------------


def test_the_stepped_water_heater_follows_the_surplus_and_its_thermostat_holds() -> None:
    result, _ = run_named("water_heater_steps")
    boiler = result.devices["boiler"]
    assert max(t for t in boiler.temp_c if t is not None) <= 55.0 + 0.3  # the tank never overheats
    # high (3 kW) takes the morning surplus, the first cloud takes it off, the sun brings it back
    assert boiler.power_w[index(result, 600)] == 3000.0
    assert boiler.power_w[index(result, 2400)] == 0.0
    assert boiler.power_w[index(result, 3600)] == 3000.0
    moves = result.transitions("boiler")
    assert [on for _, on in moves][:3] == [True, False, True]
    # its own thermostat cut the elements off while the select still says "high"
    cut = [
        i
        for i in range(len(boiler.on))
        if boiler.on[i] and boiler.power_w[i] == 0.0 and boiler.temp_c[i] is not None
    ]
    assert cut
    assert min(boiler.temp_c[i] for i in cut) >= 55.0 - 3.0 - 0.1  # type: ignore[type-var]
    steps = [
        d.intent.action.step for d in result.accepted("boiler") if hasattr(d.intent.action, "step")
    ]
    assert steps[0] == 2  # off -> high in one step: 3.3 kW of surplus fits it


# --- grid limit ------------------------------------------------------------------------


def test_nothing_is_raised_while_the_grid_imports_above_the_limit() -> None:
    result, _ = run_named("grid_limit")
    limit = result.scenario.control.import_limit_w
    assert limit == 200
    rises = []
    previous = 0.0
    for d in result.accepted("rod"):
        action = d.intent.action
        assert isinstance(action, PowerSetpointAction)
        if action.power_w > previous:
            rises.append(d)
        previous = action.power_w
    assert rises
    for d in rises:
        grid = result.steps[index(result, seconds(result, d.intent.ts))].grid_w
        assert grid <= limit, (d.ts, grid)
    # the house itself imports above the limit for a long time (shadow run: nothing written) ...
    shadow, _ = run_named("grid_limit", shadow=True)
    over = sum(1 for s in shadow.steps if s.grid_w > limit)
    assert over > 0.1 * len(shadow.steps)
    # ... and SunCore adds next to nothing to it
    caused, _ = caused_import_wh("grid_limit")
    assert caused <= 10.0
    reasons = {r.key for d in result.accepted("rod") for r in d.reasons}
    assert "controller.import_limit" in reasons


def test_the_pool_pump_is_not_switched_on_without_a_real_surplus() -> None:
    result, _ = run_named("grid_limit")
    assert result.devices["pool"].on.count(True) == 0


def test_every_scenario_is_deterministic() -> None:
    """Two runs of the same scenario are identical (seeded house, injected time)."""
    from suncore.sim.scenario import load_scenario  # noqa: PLC0415
    from tests.sim.helpers import run_scenario  # noqa: PLC0415

    first, _ = run_named("water_heater_steps")
    second, _ = run_scenario(load_scenario("water_heater_steps"))
    assert [d.ts for d in first.decisions] == [d.ts for d in second.decisions]
    assert [s.grid_w for s in first.steps] == [s.grid_w for s in second.steps]
