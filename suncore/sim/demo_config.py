# SPDX-License-Identifier: Apache-2.0
"""The demo installation's configuration: a fully set-up house (Phase 2).

The demo starts *finished*: region, tariff, forecast, devices, goals, operation (shadow) and a
completed wizard. The wizard stays reachable (``#/setup``, settings). Devices are instantiated
from the real built-in templates with the entities the simulator registry serves, the same way
the wizard does (template defaults plus the user's input), so the demo cannot drift from
the templates: a template change that breaks the instantiation fails the demo start.
"""

from __future__ import annotations

from typing import Final

from suncore.core.battery import BatteryModel, BatterySettings
from suncore.core.config import (
    BatteryTarget,
    DevicesConfig,
    ExportTariff,
    ForecastConfig,
    ForecastSourceConfig,
    GoalsConfig,
    ImportTariff,
    LoadForecastConfig,
    OperationConfig,
    RegionConfig,
    TariffConfig,
    WizardState,
)
from suncore.core.decisions import OperatingMode
from suncore.core.devices import DeviceConfig
from suncore.core.site import EntityRef
from suncore.sim.demo_devices import (
    AC_B_POWER_ID,
    AC_ENERGY_TODAY_ID,
    AC_ENERGY_YESTERDAY_ID,
    AC_ID,
    FORECAST_TODAY_ID,
    FORECAST_TOMORROW_ID,
    OPEN_METEO_TODAY_ID,
    OPEN_METEO_TOMORROW_ID,
    POOL_POWER_ID,
    POOL_SWITCH_ID,
    PRICE_ID,
    ROD_LIMIT_ID,
    WEATHER_ID,
    WORKDAY_ID,
)
from suncore.sim.inverter import GRID_CHARGE_ID, MAX_CHARGE_ID, MAX_DISCHARGE_ID, MIN_SOC_ID
from suncore.storage import ConfigStore
from suncore.templates import DeviceTemplate, TemplateCatalog

WIZARD_STEPS: Final = (
    "region",
    "energy",
    "grid",
    "pv",
    "battery",
    "tariff",
    "devices",
    "priorities",
    "mode",
    "summary",
)
"""Ids of the wizard steps (``frontend/src/lib/wizard/steps.ts``)."""

ROD_NOMINAL_W: Final = 3000.0


def instantiate_device(
    template: DeviceTemplate,
    *,
    device_id: str,
    name: str,
    entities: dict[str, str],
    priority: int,
    nominal_w: float | None = None,
    power_kind: str | None = None,
    comfort: tuple[float, float] | None = None,
) -> DeviceConfig:
    """A device from the template defaults and the user's mapping (what the wizard stores)."""
    d = template.defaults
    power_model = d.power_model.model_dump(mode="json", exclude_none=True)
    if power_kind is not None:
        power_model["kind"] = power_kind
        if power_kind == "measured":
            power_model["power_role"] = "power"
            power_model.pop("energy_role", None)
            power_model.pop("counter_update_s", None)
    if nominal_w is not None and power_model["kind"] in ("learned", "nominal"):
        power_model["nominal_w"] = nominal_w
    if power_model["kind"] == "nominal":
        power_model.pop("power_role", None)
        power_model.pop("energy_role", None)
    band = {"min_c": comfort[0], "max_c": comfort[1]} if comfort else None
    return DeviceConfig.model_validate(
        {
            "id": device_id,
            "template_id": template.id,
            "name": name,
            "kind": template.device_kind.value,
            "entities": {role: EntityRef(entity_id=eid) for role, eid in entities.items()},
            "power_model": power_model,
            "control": d.control.model_dump(mode="json", exclude_none=True),
            "priority": priority,
            "min_run_s": d.min_run_s,
            "min_pause_s": d.min_pause_s,
            "max_switches_per_hour": d.max_switches_per_hour,
            "hysteresis_w": d.hysteresis_w,
            "hysteresis_delay_s": d.hysteresis_delay_s,
            "settle_s": d.settle_s,
            "comfort": band or (d.comfort.model_dump() if d.comfort else None),
        }
    )


def _device_template(catalog: TemplateCatalog, template_id: str) -> DeviceTemplate:
    template = catalog.get(template_id)
    if not isinstance(template, DeviceTemplate):
        msg = f"demo needs the device template {template_id!r}"
        raise TypeError(msg)
    return template


