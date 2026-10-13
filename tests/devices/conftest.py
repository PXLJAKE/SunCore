# SPDX-License-Identifier: Apache-2.0
"""Shared template fixtures: minimal valid documents per kind."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

SOURCE = [{"title": "Docs", "url": "https://example.org/docs", "date": "2026-10-13"}]


def _header(kind: str, tid: str) -> dict[str, Any]:
    return {
        "id": tid,
        "kind": kind,
        "schema_version": 1,
        "name": f"template.{tid}.name",
        "source": copy.deepcopy(SOURCE),
        "tested_with": [{"integration": "demo", "version": "1.0"}],
        "capabilities": ["demo"],
    }


def device_doc(tid: str = "acme.heater") -> dict[str, Any]:
    return {
        **_header("device", tid),
        "device_kind": "climate",
        "roles": {
            "climate": {
                "matcher": {"domain": "climate", "platform": "acme"},
                "description": f"template.{tid}.role.climate",
            },
            "energy": {
                "required": False,
                "matcher": {
                    "domain": "sensor",
                    "device_class": "energy",
                    "units": ["Wh", "kWh"],
                    "state_class": "total_increasing",
                },
                "description": f"template.{tid}.role.energy",
            },
        },
        "defaults": {
            "power_model": {"kind": "counter_derived", "energy_role": "energy"},
            "control": "setpoint",
            "min_run_s": 300,
            "min_pause_s": 180,
            "comfort": {"min_c": 21, "max_c": 24},
        },
    }


def tariff_doc(tid: str = "acme.dynamic") -> dict[str, Any]:
    return {
        **_header("tariff", tid),
        "direction": "import",
        "tariff_type": "dynamic",
        "roles": {
            "price": {
                "matcher": {"domain": "sensor", "platform": "acme", "device_class": "monetary"},
                "description": f"template.{tid}.role.price",
            }
        },
        "price_unit": "{currency}/kWh",
        "resolution_min": 15,
    }


def forecast_doc(tid: str = "acme.solar") -> dict[str, Any]:
    return {
        **_header("forecast", tid),
        "forecast_kind": "pv",
        "roles": {
            "today": {
                "matcher": {"domain": "sensor", "platform": "acme", "device_class": "energy"},
                "description": f"template.{tid}.role.today",
            }
        },
        "attributes": {
            "series_attribute": "detailed",
            "timestamp_field": "period_start",
            "p50_field": "p50",
            "p10_field": "p10",
            "p90_field": "p90",
            "value_unit": "kW",
        },
        "resolution_min": 30,
    }


def region_doc(tid: str = "xx") -> dict[str, Any]:
    return {
        **_header("region", tid),
        "country": "DE",
        "currency": "EUR",
        "timezone": "Europe/Berlin",
        "negative_price_rule": "no_remuneration",
        "feed_in_tiers": [
            {"up_to_kwp": 10, "rate_per_kwh": 0.08},
            {"rate_per_kwh": 0.07},
        ],
    }


DOCS = {
    "device": device_doc,
    "tariff": tariff_doc,
    "forecast": forecast_doc,
    "region": region_doc,
}


def write_template(root: Path, doc: dict[str, Any], *, stem: str | None = None) -> Path:
    directory = root / doc["kind"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem or doc['id']}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def builtin(tmp_path: Path) -> Path:
    root = tmp_path / "builtin"
    for factory in DOCS.values():
        write_template(root, factory())
    return root
