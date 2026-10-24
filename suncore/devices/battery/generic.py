# SPDX-License-Identifier: Apache-2.0
"""The ``generic`` battery adapter: template kind ``battery`` roles -> limits (WP10).

What each mapped writable role expresses comes from the template's ``controls``:

* ``min_soc`` number (%): the discharge floor (``min_soc_pct``);
* ``max_charge`` / ``max_discharge`` / ``grid_charge_power`` numbers (W, kW or % of the battery's
  maximum power): the power limits and the forced grid-charge power;
* ``mode`` select: ``no_discharge`` expresses ``max_discharge_w = 0``, ``no_charge``
  ``max_charge_w = 0``, ``force_charge`` a forced grid charge at the battery's full power;
  ``auto`` is the release baseline (exactly one per select, template validation);
* ``force_charge`` switch: on = forced grid charging at the battery's full power, off = auto.

Capabilities follow the **mapped** roles (an optional role the user left out gives nothing).
Ranges come from the entity's live ``min``/``max``/``step`` attributes intersected with the
template's; a select or switch can only express the extremes, so its range is a single value
(the gate rejects anything else, B6). ``min_soc`` is rounded **up** to the entity step by the
gate (``safety.battery.quantise``). Writes only on change, read-back is mandatory (the gate
compares after ``settle_s``). Release writes the baseline values of the numbers it holds and
``auto`` / off for a select / switch.

``UNVERIFIED``: no real generic battery has been driven; exercised in the simulator only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from suncore.core.battery import (
    BatteryAction,
    BatteryCapability,
    BatteryRanges,
    BatterySettings,
    FieldRange,
)
from suncore.core.battery_adapter import (
    BatteryAdapterSpec,
    BatteryModeMeaning,
    ModeSelectSpec,
    NumberLimitSpec,
)
from suncore.core.decisions import Action, Intent
from suncore.devices.battery.base import BatteryDriver
from suncore.devices.drivers.base import ServiceWriter, StateLookup, WriteRefusedError, as_float

SAME_VALUE: Final = 1e-6

_RESTRICTIVE: Final = frozenset(
    {
        BatteryModeMeaning.NO_DISCHARGE,
        BatteryModeMeaning.NO_CHARGE,
        BatteryModeMeaning.FORCE_CHARGE,
    }
)

_FIELD_OF_KIND: Final = {
    "min_soc": "min_soc_pct",
    "max_charge": "max_charge_w",
    "max_discharge": "max_discharge_w",
    "grid_charge_power": "grid_charge_w",
}


@dataclass(frozen=True, slots=True)
class _Number:
    role: str
    entity_id: str
    control: NumberLimitSpec
    field: str


class GenericBatteryDriver(BatteryDriver):
    """Battery limits through ``number`` / ``select`` / ``switch`` entities (``generic``)."""

    def __init__(
        self,
        spec: BatteryAdapterSpec,
        settings: BatterySettings,
        *,
        write: ServiceWriter,
        states: StateLookup,
    ) -> None:
        super().__init__(settings, write=write, states=states, settle_s=spec.settle_s)
        self._spec = spec
        self._numbers: dict[str, _Number] = {}
        self._mode: tuple[str, ModeSelectSpec] | None = None
        self._switch: str | None = None
        for number in spec.numbers:
            entity = self.entity_id(number.role)
            if entity is not None:  # else an optional role the user did not map
                field = _FIELD_OF_KIND[number.kind]
                self._numbers[field] = _Number(number.role, entity, number, field)
        if spec.mode is not None:
            entity = self.entity_id(spec.mode.role)
            if entity is not None:
                self._mode = (entity, spec.mode)
        if spec.force_switch_role is not None:
            self._switch = self.entity_id(spec.force_switch_role)
        self._capabilities = self._derive_capabilities()

    # --- capabilities and ranges -------------------------------------------------------------

    def _mode_meanings(self) -> set[BatteryModeMeaning]:
        if self._mode is None:
            return set()
        return {o.meaning for o in self._mode[1].options}

    def _derive_capabilities(self) -> frozenset[BatteryCapability]:
        meanings = self._mode_meanings()
        caps: set[BatteryCapability] = set()
        if "min_soc_pct" in self._numbers:
            caps.add(BatteryCapability.MIN_SOC)
        if "max_charge_w" in self._numbers or BatteryModeMeaning.NO_CHARGE in meanings:
            caps.add(BatteryCapability.CHARGE_LIMIT)
        if "max_discharge_w" in self._numbers or BatteryModeMeaning.NO_DISCHARGE in meanings:
            caps.add(BatteryCapability.DISCHARGE_LIMIT)
        if (
            "grid_charge_w" in self._numbers
            or self._switch is not None
            or BatteryModeMeaning.FORCE_CHARGE in meanings
        ):
            caps.add(BatteryCapability.GRID_CHARGE)
        return frozenset(caps)

    @property
    def capabilities(self) -> frozenset[BatteryCapability]:
        return self._capabilities

    def _factor(self, number: _Number) -> float | None:
        """Entity value x factor = W (or % for ``min_soc``); ``None`` if not convertible."""
        unit = number.control.unit
        if number.field == "min_soc_pct":
            return 1.0
        if unit == "W":
            return 1.0
        if unit == "kW":
            return 1000.0
        full = (
            self._model.max_discharge_w
            if number.field == "max_discharge_w"
            else self._model.max_charge_w
        )
        return full / 100.0

    def _number_range(self, number: _Number) -> FieldRange | None:
        view = self.lookup(number.entity_id)
        factor = self._factor(number)
        if view is None or factor is None:
            return None
        control = number.control
        low = max(control.min, as_float(view.attributes.get("min")) or control.min)
        high_attr = as_float(view.attributes.get("max"))
        high = min(control.max, high_attr if high_attr is not None else control.max)
        step = as_float(view.attributes.get("step")) or control.step
        if low > high:
            return None
        return FieldRange(low * factor, high * factor, None if step is None else step * factor)

    def ranges(self) -> BatteryRanges:
        values: dict[str, FieldRange | None] = {
            field: self._number_range(number) for field, number in self._numbers.items()
        }
        meanings = self._mode_meanings()
        mode_ok = self._mode is not None and self.lookup(self._mode[0]) is not None
        if "max_discharge_w" not in values and BatteryModeMeaning.NO_DISCHARGE in meanings:
            values["max_discharge_w"] = FieldRange(0.0, 0.0) if mode_ok else None
        if "max_charge_w" not in values and BatteryModeMeaning.NO_CHARGE in meanings:
            values["max_charge_w"] = FieldRange(0.0, 0.0) if mode_ok else None
        if "grid_charge_w" not in values:
            full = self._model.max_charge_w
            if self._switch is not None:
                switch_ok = self.lookup(self._switch) is not None
                values["grid_charge_w"] = FieldRange(full, full) if switch_ok else None
            elif BatteryModeMeaning.FORCE_CHARGE in meanings:
                values["grid_charge_w"] = FieldRange(full, full) if mode_ok else None
        return BatteryRanges(**values)

    # --- reading ------------------------------------------------------------------------------

    def _number_value(self, number: _Number) -> float | None:
        view = self.lookup(number.entity_id)
        factor = self._factor(number)
        raw = None if view is None else as_float(view.state)
        return None if raw is None or factor is None else raw * factor

    def _mode_meaning(self) -> BatteryModeMeaning | None:
        if self._mode is None:
            return None
        view = self.lookup(self._mode[0])
        if view is None:
            return None
        for option in self._mode[1].options:
            if option.value == view.state:
                return option.meaning
        return BatteryModeMeaning.OTHER

    async def read_back(self) -> Action | None:
        """The readable limits; ``None`` when an entity the adapter holds is unavailable."""
        values: dict[str, float | None] = {}
        for field, number in self._numbers.items():
            value = self._number_value(number)
            if value is None:
                return None
            values[field] = value
        meaning = self._mode_meaning()
        if self._mode is not None and meaning is None:
            return None
        meanings = self._mode_meanings()
        if "max_discharge_w" not in values and BatteryModeMeaning.NO_DISCHARGE in meanings:
            discharge = meaning is BatteryModeMeaning.NO_DISCHARGE
            values["max_discharge_w"] = 0.0 if discharge else self._model.max_discharge_w
        if "max_charge_w" not in values and BatteryModeMeaning.NO_CHARGE in meanings:
            charge = meaning is BatteryModeMeaning.NO_CHARGE
            values["max_charge_w"] = 0.0 if charge else self._model.max_charge_w
        if "grid_charge_w" not in values:
            if self._switch is not None:
                view = self.lookup(self._switch)
                if view is None:
                    return None
                values["grid_charge_w"] = self._model.max_charge_w if view.state == "on" else 0.0
            elif BatteryModeMeaning.FORCE_CHARGE in meanings:
                forced = meaning is BatteryModeMeaning.FORCE_CHARGE
                values["grid_charge_w"] = self._model.max_charge_w if forced else 0.0
        if not values:
            return None
        grid, cap = values.get("grid_charge_w"), values.get("max_charge_w")
        if grid is not None and cap is not None and grid > cap:
            values["max_charge_w"] = None  # forced charging overrides a cap (report the force)
        return BatteryAction(**values)

    # --- writing ------------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = self.battery_action(intent)
        self.begin()
        await self._write_state(action)
        self._applied = action
        return self.last_context_id()

    async def _write_state(self, action: BatteryAction) -> None:
        for field, number in self._numbers.items():
            value = getattr(action, field)
            if value is not None:
                await self._write_number(number, value)
        if self._mode is not None:
            await self._write_mode(action)
        if self._switch is not None and "grid_charge_w" not in self._numbers:
            grid = action.grid_charge_w
            if grid is not None:
                await self._write_switch(on=grid > 0)

    async def _write_number(self, number: _Number, value: float) -> None:
        view = self.lookup(number.entity_id)
        factor = self._factor(number)
        if view is None or factor is None:
            raise WriteRefusedError(f"{number.entity_id} is unavailable")
        if not math.isfinite(value):
            raise WriteRefusedError(f"{number.field} is not finite")
        target = round(value / factor, 6)
        current = as_float(view.state)
        if current is not None and math.isclose(current, target, abs_tol=SAME_VALUE):
            return  # write only on change
        await self.service(
            "number", "set_value", data={"value": target}, entity_id=number.entity_id
        )

    def _wanted_meaning(self, action: BatteryAction) -> BatteryModeMeaning:
        meanings = self._mode_meanings()
        if (
            "grid_charge_w" not in self._numbers
            and self._switch is None
            and BatteryModeMeaning.FORCE_CHARGE in meanings
            and (action.grid_charge_w or 0) > 0
        ):
            return BatteryModeMeaning.FORCE_CHARGE
        if (
            "max_discharge_w" not in self._numbers
            and BatteryModeMeaning.NO_DISCHARGE in meanings
            and action.max_discharge_w is not None
            and action.max_discharge_w <= 0
        ):
            return BatteryModeMeaning.NO_DISCHARGE
        if (
            "max_charge_w" not in self._numbers
            and BatteryModeMeaning.NO_CHARGE in meanings
            and action.max_charge_w is not None
            and action.max_charge_w <= 0
        ):
            return BatteryModeMeaning.NO_CHARGE
        return BatteryModeMeaning.AUTO

    async def _write_mode(self, action: BatteryAction) -> None:
        assert self._mode is not None
        entity, control = self._mode
        wanted = self._wanted_meaning(action)
        option = next(o.value for o in control.options if o.meaning is wanted)
        view = self.lookup(entity)
        if view is None:
            raise WriteRefusedError(f"{entity} is unavailable")
        if view.state == option:
            return
        if wanted is BatteryModeMeaning.AUTO and self._mode_meaning() not in _RESTRICTIVE:
            return  # nothing to lift: the user's own option (e.g. ``other``) stays
        await self.service("select", "select_option", data={"option": option}, entity_id=entity)

    async def _write_switch(self, *, on: bool) -> None:
        assert self._switch is not None
        view = self.lookup(self._switch)
        if view is None:
            raise WriteRefusedError(f"{self._switch} is unavailable")
        if (view.state == "on") == on:
            return
        await self.service("switch", "turn_on" if on else "turn_off", entity_id=self._switch)

    async def release(self, baseline: Action | None) -> None:
        """Back to the baseline: the numbers SunCore holds, ``auto`` / off for select / switch."""
        self.begin()
        self._applied = None
        if isinstance(baseline, BatteryAction):
            for field, number in self._numbers.items():
                value = getattr(baseline, field)
                if value is not None and self.lookup(number.entity_id) is not None:
                    await self._write_number(number, value)
        if self._mode is not None and self.lookup(self._mode[0]) is not None:
            auto = next(
                o.value for o in self._mode[1].options if o.meaning is BatteryModeMeaning.AUTO
            )
            view = self.lookup(self._mode[0])
            if view is not None and view.state != auto:
                await self.service(
                    "select", "select_option", data={"option": auto}, entity_id=self._mode[0]
                )
        if (
            self._switch is not None
            and "grid_charge_w" not in self._numbers
            and self.lookup(self._switch) is not None
        ):
            await self._write_switch(on=False)


__all__ = ["GenericBatteryDriver"]
