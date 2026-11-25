# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/params``: listing, validated all-or-nothing writes, safety confirmation, reset."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from suncore.api import create_app
from suncore.core.config import ForecastConfig, TariffConfig
from suncore.core.control import SAFETY_LIMITS
from suncore.core.expert import ExpertConfig
from suncore.core.params import REGISTRY
from tests.api.conftest import FakeServices, client_for

URL = "/api/v1/params"


def tariff() -> TariffConfig:
    return TariffConfig.model_validate(
        {
            "import": {"kind": "fixed", "fixed_price": 0.3},
            "export": {"kind": "fixed", "fixed_price": 0.08},
        }
    )


def by_key(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["key"]: p for p in body["params"]}


async def test_get_lists_every_parameter_with_default_and_value(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.get(URL)
    assert response.status_code == 200
    body = response.json()
    params = by_key(body)
    assert set(params) == set(REGISTRY)
    assert body["groups"] == ["controller", "planner", "tariff", "forecast"]
    tick = params["controller.tick_s"]
    assert (tick["default"], tick["value"], tick["modified"]) == (5.0, 5.0, False)
    assert (tick["minimum"], tick["maximum"], tick["unit"]) == (2.0, 60.0, "s")
    assert tick["help_key"] == "param.controller.tick_s.help"
    assert tick["safety"] is False
    assert params["controller.min_write_interval_s"]["safety"] is True
    assert params["controller.min_write_interval_s"]["tags"] == ["expert", "safety"]
    # A config parameter of a section that does not exist yet is shown, not changeable.
    assert params["tariff.export.remunerate_negative"]["available"] is False
    assert params["controller.tick_s"]["available"] is True


async def test_put_stores_in_the_expert_section_and_marks_modified(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.put(
            URL, json={"changes": {"controller.tick_s": 10, "planner.mip_gap": 0.05}}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert sorted(body["changed"]) == ["controller.tick_s", "planner.mip_gap"]
        params = by_key(body)
        assert params["controller.tick_s"]["value"] == 10.0
        assert params["controller.tick_s"]["modified"] is True
        again = by_key((await client.get(URL)).json())
    assert again["planner.mip_gap"]["value"] == 0.05
    stored = services.sections["expert"]
    assert isinstance(stored, ExpertConfig)
    assert stored.overrides == {"controller.tick_s": 10.0, "planner.mip_gap": 0.05}


async def test_put_rejects_unknown_wrong_type_and_out_of_range_without_storing(
    services: FakeServices,
) -> None:
    async with client_for(create_app(services)) as client:
        for key, value, issue in (
            ("controller.nope", 1, "api.validation.param_unknown"),
            ("controller.tick_s", "fast", "api.validation.param_wrong_type"),
            ("controller.tick_s", 1000, "api.validation.param_out_of_range"),
            ("forecast.learning.load_ml_mode", "magic", "api.validation.param_not_in_choices"),
        ):
            response = await client.put(
                URL, json={"changes": {"planner.budget_s": 9.0, key: value}}
            )
            assert response.status_code == 422, (key, response.text)
            body = response.json()
            assert body["detail"] == "api.error.validation"
            assert body["issues"][0]["key"] == issue
            assert body["issues"][0]["loc"] == ["body", "changes", key]
    assert "expert" not in services.sections  # all or nothing


async def test_put_out_of_range_args_name_the_hard_range(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.put(
            URL,
            json={"changes": {"controller.min_write_interval_s": 0.5}, "confirm_safety": True},
        )
    issue = response.json()["issues"][0]
    assert issue["key"] == "api.validation.param_out_of_range"
    assert issue["args"]["min"] == 1.0
    assert issue["args"]["max"] == 60.0
    assert issue["args"]["param"] == "controller.min_write_interval_s"


async def test_safety_keys_need_confirm_safety_and_never_exceed_the_hard_limits(
    services: FakeServices,
) -> None:
    async with client_for(create_app(services)) as client:
        denied = await client.put(URL, json={"changes": {"controller.reserve_w": 300}})
        assert denied.status_code == 422
        assert denied.json()["issues"][0]["key"] == "api.validation.param_confirm_required"
        assert "expert" not in services.sections
        ok = await client.put(
            URL, json={"changes": {"controller.reserve_w": 300}, "confirm_safety": True}
        )
        assert ok.status_code == 200, ok.text
        # Confirmation never widens a range: every safety limit holds with confirm_safety.
        for name, (lo, hi) in SAFETY_LIMITS.items():
            for bad in (lo - 1e-6 if lo else -1.0, hi + 1.0):
                r = await client.put(
                    URL,
                    json={"changes": {f"controller.{name}": bad}, "confirm_safety": True},
                )
                assert r.status_code == 422, (name, bad)
    stored = services.sections["expert"]
    assert isinstance(stored, ExpertConfig)
    assert stored.overrides == {"controller.reserve_w": 300.0}


async def test_confirm_is_only_asked_for_safety_values_that_change(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        await client.put(
            URL, json={"changes": {"controller.reserve_w": 300}, "confirm_safety": True}
        )
        same = await client.put(
            URL, json={"changes": {"controller.reserve_w": 300, "controller.tick_s": 9}}
        )
    assert same.status_code == 200, same.text
    assert same.json()["changed"] == ["controller.tick_s"]


async def test_config_params_are_written_into_their_section(services: FakeServices) -> None:
    services.sections["tariff"] = tariff()
    services.sections["forecast"] = ForecastConfig()
    async with client_for(create_app(services)) as client:
        response = await client.put(
            URL,
            json={
                "changes": {
                    "tariff.export.remunerate_negative": True,
                    "tariff.export.negative_reference_entity": "sensor.spot",
                    "forecast.learning.min_history_days": 14,
                }
            },
        )
        assert response.status_code == 200, response.text
        params = by_key(response.json())
    assert params["tariff.export.remunerate_negative"]["value"] is True
    assert params["tariff.export.negative_reference_entity"]["value"] == "sensor.spot"
    stored = services.sections["tariff"]
    assert isinstance(stored, TariffConfig)
    assert stored.export.remunerate_negative is True
    assert stored.import_.fixed_price == 0.3  # the rest of the section is untouched
    forecast = services.sections["forecast"]
    assert isinstance(forecast, ForecastConfig)
    assert forecast.learning.min_history_days == 14
    assert "expert" not in services.sections  # nothing in the expert section was touched


async def test_config_param_without_its_section_is_a_409(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.put(
            URL, json={"changes": {"tariff.export.remunerate_negative": True}}
        )
    assert response.status_code == 409
    assert response.json() == {"detail": "api.error.param_section_missing"}


async def test_reset_one_key_and_a_group(services: FakeServices) -> None:
    services.sections["tariff"] = tariff()
    async with client_for(create_app(services)) as client:
        await client.put(
            URL,
            json={
                "changes": {
                    "controller.tick_s": 10,
                    "controller.smoothing_s": 120,
                    "planner.mip_gap": 0.05,
                    "tariff.export.remunerate_negative": True,
                },
            },
        )
        one = await client.post(f"{URL}/reset", json={"key": "controller.tick_s"})
        assert one.status_code == 200, one.text
        assert one.json()["changed"] == ["controller.tick_s"]
        assert by_key(one.json())["controller.smoothing_s"]["value"] == 120.0
        group = await client.post(f"{URL}/reset", json={"group": "controller"})
        assert group.json()["changed"] == ["controller.smoothing_s"]
        tariff_reset = await client.post(f"{URL}/reset", json={"group": "tariff"})
        assert tariff_reset.json()["changed"] == ["tariff.export.remunerate_negative"]
    stored = services.sections["expert"]
    assert isinstance(stored, ExpertConfig)
    assert stored.overrides == {"planner.mip_gap": 0.05}
    current = services.sections["tariff"]
    assert isinstance(current, TariffConfig)
    assert current.export.remunerate_negative is False


async def test_reset_needs_no_confirmation_and_restores_a_safety_default(
    services: FakeServices,
) -> None:
    async with client_for(create_app(services)) as client:
        await client.put(
            URL, json={"changes": {"controller.reserve_w": 300}, "confirm_safety": True}
        )
        response = await client.post(f"{URL}/reset", json={"key": "controller.reserve_w"})
    assert response.status_code == 200
    assert by_key(response.json())["controller.reserve_w"]["value"] == 100.0
    assert services.sections["expert"] == ExpertConfig()


async def test_reset_validates_its_target(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        both = await client.post(f"{URL}/reset", json={"key": "controller.tick_s", "group": "x"})
        neither = await client.post(f"{URL}/reset", json={})
        unknown_key = await client.post(f"{URL}/reset", json={"key": "nope"})
        unknown_group = await client.post(f"{URL}/reset", json={"group": "nope"})
        # a config parameter whose section does not exist is at its default already
        missing = await client.post(f"{URL}/reset", json={"group": "tariff"})
    assert both.status_code == neither.status_code == 422
    assert both.json()["issues"][0]["key"] == "api.validation.param_reset_target"
    assert unknown_key.status_code == unknown_group.status_code == 404
    assert unknown_key.json() == {"detail": "api.error.unknown_param"}
    assert missing.status_code == 200
    assert missing.json()["changed"] == []


async def test_put_config_expert_cannot_bypass_the_confirmation(services: FakeServices) -> None:
    """The generic section route accepts the document only without a safety change."""
    async with client_for(create_app(services)) as client:
        sneaky = await client.put(
            "/api/v1/config/expert", json={"overrides": {"controller.reserve_w": 300}}
        )
        assert sneaky.status_code == 422
        assert sneaky.json()["issues"][0]["key"] == "api.validation.param_confirm_required"
        fine = await client.put(
            "/api/v1/config/expert", json={"overrides": {"controller.tick_s": 9}}
        )
        assert fine.status_code == 200, fine.text
        out_of_range = await client.put(
            "/api/v1/config/expert", json={"overrides": {"controller.tick_s": 500}}
        )
        assert out_of_range.status_code == 422
        # Lowering a safety override back towards the default is a safety change too.
        services.sections["expert"] = ExpertConfig(overrides={"controller.reserve_w": 300})
        lowered = await client.put("/api/v1/config/expert", json={"overrides": {}})
        assert lowered.status_code == 422


def test_the_expert_api_has_no_write_path_to_devices_or_modes() -> None:
    """Boundary: expert mode writes configuration only (ADR-0015 decision 4)."""
    root = Path(__file__).resolve().parents[2] / "suncore"
    forbidden = {"apply", "call_service", "set_device_mode", "set_global_mode", "set_battery_mode"}
    for rel in ("api/params.py", "core/params.py", "core/expert.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute | ast.Name):
                name = node.attr if isinstance(node, ast.Attribute) else node.id
                assert name not in forbidden, (rel, name)


@pytest.mark.parametrize("body", [{"changes": {}}, {}, {"changes": {"a": 1}, "extra": 1}])
async def test_put_rejects_malformed_bodies(services: FakeServices, body: dict[str, Any]) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.put(URL, json=body)
    assert response.status_code == 422


async def test_put_rejects_a_staleness_window_beyond_its_hard_bound(services: FakeServices) -> None:
    """``inputs_max_age_ticks * tick_s`` > 180 s is a 422 naming the largest allowed value,
    with or without confirmation, also when the two keys arrive in separate requests."""
    async with client_for(create_app(services)) as client:
        both = await client.put(
            URL,
            json={
                "changes": {"controller.tick_s": 60, "controller.inputs_max_age_ticks": 10},
                "confirm_safety": True,
            },
        )
        first = await client.put(URL, json={"changes": {"controller.tick_s": 60}})
        second = await client.put(
            URL,
            json={"changes": {"controller.inputs_max_age_ticks": 10}, "confirm_safety": True},
        )
    assert both.status_code == 422, both.text
    issue = both.json()["issues"][0]
    assert issue["key"] == "api.validation.param_out_of_range"
    assert issue["args"]["max"] == 3.0
    assert first.status_code == 200
    assert second.status_code == 422
    stored = services.sections["expert"]
    assert isinstance(stored, ExpertConfig)
    assert stored.overrides == {"controller.tick_s": 60.0}
