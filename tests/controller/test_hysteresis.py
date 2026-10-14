# SPDX-License-Identifier: Apache-2.0
"""The per-device state machine: delays, protection, in-flight, decisions fed back."""

from __future__ import annotations

from datetime import timedelta

from suncore.controller import reasons as rs
from suncore.controller.hysteresis import DeviceMachine, Phase, Transition
from suncore.controller.levels import ConfigQuantiser, build_levels
from suncore.core.control import ControlSettings
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from tests.safety.helpers import T0, climate_device, switch_device

SETTINGS = ControlSettings()


def machine(**device: object) -> DeviceMachine:
    config = switch_device(**device)  # type: ignore[arg-type]
    m = DeviceMachine(config, SETTINGS)
    table = build_levels(config, None, None, SETTINGS, ConfigQuantiser())
    assert table is not None
    m.sync(T0, table, 0)
    return m


def decision(m: DeviceMachine, kind: DecisionKind, at: float, *reasons: Reason) -> Decision:
    assert m.in_flight_id is not None
    assert m.in_flight_level is not None
    on = m.in_flight_level > 0
    intent = Intent(
        id=m.in_flight_id,
        ts=T0,
        asset_id=m.device.id,
        action=SwitchAction(on=on),
        reasons=(rs.plain(rs.SWITCH_ON),),
        origin=Origin.CONTROLLER,
    )
    return Decision(
        intent=intent,
        kind=kind,
        ts=T0 + timedelta(seconds=at),
        reasons=reasons,
        mode=OperatingMode.AUTOMATIC,
    )


def submit(m: DeviceMachine, level: int, at: float) -> None:
    now = T0 + timedelta(seconds=at)
    assert m.not_ready(now) is None
    m.submitted(now, m.next_intent_id(now), level)


def test_sync_adopts_the_observed_level_once() -> None:
    config = switch_device()
    m = DeviceMachine(config, SETTINGS)
    assert m.phase is Phase.IDLE
    table = build_levels(config, None, None, SETTINGS, ConfigQuantiser())
    assert table is not None
    m.sync(T0, table, 1)
    assert (m.phase, m.level, m.active) == (Phase.STEADY, 1, True)
    m.sync(T0, table, 0)
    assert m.level == 1  # later observations do not overwrite the machine's level
    assert m.current_action() == SwitchAction(on=True)


def test_reset_keeps_the_switch_history() -> None:
    m = machine()
    m.switch_times.append(T0)
    m.reset(T0, rs.plain(rs.STATE_UNKNOWN))
    assert m.phase is Phase.IDLE
    assert not m.synced
    assert m.table is None
    assert m.current_action() is None
    assert m.switch_times == [T0]
    assert m.reasons == (rs.plain(rs.STATE_UNKNOWN),)


def test_want_waits_for_the_hysteresis_delay() -> None:
    m = machine()
    up = Transition(1, rs.plain(rs.SWITCH_ON))
    assert m.want(T0, up) == SETTINGS.default_hysteresis_delay_s
    assert m.phase is Phase.PENDING_UP
    assert m.want(T0 + timedelta(seconds=40), up) == 20.0
    assert m.want(T0 + timedelta(seconds=60), up) == 0.0
    # The condition breaks: back to steady, the delay starts over.
    m.steady(T0 + timedelta(seconds=61), rs.plain(rs.IDLE))
    assert m.phase is Phase.STEADY
    assert m.pending_level is None
    assert m.want(T0 + timedelta(seconds=62), up) == 60.0


def test_forced_transitions_skip_the_delay() -> None:
    m = machine()
    assert m.want(T0, Transition(1, rs.plain(rs.SWITCH_ON), forced=True)) == 0.0


def test_device_level_delay_overrides_the_default() -> None:
    config = switch_device().model_copy(update={"hysteresis_delay_s": 5, "hysteresis_w": 50.0})
    m = DeviceMachine(config, SETTINGS)
    assert m.hysteresis_delay_s == 5.0
    assert m.hysteresis_w == 50.0


