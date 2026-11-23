# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/templates``: the gallery, details and loader errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from suncore.api import create_app
from suncore.templates import load_templates
from tests.api.conftest import TEMPLATES_DIR, FakeServices, client_for


async def test_list_all_templates(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/templates")
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    order = [(t["kind"], t["id"]) for t in body["templates"]]
    assert order == sorted(order)
    ids = {t["id"] for t in body["templates"]}
    assert {"lg.thinq_climate", "generic.switch", "tibber.price", "solcast.pv", "de"} <= ids
    lg = next(t for t in body["templates"] if t["id"] == "lg.thinq_climate")
    assert lg["kind"] == "device"
    assert lg["name"] == "template.lg.thinq_climate.name"
    assert lg["origin"] == "builtin"
    assert lg["overridden"] is False
    assert lg["device_kind"] == "climate"
    assert "climate" in lg["roles"]
    assert lg["unverified"]
    de = next(t for t in body["templates"] if t["id"] == "de")
    assert de["roles"] == []
    assert de["device_kind"] is None


@pytest.mark.parametrize("kind", ["device", "tariff", "forecast", "region"])
async def test_filter_by_kind(services: FakeServices, kind: str) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/templates", params={"kind": kind})
    kinds = {t["kind"] for t in r.json()["templates"]}
    assert kinds == {kind}


async def test_unknown_kind_is_rejected(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/templates", params={"kind": "inverter"})
    assert r.status_code == 422


async def test_template_detail(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/templates/lg.thinq_climate")
        missing = await client.get("/api/v1/templates/nope.nope")
    assert r.status_code == 200
    body = r.json()
    assert body["origin"] == "builtin"
    template = body["template"]
    assert template["kind"] == "device"
    assert template["id"] == "lg.thinq_climate"
    assert "roles" in template
    assert "defaults" in template
    assert missing.status_code == 404
    assert missing.json() == {"detail": "api.error.unknown_template"}


async def test_user_templates_and_loader_errors_are_exposed(
    services: FakeServices, tmp_path: Path
) -> None:
    user = tmp_path / "templates"
    (user / "device").mkdir(parents=True)
    source = (TEMPLATES_DIR / "device" / "generic.switch.yaml").read_text()
    (user / "device" / "generic.switch.yaml").write_text(source)  # overrides the built-in
    (user / "device" / "broken.thing.yaml").write_text("id: broken.thing\nkind: device\n")
    services.catalog = load_templates(TEMPLATES_DIR, user)
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/templates")
        detail = await client.get("/api/v1/templates/generic.switch")
    body = r.json()
    switch = next(t for t in body["templates"] if t["id"] == "generic.switch")
    assert switch["origin"] == "user"
    assert switch["overridden"] is True
    assert detail.json()["origin"] == "user"
    assert [e["file"] for e in body["errors"]] == ["device/broken.thing.yaml"]
    assert body["errors"][0]["message"]
    assert str(tmp_path) not in r.text  # no filesystem paths on the wire


def test_loader_messages_do_not_leak_the_data_directory(tmp_path: Path) -> None:
    from suncore.api.models import _error_out  # noqa: PLC0415
    from suncore.templates.loader import TemplateFileError  # noqa: PLC0415

    path = tmp_path / "templates" / "device" / "a.yaml"
    other = tmp_path / "templates" / "device" / "b.yaml"
    err = TemplateFileError(path, f"duplicate id `x.y` (also {other}); see {path}: line 3 a/b/c")
    out = _error_out(err)
    assert out.file == "device/a.yaml"
    assert str(tmp_path) not in out.message
    assert out.message == "duplicate id `x.y` (also device/b.yaml); see device/a.yaml: line 3 a/b/c"
