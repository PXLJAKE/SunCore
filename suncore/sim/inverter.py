# SPDX-License-Identifier: Apache-2.0
"""A hybrid inverter with its battery for the simulator (Phase 4 WP10).

Deterministic and time injected. The inverter runs its **own self-consumption control** (the
fallback SunCore relies on): surplus charges, deficit discharges, both capped by its limits and
stopped at its floor ``max(min SoC setting, BMS cut-off)``. SunCore can only change the limits,
through the same entities and services a real installation offers:

* ``kostal_plenticore`` style: ``number.sim_battery_min_soc`` (%, 5-100, step 5). A write is
  applied at once and the entity reports it after ``rest_refresh_s`` (the forced refresh); with
  ``rest_write_fails`` a write is **silently** ignored (``ApiException`` swallowed, research);
* ``generic``: ``number.sim_battery_max_charge`` / ``..._max_discharge`` / ``..._grid_charge``
  (W) next to the min-SoC number;
* ``kostal_modbus``: ``modbus.write_register`` on registers 1280/1282 (limits), 1284/1286
  (fallback limits) and 1288 (U32 timeout), float32 words in the configured order; modbus sensors
  ``sensor.sim_modbus_<register>`` report them (and 1080, 1076, 1078). Writes are ignored unless
  register 1080 is ``2``. **Fallback timer:** once 1280/1282 were written they must be re-written
  within 1288 seconds, otherwise 1284/1286 apply (Kostal §3.5).

The SoC the integration **reports** sags below the true SoC at night (``bms_drift``): the BMS
recalibrates by up to ``max_drift_pct`` in the dark hours and catches up while charging (the
maintainer's BMS drops from 10 % to 6-7 %). A **hang** (``hang_from_s`` .. ``hang_to_s``)
freezes the battery power at its last value and ignores every write.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from suncore.core.battery import ModbusByteOrder
from suncore.devices.battery.modbus_codec import words_float32, words_uint32

AdapterKind = Literal["generic", "kostal_plenticore", "kostal_modbus"]

MIN_SOC_ID: Final = "number.sim_battery_min_soc"
MAX_CHARGE_ID: Final = "number.sim_battery_max_charge"
MAX_DISCHARGE_ID: Final = "number.sim_battery_max_discharge"
GRID_CHARGE_ID: Final = "number.sim_battery_grid_charge"
MODBUS_IDS: Final = {
    5: "sensor.sim_modbus_5",
    1080: "sensor.sim_modbus_1080",
    1280: "sensor.sim_modbus_1280",
    1282: "sensor.sim_modbus_1282",
    1284: "sensor.sim_modbus_1284",
    1286: "sensor.sim_modbus_1286",
    1288: "sensor.sim_modbus_1288",
    1076: "sensor.sim_modbus_1076",
    1078: "sensor.sim_modbus_1078",
}
MODBUS_ROLES: Final = {
    "byte_order": MODBUS_IDS[5],
    "external_control": MODBUS_IDS[1080],
    "charge_limit": MODBUS_IDS[1280],
    "discharge_limit": MODBUS_IDS[1282],
    "fallback_charge_limit": MODBUS_IDS[1284],
    "fallback_discharge_limit": MODBUS_IDS[1286],
    "fallback_timeout": MODBUS_IDS[1288],
    "own_charge_limit": MODBUS_IDS[1076],
    "own_discharge_limit": MODBUS_IDS[1078],
}
MODBUS_HUB: Final = "sim_kostal"
MODBUS_SLAVE: Final = 71


class InverterParams(BaseModel):
    """The simulated inverter's behaviour (scenario ``house.inverter``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: AdapterKind = "generic"
    min_soc_pct: float = Field(default=10.0, ge=0, le=100)
    """The inverter's own min-SoC setting (the user's floor; the release baseline)."""
    bms_cutoff_pct: float = Field(default=5.0, ge=0, le=100)
    max_drift_pct: float = Field(default=0.0, ge=0, le=20)
    """How far the reported SoC sags below the true SoC by the end of the night."""
    drift_per_h: float = Field(default=1.0, ge=0, le=20)
    rest_refresh_s: float = Field(default=2.0, ge=0, le=120)
    rest_write_fails: bool = False
    external_control: int = Field(default=2, ge=0, le=255)
    """Register 1080 (2 = external battery management via Modbus enabled)."""
    byte_order: ModbusByteOrder = ModbusByteOrder.LITTLE
    fallback_timeout_s: int = Field(default=0, ge=0, le=10_800)
    """Register 1288 before SunCore writes it (0 = never written)."""
    hang_from_s: int | None = None
    hang_to_s: int | None = None
    modbus_scan_s: float = Field(default=0.0, ge=0, le=300)
    """The ``scan_interval`` of the user's modbus sensors: they show a register's value only at
    the next poll (0 = at once)."""
    stale_limits: dict[int, float] = Field(default_factory=dict)
    """Values left in the limit registers (1280/1282) by an earlier controller; not active
    until written again in this session, but what the sensors show (review minor 6)."""


