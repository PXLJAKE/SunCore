# SPDX-License-Identifier: Apache-2.0
"""The inverter limit adapters (PR 6-G): a ``number`` entity, or Kostal register 533.

Both read the limit back from the entity cache only (never ``get_states``) and write through
the injected, rate-limited ``ServiceWriter``. A write is never believed until the read-back
shows it (the gate's X4): ``number.set_value`` on the Kostal REST number fails silently
(``coordinator.async_write_data`` swallows ``ApiException``) and ``modbus.write_register``
only logs a failed write (research ``ha-websocket-api.md`` "Modbus write action").

* :class:`NumberLimitDriver` (``generic_number``, ``kostal_rest``): ``number.set_value`` with
  the limit in the entity's unit (W, kW or %). The release writes the user's own value back.
* :class:`ModbusLimitDriver` (``kostal_modbus``): ``modbus.write_register`` with ``hub``,
  ``slave``, ``address`` 533 and an ``int`` value, which Home Assistant sends with function
  code 0x06 (write single register; verified 2026-11-24 against ``home-assistant/core`` ``dev``
  ``components/modbus/services.py``, Kostal lists 0x06 for 533). The register is volatile
  (lost on an inverter reset, Kostal §3.3): the gate re-writes it every ``heartbeat_s`` while
  its lease holds, and a release always writes ``default_pct`` (100 %) - the gate takes the
  register over only while it shows that default (review A2). Read-back through a ``modbus:``
  YAML sensor on register 533 that the user configures - **whether 533 can be read at all is
  ``UNVERIFIED``** (Kostal lists only FC 0x06 for it); without a readable sensor nothing is
  ever believed and the gate never takes over.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from suncore.core.config import InverterConfig, InverterModbusLink
from suncore.core.decisions import Action, Intent, InverterLimitAction, Reason
from suncore.core.inverter import INVERTER_ASSET_ID, InverterAdapterSpec, LimitCapability
from suncore.devices.drivers.base import (
    UNAVAILABLE_STATES,
    ActionNotSupportedError,
    EntityStateView,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    as_float,
)

MODBUS_DOMAIN: Final = "modbus"
WRITE_REGISTER: Final = "write_register"
KOSTAL_ACTIVE_POWER_REGISTER: Final = 533
"""Kostal §3.3 "Active power setpoint, 1..100", U16 %, FC 0x06 (volatile)."""
_WATT_UNITS: Final = {"W": 1.0, "kW": 1000.0}


class InverterDriver:
    """Common base: identity, the read-back entity, the single write helper, the held limit."""

    def __init__(
        self,
        spec: InverterAdapterSpec,
        config: InverterConfig,
        *,
        write: ServiceWriter,
        states: StateLookup,
    ) -> None:
        if config.capability is None:
            msg = "the inverter adapter needs a capability"
            raise ValueError(msg)
        if config.limit_entity is None:
            msg = "the inverter adapter needs the entity that shows the limit (read-back)"
            raise ValueError(msg)
        self._spec = spec
        self._config = config
        self._capability: LimitCapability = config.capability
        self._entity = config.limit_entity.entity_id
        self._write = write
        self._states = states
        self._applied: InverterLimitAction | None = None
        self._context_ids: tuple[str, ...] = ()
        self._unit: str | None = None
        """The entity's unit of the last good read (review A5): a write while the entity is
        unavailable converts with it; never read = no write."""

    # --- identity ---------------------------------------------------------------------------

    @property
    def asset_id(self) -> str:
        return INVERTER_ASSET_ID

    @property
    def capability(self) -> LimitCapability:
        return self._capability

    @property
    def settle_s(self) -> float:
        return self._spec.settle_s

    @property
    def heartbeat_s(self) -> float | None:
        return None

    @property
    def entity_id(self) -> str:
        return self._entity

    @property
    def applied(self) -> InverterLimitAction | None:
        """The limit of the last successful ``apply`` (``None`` after a release)."""
        return self._applied

    def refusal(self) -> Reason | None:
        return None

    @property
    def default_limit(self) -> InverterLimitAction | None:
        """The inverter's own default a volatile register falls back to (``None`` = not
        volatile: the user's value is the baseline)."""
        return None

    # --- reading ------------------------------------------------------------------------------

    def lookup(self) -> EntityStateView | None:
        view = self._states(self._entity)
        if view is None or view.state in UNAVAILABLE_STATES:
            return None
        return view

    def readable(self) -> bool:
        """The read-back entity shows a value (the gate's X5 inverter scope)."""
        return self._value() is not None

    def _value(self) -> float | None:
        """The limit the entity shows, in the capability's unit (W or %)."""
        view = self.lookup()
        if view is None:
            return None
        value = as_float(view.state)
        if value is None:
            return None
        if self._capability == "export_limit":
            unit = str(view.attributes.get("unit_of_measurement", "W"))
            factor = _WATT_UNITS.get(unit)
            if factor is None:
                return None
            self._unit = unit
            return value * factor
        unit = str(view.attributes.get("unit_of_measurement", "%"))
        if unit != "%":
            return None
        self._unit = unit
        return value

    def _action(self, value: float) -> InverterLimitAction:
        if self._capability == "export_limit":
            return InverterLimitAction(max_export_w=max(0.0, value))
        return InverterLimitAction(max_ac_pct=min(100.0, max(0.0, value)))

    async def read_back(self) -> Action | None:
        value = self._value()
        return None if value is None else self._action(value)

    def limit_range(self) -> tuple[float, float]:
        """The writable range: the template's, narrowed by the entity's ``min``/``max``."""
        low = self._spec.min
        high = self._spec.max
        if high is None:
            high = (
                100.0
                if self._capability == "ac_limit"
                else (self._config.nominal_ac_w or 1_000_000.0)
            )
        view = self.lookup()
        if view is not None:
            factor = 1.0
            if self._capability == "export_limit":
                unit = str(view.attributes.get("unit_of_measurement", "W"))
                factor = _WATT_UNITS.get(unit, 1.0)
            entity_min = as_float(view.attributes.get("min"))
            entity_max = as_float(view.attributes.get("max"))
            if entity_min is not None:
                low = max(low, entity_min * factor)
            if entity_max is not None:
                high = min(high, entity_max * factor)
        return low, high

    # --- writing ------------------------------------------------------------------------------

    def limit_of(self, intent: Intent) -> InverterLimitAction:
        action = intent.action
        if not isinstance(action, InverterLimitAction):
            msg = f"{type(self).__name__} cannot apply a {action.kind.value} action"
            raise ActionNotSupportedError(msg)
        if (action.max_export_w is None) != (self._capability == "ac_limit"):
            msg = f"the adapter takes {self._capability}"
            raise WriteRefusedError(msg)
        return action

    @staticmethod
    def value_of(action: InverterLimitAction) -> float:
        value = action.max_export_w if action.max_export_w is not None else action.max_ac_pct
        assert value is not None
        return value

    async def _call(
        self,
        domain: str,
        service: str,
        *,
        data: Mapping[str, Any],
        entity_id: str | None = None,
    ) -> str | None:
        result = await self._write(
            domain,
            service,
            service_data=dict(data),
            target={"entity_id": entity_id} if entity_id is not None else None,
        )
        context_id = result.context_id
        if context_id is not None:
            self._context_ids = (*self._context_ids, context_id)
        return context_id

    async def write_value(self, value: float) -> str | None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def apply(self, intent: Intent) -> str | None:
        action = self.limit_of(intent)
        self._context_ids = ()
        context_id = await self.write_value(self.value_of(action))
        self._applied = action
        return context_id

    async def keep_alive(self, limit: InverterLimitAction) -> None:
        """Re-write ``limit``: the confirmed limit whose lease the gate just checked (review
        A1 - never a later, unconfirmed attempt)."""
        await self.write_value(self.value_of(limit))

    async def release(self, baseline: Action | None) -> None:
        """Give the user's own limit back: write the takeover baseline explicitly."""
        self._applied = None
        await self.write_value(self.release_value(baseline))

    def release_value(self, baseline: Action | None) -> float:
        """The value a release writes: the takeover baseline, else the inverter's default."""
        if isinstance(baseline, InverterLimitAction):
            return self.value_of(baseline)
        return self.release_default()

    def release_default(self) -> float:
        """What a release writes without a known baseline (the inverter's default)."""
        msg = "no baseline to restore"
        raise WriteRefusedError(msg)


