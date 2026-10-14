# SPDX-License-Identifier: Apache-2.0
"""Builders and hypothesis strategies shared by the safety tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st

from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.decisions import (
    Action,
    Intent,
    ModeAction,
    Origin,
    PowerSetpointAction,
    Reason,
    ReleaseAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ComfortBand,
    ContinuousControl,
    DeviceConfig,
    DeviceKind,
    OnOffControl,
    OverridePolicy,
    PowerModel,
    PowerModelKind,
    SetpointControl,
    Step,
    StepsControl,
)
from suncore.core.signals import Quality
from suncore.core.site import EntityRef
from suncore.safety.runtime import DeviceRuntime

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
SETTINGS = ControlSettings()


def switch_device(
    device_id: str = "pool",
    *,
    nominal_w: float = 1000.0,
    min_run_s: int = 0,
    min_pause_s: int = 0,
    max_switches_per_hour: int | None = None,
    settle_s: int | None = None,
) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="generic.switch",
        name="Pool",
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id=f"switch.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=nominal_w),
        control=OnOffControl(kind="on_off", role="switch"),
        min_run_s=min_run_s,
        min_pause_s=min_pause_s,
        max_switches_per_hour=max_switches_per_hour,
        settle_s=settle_s,
    )


def climate_device(
    device_id: str = "ac",
    *,
    nominal_w: float = 800.0,
    allow_switch_on: bool = False,
    min_c: float = 20.0,
    max_c: float = 26.0,
    override_policy: OverridePolicy | None = None,
    settle_s: int | None = None,
) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="lg.thinq_climate",
        name="AC",
        kind=DeviceKind.CLIMATE,
        entities={"climate": EntityRef(entity_id=f"climate.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=nominal_w),
        control=SetpointControl(kind="setpoint", role="climate", shift_max_c=2.0),
        comfort=ComfortBand(min_c=min_c, max_c=max_c),
        allow_switch_on=allow_switch_on,
        switch_on_hvac_mode="cool" if allow_switch_on else None,
        override_policy=override_policy or OverridePolicy(),
        settle_s=settle_s,
    )


def steps_device(device_id: str = "heater", *, settle_s: int | None = 0) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="generic.water_heater_steps",
        name="Heater",
        kind=DeviceKind.WATER_HEATER,
        entities={"level": EntityRef(entity_id=f"select.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=3000.0),
        control=StepsControl(
            kind="steps",
            role="level",
            steps=(
                Step(value="off", power_w=0),
                Step(value="1", power_w=1000),
                Step(value="3", power_w=3000),
            ),
        ),
        settle_s=settle_s,
    )


def continuous_device(device_id: str = "dimmer", *, unit: str = "W") -> DeviceConfig:
    control = (
        ContinuousControl(kind="continuous", role="power", unit="W", min=0, max=3000)
        if unit == "W"
        else ContinuousControl(
            kind="continuous", role="power", unit="A", min=0, max=16, voltage_v=230, phases=3
        )
    )
    return DeviceConfig(
        id=device_id,
        template_id="generic.number_load",
        name="Dimmer",
        kind=DeviceKind.NUMBER_LOAD,
        entities={"power": EntityRef(entity_id=f"number.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=3000.0),
        control=control,
        settle_s=0,
    )


def make_intent(
    action: Action,
    *,
    asset_id: str = "pool",
    ts: datetime = T0,
    origin: Origin = Origin.CONTROLLER,
    intent_id: str = "i-1",
) -> Intent:
    return Intent(
        id=intent_id,
        ts=ts,
        asset_id=asset_id,
        action=action,
        reasons=(Reason(key="controller.surplus"),),
        origin=origin,
    )


def flows(
    grid_w: float | None = -1500.0,
    battery_w: float | None = 0.0,
    soc_pct: float | None = 50.0,
    pv_w: float | None = 3000.0,
) -> FlowSnapshot:
    def flow(value: float | None) -> Flow:
        return Flow(value, Quality.OK if value is not None else Quality.UNAVAILABLE)

    return FlowSnapshot(
        grid_w=flow(grid_w),
        pv_w=flow(pv_w),
        battery_w=flow(battery_w),
        house_w=flow(None),
        battery_soc_pct=flow(soc_pct),
        ts=T0,
    )


def surplus(
    value_w: float, uncertainty_w: float = 0.0, quality: Quality = Quality.OK
) -> PowerEstimate:
    return PowerEstimate(value_w=max(0.0, value_w), uncertainty_w=uncertainty_w, quality=quality)


@dataclass
class FakeClock:
    """Injected clock; ``advance`` moves it, nothing else does."""

    now: datetime = T0
    step: timedelta = timedelta(0)

    def __call__(self) -> datetime:
        current = self.now
        self.now = current + self.step
        return current

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


@dataclass
class SpyActuator:
    """Records writes; ``observed`` is what ``read_back`` returns (settable per test)."""

    asset: str
    observed: Action | None = None
    applied: list[Intent] = field(default_factory=list)
    released: list[Action | None] = field(default_factory=list)
    read_backs: int = 0
    fail_apply: Exception | None = None
    fail_release: Exception | None = None
    follow: bool = True
    """``True``: ``read_back`` returns the last applied action (a well-behaved device)."""
    context_id: str | None = "ctx-1"
    hang_release: bool = False

    @property
    def asset_id(self) -> str:
        return self.asset

    async def apply(self, intent: Intent) -> str | None:
        if self.fail_apply is not None:
            raise self.fail_apply
        self.applied.append(intent)
        if self.follow:
            self.observed = intent.action
        return self.context_id

    async def read_back(self) -> Action | None:
        self.read_backs += 1
        return self.observed

    async def release(self, baseline: Action | None) -> None:
        if self.hang_release:
            import asyncio  # noqa: PLC0415

            await asyncio.sleep(3600)
        if self.fail_release is not None:
            raise self.fail_release
        self.released.append(baseline)
        if baseline is not None:
            self.observed = baseline


def runtime(device_id: str = "pool", **update: object) -> DeviceRuntime:
    return DeviceRuntime(device_id=device_id).model_copy(update=update)


# --- strategies -----------------------------------------------------------------

finite_w = st.floats(min_value=0, max_value=20_000, allow_nan=False, allow_infinity=False)
signed_w = st.floats(min_value=-20_000, max_value=20_000, allow_nan=False, allow_infinity=False)
temps = st.floats(min_value=10, max_value=35, allow_nan=False, allow_infinity=False)

actions = st.one_of(
    st.builds(SwitchAction, on=st.booleans()),
    st.builds(PowerSetpointAction, power_w=finite_w),
    st.builds(SetpointAction, setpoint_c=temps, base_c=temps),
    st.builds(StepAction, step=st.integers(min_value=0, max_value=2)),
    st.builds(ModeAction, mode=st.sampled_from(["off", "cool", "heat", "eco"])),
    st.just(ReleaseAction()),
)

states = st.one_of(
    st.none(),
    st.builds(
        DeviceState,
        on=st.one_of(st.none(), st.booleans()),
        step=st.one_of(st.none(), st.integers(min_value=0, max_value=2)),
        setpoint_c=st.one_of(st.none(), temps),
        value=st.one_of(st.none(), finite_w),
        mode=st.one_of(st.none(), st.sampled_from(["off", "cool", "heat"])),
        quality=st.sampled_from(Quality),
    ),
)

offsets = st.integers(min_value=0, max_value=7200)


def runtimes_for(device_id: str) -> st.SearchStrategy[DeviceRuntime]:
    return st.builds(
        lambda on, off, switches, last_written, last_write: DeviceRuntime(
            device_id=device_id,
            last_on_ts=None if on is None else T0 - timedelta(seconds=on),
            last_off_ts=None if off is None else T0 - timedelta(seconds=off),
            switch_times=tuple(sorted(T0 - timedelta(seconds=s) for s in switches)),
            last_written=last_written,
            last_write_ts=None if last_write is None else T0 - timedelta(seconds=last_write),
            baseline=SwitchAction(on=False) if last_written is not None else None,
        ),
        on=st.one_of(st.none(), offsets),
        off=st.one_of(st.none(), offsets),
        switches=st.lists(offsets, max_size=12),
        last_written=st.one_of(st.none(), actions),
        last_write=st.one_of(st.none(), st.integers(min_value=0, max_value=600)),
    )


flow_snapshots = st.builds(
    flows,
    grid_w=st.one_of(st.none(), signed_w),
    battery_w=st.one_of(st.none(), signed_w),
    soc_pct=st.one_of(st.none(), st.floats(min_value=0, max_value=100)),
    pv_w=st.one_of(st.none(), finite_w),
)

surpluses = st.one_of(
    st.none(),
    st.builds(
        PowerEstimate,
        value_w=finite_w,
        uncertainty_w=st.floats(min_value=0, max_value=5000),
        quality=st.sampled_from(Quality),
    ),
)
