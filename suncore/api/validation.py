# SPDX-License-Identifier: Apache-2.0
"""Render request validation errors as catalogue keys instead of Pydantic's English text.

Body of a 422 caused by request validation::

    {"detail": "api.error.validation",
     "issues": [{"loc": ["body", "grid"], "key": "api.validation.grid_variant_both", "args": {}}]}

``key`` is looked up by the Pydantic error ``type`` in :data:`VALIDATION_KEYS`; our own
cross-field validators raise ``suncore.<key>`` errors (``suncore.core.validation.invalid``).
An unknown type falls back to :data:`FALLBACK_KEY`. ``args`` are the scalar parts of the error
context (``ge``, ``max_length``, ``entity_id``, ...) for the ICU message. The offending input
is never echoed back.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

ERR_VALIDATION: Final = "api.error.validation"
FALLBACK_KEY: Final = "api.validation.invalid"
MAX_ISSUES: Final = 50

VALIDATION_KEYS: Final[dict[str, str]] = {
    "missing": "api.validation.missing",
    "extra_forbidden": "api.validation.extra_forbidden",
    "string_type": "api.validation.string_type",
    "string_too_short": "api.validation.string_too_short",
    "string_too_long": "api.validation.string_too_long",
    "string_pattern_mismatch": "api.validation.string_pattern_mismatch",
    "int_type": "api.validation.int_type",
    "int_parsing": "api.validation.int_parsing",
    "int_from_float": "api.validation.int_from_float",
    "float_type": "api.validation.float_type",
    "float_parsing": "api.validation.float_parsing",
    "bool_type": "api.validation.bool_type",
    "bool_parsing": "api.validation.bool_parsing",
    "greater_than": "api.validation.greater_than",
    "greater_than_equal": "api.validation.greater_than_equal",
    "less_than": "api.validation.less_than",
    "less_than_equal": "api.validation.less_than_equal",
    "multiple_of": "api.validation.multiple_of",
    "finite_number": "api.validation.finite_number",
    "literal_error": "api.validation.literal_error",
    "enum": "api.validation.enum",
    "union_tag_invalid": "api.validation.union_tag_invalid",
    "union_tag_not_found": "api.validation.union_tag_not_found",
    "model_type": "api.validation.model_type",
    "model_attributes_type": "api.validation.model_attributes_type",
    "dict_type": "api.validation.dict_type",
    "list_type": "api.validation.list_type",
    "tuple_type": "api.validation.tuple_type",
    "set_type": "api.validation.set_type",
    "too_short": "api.validation.too_short",
    "too_long": "api.validation.too_long",
    "time_parsing": "api.validation.time_parsing",
    "time_type": "api.validation.time_type",
    "datetime_parsing": "api.validation.datetime_parsing",
    "datetime_type": "api.validation.datetime_type",
    "date_parsing": "api.validation.date_parsing",
    "date_type": "api.validation.date_type",
    "json_invalid": "api.validation.json_invalid",
    "json_type": "api.validation.json_type",
    "value_error": "api.validation.value_error",
    "assertion_error": "api.validation.assertion_error",
    "none_required": "api.validation.none_required",
    "missing_argument": "api.validation.missing_argument",
    "suncore.battery_band_power": "api.validation.battery_band_power",
    "suncore.battery_band_soc": "api.validation.battery_band_soc",
    "suncore.comfort_not_applicable": "api.validation.comfort_not_applicable",
    "suncore.comfort_order": "api.validation.comfort_order",
    "suncore.control_amps_only": "api.validation.control_amps_only",
    "suncore.control_percent_max": "api.validation.control_percent_max",
    "suncore.control_range": "api.validation.control_range",
    "suncore.control_role_unmapped": "api.validation.control_role_unmapped",
    "suncore.counter_invert": "api.validation.counter_invert",
    "suncore.counter_update_without_role": "api.validation.counter_update_without_role",
    "suncore.device_ids_unique": "api.validation.device_ids_unique",
    "suncore.entity_mapped_twice": "api.validation.entity_mapped_twice",
    "suncore.entity_reused": "api.validation.entity_reused",
    "suncore.export_fixed_invalid": "api.validation.export_fixed_invalid",
    "suncore.export_limit_exclusive": "api.validation.export_limit_exclusive",
    "suncore.export_none_extra": "api.validation.export_none_extra",
    "suncore.export_pct_needs_kwp": "api.validation.export_pct_needs_kwp",
    "suncore.export_tiered_invalid": "api.validation.export_tiered_invalid",
    "suncore.forecast_templates_unique": "api.validation.forecast_templates_unique",
    "suncore.grid_pair_incomplete": "api.validation.grid_pair_incomplete",
    "suncore.grid_variant_both": "api.validation.grid_variant_both",
    "suncore.grid_variant_none": "api.validation.grid_variant_none",
    "suncore.hvac_mode_not_climate": "api.validation.hvac_mode_not_climate",
    "suncore.import_dynamic_extra": "api.validation.import_dynamic_extra",
    "suncore.import_dynamic_needs_source": "api.validation.import_dynamic_needs_source",
    "suncore.import_fixed_extra": "api.validation.import_fixed_extra",
    "suncore.import_fixed_needs_price": "api.validation.import_fixed_needs_price",
    "suncore.invert_meaningless": "api.validation.invert_meaningless",
    "suncore.mode_identity_required": "api.validation.mode_identity_required",
    "suncore.modes_unique": "api.validation.modes_unique",
    "suncore.override_boost_minutes": "api.validation.override_boost_minutes",
    "suncore.override_hours": "api.validation.override_hours",
    "suncore.override_minutes_only_boost": "api.validation.override_minutes_only_boost",
    "suncore.override_next_day": "api.validation.override_next_day",
    "suncore.override_until_only_pause": "api.validation.override_until_only_pause",
    "suncore.override_until_past": "api.validation.override_until_past",
    "suncore.para14a_signal_needs_flag": "api.validation.para14a_signal_needs_flag",
    "suncore.percent_needs_nominal": "api.validation.percent_needs_nominal",
    "suncore.plan_interval_order": "api.validation.plan_interval_order",
    "suncore.plan_overlap": "api.validation.plan_overlap",
    "suncore.battery_action_empty": "api.validation.battery_action_empty",
    "suncore.battery_corridor": "api.validation.battery_corridor",
    "suncore.battery_entities_need_adapter": "api.validation.battery_entities_need_adapter",
    "suncore.forecast_auto_weight": "api.validation.forecast_auto_weight",
    "suncore.forecast_manual_weight": "api.validation.forecast_manual_weight",
    "suncore.grid_charge_needs_limit": "api.validation.grid_charge_needs_limit",
    "suncore.import_formula_dynamic_only": "api.validation.import_formula_dynamic_only",
    "suncore.import_role_price": "api.validation.import_role_price",
    "suncore.import_source_dynamic_only": "api.validation.import_source_dynamic_only",
    "suncore.import_tou_extra": "api.validation.import_tou_extra",
    "suncore.import_tou_needs_periods": "api.validation.import_tou_needs_periods",
    "suncore.import_tou_only": "api.validation.import_tou_only",
    "suncore.price_grid": "api.validation.price_grid",
    "suncore.price_unit": "api.validation.price_unit",
    "suncore.quantile_grid": "api.validation.quantile_grid",
    "suncore.quantile_order": "api.validation.quantile_order",
    "suncore.quantile_unit": "api.validation.quantile_unit",
    "suncore.range_order": "api.validation.range_order",
    "suncore.series_step": "api.validation.series_step",
    "suncore.import_formula_all_in": "api.validation.import_formula_all_in",
    "suncore.grid_charge_above_cap": "api.validation.grid_charge_above_cap",
    "suncore.net_risk_pair": "api.validation.net_risk_pair",
    "suncore.power_model_counter": "api.validation.power_model_counter",
    "suncore.power_model_measured": "api.validation.power_model_measured",
    "suncore.power_model_nominal": "api.validation.power_model_nominal",
    "suncore.power_role_unmapped": "api.validation.power_role_unmapped",
    "suncore.power_update_without_role": "api.validation.power_update_without_role",
    "suncore.pv_names_unique": "api.validation.pv_names_unique",
    "suncore.relay_step_distinct": "api.validation.relay_step_distinct",
    "suncore.relay_step_unique": "api.validation.relay_step_unique",
    "suncore.relay_step_unknown": "api.validation.relay_step_unknown",
    "suncore.relays_unique": "api.validation.relays_unique",
    "suncore.setpoint_needs_comfort": "api.validation.setpoint_needs_comfort",
    "suncore.soc_invert": "api.validation.soc_invert",
    "suncore.soc_order": "api.validation.soc_order",
    "suncore.steps_order": "api.validation.steps_order",
    "suncore.switch_on_needs_mode": "api.validation.switch_on_needs_mode",
    "suncore.switch_on_not_climate": "api.validation.switch_on_not_climate",
    "suncore.tier_order": "api.validation.tier_order",
    "suncore.tier_unbounded": "api.validation.tier_unbounded",
    "suncore.unknown_timezone": "api.validation.unknown_timezone",
    "suncore.window_days_unique": "api.validation.window_days_unique",
    "suncore.window_empty": "api.validation.window_empty",
    "suncore.window_no_days": "api.validation.window_no_days",
}
"""Pydantic error type to catalogue key (``suncore.*`` are our own validators)."""

_SKIP_CONTEXT: Final = frozenset({"error"})


def issue_from_error(error: dict[str, Any]) -> dict[str, Any]:
    """One wire issue from a Pydantic error dict (``loc``, ``type``, ``ctx``)."""
    ctx = error.get("ctx") or {}
    args = {
        name: value
        for name, value in ctx.items()
        if name not in _SKIP_CONTEXT and isinstance(value, str | int | float | bool)
    }
    return {
        "loc": [part if isinstance(part, int) else str(part) for part in error.get("loc", ())],
        "key": VALIDATION_KEYS.get(str(error.get("type")), FALLBACK_KEY),
        "args": args,
    }


async def validation_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    issues = [issue_from_error(dict(err)) for err in exc.errors()[:MAX_ISSUES]]
    return JSONResponse(status_code=422, content={"detail": ERR_VALIDATION, "issues": issues})