class NumberLimitDriver(InverterDriver):
    """``generic_number`` and ``kostal_rest``: ``number.set_value`` on the limit entity."""

    async def write_value(self, value: float) -> str | None:
        view = self.lookup()
        unit = (
            str(
                view.attributes.get(
                    "unit_of_measurement", "W" if self._capability == "export_limit" else "%"
                )
            )
            if view is not None
            else self._unit
        )
        allowed = _WATT_UNITS if self._capability == "export_limit" else {"%": 1.0}
        if unit not in allowed:
            # Review A5: a value in an unknown unit is never written (also not on release).
            msg = f"the unit of {self._entity} is unknown"
            raise WriteRefusedError(msg)
        native = value / allowed[unit]
        return await self._call(
            "number", "set_value", data={"value": round(native, 3)}, entity_id=self._entity
        )


class ModbusLimitDriver(InverterDriver):
    """``kostal_modbus``: register 533 (whole percent, 1..100) via ``modbus.write_register``."""

    def __init__(
        self,
        spec: InverterAdapterSpec,
        config: InverterConfig,
        *,
        write: ServiceWriter,
        states: StateLookup,
    ) -> None:
        if config.modbus is None:
            msg = "kostal_modbus: inverter.modbus (hub, unit id) is not configured"
            raise ValueError(msg)
        if config.capability != "ac_limit":
            msg = "kostal_modbus: register 533 is an AC limit in %"
            raise ValueError(msg)
        super().__init__(spec, config, write=write, states=states)
        self._link: InverterModbusLink = config.modbus
        self._register = (
            spec.register if spec.register is not None else KOSTAL_ACTIVE_POWER_REGISTER
        )

    @property
    def heartbeat_s(self) -> float | None:
        return self._link.heartbeat_s if self._spec.volatile else None

    @property
    def link(self) -> InverterModbusLink:
        return self._link

    def limit_range(self) -> tuple[float, float]:
        low, high = super().limit_range()
        return max(1.0, low), min(100.0, high)  # 533 takes 1..100

    async def write_value(self, value: float) -> str | None:
        pct = int(min(100, max(1, math.ceil(value - 1e-9))))
        # An int (not a list): function code 0x06, write single register (Kostal: FC 0x06).
        return await self._call(
            MODBUS_DOMAIN,
            WRITE_REGISTER,
            data={
                "hub": self._link.hub,
                "slave": self._link.slave,
                "address": self._register,
                "value": pct,
            },
        )

    def release_default(self) -> float:
        return self._spec.default_pct

    @property
    def default_limit(self) -> InverterLimitAction | None:
        return (
            InverterLimitAction(max_ac_pct=self._spec.default_pct) if self._spec.volatile else None
        )

    def release_value(self, baseline: Action | None) -> float:
        """A volatile register always gets the inverter's default back (``default_pct``,
        100 %), never a value read from a sensor (review A2): the gate takes over only when the
        baseline is that default, so both are the same."""
        if self._spec.volatile:
            return self._spec.default_pct
        return super().release_value(baseline)


__all__ = [
    "KOSTAL_ACTIVE_POWER_REGISTER",
    "MODBUS_DOMAIN",
    "WRITE_REGISTER",
    "InverterDriver",
    "ModbusLimitDriver",
    "NumberLimitDriver",
]
