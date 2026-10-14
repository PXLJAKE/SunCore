# SPDX-License-Identifier: Apache-2.0
"""Power <-> control value conversion for continuous loads (W, A, %).

Pure functions shared by the power models and the number driver. Signs: device power
+ = consumption, never negative here.
"""

from __future__ import annotations

from suncore.core.devices import ContinuousControl


def value_to_power_w(value: float, control: ContinuousControl, nominal_w: float | None) -> float:
    """Power a continuous control ``value`` (in the control's unit) draws, in W (>= 0)."""
    value = max(0.0, value)
    if control.unit == "W":
        return value
    if control.unit == "A":
        return value * control.voltage_v * control.phases
    return value / 100.0 * (nominal_w or 0.0)


def power_to_value(power_w: float, control: ContinuousControl, nominal_w: float | None) -> float:
    """The control value (in the control's unit) that draws ``power_w``; ``0`` if undefined.

    ``%`` is relative to ``nominal_w``; without a nominal power the value is ``0`` (the
    device schema rejects this combination, see ``percent_needs_nominal``).
    """
    power_w = max(0.0, power_w)
    if control.unit == "W":
        return power_w
    if control.unit == "A":
        return power_w / (control.voltage_v * control.phases)
    if not nominal_w:
        return 0.0
    return power_w / nominal_w * 100.0


__all__ = ["power_to_value", "value_to_power_w"]
