# SPDX-License-Identifier: Apache-2.0
"""Template models: header rules, per-kind rules and the discriminated union."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from suncore.core.actions import ERR_ACTION_NOT_ALLOWED
from suncore.templates import (
    TEMPLATE_ADAPTER,
    DeviceTemplate,
    ForecastTemplate,
    RegionTemplate,
    TariffTemplate,
)
from suncore.templates.models import price_unit_factor
from tests.templates.conftest import DOCS, device_doc, forecast_doc, region_doc, tariff_doc

EXPECTED = {
    "device": DeviceTemplate,
    "tariff": TariffTemplate,
    "forecast": ForecastTemplate,
    "region": RegionTemplate,
}


@pytest.mark.parametrize("kind", list(DOCS))
def test_union_dispatches_on_kind(kind: str) -> None:
    template = TEMPLATE_ADAPTER.validate_python(DOCS[kind]())
    assert isinstance(template, EXPECTED[kind])
    assert template.kind == kind


def test_unknown_kind_rejected() -> None:
    doc = device_doc()
    doc["kind"] = "inverter"
    with pytest.raises(ValidationError, match="kind"):
        TEMPLATE_ADAPTER.validate_python(doc)


def _bad(doc: dict[str, Any], mutate: Any) -> None:
    doc = copy.deepcopy(doc)
    mutate(doc)
    with pytest.raises(ValidationError):
        TEMPLATE_ADAPTER.validate_python(doc)


def test_header_rules() -> None:
    base = device_doc()
    _bad(base, lambda d: d.update(schema_version=2))
    _bad(base, lambda d: d.update(id="Acme.Heater"))
    _bad(base, lambda d: d.update(id="acme"))  # device ids need <vendor>.<model>
    _bad(base, lambda d: d.update(name="Heater"))  # must be the i18n key
    _bad(base, lambda d: d.update(name="template.other.heater.name"))
    _bad(base, lambda d: d.update(source=[]))
    _bad(base, lambda d: d["source"][0].update(url="ftp://x"))
    _bad(base, lambda d: d.update(capabilities=["energy_counter", "energy_counter"]))
    _bad(base, lambda d: d.update(capabilities=["not_in_vocabulary"]))
    _bad(base, lambda d: d.update(min_app_version="v1"))
    _bad(base, lambda d: d.update(bogus=1))
    _bad(base, lambda d: d.pop("tested_with"))


def test_empty_tested_with_requires_unverified() -> None:
    doc = device_doc()
    doc["tested_with"] = []
    with pytest.raises(ValidationError, match="unverified"):
        TEMPLATE_ADAPTER.validate_python(doc)
    doc["unverified"] = ["entity names not confirmed on real hardware"]
    assert TEMPLATE_ADAPTER.validate_python(doc).unverified


def test_device_rules() -> None:
    base = device_doc()
    _bad(base, lambda d: d.update(roles={}))
    _bad(base, lambda d: d["defaults"]["power_model"].update(energy_role="missing"))
    _bad(base, lambda d: d["defaults"]["power_model"].update(kind="measured"))
    _bad(base, lambda d: d["defaults"]["power_model"].pop("energy_role"))
    _bad(base, lambda d: d["defaults"].update(control={"kind": "on_off", "role": "missing"}))
    _bad(base, lambda d: d.update(device_kind="switch"))  # comfort not applicable
    _bad(base, lambda d: d["defaults"].update(comfort={"min_c": 25, "max_c": 20}))
    _bad(base, lambda d: d["roles"]["climate"]["matcher"].update(domain="Climate"))
    _bad(base, lambda d: d["roles"]["energy"]["matcher"].update(units=[]))
    _bad(base, lambda d: d["defaults"].update(control="setpoint"))  # bare enum is gone
    _bad(base, lambda d: d["roles"]["climate"].update(description="no key"))


def test_device_message_keys() -> None:
    template = TEMPLATE_ADAPTER.validate_python(device_doc())
    assert template.message_keys() == [
        "template.acme.heater.name",
        "template.acme.heater.role.climate",
        "template.acme.heater.role.energy",
    ]


def test_tariff_rules() -> None:
    base = tariff_doc()
    _bad(base, lambda d: d.update(roles={}))  # dynamic needs a price role
    _bad(base, lambda d: d.update(price_unit="EUR"))
    _bad(base, lambda d: d.update(resolution_min=30))
    _bad(base, lambda d: d.update(direction="both"))
    _bad(base, lambda d: d.pop("price_series"))
    _bad(base, lambda d: d.pop("price_includes"))
    _bad(base, lambda d: d["price_series"]["fields"].update(value_unit="USD/MWh"))
    fixed = copy.deepcopy(base)
    fixed.update(tariff_type="fixed", roles={})
    del fixed["price_series"], fixed["price_includes"]
    assert TEMPLATE_ADAPTER.validate_python(fixed).kind == "tariff"
    _bad(fixed, lambda d: d.update(price_series=base["price_series"]))  # series only for dynamic
    derived = copy.deepcopy(base)
    derived["resolution_min"] = None
    assert TEMPLATE_ADAPTER.validate_python(derived).kind == "tariff"


def test_forecast_rules() -> None:
    base = forecast_doc()
    _bad(base, lambda d: d.update(forecast_kind="wind"))
    _bad(base, lambda d: d.update(roles={}))
    _bad(base, lambda d: d.update(forecast_kind="price"))  # prices belong to tariffs
    _bad(base, lambda d: d["series"]["fields"].pop("value_field"))
    _bad(base, lambda d: d["series"]["fields"].update(value_unit="EUR"))
    _bad(base, lambda d: d["series"].update(roles=["missing"]))
    _bad(base, lambda d: d.update(resolution_min=0))
    optional = copy.deepcopy(base)
    del optional["series"]["fields"]["p10_field"]
    del optional["series"]["fields"]["p90_field"]
    assert TEMPLATE_ADAPTER.validate_python(optional).kind == "forecast"
    _bad(optional, lambda d: d.update(capabilities=["pv_quantiles"]))  # needs p10 and p90


def test_region_rules() -> None:
    base = region_doc()
    assert TEMPLATE_ADAPTER.validate_python(base).kind == "region"
    generic = {**base, "id": "generic", "name": "template.generic.name", "country": "generic"}
    generic["timezone"] = None
    generic["currency"] = None
    assert TEMPLATE_ADAPTER.validate_python(generic).kind == "region"
    _bad(base, lambda d: d.update(currency=None))  # only `generic` may omit the currency
    _bad(base, lambda d: d.update(country="Germany"))
    _bad(base, lambda d: d.update(currency="eur"))
    _bad(base, lambda d: d.update(timezone="Berlin"))
    _bad(base, lambda d: d.update(negative_price_rule="maybe"))
    _bad(base, lambda d: d["feed_in_tiers"][0].update(up_to_kwp=None))  # unbounded not last
    _bad(base, lambda d: d["feed_in_tiers"].insert(1, {"up_to_kwp": 5, "rate_per_kwh": 0.1}))
    _bad(base, lambda d: d["feed_in_tiers"][0].update(rate_per_kwh=-0.1))
    _bad(
        base,
        lambda d: d["feed_in_tiers"][0].update(valid_from="2026-02-01", valid_until="2026-01-01"),
    )


def test_region_id_may_have_variant_but_not_more() -> None:
    doc = region_doc("de.nrw")
    assert TEMPLATE_ADAPTER.validate_python(doc).id == "de.nrw"
    _bad(region_doc(), lambda d: d.update(id="a.b.c", name="template.a.b.c.name"))


# --- ADR-0012 additions ----------------------------------------------------------


def test_role_references_same_device_as() -> None:
    doc = device_doc()
    doc["roles"]["energy"]["same_device_as"] = "climate"
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "device"
    _bad(doc, lambda d: d["roles"]["energy"].update(same_device_as="energy"))
    _bad(doc, lambda d: d["roles"]["energy"].update(same_device_as="nope"))
    _bad(doc, lambda d: d["roles"]["climate"].update(same_device_as="energy"))  # chain


def test_matcher_regex_and_exclusions() -> None:
    doc = device_doc()
    matcher = doc["roles"]["energy"]["matcher"]
    matcher.pop("units")
    matcher.update(
        translation_key_pattern=r"^energy_usage_today(_for_location)?$", unit_pattern="Wh"
    )
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "device"
    _bad(doc, lambda d: d["roles"]["energy"]["matcher"].update(translation_key="x"))
    _bad(doc, lambda d: d["roles"]["energy"]["matcher"].update(units=["Wh"]))
    _bad(doc, lambda d: d["roles"]["energy"]["matcher"].update(unit_pattern="("))
    _bad(doc, lambda d: d["roles"]["energy"]["matcher"].update(translation_key_pattern="x" * 201))
    _bad(doc, lambda d: d["roles"]["energy"]["matcher"].update(platforms=[]))
    _bad(doc, lambda d: d["roles"]["energy"]["matcher"].update(platforms=["a"] * 9))
    ok = device_doc()
    ok["roles"]["climate"]["matcher"]["domain"] = "co2_2"
    assert TEMPLATE_ADAPTER.validate_python(ok).kind == "device"  # digits allowed (plan R8)


def test_learned_default_needs_no_nominal_w() -> None:
    doc = device_doc()
    doc["defaults"]["power_model"] = {
        "kind": "learned",
        "energy_role": "energy",
        "counter_update_s": 3600,
    }
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "device"
    _bad(doc, lambda d: d["defaults"]["power_model"].update(counter_update_s=0))
    _bad(doc, lambda d: d["defaults"]["power_model"].pop("energy_role"))


def test_controller_defaults_bounds() -> None:
    doc = device_doc()
    doc["defaults"].update(hysteresis_w=50, hysteresis_delay_s=30, settle_s=5)
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "device"
    _bad(doc, lambda d: d["defaults"].update(settle_s=601))
    _bad(doc, lambda d: d["defaults"].update(hysteresis_delay_s=3601))
    _bad(doc, lambda d: d["defaults"].update(hysteresis_w=-1))


def test_action_allowlist_refuses_unknown_service_with_reason_key() -> None:
    doc = tariff_doc()
    doc["price_series"].update(domain="light", service="turn_on")
    with pytest.raises(ValidationError, match=ERR_ACTION_NOT_ALLOWED):
        TEMPLATE_ADAPTER.validate_python(doc)
    for domain, service in (
        ("tibber", "get_prices"),
        ("nordpool", "get_prices_for_date"),
        ("weather", "get_forecasts"),
    ):
        doc["price_series"].update(domain=domain, service=service)
        assert TEMPLATE_ADAPTER.validate_python(doc).kind == "tariff"


def test_action_series_bounds() -> None:
    base = tariff_doc()
    _bad(base, lambda d: d["price_series"].update(max_horizon_h=0))
    _bad(base, lambda d: d["price_series"].update(max_horizon_h=337))
    _bad(base, lambda d: d["price_series"].update(response_path="prices..x"))
    _bad(base, lambda d: d["price_series"].update(response_path=""))


def test_capability_evidence() -> None:
    doc = device_doc()
    doc["capabilities"] = ["energy_counter", "temperature_setpoint"]
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "device"
    _bad(doc, lambda d: d.update(capabilities=["on_off"]))  # control is setpoint
    _bad(doc, lambda d: d.update(capabilities=["power_measured"]))  # no power role
    region = region_doc()
    region["capabilities"] = ["feed_in_tiers", "negative_price_rule"]
    assert TEMPLATE_ADAPTER.validate_python(region).kind == "region"
    _bad(region, lambda d: d.update(feed_in_tiers=[]))
    _bad(region, lambda d: d.update(negative_price_rule="none"))


def test_min_app_version_is_a_header_field() -> None:
    doc = device_doc()
    doc["min_app_version"] = "0.3.0"
    assert TEMPLATE_ADAPTER.validate_python(doc).min_app_version == "0.3.0"


def test_tariff_prices_per_mwh_are_accepted_and_converted() -> None:
    """M4: ENTSO-E/EPEX style prices in <currency>/MWh; the adapter divides by 1000."""
    doc = tariff_doc()
    doc["price_series"]["fields"]["value_unit"] = "{currency}/MWh"
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "tariff"
    doc["price_unit"] = "EUR/MWh"
    doc["price_series"]["fields"]["value_unit"] = "EUR/MWh"
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "tariff"
    _bad(doc, lambda d: d["price_series"]["fields"].update(value_unit="USD/kWh"))
    _bad(doc, lambda d: d["price_series"]["fields"].update(value_unit="EUR/Wh"))
    _bad(doc, lambda d: d.update(price_unit="EUR/GWh"))
    assert price_unit_factor("{currency}/kWh") == 1.0
    assert price_unit_factor("EUR/MWh") * 85.0 == pytest.approx(0.085)
    with pytest.raises(ValueError, match="not a price unit"):
        price_unit_factor("EUR")


def test_attribute_series_mapping_shape() -> None:
    """M4: ``{iso_timestamp: value}`` attributes (Forecast.Solar ``wh_hours``)."""
    doc = forecast_doc()
    doc["series"].update(attribute="wh_hours", shape="mapping")
    doc["series"]["fields"] = {"value_unit": "Wh"}
    doc["resolution_min"] = 60
    template = TEMPLATE_ADAPTER.validate_python(doc)
    assert isinstance(template, ForecastTemplate)
    assert template.series.shape == "mapping"  # type: ignore[union-attr]
    _bad(doc, lambda d: d["series"]["fields"].update(timestamp_field="t"))
    _bad(doc, lambda d: d["series"]["fields"].update(p10_field="p10"))
    _bad(doc, lambda d: d["series"].update(shape="table"))
    listed = forecast_doc()
    assert TEMPLATE_ADAPTER.validate_python(listed).series.shape == "list"  # type: ignore[union-attr]
    _bad(listed, lambda d: d["series"]["fields"].pop("timestamp_field"))
    action = tariff_doc()
    _bad(action, lambda d: d["price_series"]["fields"].pop("timestamp_field"))
    nested = forecast_doc()
    nested["series"]["list_path"] = "result.watts"
    assert TEMPLATE_ADAPTER.validate_python(nested).kind == "forecast"
    _bad(nested, lambda d: d["series"].update(list_path="result..watts"))


def test_action_series_per_date_requests_and_user_parameters() -> None:
    """WP4: Nord Pool style action - one call per delivery date, request fields from the user."""
    doc = tariff_doc()
    series = doc["price_series"]
    series.update(domain="nordpool", service="get_prices_for_date", response_path="*")
    series.pop("start_field")
    series.pop("end_field")
    series.update(
        date_field="date",
        params=[
            {
                "name": "config_entry",
                "required": True,
                "description": "template.acme.dynamic.param.entry",
            },
            {
                "name": "areas",
                "required": False,
                "description": "template.acme.dynamic.param.areas",
            },
        ],
    )
    series["fields"].update(end_field="end", value_unit="{currency}/MWh")
    doc["price_unit"] = "{currency}/MWh"
    template = TEMPLATE_ADAPTER.validate_python(doc)
    assert isinstance(template, TariffTemplate)
    assert template.message_keys() == [
        "template.acme.dynamic.name",
        "template.acme.dynamic.role.price",
        "template.acme.dynamic.param.entry",
        "template.acme.dynamic.param.areas",
    ]
    _bad(doc, lambda d: d["price_series"].update(start_field="start"))  # date XOR start/end
    _bad(doc, lambda d: d["price_series"].update(end_field="end"))
    _bad(
        doc,
        lambda d: d["price_series"]["params"].append(
            {"name": "areas", "required": False, "description": "template.acme.dynamic.param.x"}
        ),
    )
    _bad(
        doc,
        lambda d: d["price_series"]["params"].append(
            {"name": "date", "required": False, "description": "template.acme.dynamic.param.x"}
        ),
    )
    _bad(doc, lambda d: d["price_series"].update(params=[{"name": "x", "description": "bad key"}]))
    _bad(doc, lambda d: d["price_series"].update(response_path="*."))


def test_the_slot_end_field_belongs_to_lists_not_to_mapping_attributes() -> None:
    doc = tariff_doc()
    doc["price_series"]["fields"]["end_field"] = "end"
    assert TEMPLATE_ADAPTER.validate_python(doc).kind == "tariff"
    mapping = forecast_doc()
    mapping["series"].update(attribute="wh_hours", shape="mapping")
    mapping["series"]["fields"] = {"value_unit": "Wh", "end_field": "end"}
    mapping["resolution_min"] = 60
    _bad(mapping, lambda d: d["series"].update(shape="mapping"))  # end_field on a mapping
