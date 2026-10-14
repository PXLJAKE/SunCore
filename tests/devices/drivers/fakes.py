# SPDX-License-Identifier: Apache-2.0
"""A fake Home Assistant for the driver tests: records exact service calls, holds states."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from suncore.core.decisions import Action, Intent, Origin, Reason
from suncore.core.devices import (
    ComfortBand,
    ContinuousControl,
    DeviceConfig,
    DeviceKind,
    Mode,
    ModeControl,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    RelaysControl,
    RelayStep,
    SetpointControl,
    Step,
    StepsControl,
)
from suncore.core.site import EntityRef
from tests.devices.helpers import T0


@dataclass(frozen=True)
class FakeState:
    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeResult:
    context_id: str | None


@dataclass(frozen=True)
class Call:
    domain: str
    service: str
    target: Mapping[str, Any]
    service_data: Mapping[str, Any] | None


class FakeHA:
    """``states`` is the entity cache, ``calls`` the recorded writes (domain, service, ...)."""

    def __init__(self) -> None:
        self.states: dict[str, FakeState] = {}
        self.calls: list[Call] = []
        self.fail: Exception | None = None
        self.on_call: Callable[[Call], None] | None = None
        self._n = 0

    def set(self, entity_id: str, state: str, **attributes: Any) -> None:
        self.states[entity_id] = FakeState(entity_id, state, attributes)

    def lookup(self, entity_id: str) -> FakeState | None:
        return self.states.get(entity_id)

    async def write(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
    ) -> FakeResult:
        if self.fail is not None:
            raise self.fail
        call = Call(
            domain, service, dict(target or {}), dict(service_data) if service_data else None
        )
        self.calls.append(call)
        self._n += 1
        if self.on_call is not None:
            self.on_call(call)
        return FakeResult(f"ctx-{self._n}")

    @property
    def sent(self) -> list[tuple[str, str, dict[str, Any], dict[str, Any] | None]]:
        return [
            (c.domain, c.service, dict(c.target), dict(c.service_data) if c.service_data else None)
            for c in self.calls
        ]


def intent(action: Action, asset: str = "dev") -> Intent:
    return Intent(
        id="i",
        ts=T0,
        asset_id=asset,
        action=action,
        reasons=(Reason(key="controller.surplus"),),
        origin=Origin.CONTROLLER,
    )


def _device(
    device_id: str,
    kind: DeviceKind,
    entities: dict[str, str],
    control: Any,
    nominal_w: float | None = 1000.0,
    **extra: Any,
) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="generic.test",
        name=device_id,
        kind=kind,
        entities={role: EntityRef(entity_id=e) for role, e in entities.items()},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=nominal_w),
        control=control,
        **extra,
    )


def onoff_device(entity: str = "switch.pump") -> DeviceConfig:
    return _device(
        "pump", DeviceKind.SWITCH, {"switch": entity}, OnOffControl(kind="on_off", role="switch")
    )


def climate_device(
    *, allow_switch_on: bool = False, mode: str | None = "cool", step_c: float = 0.5
) -> DeviceConfig:
    return _device(
        "ac",
        DeviceKind.CLIMATE,
        {"climate": "climate.ac"},
        SetpointControl(kind="setpoint", role="climate", shift_max_c=2, step_c=step_c),
        nominal_w=900,
        comfort=ComfortBand(min_c=20, max_c=28),
        allow_switch_on=allow_switch_on,
        switch_on_hvac_mode=mode if allow_switch_on else None,
    )


STEPS = (
    Step(value="off", power_w=0),
    Step(value="low", power_w=1000),
    Step(value="high", power_w=3000),
)


def select_steps_device() -> DeviceConfig:
    return _device(
        "boiler",
        DeviceKind.WATER_HEATER,
        {"level": "select.boiler_level"},
        StepsControl(kind="steps", role="level", steps=STEPS),
        nominal_w=3000,
    )


def number_steps_device() -> DeviceConfig:
    steps = (Step(value=0, power_w=0), Step(value=1, power_w=1000), Step(value=2.5, power_w=2500))
    return _device(
        "boiler",
        DeviceKind.WATER_HEATER,
        {"level": "number.boiler_level"},
        StepsControl(kind="steps", role="level", steps=steps),
        nominal_w=2500,
    )


def relays_device() -> DeviceConfig:
    control = RelaysControl(
        kind="relays",
        roles=("r1", "r2", "r3"),
        steps=(
            RelayStep(relays=(), power_w=0),
            RelayStep(relays=("r1",), power_w=2000),
            RelayStep(relays=("r1", "r2"), power_w=4000),
            RelayStep(relays=("r1", "r2", "r3"), power_w=6000),
        ),
    )
    return _device(
        "boiler",
        DeviceKind.WATER_HEATER,
        {"r1": "switch.heater_1", "r2": "switch.heater_2", "r3": "switch.heater_3"},
        control,
        nominal_w=6000,
    )


def number_device(unit: str = "W", **kw: Any) -> DeviceConfig:
    if unit == "W":
        control = ContinuousControl(
            kind="continuous", role="power", unit="W", min=0, max=3000, step=100
        )
    elif unit == "A":
        control = ContinuousControl(
            kind="continuous", role="power", unit="A", min=6, max=16, voltage_v=230, phases=3
        )
    else:
        control = ContinuousControl(kind="continuous", role="power", unit="%", min=0, max=100)
    return _device(
        "rod",
        DeviceKind.NUMBER_LOAD,
        {"power": "number.rod"},
        control,
        nominal_w=kw.pop("nominal_w", 3000.0),
    )


def mode_device(entity: str = "select.mode") -> DeviceConfig:
    control = ModeControl(
        kind="mode",
        role="mode",
        modes=(Mode(value="eco", power_w=500), Mode(value="boost", power_w=2000)),
    )
    return _device("modal", DeviceKind.GENERIC, {"mode": entity}, control, nominal_w=2000)
