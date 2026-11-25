# SPDX-License-Identifier: Apache-2.0
"""The inverter's export / AC output limiter for the simulator (PR 6-G).

Deterministic and time injected. The inverter always enforces its **own** static export limit
(``hardware_export_limit_w``, the maintainer's 13.5 kW) and, on top, whatever limit the entity
SunCore writes holds - through the same entities and services a real installation offers:

* ``generic_number``: ``number.sim_inverter_export_limit`` (W) for ``export_limit`` or
  ``number.sim_inverter_ac_limit`` (%) for ``ac_limit``, applied at once;
* ``kostal_rest``: ``number.sim_inverter_active_power_limitation`` (W), shown after
  ``rest_refresh_s``; with ``write_fails`` a write is silently ignored (``ApiException``
  swallowed, research ``integrations.md``). The simulator reads it as an export limit - the
  real semantics are ``UNVERIFIED``;
* ``kostal_modbus``: ``modbus.write_register`` on register 533 (``int`` 1..100 % of
  ``nominal_ac_w``, hub ``sim_kostal``, unit 71); ``sensor.sim_modbus_533`` reads it back (the
  real readability is ``UNVERIFIED``). The register is **volatile**: at every offset in
  ``resets_at_s`` the inverter restarts and 533 is back at 100 %.

Physics: the battery has taken its share first (the hybrid inverter's own control); an export
limit then curtails PV so that ``pv - load - battery <= limit``; an AC limit curtails PV so that
the AC output ``pv - battery`` stays at or below ``pct * nominal`` (battery discharge counts as
AC output: whether the real register 533 includes it is ``UNVERIFIED``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from suncore.sim.inverter import MODBUS_HUB, MODBUS_SLAVE

LimiterAdapter = Literal["generic_number", "kostal_rest", "kostal_modbus"]
LimiterCapability = Literal["export_limit", "ac_limit"]

GENERIC_EXPORT_ID: Final = "number.sim_inverter_export_limit"
GENERIC_AC_ID: Final = "number.sim_inverter_ac_limit"
REST_LIMIT_ID: Final = "number.sim_inverter_active_power_limitation"
MODBUS_533_ID: Final = "sensor.sim_modbus_533"
REGISTER_533: Final = 533


class ExportLimiterParams(BaseModel):
    """The simulated inverter limiter (scenario ``house.export_limiter``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: LimiterAdapter = "generic_number"
    capability: LimiterCapability = "export_limit"
    nominal_ac_w: float = Field(default=10_000.0, gt=0)
    hardware_export_limit_w: float | None = Field(default=None, ge=0)
    """The inverter's own static export limit (always enforced, never written by SunCore)."""
    initial_export_limit_w: float = Field(default=13_500.0, ge=0)
    """The user's own value of the export-limit number (the release baseline)."""
    initial_ac_pct: float = Field(default=100.0, ge=1, le=100)
    rest_refresh_s: float = Field(default=2.0, ge=0, le=120)
    write_fails: bool = False
    resets_at_s: tuple[int, ...] = ()
    """Inverter restarts (offsets from the start): the volatile register 533 is back at 100 %."""


@dataclass(slots=True)
class LimiterLog:
    """Evidence for the tests: what the limiter did, independent of SunCore's view."""

    writes: list[tuple[datetime, str, Any]] = field(default_factory=list)
    curtailed: list[tuple[datetime, float]] = field(default_factory=list)
    resets: list[datetime] = field(default_factory=list)


class SimExportLimiter:
    """The limit the inverter enforces and the entities that show it."""

    def __init__(self, params: ExportLimiterParams, *, start: datetime) -> None:
        self.params = params
        self.start = start
        self.export_limit_w = params.initial_export_limit_w
        self.ac_pct = params.initial_ac_pct if params.adapter != "kostal_modbus" else 100.0
        self._shown_export_w = self.export_limit_w
        self._changed_at: datetime | None = None
        self._resets = sorted(params.resets_at_s)
        self.log = LimiterLog()

    @property
    def capability(self) -> LimiterCapability:
        if self.params.adapter == "kostal_modbus":
            return "ac_limit"
        if self.params.adapter == "kostal_rest":
            return "export_limit"
        return self.params.capability

    @property
    def limit_entity(self) -> str:
        if self.params.adapter == "kostal_modbus":
            return MODBUS_533_ID
        if self.params.adapter == "kostal_rest":
            return REST_LIMIT_ID
        return GENERIC_EXPORT_ID if self.params.capability == "export_limit" else GENERIC_AC_ID

    # --- physics -----------------------------------------------------------------------------

    def _reset_due(self, now: datetime) -> None:
        offset = (now - self.start).total_seconds()
        while self._resets and self._resets[0] <= offset:
            self._resets.pop(0)
            if self.params.adapter == "kostal_modbus":
                self.ac_pct = 100.0
            self.log.resets.append(now)

    def curtail(self, now: datetime, pv_w: float, load_w: float, battery_w: float) -> float:
        """The PV power after the inverter's limits (never above ``pv_w``)."""
        self._reset_due(now)
        out = pv_w
        if self.capability == "ac_limit":
            ac_cap = self.ac_pct / 100.0 * self.params.nominal_ac_w
            if ac_cap + battery_w < out:
                # A real inverter regulates around its limit; a perfectly constant PV reading
                # would (rightly) trip the watchdog's frozen-PV check after frozen_pv_s.
                out = max(0.0, ac_cap + battery_w - _jitter_w(now, self.start))
        caps = [c for c in (self.params.hardware_export_limit_w,) if c is not None]
        if self.capability == "export_limit":
            caps.append(self.export_limit_w)
        if caps:
            export = out - load_w - battery_w
            cap = min(caps)
            if export > cap:
                out = max(0.0, out - (export - cap))
        if out < pv_w:
            self.log.curtailed.append((now, pv_w - out))
        return out

    # --- Home Assistant side ----------------------------------------------------------------

    def handles(self, entity_id: str) -> bool:
        return entity_id == self.limit_entity and self.params.adapter != "kostal_modbus"

    def states(self, now: datetime) -> list[tuple[str, str, dict[str, Any]]]:
        self._reset_due(now)
        if self.params.adapter == "kostal_modbus":
            return [
                (
                    MODBUS_533_ID,
                    f"{self.ac_pct:.0f}",
                    {"unit_of_measurement": "%", "friendly_name": "Kostal register 533"},
                )
            ]
        if self.capability == "ac_limit":
            value, unit, high = self.ac_pct, "%", 100.0
        else:
            value, unit = self._shown(now), "W"
            high = self.params.nominal_ac_w * 2
        return [
            (
                self.limit_entity,
                f"{value:.1f}",
                {
                    "unit_of_measurement": unit,
                    "min": 0,
                    "max": high,
                    "step": 1,
                    "mode": "box",
                    "friendly_name": self.limit_entity.split(".", 1)[1].replace("_", " "),
                },
            )
        ]

    def _shown(self, now: datetime) -> float:
        if self.params.adapter != "kostal_rest":
            return self.export_limit_w
        changed = self._changed_at
        if changed is not None and (now - changed).total_seconds() >= self.params.rest_refresh_s:
            self._shown_export_w = self.export_limit_w
            self._changed_at = None
        return self._shown_export_w

    def set_number(self, now: datetime, entity_id: str, value: float) -> None:
        """``number.set_value`` on the limit number (applied at once)."""
        self.log.writes.append((now, entity_id, value))
        if self.params.write_fails:
            return  # silently ignored
        if self.capability == "ac_limit":
            self.ac_pct = min(100.0, max(0.0, value))
        else:
            self.export_limit_w = max(0.0, value)
            self._changed_at = now

    def write_register(self, now: datetime, data: dict[str, Any]) -> bool:
        """``modbus.write_register`` on 533; ``False`` = not for this limiter (another register,
        another hub or unit). A wrong value is ignored silently (HA only logs)."""
        if (
            self.params.adapter != "kostal_modbus"
            or data.get("address") != REGISTER_533
            or data.get("hub") != MODBUS_HUB
            or data.get("slave", 1) != MODBUS_SLAVE
        ):
            return False
        self.log.writes.append((now, "modbus", dict(data)))
        value = data.get("value")
        if self.params.write_fails or not isinstance(value, int) or isinstance(value, bool):
            return True
        if 1 <= value <= 100:
            self.ac_pct = float(value)
        return True


def _jitter_w(now: datetime, start: datetime) -> float:
    """0-8 W below the limit, deterministic in time (10 s slots)."""
    return float((int((now - start).total_seconds()) // 10) % 5) * 2.0


__all__ = [
    "GENERIC_AC_ID",
    "GENERIC_EXPORT_ID",
    "MODBUS_533_ID",
    "REGISTER_533",
    "REST_LIMIT_ID",
    "ExportLimiterParams",
    "LimiterAdapter",
    "LimiterCapability",
    "SimExportLimiter",
]
