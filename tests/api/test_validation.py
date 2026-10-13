# SPDX-License-Identifier: Apache-2.0
"""422 bodies carry catalogue keys, not Pydantic's English text."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from suncore.api import create_app
from suncore.api.validation import FALLBACK_KEY, VALIDATION_KEYS, issue_from_error
from tests.api.conftest import FakeServices, client_for, make_site

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "src" / "lib" / "i18n" / "locales"


async def put_site(services: FakeServices, body: dict[str, Any]) -> Any:
    async with client_for(create_app(services)) as client:
        return await client.put("/api/v1/config/site", json=body)


def issues_of(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "api.error.validation"
    return body["issues"]  # type: ignore[no-any-return]


async def test_missing_field(services: FakeServices) -> None:
    issues = issues_of(await put_site(services, {"pv": []}))
    assert issues == [{"loc": ["body", "grid"], "key": "api.validation.missing", "args": {}}]


async def test_custom_validator_key_and_args(services: FakeServices) -> None:
    body = make_site().model_dump(mode="json")
    body["grid"] = {
        "power": {"entity_id": "sensor.a"},
        "import_power": {"entity_id": "sensor.b"},
        "export_power": {"entity_id": "sensor.c"},
    }
    issues = issues_of(await put_site(services, body))
    assert issues == [
        {"loc": ["body", "grid"], "key": "api.validation.grid_variant_both", "args": {}}
    ]


async def test_context_args_are_forwarded(services: FakeServices) -> None:
    body = make_site().model_dump(mode="json")
    body["battery"] = {
        "power": {"entity_id": "sensor.p"},
        "soc": {"entity_id": "sensor.s"},
        "min_soc_pct": 80,
        "max_soc_pct": 20,
    }
    issues = issues_of(await put_site(services, body))
    assert issues == [
        {
            "loc": ["body", "battery"],
            "key": "api.validation.soc_order",
            "args": {"min_soc_pct": 80, "max_soc_pct": 20},
        }
    ]
    body["battery"] = {
        "power": {"entity_id": "sensor.p"},
        "soc": {"entity_id": "sensor.s"},
        "min_soc_pct": 101,
    }
    issues = issues_of(await put_site(services, body))
    assert issues[0]["key"] == "api.validation.less_than_equal"
    assert issues[0]["args"] == {"le": 100}
    assert issues[0]["loc"] == ["body", "battery", "min_soc_pct"]


async def test_entity_id_pattern_and_extra_fields_and_list_index(services: FakeServices) -> None:
    body = make_site().model_dump(mode="json")
    body["pv"] = [{"name": "A", "power": {"entity_id": "Not An Entity"}, "bogus": 1}]
    issues = issues_of(await put_site(services, body))
    by_key = {i["key"]: i for i in issues}
    assert by_key["api.validation.string_pattern_mismatch"]["loc"] == [
        "body",
        "pv",
        0,
        "power",
        "entity_id",
    ]
    assert by_key["api.validation.extra_forbidden"]["loc"] == ["body", "pv", 0, "bogus"]


async def test_input_is_never_echoed(services: FakeServices) -> None:
    secret = "TOP-SECRET-VALUE"
    r = await put_site(services, {"grid": {"power": {"entity_id": secret}}})
    assert secret not in r.text
    assert "msg" not in r.json()["issues"][0]


async def test_query_parameter_errors_use_the_same_shape(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/decisions", params={"limit": 0})
        bad = await client.get("/api/v1/decisions", params={"limit": "x"})
    assert issues_of(r) == [
        {"loc": ["query", "limit"], "key": "api.validation.greater_than_equal", "args": {"ge": 1}}
    ]
    assert issues_of(bad)[0]["key"] == "api.validation.int_parsing"


async def test_invalid_json_body(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.put(
            "/api/v1/config/site", content=b"{nope", headers={"content-type": "application/json"}
        )
    assert issues_of(r)[0]["key"] == "api.validation.json_invalid"


async def test_too_many_issues_are_capped(services: FakeServices) -> None:
    body = make_site().model_dump(mode="json")
    body["pv"] = [{"name": f"A{i}", "bogus": 1, "other": 2} for i in range(60)]
    assert len(issues_of(await put_site(services, body))) == 50


def test_unknown_error_types_fall_back() -> None:
    assert issue_from_error({"loc": ("body", 2), "type": "brand_new"}) == {
        "loc": ["body", 2],
        "key": FALLBACK_KEY,
        "args": {},
    }
    issue = issue_from_error(
        {"loc": ("x",), "type": "value_error", "ctx": {"error": ValueError("x"), "n": 1, "o": [1]}}
    )
    assert issue["args"] == {"n": 1}


def test_every_custom_validator_error_has_a_catalogue_key() -> None:
    """Each ``invalid("key", ...)`` in core is mapped, and every mapping has a user."""
    used: set[str] = set()
    for path in (ROOT / "suncore" / "core").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "invalid"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                used.add(f"suncore.{node.args[0].value}")
    mapped = {k for k in VALIDATION_KEYS if k.startswith("suncore.")}
    assert used
    assert used == mapped


def test_message_arguments_exist_in_the_error_context() -> None:
    """ICU placeholders of custom keys are exactly the context names the validator passes."""
    en = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    contexts: dict[str, set[str]] = {}
    for path in (ROOT / "suncore" / "core").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "invalid"
            ):
                key = f"api.validation.{node.args[0].value}"  # type: ignore[attr-defined]
                contexts[key] = {kw.arg for kw in node.keywords if kw.arg}
    import re  # noqa: PLC0415

    for key, names in contexts.items():
        placeholders = set(re.findall(r"\{(\w+)", en[key]))
        assert placeholders <= names, (key, placeholders, names)


@pytest.mark.parametrize("key", sorted(set(VALIDATION_KEYS.values()) | {FALLBACK_KEY}))
def test_keys_exist_in_both_catalogues(key: str) -> None:
    for lang in ("en", "de"):
        assert key in json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
