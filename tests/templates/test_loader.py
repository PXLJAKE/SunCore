# SPDX-License-Identifier: Apache-2.0
"""Loader: built-in strictness, user overrides, per-file error collection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from suncore.templates import (
    BuiltinTemplateError,
    DeviceTemplate,
    load_templates,
    parse_template,
)
from suncore.templates.loader import version_key
from tests.templates.conftest import DOCS, device_doc, tariff_doc, write_template


def test_loads_all_kinds(builtin: Path) -> None:
    catalog = load_templates(builtin)
    assert set(catalog.templates) == {"acme.heater", "acme.dynamic", "acme.solar", "xx"}
    assert catalog.errors == ()
    assert catalog.overridden == ()
    assert all(o == "builtin" for o in catalog.origins.values())
    assert [t.id for t in catalog.devices()] == ["acme.heater"]
    assert [t.id for t in catalog.tariffs()] == ["acme.dynamic"]
    assert [t.id for t in catalog.forecasts()] == ["acme.solar"]
    assert [t.id for t in catalog.regions()] == ["xx"]
    assert catalog.get("nope") is None


def test_missing_directories_are_fine(tmp_path: Path) -> None:
    assert load_templates(tmp_path / "does-not-exist").templates == {}
    assert load_templates(tmp_path, tmp_path / "also-missing").errors == ()


def test_user_template_overrides_by_id(builtin: Path, tmp_path: Path) -> None:
    user = tmp_path / "user"
    doc = device_doc()
    doc["defaults"]["min_run_s"] = 999
    write_template(user, doc)
    write_template(user, device_doc("me.custom"))
    catalog = load_templates(builtin, user)
    heater = catalog.get("acme.heater")
    assert isinstance(heater, DeviceTemplate)
    assert heater.defaults.min_run_s == 999
    assert catalog.origins["acme.heater"] == "user"
    assert catalog.origins["me.custom"] == "user"
    assert catalog.origins["acme.dynamic"] == "builtin"
    assert catalog.overridden == ("acme.heater",)
    assert catalog.errors == ()


def test_builtin_invalid_raises_with_all_errors(builtin: Path) -> None:
    (builtin / "device" / "broken.yaml").write_text("id: [", encoding="utf-8")
    bad = device_doc("acme.bad")
    bad["roles"] = {}
    write_template(builtin, bad)
    with pytest.raises(BuiltinTemplateError) as exc:
        load_templates(builtin)
    messages = [str(e) for e in exc.value.errors]
    assert len(messages) == 2
    assert any("broken.yaml" in m and "invalid YAML" in m for m in messages)
    assert any("acme.bad.yaml" in m and "roles" in m for m in messages)


def test_builtin_duplicate_id_raises(builtin: Path) -> None:
    # same id in a second file name -> name mismatch error
    write_template(builtin, device_doc(), stem="copy")
    with pytest.raises(BuiltinTemplateError, match="file name must be"):
        load_templates(builtin)


def test_user_errors_are_collected_not_raised(builtin: Path, tmp_path: Path) -> None:
    user = tmp_path / "user"
    write_template(user, device_doc("me.good"))
    (user / "device").mkdir(parents=True, exist_ok=True)
    (user / "device" / "garbage.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    (user / "device" / "syntax.yaml").write_text("a: b: c", encoding="utf-8")
    (user / "device" / "binary.yaml").write_bytes(b"\xff\xfe\x00")
    write_template(user, device_doc("me.mismatch"), stem="other_name")
    (user / "device" / "me.wrongdir.yaml").write_text(
        yaml.safe_dump(tariff_doc("me.wrongdir")), encoding="utf-8"
    )
    # builtin id reused with another kind
    clash = tariff_doc("acme.heater")
    write_template(user, clash)
    big = user / "device" / "big.yaml"
    big.write_text("#" + "x" * 300_000, encoding="utf-8")

    catalog = load_templates(builtin, user)

    assert "me.good" in catalog.templates
    assert catalog.get("acme.heater") is not None
    assert catalog.get("acme.heater").kind == "device"  # type: ignore[union-attr]
    text = "\n".join(str(e) for e in catalog.errors)
    for needle in (
        "garbage.yaml: top level must be a mapping",
        "syntax.yaml: invalid YAML",
        "binary.yaml: not valid UTF-8",
        "other_name.yaml: file name must be `me.mismatch.yaml`",
        "does not match directory",
        "kind fixed",
        "big.yaml: file too large",
    ):
        assert needle in text, needle
    assert len(catalog.errors) == 7


def test_user_duplicate_ids_report_second(builtin: Path, tmp_path: Path) -> None:
    user = tmp_path / "user"
    write_template(user, device_doc("me.dup"))
    # a second file with the same id cannot share the file name, so it is a name error;
    # ids are unique per directory scan by construction
    write_template(user, device_doc("me.dup"), stem="me.dup2")
    catalog = load_templates(builtin, user)
    assert [e.path.name for e in catalog.errors] == ["me.dup2.yaml"]
    assert "me.dup" in catalog.templates


def test_validation_message_names_the_field(tmp_path: Path) -> None:
    doc = DOCS["region"]()
    doc["currency"] = "euro"
    path = write_template(tmp_path, doc)
    with pytest.raises(ValueError, match=r"currency"):
        parse_template(path)
    with pytest.raises(ValueError, match="does not match directory"):
        parse_template(write_template(tmp_path, DOCS["region"]("yy")), "device")
    with pytest.raises(ValueError, match="cannot read"):
        parse_template(tmp_path / "region" / "nope.yaml")


def test_unknown_kind_directory_is_an_error(builtin: Path) -> None:
    battery = builtin / "battery"
    battery.mkdir()
    (battery / "kostal.plenticore.yaml").write_text("id: x\n", encoding="utf-8")
    with pytest.raises(BuiltinTemplateError, match="unknown template kind directory `battery`"):
        load_templates(builtin)


def test_min_app_version_reported_before_field_errors(tmp_path: Path) -> None:
    doc = device_doc("acme.future")
    doc["min_app_version"] = "9.1.0"
    doc["brand_new_field"] = 1  # would fail on an older SunCore too
    path = write_template(tmp_path, doc)
    with pytest.raises(ValueError, match=r"needs SunCore >= 9\.1\.0 \(this is 0\.2\.0\)"):
        parse_template(path, app_version="0.2.0")
    doc.pop("brand_new_field")
    path = write_template(tmp_path, doc)
    assert parse_template(path, app_version="9.1.0").id == "acme.future"
    assert parse_template(path, app_version="10.0").id == "acme.future"
    assert parse_template(path).id == "acme.future"  # no app version: not checked


def test_user_template_needing_newer_app_is_skipped(builtin: Path, tmp_path: Path) -> None:
    user = tmp_path / "user"
    doc = device_doc("me.future")
    doc["min_app_version"] = "3.0"
    write_template(user, doc)
    catalog = load_templates(builtin, user, app_version="0.2.0")
    assert catalog.get("me.future") is None
    assert "needs SunCore >= 3.0" in str(catalog.errors[0])


@pytest.mark.parametrize(
    ("app", "required", "ok"),
    [("0.2.0", "0.2", True), ("0.2.0rc1", "0.2.0", False), ("0.2.1", "0.2.0", True),
     ("1.0.0.dev0", "1.0", False), ("0.10.0", "0.9.9", True), ("0.2.0", "0.2.0rc1", True)],
)  # fmt: skip
def test_version_ordering(app: str, required: str, ok: bool) -> None:
    assert (version_key(app) >= version_key(required)) is ok


_BOMB = "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n"


def test_alias_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(_BOMB, encoding="utf-8")
    with pytest.raises(ValueError, match="aliases are not allowed"):
        parse_template(path)


def test_user_template_with_alias_is_a_catalog_error(builtin: Path, tmp_path: Path) -> None:
    user = tmp_path / "user"
    write_template(user, device_doc("me.good"))
    (user / "device" / "me.bomb.yaml").write_text(_BOMB, encoding="utf-8")
    catalog = load_templates(builtin, user)
    assert "me.good" in catalog.templates
    assert any("aliases are not allowed" in str(e) for e in catalog.errors)


def test_builtin_with_alias_raises(builtin: Path) -> None:
    (builtin / "device" / "acme.bomb.yaml").write_text(_BOMB, encoding="utf-8")
    with pytest.raises(BuiltinTemplateError, match="aliases are not allowed"):
        load_templates(builtin)
