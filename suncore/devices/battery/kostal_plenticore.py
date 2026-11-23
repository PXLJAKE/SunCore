# SPDX-License-Identifier: Apache-2.0
"""The ``kostal_plenticore`` battery adapter: the core integration's ``Battery:MinSoc`` number.

Facts (``docs/research/integrations.md`` "Kostal Plenticore", HA core ``5cfe9aad``
``components/kostal_plenticore/{number,select,coordinator}.py``):

* ``number`` "Battery min SoC" (``devices:local`` / ``Battery:MinSoc``, %, default 5-100 step 5;
  the live ``min``/``max`` attributes come from the inverter). **Disabled by default** (the user
  enables it), no translation key, ``unique_id`` suffix ``devices:local_Battery:MinSoc``.
* ``number.set_value`` writes immediately over REST, then refreshes the settings coordinator.
  **A failed write is silent** (``async_write_data`` swallows ``ApiException``), so a write is
  believed only when the entity shows the value afterwards: the gate's read-back after
  ``settle_s`` (35 s: the settings coordinator polls every 30 s - should the forced refresh not
  land, the next poll does) is mandatory and a mismatch is a failed write.
* The select "Battery charging / usage mode" (``None`` / ``SmartBatteryControl`` /
  ``TimeControl``) reports a written option only after the next 30 s poll; this adapter only
  **reads** it (shown in the state, never written).
* **No charge or discharge power set-point** exists over REST: the capabilities are ``min_soc``
  only. A discharge lock is ``min_soc_pct`` at (rounded up to the step) the live SoC, the reserve
  a floor at the effective reserve; grid charging is not executable (plan and shadow only).
* No hardware fallback: a floor SunCore wrote survives a SunCore crash until ``recover()`` at the
  next start releases it (ADR-0013 risk); the gate holds it only while the plan interval asks.

``UNVERIFIED`` on hardware: the read-back timing after the forced refresh; whether the
inverter rejects values outside its live range or clamps them.
"""

from __future__ import annotations

from typing import Final

from suncore.core.battery import BatterySettings
from suncore.core.battery_adapter import BatteryAdapterSpec
from suncore.devices.battery.generic import GenericBatteryDriver
from suncore.devices.drivers.base import ServiceWriter, StateLookup

KOSTAL_REST_SETTLE_S: Final = 35.0
"""One settings poll (30 s) plus margin."""
CHARGE_MODE_ROLE: Final = "charge_mode"
"""The read-only select role (``None`` / ``Battery:SmartBatteryControl:Enable`` / ...)."""


class KostalPlenticoreDriver(GenericBatteryDriver):
    """``min_soc`` through ``number.<...>_battery_min_soc`` of ``kostal_plenticore``."""

    def __init__(
        self,
        spec: BatteryAdapterSpec,
        settings: BatterySettings,
        *,
        write: ServiceWriter,
        states: StateLookup,
    ) -> None:
        if spec.control_kinds() != {"min_soc"}:
            msg = "kostal_plenticore: the template must have exactly one `min_soc` control"
            raise ValueError(msg)
        super().__init__(spec, settings, write=write, states=states)
        self._settle_s = max(self._settle_s, KOSTAL_REST_SETTLE_S)

    def charge_mode(self) -> str | None:
        """The inverter's battery usage mode as the select shows it (read only)."""
        view = self.lookup(self.entity_id(CHARGE_MODE_ROLE))
        return None if view is None else view.state


__all__ = ["CHARGE_MODE_ROLE", "KOSTAL_REST_SETTLE_S", "KostalPlenticoreDriver"]
