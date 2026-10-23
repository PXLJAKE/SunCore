# SPDX-License-Identifier: Apache-2.0
"""Catalogue keys of the planner's explanations (``planner.*``).

Every key here exists in ``frontend/src/lib/i18n/locales/{en,de}.json``
(``scripts/check_reasons.py`` reads this module like the controller's). The risk model's two
keys (``planner.battery.morning_buffer``, ``planner.battery.evening_target``) are built by
``suncore.forecast.risk`` and reach the planner as finished :class:`Reason` objects.
"""

from __future__ import annotations

from typing import Final

# --- plan level: who made the plan and on what ------------------------------------------
SOLVER_OPTIMAL: Final = "planner.solver.optimal"
SOLVER_INCUMBENT: Final = "planner.solver.incumbent"
HEURISTIC_RULES: Final = "planner.heuristic.rules"
FALLBACK_TIMEOUT: Final = "planner.fallback.timeout"
FALLBACK_INFEASIBLE: Final = "planner.fallback.infeasible"
FALLBACK_ERROR: Final = "planner.fallback.error"
FALLBACK_SOLVER_UNAVAILABLE: Final = "planner.fallback.solver_unavailable"
FALLBACK_INVALID_SOLUTION: Final = "planner.fallback.invalid_solution"
FALLBACK_HEURISTIC_BETTER: Final = "planner.fallback.heuristic_better"
FALLBACK_NO_PRICES: Final = "planner.fallback.no_prices"
FALLBACK_SPOT_WITHOUT_FORMULA: Final = "planner.fallback.spot_without_formula"
INPUT_PRICES_PARTIAL: Final = "planner.input.prices_partial"
INPUT_NO_PV_FORECAST: Final = "planner.input.no_pv_forecast"
INPUT_NO_LOAD_FORECAST: Final = "planner.input.no_load_forecast"
INPUT_NO_BATTERY_SOC: Final = "planner.input.no_battery_soc"
INPUT_DEVICE_POWER_UNKNOWN: Final = "planner.input.device_power_unknown"

# --- interval level -------------------------------------------------------------------
GRID_CHARGE: Final = "planner.battery.grid_charge"
CHARGE_SURPLUS: Final = "planner.battery.charge_surplus"
DISCHARGE_HOUSE: Final = "planner.battery.discharge_house"
HOLD_FOR_EVENING: Final = "planner.battery.hold_for_evening"
HOLD_FOR_PRICE: Final = "planner.battery.hold_for_price"
AT_RESERVE: Final = "planner.battery.at_reserve"
BATTERY_FULL: Final = "planner.battery.full"
PREFER_FULL: Final = "planner.battery.prefer_full"
USER_TARGET: Final = "planner.battery.user_target"
TARGET_SHORTFALL: Final = "planner.battery.target_shortfall"
DEVICE_SURPLUS: Final = "planner.device.surplus"
DEVICE_THROTTLED: Final = "planner.device.throttled"
EXPORT_NEGATIVE_PRICE_AVOIDED: Final = "planner.export.negative_price_avoided"
EXPORT_LIMIT: Final = "planner.export.limit"
EXPORT_SURPLUS: Final = "planner.export.surplus"
GRID_IMPORT: Final = "planner.grid.import"
BALANCED: Final = "planner.interval.balanced"

# Built by the risk model (WP7); listed so the planner names them in one place.
MORNING_BUFFER: Final = "planner.battery.morning_buffer"
EVENING_TARGET: Final = "planner.battery.evening_target"
