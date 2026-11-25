# SPDX-License-Identifier: Apache-2.0
"""Template kind ``inverter`` (PR 6-G, schema v1 addition): validation and adapter spec."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from suncore.core.inverter import InverterAdapterSpec
from suncore.templates import InverterTemplate, load_templates
from suncore.templates.models import TEMPLATE_ADAPTER, TEMPLATE_KINDS
from tests.templates.conftest import _header

ROOT = Path(__file__).resolve().parents[2]


def inverter_doc(tid: str = "acme.inverter_limit", **extra: Any) -> dict[str, Any]:
    return {
        **_header("inverter", tid),
        "capabilities": ["export_limit"],
        "driver": "kostal_rest",
        "limit_capability": "export_limit",
        "roles": {
            "limit": {
                "matcher": {"domain": "number", "units": ["W"]},
                "description": f"template.{tid}.role.limit",
            }
        },
        **extra,
    }


def modbus_doc() -> dict[str, Any]:
    doc = inverter_doc(
        "acme.modbus_limit",
        driver="kostal_modbus",
        limit_capability="ac_limit",
        capabilities=["ac_limit"],
        volatile=True,
        modbus_register=533,
        max=100,
    )
    doc["roles"]["limit"]["matcher"] = {"domain": "sensor", "platforms": ["modbus"]}
    return doc


def parse(doc: dict[str, Any]) -> InverterTemplate:
    template = TEMPLATE_ADAPTER.validate_python(doc)
    assert isinstance(template, InverterTemplate)
    return template


def test_inverter_is_a_template_kind() -> None:
    assert "inverter" in TEMPLATE_KINDS


def test_a_valid_template_and_its_adapter_spec() -> None:
    template = parse(inverter_doc())
    assert template.adapter_spec() == InverterAdapterSpec(
        template_id="acme.inverter_limit",
        driver="kostal_rest",
        capability="export_limit",
        settle_s=10.0,
    )
    spec = parse(modbus_doc()).adapter_spec()
    assert spec.volatile
    assert spec.register == 533
    assert spec.default_pct == 100.0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"roles": {}}, "role"),
        ({"capabilities": ["ac_limit"]}, "capabilities"),
        ({"limit_capability": None, "capabilities": ["export_limit", "ac_limit"]}, "kostal_rest"),
        ({"modbus_register": 533}, "modbus_register"),
        ({"min": 50, "max": 10}, "below min"),
    ],
)
def test_invalid_templates_are_refused(change: dict[str, Any], message: str) -> None:
    doc = inverter_doc()
    doc.update(change)
    with pytest.raises(ValidationError, match=message):
        parse(doc)


def test_the_limit_role_domain_follows_the_driver() -> None:
    doc = inverter_doc()
    doc["roles"]["limit"]["matcher"]["domain"] = "sensor"
    with pytest.raises(ValidationError, match="needs a `number` entity"):
        parse(doc)
    doc = modbus_doc()
    doc["roles"]["limit"]["matcher"] = {"domain": "number"}
    with pytest.raises(ValidationError, match="needs a `sensor` entity"):
        parse(doc)


@pytest.mark.parametrize("missing", ["volatile", "modbus_register"])
def test_the_modbus_driver_needs_its_register_and_volatile(missing: str) -> None:
    doc = copy.deepcopy(modbus_doc())
    doc.pop(missing)
    with pytest.raises(ValidationError, match="kostal_modbus"):
        parse(doc)


def test_an_ac_limit_is_at_most_100_percent() -> None:
    doc = modbus_doc()
    doc["max"] = 150
    with pytest.raises(ValidationError, match="100 %"):
        parse(doc)


def test_the_builtin_inverter_templates_load() -> None:
    catalog = load_templates(ROOT / "templates")
    ids = sorted(t.id for t in catalog.inverters())
    assert ids == [
        "generic.inverter_limit",
        "kostal.plenticore_g3_modbus_limit",
        "kostal.plenticore_power_limit",
    ]
