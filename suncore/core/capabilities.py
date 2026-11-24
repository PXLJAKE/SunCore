# SPDX-License-Identifier: Apache-2.0
"""Closed vocabulary of template capabilities (ADR-0012 item 8).

Phase 3/4 key behaviour on these members, so a template may only use listed ones.
Members are added per need (additive under the schema v1 compatibility policy).
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class Capability(StrEnum):
    """What a template provides."""

    POWER_MEASURED = "power_measured"
    ENERGY_COUNTER = "energy_counter"
    ON_OFF = "on_off"
    POWER_SETPOINT = "power_setpoint"
    TEMPERATURE_SETPOINT = "temperature_setpoint"
    HVAC_MODES = "hvac_modes"
    PRESET_MODES = "preset_modes"
    FAN_MODES = "fan_modes"
    PRICE_NOW = "price_now"
    PRICE_CURVE = "price_curve"
    PV_CURVE = "pv_curve"
    PV_QUANTILES = "pv_quantiles"
    FEED_IN_TIERS = "feed_in_tiers"
    NEGATIVE_PRICE_RULE = "negative_price_rule"
    # Phase 4 (battery kind, additive):
    BATTERY_SOC = "battery_soc"
    MIN_SOC_LIMIT = "min_soc_limit"
    CHARGE_LIMIT = "charge_limit"
    DISCHARGE_LIMIT = "discharge_limit"
    GRID_CHARGE = "grid_charge"
    BATTERY_MODES = "battery_modes"
    HARDWARE_FALLBACK = "hardware_fallback"
    """The inverter falls back to its own limits when SunCore stops writing (Phase 4 WP10)."""
    # Phase 6 (additive, ADR-0017/0018):
    EV_CHARGING = "ev_charging"
    """Controls an EV charge point (control ``charger`` or ``loadpoint``)."""
    PHASE_SWITCHING = "phase_switching"
    """The charger switches between one and three phases (a ``phases`` role)."""
    SG_READY = "sg_ready"
    """SG-Ready heat pump interface (control ``sg_ready``)."""
    REMOTE_START = "remote_start"
    """A program can be started remotely (a ``button``/``switch`` role, ``UNVERIFIED``)."""
    EXPORT_LIMIT = "export_limit"
    """The inverter takes an export limit in W."""
    AC_LIMIT = "ac_limit"
    """The inverter takes an AC output limit in % of its nominal power."""
    BACKUP_STATE = "backup_state"
    """A role shows island (backup power) operation."""