def test_protection_mirrors_min_run_min_pause_and_switch_limit() -> None:
    m = machine(min_run_s=300, min_pause_s=120, max_switches_per_hour=2)
    assert m.max_switches_per_hour == 2
    assert m.blocked_by_protection(T0, 1) is None
    # Switched on at 0 s.
    submit(m, 1, 0)
    assert m.report(decision(m, DecisionKind.ACCEPTED, 0))
    assert m.level == 1
    assert m.last_on_ts == T0
    assert m.blocked_by_protection(T0 + timedelta(seconds=100), 0) == rs.min_run_wait(200)
    assert m.blocked_by_protection(T0 + timedelta(seconds=100), 1) is None  # no switch
    # Off at 300 s.
    submit(m, 0, 300)
    assert m.report(decision(m, DecisionKind.ACCEPTED, 300))
    assert m.blocked_by_protection(T0 + timedelta(seconds=360), 1) == rs.min_pause_wait(60)
    # Two switches in the hour: the limit.
    assert m.blocked_by_protection(T0 + timedelta(seconds=500), 1) == rs.switch_limit(2)
    assert m.switches_in_window(T0 + timedelta(hours=1, seconds=1)) == 1
    assert m.blocked_by_protection(T0 + timedelta(hours=1, seconds=301), 1) is None


def test_set_points_are_not_switches() -> None:
    config = climate_device()
    m = DeviceMachine(config, SETTINGS)
    from suncore.core.control import DeviceState  # noqa: PLC0415

    state = DeviceState(on=True, mode="heat", setpoint_c=21.0)
    table = build_levels(config, state, None, SETTINGS, ConfigQuantiser())
    assert table is not None
    m.sync(T0, table, 0)
    assert m.blocked_by_protection(T0, 2) is None


def test_in_flight_blocks_until_the_decision_or_the_timeout() -> None:
    m = machine()
    submit(m, 1, 0)
    assert m.phase is Phase.IN_FLIGHT
    assert m.not_ready(T0 + timedelta(seconds=5)) == rs.plain(rs.IN_FLIGHT)
    timeout = m.in_flight_timeout_s
    assert timeout == SETTINGS.default_settle_s + SETTINGS.intent_max_age_s + 2 * SETTINGS.tick_s
    later = T0 + timedelta(seconds=timeout + 1)
    assert m.not_ready(later) == rs.plain(rs.RETRY_WAIT)
    assert m.phase is Phase.RETRY
    assert m.in_flight_id is None
    assert m.not_ready(later + timedelta(seconds=SETTINGS.default_hysteresis_delay_s)) is None
    assert m.phase is Phase.STEADY


def test_rejection_starts_the_retry_cool_down() -> None:
    m = machine()
    submit(m, 1, 0)
    reason = Reason(key="safety.grid_importing", args={"grid_w": 10})
    assert m.report(decision(m, DecisionKind.REJECTED, 0, reason))
    assert m.level == 0
    assert m.phase is Phase.RETRY
    assert m.reasons == (reason,)
    assert m.not_ready(T0 + timedelta(seconds=1)) == rs.plain(rs.RETRY_WAIT)


def test_no_change_rejection_counts_as_applied() -> None:
    m = machine()
    submit(m, 1, 0)
    assert m.report(decision(m, DecisionKind.REJECTED, 0, Reason(key="safety.write_no_change")))
    assert m.level == 1
    assert m.phase is Phase.STEADY


def test_shadow_and_proposed_apply_the_virtual_level() -> None:
    m = machine()
    submit(m, 1, 0)
    assert m.report(decision(m, DecisionKind.SHADOW, 0))
    assert (m.level, m.applied_kind) == (1, DecisionKind.SHADOW)
    assert m.retry_until is None
    submit(m, 0, 10)
    assert m.report(decision(m, DecisionKind.PROPOSED, 10))
    assert m.applied_kind is DecisionKind.PROPOSED
    # A proposal is valid for the TTL: no new proposal before it expired.
    assert m.not_ready(T0 + timedelta(seconds=20)) == rs.plain(rs.RETRY_WAIT)


def test_foreign_decisions_are_ignored() -> None:
    m = machine()
    submit(m, 1, 0)
    other = decision(m, DecisionKind.ACCEPTED, 0)
    other = other.model_copy(update={"intent": other.intent.model_copy(update={"id": "x"})})
    assert not m.report(other)
    assert m.in_flight_id is not None


def test_intent_ids_are_unique() -> None:
    m = machine()
    ids = {m.next_intent_id(T0) for _ in range(5)}
    assert len(ids) == 5
    assert all(i.startswith("ctl:pool:") for i in ids)
