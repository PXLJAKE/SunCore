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
