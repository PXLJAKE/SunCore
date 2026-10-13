# SPDX-License-Identifier: Apache-2.0
"""Template models: header rules, per-kind rules and the discriminated union."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from suncore.devices.templates import (
    TEMPLATE_ADAPTER,
    DeviceTemplate,
    ForecastTemplate,
    RegionTemplate,
    TariffTemplate,
)
from tests.devices.conftest import DOCS, device_doc, forecast_doc, region_doc, tariff_doc

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
    doc["kind"] = "battery"
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
    _bad(base, lambda d: d.update(capabilities=["a", "a"]))
    _bad(base, lambda d: d.update(capabilities=["Not Slug"]))
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
    _bad(base, lambda d: d["defaults"]["power_model"].update(kind="nominal", energy_role=None))
    _bad(base, lambda d: d.update(device_kind="switch"))  # comfort not applicable
    _bad(base, lambda d: d["defaults"].update(comfort={"min_c": 25, "max_c": 20}))
    _bad(base, lambda d: d["roles"]["climate"]["matcher"].update(domain="Climate"))
    _bad(base, lambda d: d["roles"]["energy"]["matcher"].update(units=[]))
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
    fixed = copy.deepcopy(base)
    fixed.update(tariff_type="fixed", roles={})
    assert TEMPLATE_ADAPTER.validate_python(fixed).kind == "tariff"


def test_forecast_rules() -> None:
    base = forecast_doc()
    _bad(base, lambda d: d.update(forecast_kind="wind"))
    _bad(base, lambda d: d.update(roles={}))
    _bad(base, lambda d: d["attributes"].pop("p50_field"))
    _bad(base, lambda d: d.update(resolution_min=0))
    optional = copy.deepcopy(base)
    del optional["attributes"]["p10_field"]
    del optional["attributes"]["p90_field"]
    assert TEMPLATE_ADAPTER.validate_python(optional).kind == "forecast"


def test_region_rules() -> None:
    base = region_doc()
    assert TEMPLATE_ADAPTER.validate_python(base).kind == "region"
    generic = {**base, "id": "generic", "name": "template.generic.name", "country": "generic"}
    generic["timezone"] = None
    assert TEMPLATE_ADAPTER.validate_python(generic).kind == "region"
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
