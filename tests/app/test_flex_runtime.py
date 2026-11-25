# SPDX-License-Identifier: Apache-2.0
"""PR 6-M: EV state, program arm/cancel and grid status through the runtime (no device write)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from suncore.api.flex_types import EvTargetError, FlexStoreError, NotAnEvError
from suncore.app.grid import GridMonitor
from suncore.app.programs import ProgramJobBook
from suncore.app.runtime import Runtime
from suncore.core.controlstate import UnknownDeviceError
from suncore.core.devices import (
    ChargerControl,
    DeviceConfig,
    DeviceKind,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    ProgramSpec,
)
from suncore.core.flex import EvMode, EvState, EvTarget, JobState, ProgramJobError
from suncore.core.site import EntityRef

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
PM = PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=2000)


def washer() -> DeviceConfig:
    return DeviceConfig(
        id="washer",
        template_id="generic.program_plug",
        name="Washer",
        kind=DeviceKind.PROGRAM,
        entities={"switch": EntityRef(entity_id="switch.washer")},
        power_model=PM,
        control=OnOffControl(kind="on_off", role="switch"),
        program=ProgramSpec(runtime_min=120, start_method="plug", resume_confirmed=True),
    )


def plug() -> DeviceConfig:
    return DeviceConfig(
        id="pool",
        template_id="generic.switch",
        name="Pool",
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id="switch.pool")},
        power_model=PM,
        control=OnOffControl(kind="on_off", role="switch"),
    )


def charger() -> DeviceConfig:
    return DeviceConfig(
        id="wallbox",
        template_id="generic.wallbox",
        name="Wallbox",
        kind=DeviceKind.WALLBOX,
        entities={"current": EntityRef(entity_id="number.wb_current")},
        power_model=PM,
        control=ChargerControl(kind="charger", role="current", max_current_a=16),
    )


class FlexStore:
    def __init__(self) -> None:
        self.saved: list[EvState] = []

    async def put_ev(self, state: EvState) -> None:
        self.saved.append(state)


def runtime() -> Any:
    rt = SimpleNamespace(
        devices=SimpleNamespace(devices=[washer(), plug(), charger()]),
        clock=lambda: NOW,
        flex_store=FlexStore(),
        _ev_states={},
        program_jobs=ProgramJobBook(None),
        planned=[],
        notified=0,
    )
    rt._device_config = lambda device_id: Runtime._device_config(rt, device_id)
    rt._planning_event = rt.planned.append
    rt._notify_control = lambda: setattr(rt, "notified", rt.notified + 1)
    return rt


async def test_set_ev_state_persists_and_wakes_the_planner() -> None:
    rt = runtime()
    by = NOW + timedelta(hours=8)
    state = await Runtime.set_ev_state(rt, "wallbox", EvMode.TARGET, EvTarget(soc_pct=80, by=by))
    assert rt._ev_states["wallbox"] is state
    assert rt.flex_store.saved == [state]
    assert state.mode is EvMode.TARGET
    assert rt.planned == ["manual"]
    assert rt.notified == 1
    seen, job = await Runtime.flex_view(rt, "wallbox")
    assert seen is state
    assert job is None


async def test_a_target_is_dropped_outside_target_mode() -> None:
    rt = runtime()
    by = NOW + timedelta(hours=8)
    state = await Runtime.set_ev_state(rt, "wallbox", EvMode.NOW, EvTarget(soc_pct=80, by=by))
    assert state.target is None


async def test_ev_state_of_a_plug_or_an_unknown_device_is_refused() -> None:
    rt = runtime()
    with pytest.raises(NotAnEvError):
        await Runtime.set_ev_state(rt, "pool", EvMode.NOW, None)
    with pytest.raises(UnknownDeviceError):
        await Runtime.set_ev_state(rt, "nope", EvMode.NOW, None)
    assert rt.flex_store.saved == []


async def test_program_arm_then_cancel_goes_through_the_job_book() -> None:
    rt = runtime()
    deadline = NOW + timedelta(hours=6)
    job = await Runtime.arm_program(rt, "washer", deadline)
    assert job.state is JobState.ARMED
    assert rt.program_jobs.current("washer") == job
    with pytest.raises(ProgramJobError) as again:
        await Runtime.arm_program(rt, "washer", deadline)
    assert again.value.key == "api.error.program_job_open"
    cancelled = await Runtime.cancel_program(rt, "washer")
    assert cancelled.state is JobState.CANCELLED
    assert rt.program_jobs.current("washer") is None
    with pytest.raises(ProgramJobError) as none:
        await Runtime.cancel_program(rt, "washer")
    assert none.value.key == "api.error.program_no_job"


async def test_arm_refuses_a_deadline_in_the_past_and_a_non_program() -> None:
    rt = runtime()
    with pytest.raises(ProgramJobError) as past:
        await Runtime.arm_program(rt, "washer", NOW - timedelta(hours=1))
    assert past.value.key == "api.error.program_deadline_past"
    with pytest.raises(ProgramJobError) as plain:
        await Runtime.arm_program(rt, "pool", NOW + timedelta(hours=1))
    assert plain.value.key == "api.error.program_not_a_program"


async def test_grid_status_without_a_stack_is_unknown() -> None:
    rt = SimpleNamespace(
        clock=lambda: NOW,
        stack=None,
        site=None,
        _timezone=lambda: UTC,
    )
    status = await Runtime.grid_status(rt)
    assert (status.state, status.para14a, status.month) == ("unknown", "not_configured", "2026-11")
    assert status.month_peak_w is None
    assert status.peak_limit_w is None


async def test_grid_status_reads_the_monitor() -> None:
    monitor = GridMonitor(UTC)
    monitor.seed("2026-11", 4200.0)
    rt = SimpleNamespace(
        clock=lambda: NOW,
        stack=SimpleNamespace(grid_monitor=monitor),
        site=None,
        _timezone=lambda: UTC,
    )
    status = await Runtime.grid_status(rt)
    assert status.month_peak_w == 4200.0


async def test_grid_events_without_a_database_are_empty() -> None:
    rt = SimpleNamespace(clock=lambda: NOW, timeseries_db=None)
    view = await Runtime.grid_events(rt, 30)
    assert view.events == ()
    assert view.peaks == ()


async def test_ev_target_time_is_bounded_to_the_next_seven_days() -> None:
    rt = runtime()
    for by, key in (
        (NOW - timedelta(minutes=1), "api.error.ev_target_past"),
        (NOW, "api.error.ev_target_past"),
        (NOW + timedelta(days=7, seconds=1), "api.error.ev_target_far"),
    ):
        with pytest.raises(EvTargetError) as err:
            await Runtime.set_ev_state(rt, "wallbox", EvMode.TARGET, EvTarget(soc_pct=80, by=by))
        assert err.value.key == key
    ok = EvTarget(soc_pct=80, by=NOW + timedelta(days=7))
    await Runtime.set_ev_state(rt, "wallbox", EvMode.TARGET, ok)
    assert rt.flex_store.saved[-1].target == ok


async def test_a_store_failure_is_a_flex_store_error_and_changes_nothing() -> None:
    rt = runtime()

    async def boom(state: EvState) -> None:
        raise OSError("disk full")

    rt.flex_store.put_ev = boom
    with pytest.raises(FlexStoreError):
        await Runtime.set_ev_state(rt, "wallbox", EvMode.NOW, None)
    assert "wallbox" not in rt._ev_states


def _site(para14a: bool) -> SimpleNamespace:
    return SimpleNamespace(grid=SimpleNamespace(para14a=para14a, peak_shaving=None))


async def test_para14a_is_unknown_only_when_configured_without_a_reading() -> None:
    def rt(site: object, monitor: object | None) -> SimpleNamespace:
        return SimpleNamespace(
            clock=lambda: NOW,
            stack=None if monitor is None else SimpleNamespace(grid_monitor=monitor),
            site=site,
            _timezone=lambda: UTC,
        )

    # configured, no monitor or no reading yet: unknown
    assert (await Runtime.grid_status(rt(_site(True), None))).para14a == "unknown"
    assert (await Runtime.grid_status(rt(_site(True), GridMonitor(UTC)))).para14a == "unknown"
    # not configured: a distinct state, never "unknown"
    assert (await Runtime.grid_status(rt(_site(False), None))).para14a == "not_configured"
    assert (await Runtime.grid_status(rt(_site(False), GridMonitor(UTC)))).para14a == (
        "not_configured"
    )
