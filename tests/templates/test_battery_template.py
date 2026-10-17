# SPDX-License-Identifier: Apache-2.0
"""Template kind ``battery`` and the forecast ``list_path`` (schema v1 additions, Phase 4)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from suncore.templates import BatteryTemplate, load_templates
from suncore.templates.models import (
    TEMPLATE_ADAPTER,
    TEMPLATE_KINDS,
    ForecastTemplate,
    ModeSelectControl,
)
from tests.templates.conftest import _header, forecast_doc, write_template


def _role(tid: str, name: str, domain: str, **matcher: object) -> dict[str, Any]:
    return {
        "matcher": {"domain": domain, **matcher},
        "description": f"template.{tid}.role.{name}",
    }


def battery_doc(tid: str = "acme.battery") -> dict[str, Any]:
    return {
        **_header("battery", tid),
        "capabilities": [
            "battery_soc",
            "power_measured",
            "min_soc_limit",
            "charge_limit",
            "discharge_limit",
            "grid_charge",
            "battery_modes",
        ],
        "roles": {
            "soc": _role(tid, "soc", "sensor", device_class="battery", units=["%"]),
            "power": _role(tid, "power", "sensor", device_class="power"),
            "min_soc_number": _role(tid, "min_soc_number", "number"),
            "max_charge_number": _role(tid, "max_charge_number", "number"),
            "max_discharge_number": _role(tid, "max_discharge_number", "number"),
            "mode_select": _role(tid, "mode_select", "select"),
        },
        "controls": {
            "min_soc_number": {"kind": "min_soc", "unit": "%", "min": 5, "max": 100, "step": 1},
            "max_charge_number": {"kind": "max_charge", "unit": "W", "min": 0, "max": 10_000},
            "max_discharge_number": {"kind": "max_discharge", "unit": "kW", "min": 0, "max": 10},
            "mode_select": {
                "kind": "mode",
                "options": [
                    {"value": "Automatic", "meaning": "auto"},
                    {"value": "Charge from grid", "meaning": "force_charge"},
                ],
            },
        },
        "settle_s": 5,
    }


def test_battery_kind_is_registered_and_parses() -> None:
    assert "battery" in TEMPLATE_KINDS
    template = TEMPLATE_ADAPTER.validate_python(battery_doc())
    assert isinstance(template, BatteryTemplate)
    assert template.settle_s == 5
    assert "template.acme.battery.role.soc" in template.message_keys()


def test_loader_reads_the_battery_directory(tmp_path: Path) -> None:
    write_template(tmp_path, battery_doc())
    catalog = load_templates(tmp_path)
    assert [t.id for t in catalog.batteries()] == ["acme.battery"]


def _mutated(mutate: Any) -> dict[str, Any]:
    doc = copy.deepcopy(battery_doc())
    mutate(doc)
    return doc


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["controls"].update(ghost={"kind": "force_charge"}), "unknown role"),
        (
            lambda d: d["controls"].update(power={"kind": "force_charge"}),
            "needs a `switch` entity",
        ),
        (
            lambda d: d["controls"]["min_soc_number"].update(unit="W"),
            "`min_soc` is set in %",
        ),
        (lambda d: d["controls"]["max_charge_number"].update(min=10_000), "below `max`"),
        (lambda d: d["controls"]["min_soc_number"].update(max=120), "at most 100"),
        (
            lambda d: d["controls"]["mode_select"]["options"][1].update(meaning="auto"),
            "exactly one option",
        ),
        (
            lambda d: d["controls"]["mode_select"]["options"][1].update(value="Automatic"),
            "unique",
        ),
        (
            lambda d: (
                d["roles"].update(extra=_role("acme.battery", "extra", "number")),
                d["controls"].update(extra={"kind": "min_soc", "unit": "%", "min": 0, "max": 1}),
            ),
            "at most once",
        ),
        (
            lambda d: (d["controls"].pop("mode_select"), d["capabilities"].remove("grid_charge")),
            "`battery_modes` declared",
        ),
        (lambda d: d["controls"].pop("min_soc_number"), "`min_soc_limit` declared"),
        (lambda d: d["roles"]["soc"]["matcher"].pop("device_class"), "`battery_soc` declared"),
        (lambda d: d.update(id="nodot", name="template.nodot.name"), "vendor"),
        (lambda d: d["roles"].clear(), "at least one role"),
    ],
)
def test_battery_template_rejects(mutate: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        TEMPLATE_ADAPTER.validate_python(_mutated(mutate))


def test_grid_charge_capability_accepts_a_switch_or_a_power_number() -> None:
    def switch(d: dict[str, Any]) -> None:
        d["controls"].pop("mode_select")
        d["capabilities"].remove("battery_modes")
        d["roles"]["force"] = _role("acme.battery", "force", "switch")
        d["controls"]["force"] = {"kind": "force_charge"}

    assert TEMPLATE_ADAPTER.validate_python(_mutated(switch))

    def none(d: dict[str, Any]) -> None:
        d["controls"].pop("mode_select")
        d["capabilities"].remove("battery_modes")

    with pytest.raises(ValidationError, match="no grid charge control"):
        TEMPLATE_ADAPTER.validate_python(_mutated(none))


def test_forecast_series_list_path() -> None:
    doc = forecast_doc()
    doc["series"]["list_path"] = "forecast.detailed"
    template = TEMPLATE_ADAPTER.validate_python(doc)
    assert isinstance(template, ForecastTemplate)
    assert template.series.list_path == "forecast.detailed"  # type: ignore[union-attr]
    doc["series"]["list_path"] = "a..b"
    with pytest.raises(ValidationError):
        TEMPLATE_ADAPTER.validate_python(doc)


def test_mode_option_value_is_kept_verbatim() -> None:
    """The option is written to the select as listed; whitespace must not be stripped."""
    doc = battery_doc()
    doc["controls"]["mode_select"]["options"][1]["value"] = " Force charge "
    template = TEMPLATE_ADAPTER.validate_python(doc)
    assert isinstance(template, BatteryTemplate)
    control = template.controls["mode_select"]
    assert isinstance(control, ModeSelectControl)
    assert control.options[1].value == " Force charge "