@dataclass(slots=True)
class InverterLog:
    """Evidence for the tests: what the inverter did, independent of SunCore's view."""

    writes: list[tuple[datetime, str, Any]] = field(default_factory=list)
    fallback_active: list[tuple[datetime, bool]] = field(default_factory=list)


class SimInverter:
    """Battery dispatch under the inverter's own control and SunCore's limits."""

    def __init__(
        self,
        params: InverterParams,
        *,
        capacity_wh: float,
        max_charge_w: float,
        max_discharge_w: float,
        round_trip_efficiency: float,
        soc_max_pct: float,
        initial_soc_pct: float,
        start: datetime,
    ) -> None:
        self.params = params
        self.capacity_wh = capacity_wh
        self.own_charge_w = max_charge_w
        self.own_discharge_w = max_discharge_w
        self.leg = math.sqrt(round_trip_efficiency)
        self.soc_max_pct = soc_max_pct
        self.soc_pct = initial_soc_pct
        """The true state of charge."""
        self.drift_pct = 0.0
        self.start = start
        self.min_soc_pct = params.min_soc_pct
        self.max_charge_w = max_charge_w
        self.max_discharge_w = max_discharge_w
        self.grid_charge_w = 0.0
        self.registers: dict[int, float] = {
            1280: max_charge_w,
            1282: max_discharge_w,
            1284: 0.0,
            1286: 0.0,
            1288: float(params.fallback_timeout_s),
        }
        self.registers.update({k: float(v) for k, v in params.stale_limits.items()})
        self.limits_written_at: datetime | None = None
        self._scanned: dict[int, float] = {}
        self._scanned_at: datetime | None = None
        self.battery_w = 0.0
        self.log = InverterLog()
        self._shown_min_soc = params.min_soc_pct
        self._min_soc_changed_at: datetime | None = None

    # --- state -------------------------------------------------------------------------------

    def hung(self, now: datetime) -> bool:
        p = self.params
        if p.hang_from_s is None or p.hang_to_s is None:
            return False
        offset = (now - self.start).total_seconds()
        return p.hang_from_s <= offset < p.hang_to_s

    @property
    def reported_soc_pct(self) -> float:
        return max(0.0, self.soc_pct - self.drift_pct)

    def fallback_active(self, now: datetime) -> bool:
        """§3.5: the limits were written but not re-written within register 1288."""
        if self.params.adapter != "kostal_modbus" or self.limits_written_at is None:
            return False
        timeout = self.registers[1288]
        return timeout > 0 and (now - self.limits_written_at).total_seconds() > timeout

    def caps(self, now: datetime) -> tuple[float, float]:
        """Effective charge and discharge caps (W)."""
        if self.params.adapter == "kostal_modbus":
            if self.limits_written_at is None:
                return self.own_charge_w, self.own_discharge_w
            if self.fallback_active(now):
                return self.registers[1284], self.registers[1286]
            return self.registers[1280], self.registers[1282]
        return self.max_charge_w, self.max_discharge_w

    # --- physics -----------------------------------------------------------------------------

    def dispatch(self, now: datetime, surplus_w: float, step_s: float) -> float:
        """Battery AC power (+ = charging) for one step of ``step_s``; ``surplus = pv - load``."""
        hours = step_s / 3600
        if self.hung(now):
            self._integrate(self.battery_w, hours)  # stuck at the last power
            return self.battery_w
        charge_cap, discharge_cap = self.caps(now)
        # The inverter judges its floor by the BMS reading (which may sag below the true SoC).
        floor = max(self.min_soc_pct, self.params.bms_cutoff_pct)
        room_wh = max(0.0, (self.soc_max_pct - self.soc_pct) / 100 * self.capacity_wh)
        avail_wh = max(0.0, (self.reported_soc_pct - floor) / 100 * self.capacity_wh)
        charge_limit = min(charge_cap, self.own_charge_w, room_wh / (hours * self.leg))
        if self.grid_charge_w > 0 and room_wh > 0:
            power = min(max(self.grid_charge_w, surplus_w), max(0.0, charge_limit))
        elif surplus_w >= 0:
            power = min(surplus_w, max(0.0, charge_limit))
        else:
            limit = min(discharge_cap, self.own_discharge_w, avail_wh * self.leg / hours)
            power = -min(-surplus_w, max(0.0, limit))
        self._integrate(power, hours)
        self._drift(now, power, hours)
        self.battery_w = power
        self.log.fallback_active.append((now, self.fallback_active(now)))
        return power

    def _integrate(self, power: float, hours: float) -> None:
        if power >= 0:
            self.soc_pct += power * hours * self.leg / self.capacity_wh * 100
        else:
            self.soc_pct += power * hours / self.leg / self.capacity_wh * 100
        self.soc_pct = min(self.soc_max_pct, max(0.0, self.soc_pct))

    def _drift(self, now: datetime, power: float, hours: float) -> None:
        p = self.params
        if p.max_drift_pct <= 0:
            return
        if power <= 0 and not 6 <= now.hour < 20:
            self.drift_pct = min(p.max_drift_pct, self.drift_pct + p.drift_per_h * hours)
        elif power > 0:
            self.drift_pct = max(0.0, self.drift_pct - 4 * p.drift_per_h * hours)

    # --- Home Assistant side ----------------------------------------------------------------

    def entity_ids(self) -> tuple[str, ...]:
        ids = [MIN_SOC_ID]
        if self.params.adapter == "generic":
            ids += [MAX_CHARGE_ID, MAX_DISCHARGE_ID, GRID_CHARGE_ID]
        if self.params.adapter == "kostal_modbus":
            ids += list(MODBUS_IDS.values())
        return tuple(ids)

    def handles(self, entity_id: str) -> bool:
        return entity_id in self.entity_ids()

    def roles(self) -> dict[str, str]:
        """Role -> entity of the battery template of ``adapter``."""
        if self.params.adapter == "kostal_plenticore":
            return {"min_soc_number": MIN_SOC_ID}
        if self.params.adapter == "kostal_modbus":
            return dict(MODBUS_ROLES)
        return {
            "min_soc_number": MIN_SOC_ID,
            "max_charge_number": MAX_CHARGE_ID,
            "max_discharge_number": MAX_DISCHARGE_ID,
            "grid_charge_number": GRID_CHARGE_ID,
        }

    def states(self, now: datetime) -> list[tuple[str, str, dict[str, Any]]]:
        rows: list[tuple[str, str, dict[str, Any]]] = []

        def number(entity_id: str, value: float, unit: str, high: float, step: float) -> None:
            rows.append(
                (
                    entity_id,
                    _text(value),
                    {
                        "unit_of_measurement": unit,
                        "min": 5 if unit == "%" else 0,
                        "max": high,
                        "step": step,
                        "mode": "box",
                        "friendly_name": entity_id.split(".", 1)[1].replace("_", " "),
                    },
                )
            )

        number(MIN_SOC_ID, self._min_soc_shown(now), "%", 100, 5)
        if self.params.adapter == "generic":
            number(MAX_CHARGE_ID, self.max_charge_w, "W", self.own_charge_w, 1)
            number(MAX_DISCHARGE_ID, self.max_discharge_w, "W", self.own_discharge_w, 1)
            number(GRID_CHARGE_ID, self.grid_charge_w, "W", self.own_charge_w, 1)
        if self.params.adapter == "kostal_modbus":
            values = self._scan(now)
            for register, entity_id in MODBUS_IDS.items():
                unit = "s" if register == 1288 else (None if register in (5, 1080) else "W")
                attributes: dict[str, Any] = {"friendly_name": f"Kostal register {register}"}
                if unit is not None:
                    attributes["unit_of_measurement"] = unit
                rows.append((entity_id, _text(values[register]), attributes))
        return rows

    def _registers_now(self) -> dict[int, float]:
        return {
            5: 1.0 if self.params.byte_order is ModbusByteOrder.BIG else 0.0,
            1080: float(self.params.external_control),
            1076: self.own_charge_w,
            1078: self.own_discharge_w,
            **self.registers,
        }

    def _scan(self, now: datetime) -> dict[int, float]:
        """What the modbus sensors show: the registers as of their last poll."""
        scan = self.params.modbus_scan_s
        if (
            not self._scanned
            or scan <= 0
            or self._scanned_at is None
            or (now - self._scanned_at).total_seconds() >= scan
        ):
            self._scanned = self._registers_now()
            self._scanned_at = now
        return self._scanned

    def _min_soc_shown(self, now: datetime) -> float:
        """The REST integration shows a written floor only after its refresh."""
        changed = self._min_soc_changed_at
        if changed is not None and (now - changed).total_seconds() >= self.params.rest_refresh_s:
            self._shown_min_soc = self.min_soc_pct
            self._min_soc_changed_at = None
        return self._shown_min_soc

    # --- services ----------------------------------------------------------------------------

    def set_number(self, now: datetime, entity_id: str, value: float) -> None:
        """``number.set_value`` on one of the inverter's numbers (applied at once)."""
        self.log.writes.append((now, entity_id, value))
        if self.hung(now) or (self.params.rest_write_fails and entity_id == MIN_SOC_ID):
            return  # silently ignored, like a swallowed REST ApiException
        if entity_id == MIN_SOC_ID:
            self.min_soc_pct = min(100.0, max(5.0, value))
            self._min_soc_changed_at = now
        elif entity_id == MAX_CHARGE_ID:
            self.max_charge_w = min(self.own_charge_w, max(0.0, value))
        elif entity_id == MAX_DISCHARGE_ID:
            self.max_discharge_w = min(self.own_discharge_w, max(0.0, value))
        elif entity_id == GRID_CHARGE_ID:
            self.grid_charge_w = min(self.own_charge_w, max(0.0, value))

    def write_register(self, now: datetime, data: dict[str, Any]) -> None:
        """``modbus.write_register``: failures are silent (HA only logs them)."""
        self.log.writes.append((now, "modbus", dict(data)))
        if self.params.adapter != "kostal_modbus" or self.hung(now):
            return
        if data.get("hub") != MODBUS_HUB or data.get("slave", 1) != MODBUS_SLAVE:
            return  # another hub or unit: nothing happens here
        if self.params.external_control != 2:
            return
        address = data.get("address")
        words = data.get("value")
        if not isinstance(address, int) or not isinstance(words, list) or len(words) != 2:
            return
        order = self.params.byte_order
        if address == 1288:
            self.registers[1288] = float(words_uint32(words, order))
            return
        if address not in (1280, 1282, 1284, 1286):
            return
        value = words_float32(words, order)
        if address in (1280, 1282):
            value = min(self.own_charge_w if address == 1280 else self.own_discharge_w, value)
            self.limits_written_at = now
        self.registers[address] = max(0.0, value)


def _text(value: float) -> str:
    return f"{value:.1f}"


__all__ = [
    "GRID_CHARGE_ID",
    "MAX_CHARGE_ID",
    "MAX_DISCHARGE_ID",
    "MIN_SOC_ID",
    "MODBUS_HUB",
    "MODBUS_IDS",
    "MODBUS_ROLES",
    "MODBUS_SLAVE",
    "AdapterKind",
    "InverterParams",
    "SimInverter",
]
