# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from suncore.core.site import EntityRef, SiteConfig


def ref(eid: str, invert: bool = False) -> dict[str, Any]:
    return {"entity_id": eid, "invert_sign": invert}


def test_minimal_signed_grid() -> None:
    site = SiteConfig.model_validate({"grid": {"power": ref("sensor.g")}})
    assert site.schema_version == 2
    assert site.pv == []
    assert site.battery is None
    assert site.house is None
    assert site.grid.power == EntityRef(entity_id="sensor.g")
    assert not site.grid.is_pair


def test_pair_grid_and_full() -> None:
    site = SiteConfig.model_validate(
        {
            "grid": {"import_power": ref("sensor.i"), "export_power": ref("sensor.e")},
            "pv": [
                {"name": "A", "power": ref("sensor.p1")},
                {"name": "B", "power": ref("sensor.p2", True)},
            ],
            "battery": {"power": ref("sensor.bp"), "soc": ref("sensor.bs")},
            "house": ref("sensor.h"),
        }
    )
    assert site.grid.is_pair
    assert [r.entity_id for r in site.entity_refs()] == [
        "sensor.i", "sensor.e", "sensor.p1", "sensor.p2", "sensor.bp", "sensor.bs", "sensor.h",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "grid",
    [
        {},
        {
            "power": ref("sensor.a"),
            "import_power": ref("sensor.b"),
            "export_power": ref("sensor.c"),
        },
        {"power": ref("sensor.a"), "import_power": ref("sensor.b")},
        {"import_power": ref("sensor.b")},
        {"export_power": ref("sensor.c")},
    ],
)
def test_grid_needs_exactly_one_variant(grid: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({"grid": grid})


@pytest.mark.parametrize("bad", ["Sensor.x", "sensor", "sensor.", ".x", "sensor.x-y", "a.b.c", ""])
def test_entity_id_pattern(bad: str) -> None:
    with pytest.raises(ValidationError):
        EntityRef(entity_id=bad)


@pytest.mark.parametrize(
    "good",
    [
        "sensor.1st_floor",
        "1wire.x",
        "1.a",
        "a.1",
        "input_boolean.hello_world_0123",
        "light.1kitchen",
    ],
)
def test_entity_id_pattern_accepts_ha_valid_ids(good: str) -> None:
    assert EntityRef(entity_id=good).entity_id == good


@pytest.mark.parametrize(
    "bad",
    [
        "sensor._x",
        "sensor.x_",
        "sensor.a__b",
        "_x.y",
        "x_.y",
        "a__b.c",
        "Light.kitchen",
        "sensor.é",
    ],
)
def test_entity_id_pattern_rejects_ha_invalid_ids(bad: str) -> None:
    with pytest.raises(ValidationError):
        EntityRef(entity_id=bad)


def test_duplicate_entity_rejected() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        SiteConfig.model_validate(
            {"grid": {"power": ref("sensor.a")}, "pv": [{"name": "A", "power": ref("sensor.a")}]}
        )


def test_soc_invert_rejected_and_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(
            {
                "grid": {"power": ref("sensor.a")},
                "battery": {"power": ref("sensor.b"), "soc": ref("sensor.c", True)},
            }
        )
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({"grid": {"power": ref("sensor.a")}, "bogus": 1})


def test_frozen_and_json_round_trip() -> None:
    site = SiteConfig.model_validate({"grid": {"power": ref("sensor.a")}})
    with pytest.raises(ValidationError):
        site.schema_version = 2  # type: ignore[misc]
    assert SiteConfig.model_validate_json(site.model_dump_json()) == site
