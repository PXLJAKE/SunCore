# SPDX-License-Identifier: Apache-2.0
"""The ``kostal_modbus`` battery adapter: Kostal §3.5 limits through ``modbus.write_register``.

Facts (``docs/research/kostal-plenticore-modbus.md`` §3.5 and "Phase 4 verification",
``ha-websocket-api.md`` "Modbus write action"):

* registers 1280 / 1282 max charge / discharge power (W, float32 in two registers), 1284 / 1286
  the same "in the fallback case", 1288 time until fallback (U32, 30-10800 s). "As soon as
  register 1280 or 1282 has been written, they must continue to be written cyclically. If this
  no longer happens, after ... (register 1288) the limits in 1284 and 1286 become active": a
  documented hardware watchdog;
* external battery management must be enabled: register 1080 = ``0x02``; it is read through a
  modbus sensor the user configured, and the adapter refuses every write otherwise;
* ``modbus.write_register`` (hub, ``slave`` - default 1, so the unit id is always sent -,
  address, value as a list of raw 16-bit words) has **no float32**, **no read action** and
  **only logs a failed write**: the adapter encodes floats itself (word order from register 5,
  the battery configuration) and every write is confirmed through the modbus sensors.

Sequence: before the first limit the fallback registers 1284 / 1286 get the battery's own limits
(1076 / 1078) and 1288 the configured timeout (default 60 s), and the gate reads them back
(:meth:`prepared`) - otherwise nothing else is written. While a limit is held the gate calls
:meth:`keep_alive` every ``heartbeat_s`` (default 20 s) and the adapter re-writes 1280 / 1282.
Release stops the heartbeat and writes the battery's own limits to 1280 / 1282 explicitly; a
dead SunCore leaves the inverter in its own control after the timeout.

``modbus.write_register`` is a **write** action of this adapter only: it goes through the same
rate-limited ``ServiceCaller.write`` as every device write and is never a read-only action.

``UNVERIFIED`` on hardware: external battery management is not enabled on the maintainer's
inverter yet; the fallback timer has not been measured (the gate caps this adapter at assisted
until ``battery.fallback_measured``); the U32 word order; the modbus hub set-up through the UI.
"""

from __future__ import annotations

from typing import Final

from suncore.core.battery import (
    BatteryAction,
    BatteryCapability,
    BatteryRanges,
    BatterySettings,
    FieldRange,
    ModbusLink,
)
from suncore.core.battery_adapter import KOSTAL_MODBUS_ROLES, BatteryAdapterSpec, ModbusRegisterSpec
from suncore.core.decisions import Action, Intent, Reason
from suncore.devices.battery.base import BatteryDriver
from suncore.devices.battery.modbus_codec import float32_words, uint32_words
from suncore.devices.drivers.base import ServiceWriter, StateLookup, WriteRefusedError

MODBUS_DOMAIN: Final = "modbus"
WRITE_REGISTER: Final = "write_register"
FALLBACK_TOLERANCE_W: Final = 1.0
DEFAULT_MODBUS_SETTLE_S: Final = 15.0
"""Until a modbus sensor polled the register again (the user's ``scan_interval``; UNVERIFIED)."""

REASON_EXTERNAL_CONTROL_OFF: Final = "safety.battery_external_control_off"