def demo_devices(catalog: TemplateCatalog) -> DevicesConfig:
    """AC (LG ThinQ template, measured power from the second integration), pool pump, rod."""
    return DevicesConfig(
        devices=[
            instantiate_device(
                _device_template(catalog, "lg.thinq_climate"),
                device_id="living_room_ac",
                name="Living room AC",
                entities={
                    "climate": AC_ID,
                    "energy_today": AC_ENERGY_TODAY_ID,
                    "energy_yesterday": AC_ENERGY_YESTERDAY_ID,
                    # power of the same unit from the `smartthinq_sensors` device (5 min poll)
                    "power": AC_B_POWER_ID,
                },
                priority=20,
                power_kind="measured",
                comfort=(22.0, 26.0),
            ),
            instantiate_device(
                _device_template(catalog, "generic.switch"),
                device_id="pool_pump",
                name="Pool pump",
                entities={"switch": POOL_SWITCH_ID, "power": POOL_POWER_ID},
                priority=10,
                power_kind="measured",
            ),
            instantiate_device(
                _device_template(catalog, "generic.number_load"),
                device_id="heating_rod",
                name="Heating rod",
                entities={"number": ROD_LIMIT_ID},
                priority=30,
                nominal_w=ROD_NOMINAL_W,
            ),
        ]
    )


async def seed_demo_config(
    config: ConfigStore, catalog: TemplateCatalog, *, battery_control: bool = False
) -> None:
    """Store every section except ``site`` (the caller stores the site).

    ``battery_control``: the demo house has a controllable inverter (``generic`` adapter), the
    battery section maps its number entities.
    """
    await config.put(
        "region", RegionConfig(region_id="de", currency="EUR", timezone="Europe/Berlin")
    )
    await config.put(
        "tariff",
        TariffConfig(
            import_=ImportTariff(
                kind="dynamic",
                template_id="tibber.price",
                price_entity=EntityRef(entity_id=PRICE_ID),
            ),
            export=ExportTariff(kind="fixed", fixed_price=0.08),
        ),
    )
    await config.put(
        "forecast",
        ForecastConfig(
            sources=[
                ForecastSourceConfig(
                    template_id="solcast.pv",
                    entities={
                        "forecast_today": EntityRef(entity_id=FORECAST_TODAY_ID),
                        "forecast_tomorrow": EntityRef(entity_id=FORECAST_TOMORROW_ID),
                    },
                ),
                ForecastSourceConfig(
                    template_id="open_meteo_solar_forecast.pv",
                    entities={
                        "energy_today": EntityRef(entity_id=OPEN_METEO_TODAY_ID),
                        "energy_tomorrow": EntityRef(entity_id=OPEN_METEO_TOMORROW_ID),
                    },
                ),
                # Two entries are linked in the demo's Energy dashboard: the choice is explicit.
                ForecastSourceConfig(
                    template_id="forecast_solar.pv", config_entry_id="sim_entry_forecast_solar"
                ),
            ],
            load=LoadForecastConfig(
                workday=EntityRef(entity_id=WORKDAY_ID), weather=EntityRef(entity_id=WEATHER_ID)
            ),
        ),
    )
    await config.put("devices", demo_devices(catalog))
    await config.put(
        "goals",
        GoalsConfig(
            battery_target=BatteryTarget(soc_pct=30.0, time="06:00"),
            climate_before_export=True,
        ),
    )
    await config.put("operation", OperationConfig(mode=OperatingMode.SHADOW))
    # Observe only (no adapter): the demo battery is read, never written. The model (the
    # simulator's 10 kWh, 5 kW battery) lets the risk model show its buffer and evening target.
    battery = BatterySettings(
        model=BatteryModel(capacity_wh=10_000.0, max_charge_w=5_000.0, max_discharge_w=5_000.0)
    )
    if battery_control:
        battery = battery.model_copy(
            update={
                "adapter_template_id": "generic.battery",
                "entities": {
                    "min_soc_number": EntityRef(entity_id=MIN_SOC_ID),
                    "max_charge_number": EntityRef(entity_id=MAX_CHARGE_ID),
                    "max_discharge_number": EntityRef(entity_id=MAX_DISCHARGE_ID),
                    "grid_charge_number": EntityRef(entity_id=GRID_CHARGE_ID),
                },
            }
        )
    await config.put("battery", battery)
    await config.put("wizard", WizardState(completed=True, completed_steps=list(WIZARD_STEPS)))
