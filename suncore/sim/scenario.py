# SPDX-License-Identifier: Apache-2.0
"""Simulator scenarios as data files (BRIEF 4.14).

A scenario bundles house parameters, a start time, a duration, a step, a cloud series, an
outdoor temperature, the simulated devices (physics plus the controller's view of them),
manual user events and a fault list. Faults and events are data only here; the fake HA
connection applies them and the scenario runner drives the control stack through them.
Scenario files live in ``suncore/sim/scenarios`` as YAML.

Devices (Phase 3 WP8): each entry has a ``physics`` block (``suncore.sim.devices``) and, when
the controller should control it, a ``template`` (a device template id) plus ``control``
overrides of the :class:`~suncore.core.devices.DeviceConfig` fields on top of the template
defaults. A device without ``template`` is an uncontrolled physical load.

Fault ``target``: a signal name (:data:`SimSignal`), an entity id, or ``device:<id>`` (every
entity of that simulated device).
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from importlib import resources
from typing import Annotated, Any, Final, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.battery import GridChargePolicy
from suncore.core.decisions import OperatingMode
from suncore.sim.devices import DeviceParams
from suncore.sim.house import House, HouseParams

SimSignal = Literal["grid_power", "pv_power", "battery_power", "battery_soc", "load_power"]
SIM_SIGNALS: Final[tuple[str, ...]] = (
    "grid_power",
    "pv_power",
    "battery_power",
    "battery_soc",
    "load_power",
)
FaultKind = Literal["unavailable", "stale", "disconnect", "frozen"]
"""``frozen``: the entity keeps reporting its last value (an inverter that hangs)."""
ManualAction = Literal[
    "turn_on", "turn_off", "set_temperature", "set_hvac_mode", "set_value", "select_option"
]
ManualSource = Literal["remote", "ui"]
"""``remote``: a push with an id-only context (remote control, vendor app);
``ui``: a change made in the Home Assistant UI (context with a ``user_id``)."""
_ENTITY_ID: Final = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_DEVICE_ID: Final = r"^[a-z][a-z0-9_]{0,47}$"


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
    target: str | None = None
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
        target = self.target
        if (
            target is not None
            and target not in SIM_SIGNALS
            and not _ENTITY_ID.match(target)
            and not (target.startswith("device:") and re.match(_DEVICE_ID, target[7:]))
        ):
            msg = f"fault target {target!r} is not a signal, an entity id or 'device:<id>'"
            raise ValueError(msg)
        return self


class OutdoorParams(BaseModel):
    """Outdoor temperature: a daily cosine around ``mean_c`` peaking at ``peak_hour`` (local)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mean_c: float = Field(default=16.0, ge=-40, le=50)
    amplitude_c: float = Field(default=6.0, ge=0, le=30)
    peak_hour: float = Field(default=15.0, ge=0, lt=24)

    def at(self, when: datetime) -> float:
        hour = when.hour + when.minute / 60 + when.second / 3600
        return self.mean_c + self.amplitude_c * math.cos(2 * math.pi * (hour - self.peak_hour) / 24)


class SimDeviceSpec(BaseModel):
    """One simulated device and, optionally, how the controller sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(pattern=_DEVICE_ID)]
    physics: DeviceParams
    template: str | None = None
    """Device template id (``lg.thinq_climate`` ...); ``None`` = not controlled by SunCore."""
    name: str | None = None
    control: dict[str, Any] = Field(default_factory=dict)
    """Overrides of ``DeviceConfig`` fields (``priority``, ``power_model``, ``comfort``,
    ``min_run_s`` ...) merged over the template defaults."""
    mode: OperatingMode = OperatingMode.AUTOMATIC
    """Operating mode of the device in the runner (the global mode is automatic)."""

    @model_validator(mode="after")
    def _control_needs_template(self) -> SimDeviceSpec:
        if self.template is None and self.control:
            msg = f"device {self.id!r}: `control` needs a `template`"
            raise ValueError(msg)
        return self


class ManualEvent(BaseModel):
    """The user changes a device outside SunCore at ``at_s`` seconds after the start."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    at_s: int = Field(ge=0)
    device: Annotated[str, StringConstraints(pattern=_DEVICE_ID)]
    action: ManualAction
    value: float | str | None = None
    source: ManualSource = "remote"


class ControlParams(BaseModel):
    """Site-level controller inputs of the scenario (the runner builds ``ControlSettings``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    import_limit_w: float | None = Field(default=None, ge=0)
    reserve_soc_pct: float | None = Field(default=None, ge=0, le=100)
    climate_before_export: bool = True
    settings: dict[str, float | int] = Field(default_factory=dict)
    """Overrides of ``ControlSettings`` (``smoothing_s``, ``min_write_interval_s`` ...)."""


PriceProfile = Literal["default", "negative_midday"]
"""``negative_midday``: the spot price turns negative around 13:00 (sunny weekends in DE)."""


class PriceParams(BaseModel):
    """The simulated dynamic price (what the fake ``tibber.get_prices`` answers)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: PriceProfile = "default"
    tomorrow_after_hour: int = Field(default=13, ge=0, le=23)
    """Local hour from which tomorrow's prices are published (the real market: afternoon)."""


class SimBatteryControl(BaseModel):
    """The battery as SunCore controls it in a scenario (WP10; the inverter is
    ``house.inverter``). Mapped onto ``BatterySettings`` by the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: OperatingMode = OperatingMode.SHADOW
    reserve_pct: float = Field(default=10.0, ge=0, le=100)
    bms_drift_margin_pct: float = Field(default=5.0, ge=0, le=100)
    max_soc_pct: float = Field(default=100.0, ge=0, le=100)
    grid_charge: GridChargePolicy = GridChargePolicy()
    fallback_measured: bool = False
    heartbeat_s: float = Field(default=20.0, ge=5, le=3600)
    fallback_timeout_s: int = Field(default=60, ge=30, le=10_800)


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
    outdoor: OutdoorParams = OutdoorParams()
    devices: tuple[SimDeviceSpec, ...] = ()
    manual: tuple[ManualEvent, ...] = ()
    control: ControlParams = ControlParams()
    prices: PriceParams = PriceParams()
    battery: SimBatteryControl | None = None
    """WP10: SunCore controls the battery through ``house.inverter`` (``None`` = observe)."""

    @model_validator(mode="after")
    def _check(self) -> Scenario:
        if self.duration_s < self.step_s:
            msg = "duration_s must be at least one step"
            raise ValueError(msg)
        times = [c.at_s for c in self.clouds]
        if times != sorted(set(times)):
            msg = "clouds must be strictly ascending by at_s"
            raise ValueError(msg)
        ids = [d.id for d in self.devices]
        if len(set(ids)) != len(ids):
            msg = "device ids must be unique"
            raise ValueError(msg)
        for event in self.manual:
            if event.device not in ids:
                msg = f"manual event refers to unknown device {event.device!r}"
                raise ValueError(msg)
        for fault in self.faults:
            target = fault.target
            if target is not None and target.startswith("device:") and target[7:] not in ids:
                msg = f"fault refers to unknown device {target[7:]!r}"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _battery(self) -> Scenario:
        if self.battery is not None and self.house.inverter is None:
            msg = "battery control needs house.inverter"
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