class KostalModbusDriver(BatteryDriver):
    """§3.5 charge/discharge limits with the inverter's own fallback (``kostal_modbus``)."""

    def __init__(
        self,
        spec: BatteryAdapterSpec,
        settings: BatterySettings,
        *,
        write: ServiceWriter,
        states: StateLookup,
    ) -> None:
        if spec.modbus is None:
            msg = "kostal_modbus: the template has no register map"
            raise ValueError(msg)
        if settings.modbus is None:
            msg = "kostal_modbus: battery.modbus (hub, unit id, byte order) is not configured"
            raise ValueError(msg)
        missing = [r for r in KOSTAL_MODBUS_ROLES if r not in settings.entities]
        if missing:
            msg = f"kostal_modbus: unmapped roles {', '.join(missing)}"
            raise ValueError(msg)
        settle = spec.settle_s if spec.settle_s is not None else DEFAULT_MODBUS_SETTLE_S
        super().__init__(settings, write=write, states=states, settle_s=settle)
        self._registers: ModbusRegisterSpec = spec.modbus
        self._link: ModbusLink = settings.modbus
        timeout = self._link.fallback_timeout_s
        if not (
            self._registers.fallback_timeout_min_s
            <= timeout
            <= self._registers.fallback_timeout_max_s
        ):
            msg = "kostal_modbus: fallback_timeout_s outside the register's range"
            raise ValueError(msg)

    @property
    def capabilities(self) -> frozenset[BatteryCapability]:
        return frozenset(
            {
                BatteryCapability.CHARGE_LIMIT,
                BatteryCapability.DISCHARGE_LIMIT,
                BatteryCapability.HARDWARE_FALLBACK,
            }
        )

    @property
    def heartbeat_s(self) -> float | None:
        return self._link.heartbeat_s

    @property
    def link(self) -> ModbusLink:
        return self._link

    # --- reading ------------------------------------------------------------------------------

    def own_limits(self) -> tuple[float, float] | None:
        """The battery's own charge / discharge limits (registers 1076 / 1078)."""
        charge = self.value_of("own_charge_limit")
        discharge = self.value_of("own_discharge_limit")
        if charge is None or discharge is None or charge < 0 or discharge < 0:
            return None
        return charge, discharge

    def refusal(self) -> Reason | None:
        value = self.value_of("external_control")
        expected = self._registers.external_control_value
        if value is None or round(value) != expected:
            return Reason(
                key=REASON_EXTERNAL_CONTROL_OFF,
                args={
                    "value": "unknown" if value is None else str(round(value)),
                    "expected": expected,
                },
            )
        if self.own_limits() is None:
            return Reason(key="safety.battery_range_unknown", args={"field": "own_limits"})
        return None

    def ranges(self) -> BatteryRanges:
        own = self.own_limits()
        if own is None:
            return BatteryRanges()
        return BatteryRanges(
            max_charge_w=FieldRange(0.0, min(own[0], self._model.max_charge_w)),
            max_discharge_w=FieldRange(0.0, min(own[1], self._model.max_discharge_w)),
        )

    def prepared(self) -> bool:
        own = self.own_limits()
        charge = self.value_of("fallback_charge_limit")
        discharge = self.value_of("fallback_discharge_limit")
        timeout = self.value_of("fallback_timeout")
        if own is None or charge is None or discharge is None or timeout is None:
            return False
        return (
            abs(charge - own[0]) <= FALLBACK_TOLERANCE_W
            and abs(discharge - own[1]) <= FALLBACK_TOLERANCE_W
            and round(timeout) == self._link.fallback_timeout_s
        )

    async def read_back(self) -> Action | None:
        charge = self.value_of("charge_limit")
        discharge = self.value_of("discharge_limit")
        if charge is None or discharge is None:
            return None
        return BatteryAction(max_charge_w=max(0.0, charge), max_discharge_w=max(0.0, discharge))

    # --- writing ------------------------------------------------------------------------------

    async def _register(self, address: int, words: list[int]) -> None:
        await self.service(
            MODBUS_DOMAIN,
            WRITE_REGISTER,
            data={
                "hub": self._link.hub,
                "slave": self._link.slave,
                "address": address,
                "value": words,
            },
        )

    async def _float(self, address: int, value: float) -> None:
        await self._register(address, float32_words(float(value), self._link.byte_order))

    async def arm_fallback(self) -> None:
        """Fallback registers first: own limits to 1284 / 1286, the timeout to 1288."""
        if self.refusal() is not None:
            raise WriteRefusedError("external battery management is not enabled (1080)")
        own = self.own_limits()
        assert own is not None
        self.begin()
        await self._float(self._registers.fallback_max_charge, own[0])
        await self._float(self._registers.fallback_max_discharge, own[1])
        await self._register(
            self._registers.fallback_timeout,
            uint32_words(self._link.fallback_timeout_s, self._link.byte_order),
        )

    async def _write_limits(self, action: BatteryAction) -> None:
        own = self.own_limits()
        assert own is not None
        charge = own[0] if action.max_charge_w is None else action.max_charge_w
        discharge = own[1] if action.max_discharge_w is None else action.max_discharge_w
        # Both registers every time: the pair is the state the inverter holds (and re-writing
        # both keeps the documented "written cyclically" condition for either).
        await self._float(self._registers.max_charge, charge)
        await self._float(self._registers.max_discharge, discharge)

    async def apply(self, intent: Intent) -> str | None:
        action = self.battery_action(intent)
        refusal = self.refusal()
        if refusal is not None:
            raise WriteRefusedError(refusal.key)
        if not self.prepared():
            raise WriteRefusedError("the fallback registers are not confirmed")
        self.begin()
        await self._write_limits(action)
        self._applied = action
        return self.last_context_id()

    async def keep_alive(self) -> None:
        """Re-write the held limits (only while the gate's lease holds)."""
        action = self._applied
        if action is None or self.refusal() is not None:
            return
        self.begin()
        await self._write_limits(action)

    async def release(self, baseline: Action | None) -> None:  # noqa: ARG002 - see below
        """Stop the heartbeat and give the inverter its own limits back explicitly.

        The release target of §3.5 is the battery's own limits (the fallback values), not the
        takeover read-back of 1280 / 1282, which may hold a stale foreign value. Without the
        own limits nothing is written: the stopped heartbeat lets the inverter fall back.
        """
        self._applied = None
        self.begin()
        own = self.own_limits()
        if own is None or self.refusal() is not None:
            return
        await self._float(self._registers.max_charge, own[0])
        await self._float(self._registers.max_discharge, own[1])


__all__ = [
    "MODBUS_DOMAIN",
    "REASON_EXTERNAL_CONTROL_OFF",
    "WRITE_REGISTER",
    "KostalModbusDriver",
]
