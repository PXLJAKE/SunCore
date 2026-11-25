# SPDX-License-Identifier: Apache-2.0
"""The demo house's flexible loads (Phase 7, WP3): EV on a wallbox, washing machine, heat pump.

Behind ``SUNCORE_DEMO_FLEX=1`` the demo house gains the three Phase 6 loads, built from the
existing simulator devices (:class:`~suncore.sim.devices.SimWallbox`,
:class:`~suncore.sim.devices.SimProgram`, :class:`~suncore.sim.devices.SimHeatPump`) and the
generic templates, so the Phase 6 cards and views have something to show:

* ``wallbox`` (``generic.wallbox``): a 60 kWh car plugged in since 08:00 (scenario time) with
  30 %, charging stopped; the runtime EV state asks for 80 % by 07:00 the next morning (the
  numbers of the ``ev_arrives_late`` scenario). In shadow the car does not charge: the cards
  show what SunCore *would* do;
* ``washer`` (``generic.program_plug``): a programme of two hours, loaded at 10:30; an armed job
  asks for "finished by 17:00";
* ``heat_pump`` (``generic.heat_pump_sg_ready``): SG-Ready contacts, store probe (48.5 degrees at
  midnight, comfort 45-55) and a power sensor.

Times are scenario times (the scenario starts at local midnight), so the demo is deterministic
whatever the wall clock says. Nothing here writes to a device: the configs are stored like the
wizard stores them and the operating mode stays what the demo already uses (shadow).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final

from suncore.core.devices import DeviceConfig
from suncore.core.flex import EvMode, EvTarget
from suncore.sim.devices import (
    HeatPumpParams,
    ProgramParams,
    SimDevice,
    WallboxParams,
)
from suncore.sim.scenario import SimDeviceSpec
from suncore.templates import DeviceTemplate, TemplateCatalog

WALLBOX_ID: Final = "wallbox"
WASHER_ID: Final = "washer"
HEAT_PUMP_ID: Final = "heat_pump"

CAR_ARRIVES_S: Final = 8 * 3600
"""The car is plugged in at 08:00, before any demo start time."""
CAR_SOC_PCT: Final = 30.0
CAR_TARGET_SOC_PCT: Final = 80.0
CAR_TARGET_HOUR: Final = 7
WASHER_LOADED_S: Final = 10 * 3600 + 30 * 60
WASHER_DEADLINE_HOUR: Final = 17
HEAT_PUMP_COMFORT_C: Final = (45.0, 55.0)

_WASHER_PROFILE_W: Final = (2000.0, 480.0, 240.0, 240.0, 240.0, 240.0, 1000.0, 480.0)
_WASHER_PROFILE_WH: Final = (500.0, 120.0, 60.0, 60.0, 60.0, 60.0, 250.0, 120.0)

_HEAT_PUMP_DRAW_W: Final = (
    *(0.0,) * 18,
    600.0,
    900.0,
    600.0,
    300.0,
    *(0.0,) * 2,
)
"""Hot-water draw per hour (W): evening only, so the store is still warm at midday and the heat
pump is a surplus candidate rather than already running for the morning showers."""


def demo_flex_specs() -> tuple[SimDeviceSpec, ...]:
    """The three flexible loads as simulator specs (physics plus the controller's overrides)."""
    return (
        SimDeviceSpec(
            id=WALLBOX_ID,
            name="Garage wallbox",
            template="generic.wallbox",
            physics=WallboxParams(
                current_max=16.0,
                initial_current_a=16.0,
                initial_enabled=False,
                initial_soc_pct=CAR_SOC_PCT,
                capacity_wh=60_000.0,
                arrive_s=CAR_ARRIVES_S,
            ),
            control={
                "priority": 40,
                "power_model": {
                    "kind": "measured",
                    "power_role": "charger_power",
                    "nominal_w": 11040,
                },
                "ev": {
                    "capacity_wh": 60_000,
                    "vehicle_soc_role": "vehicle_soc",
                    "connected_role": "connected",
                },
                "behaviour_confirmed": True,
            },
        ),
        SimDeviceSpec(
            id=WASHER_ID,
            name="Washing machine",
            template="generic.program_plug",
            physics=ProgramParams(
                method="plug", profile_w=_WASHER_PROFILE_W, loaded_at_s=WASHER_LOADED_S
            ),
            control={
                "priority": 50,
                "power_model": {"kind": "measured", "power_role": "power", "nominal_w": 2000},
                "program": {
                    "runtime_min": 120,
                    "profile_wh": list(_WASHER_PROFILE_WH),
                    "start_method": "plug",
                    "idle_w": 5,
                    "resume_confirmed": True,
                },
                "behaviour_confirmed": True,
            },
        ),
        SimDeviceSpec(
            id=HEAT_PUMP_ID,
            name="Heat pump",
            template="generic.heat_pump_sg_ready",
            physics=HeatPumpParams(
                variant="sg_ready",
                power_sensor=True,
                nominal_w=1500.0,
                initial_temp_c=48.5,
                volume_l=800.0,
                draw_w=_HEAT_PUMP_DRAW_W,
            ),
            control={
                "priority": 60,
                "power_model": {"kind": "measured", "power_role": "power", "nominal_w": 1500},
                "comfort": {"min_c": HEAT_PUMP_COMFORT_C[0], "max_c": HEAT_PUMP_COMFORT_C[1]},
                "behaviour_confirmed": True,
            },
        ),
    )


def demo_flex_configs(
    catalog: TemplateCatalog, devices: Mapping[str, SimDevice]
) -> list[DeviceConfig]:
    """The ``DeviceConfig`` of each flexible load (template defaults, the simulated device's
    entities, the overrides above), exactly what the wizard would store."""
    from suncore.sim.runner import build_device_config  # noqa: PLC0415 - heavy import

    configs: list[DeviceConfig] = []
    for spec in demo_flex_specs():
        assert spec.template is not None
        template = catalog.get(spec.template)
        if not isinstance(template, DeviceTemplate):
            msg = f"demo needs the device template {spec.template!r}"
            raise TypeError(msg)
        configs.append(build_device_config(template, spec, devices[spec.id]))
    return configs


def next_local_time(now: datetime, hour: int) -> datetime:
    """The next ``hour``:00 after ``now`` in ``now``'s timezone (``now`` is timezone-aware)."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def demo_ev_target(now: datetime) -> tuple[EvMode, EvTarget]:
    """The car's request: :data:`CAR_TARGET_SOC_PCT` by the next 07:00."""
    return EvMode.TARGET, EvTarget(
        soc_pct=CAR_TARGET_SOC_PCT, by=next_local_time(now, CAR_TARGET_HOUR)
    )


def demo_washer_deadline(now: datetime) -> datetime:
    """ "Finished by" of the armed washing job: the next 17:00."""
    return next_local_time(now, WASHER_DEADLINE_HOUR)
