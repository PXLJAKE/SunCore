# SPDX-License-Identifier: Apache-2.0
"""Runtime services behind the wizard endpoints, on the demo fake (no network)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from suncore.api import (
    EnergyPrefsFailedError,
    EntityQuery,
    HAUnavailableError,
    UnknownRoleError,
    UnknownTemplateError,
    create_app,
)
from suncore.app.runtime import Runtime
from suncore.core.config import GoalsConfig, RegionConfig
from suncore.core.signals import Quality
from suncore.core.site import EntityRef, GridConfig, SiteConfig
from suncore.ha import EntityCatalogue
from suncore.ha.connection import CommandError, ConnectionState, NotConnectedError
from suncore.sim import demo_devices as d
from suncore.sim.fake_ha import demo_site_config
from tests.api.conftest import client_for
from tests.app.conftest import RecordingConnection


async def connected(runtime: Runtime) -> RecordingConnection:
    conn = cast("RecordingConnection", runtime.connection)
    for _ in range(300):
        if conn.state is ConnectionState.CONNECTED:
            return conn
        await asyncio.sleep(0.01)
    pytest.fail("fake HA did not connect")


async def test_site_role_detection_from_the_catalogue(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    found = await demo_runtime.entities(EntityQuery(role="grid_power"))
    top = found[0]
    assert top.entity_id == "sensor.sim_grid_power"
    assert top.criteria == (
        "detect.criterion.domain",
        "detect.criterion.device_class",
        "detect.criterion.units",
        "detect.criterion.name",
    )
    assert top.platform == "suncore_sim"
    assert top.device_name == "SunCore demo house"
    assert top.unit == "W"
    assert top.quality is Quality.OK
    assert top.value is not None
    # Energy counters and non-power sensors are not offered for a power role.
    ids = {e.entity_id for e in found}
    assert d.GRID_IMPORT_KWH_ID not in ids
    assert d.PRICE_ID not in ids
    soc = await demo_runtime.entities(EntityQuery(role="battery_soc"))
    assert soc[0].entity_id == "sensor.sim_battery_soc"


async def test_template_role_detection_with_same_device(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    climate = await demo_runtime.entities(
        EntityQuery(role="climate", template_id="lg.thinq_climate")
    )
    assert [e.entity_id for e in climate] == [d.AC_ID]
    assert climate[0].platform == "lg_thinq"
    assert climate[0].area_name == "Living room"
    template = demo_runtime.template_catalog.get("lg.thinq_climate")
    assert template is not None
    energy_role = next(
        name
        for name, spec in template.roles.items()  # type: ignore[union-attr]
        if spec.same_device_as is not None
    )
    free = await demo_runtime.entities(
        EntityQuery(role=energy_role, template_id="lg.thinq_climate")
    )
    scoped = await demo_runtime.entities(
        EntityQuery(
            role=energy_role, template_id="lg.thinq_climate", device_id=climate[0].device_id
        )
    )
    assert d.AC_ENERGY_TODAY_ID in {e.entity_id for e in free}
    assert d.AC_ENERGY_TODAY_ID in {e.entity_id for e in scoped}
    assert any("detect.criterion.same_device" in e.criteria for e in scoped)
    other = await demo_runtime.entities(
        EntityQuery(role=energy_role, template_id="lg.thinq_climate", device_id="some_other_device")
    )
    assert d.AC_ENERGY_TODAY_ID not in {e.entity_id for e in other}


async def test_entities_never_call_get_states_per_request(demo_runtime: Runtime) -> None:
    conn = await connected(demo_runtime)
    await demo_runtime.entities(EntityQuery(role="pv_power"))
    baseline = conn.get_states_calls
    for _ in range(20):
        await demo_runtime.entities(EntityQuery(role="pv_power"))
        await demo_runtime.entities(EntityQuery())
        await demo_runtime.entities(EntityQuery(role="climate", template_id="lg.thinq_climate"))
    assert conn.get_states_calls == baseline


async def test_registry_failure_falls_back_to_state_only_detection(demo_runtime: Runtime) -> None:
    conn = await connected(demo_runtime)
    conn.registry_error = CommandError("unknown_command", "no")
    demo_runtime.catalogue = EntityCatalogue(conn)  # a fresh start: nothing cached yet
    await demo_runtime.catalogue.refresh()
    assert demo_runtime.catalogue.registry_available is False
    found = await demo_runtime.entities(EntityQuery(role="grid_power"))
    assert found[0].entity_id == "sensor.sim_grid_power"
    assert found[0].platform is None
    climate = await demo_runtime.entities(
        EntityQuery(role="climate", template_id="lg.thinq_climate")
    )
    assert climate == []  # platform/translation key are unknown without the registry


async def test_unknown_role_and_template(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    with pytest.raises(UnknownRoleError):
        await demo_runtime.entities(EntityQuery(role="nope"))
    with pytest.raises(UnknownTemplateError):
        await demo_runtime.entities(EntityQuery(role="x", template_id="nope.nope"))
    with pytest.raises(UnknownRoleError):
        await demo_runtime.entities(EntityQuery(role="nope", template_id="lg.thinq_climate"))
    with pytest.raises(UnknownRoleError):  # a region has no roles
        await demo_runtime.entities(EntityQuery(role="x", template_id="de"))


async def test_entities_unavailable_before_any_state(demo_runtime: Runtime) -> None:
    conn = cast("RecordingConnection", demo_runtime.connection)
    await conn.close()
    demo_runtime.catalogue = EntityCatalogue(conn)  # nothing cached
    with pytest.raises(HAUnavailableError):
        await demo_runtime.entities(EntityQuery(role="grid_power"))


async def test_energy_suggestions_from_the_demo_dashboard(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    result = await demo_runtime.energy_suggestions()
    assert result.configured is True
    paths = {s.path: s for s in result.suggestions}
    assert paths["grid.power"].value["entity_id"] == "sensor.sim_grid_power"
    # The generated template sensor does not exist in the demo; the raw sensor behind
    # `stat_rate_inverted` does and already counts charging as positive.
    assert paths["battery.power"].value == {
        "entity_id": "sensor.sim_battery_power",
        "invert_sign": False,
    }
    assert result.notes == ()


async def test_energy_suggestions_not_configured_and_errors(demo_runtime: Runtime) -> None:
    conn = await connected(demo_runtime)
    conn._energy_prefs_configured = False
    assert (await demo_runtime.energy_suggestions()).configured is False

    async def unknown() -> None:
        raise CommandError("unknown_command", "Unknown command.")

    conn.energy_get_prefs = unknown  # type: ignore[method-assign,assignment]
    assert (await demo_runtime.energy_suggestions()).configured is False

    async def denied() -> None:
        raise CommandError("unauthorized", "no")

    conn.energy_get_prefs = denied  # type: ignore[method-assign,assignment]
    with pytest.raises(EnergyPrefsFailedError):
        await demo_runtime.energy_suggestions()

    async def not_found() -> None:
        raise CommandError("not_found", "No prefs.")

    conn.energy_get_prefs = not_found  # type: ignore[method-assign,assignment]
    assert (await demo_runtime.energy_suggestions()).configured is False

    async def unreachable() -> None:
        raise NotConnectedError("down")

    conn.energy_get_prefs = unreachable  # type: ignore[method-assign,assignment]
    with pytest.raises(HAUnavailableError):
        await demo_runtime.energy_suggestions()


async def test_system_check_of_the_demo_site(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    for _ in range(100):
        if demo_runtime.flows() and demo_runtime.flows().pv_w.value is not None:  # type: ignore[union-attr]
            break
        await asyncio.sleep(0.01)
    items = await demo_runtime.system_check(None)
    keys = [i.reason.key for i in items]
    assert "wizard.check.entities_fresh" in keys
    assert "wizard.check.units_ok" in keys
    assert any(k.startswith("wizard.check.balance") for k in keys)
    assert [i.subject for i in items if i.reason.key == "wizard.check.section_missing"] == [
        "region",
        "tariff",
    ]
    await demo_runtime.put_section(
        "region", RegionConfig(region_id="de", currency="EUR", timezone="Europe/Berlin")
    )
    again = await demo_runtime.system_check(None)
    assert [i.subject for i in again if i.reason.key == "wizard.check.section_missing"] == [
        "tariff"
    ]


async def test_system_check_of_an_unsaved_site_reads_unsubscribed_entities(
    demo_runtime: Runtime,
) -> None:
    await connected(demo_runtime)
    site = SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id=d.POOL_POWER_ID)),
        house=EntityRef(entity_id="sensor.not_there"),
    )
    items = await demo_runtime.system_check(site)
    missing = [i for i in items if i.reason.key == "wizard.check.entity_missing"]
    assert [i.subject for i in missing] == ["sensor.not_there"]
    # The pool plug is not part of the saved site, yet its value is known (browse copy).
    assert not any(i.subject == d.POOL_POWER_ID for i in items if i.severity == "error")


async def test_preview_reads_live_and_browse_values(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    fragment = {
        "battery": {
            "power": {"entity_id": "sensor.sim_battery_power", "invert_sign": True},
            "soc": {"entity_id": "sensor.sim_battery_soc"},
        },
        "entities": {"power": {"entity_id": d.POOL_POWER_ID}, "x": {"entity_id": "sensor.nope"}},
    }
    items = {i.path: i for i in await demo_runtime.preview(fragment)}
    assert items["battery.soc"].base_unit == "%"
    assert items["battery.soc"].unit_ok is True
    assert items["battery.power"].unit_ok is True
    assert items["entities.power"].unit == "W"  # not subscribed: from the browse copy
    assert items["entities.x"].quality is Quality.UNAVAILABLE


async def test_history_and_restore_reload_the_live_site(demo_runtime: Runtime) -> None:
    conn = await connected(demo_runtime)
    original = demo_site_config()
    other = SiteConfig(grid=GridConfig(power=EntityRef(entity_id=d.POOL_POWER_ID)))
    await demo_runtime.put_site(other)
    assert demo_runtime.site == other
    history = await demo_runtime.section_history("site")
    assert history
    conn.subscriptions.clear()
    restored = await demo_runtime.restore_section("site", history[0].id)
    assert restored == original
    assert demo_runtime.site == original
    assert conn.subscriptions  # entities were resubscribed for the restored site
    assert "sensor.sim_grid_power" in conn.subscriptions[-1]


async def test_restore_of_non_live_section_and_unknown_inputs(demo_runtime: Runtime) -> None:
    from suncore.storage import HistoryEntryNotFoundError, UnknownSectionError  # noqa: PLC0415

    await demo_runtime.put_section("goals", GoalsConfig(climate_before_export=False))
    await demo_runtime.put_section("goals", GoalsConfig(climate_before_export=True))
    entries = await demo_runtime.section_history("goals")
    restored = await demo_runtime.restore_section("goals", entries[0].id)
    assert isinstance(restored, GoalsConfig)
    assert restored.climate_before_export is False
    with pytest.raises(HistoryEntryNotFoundError):
        await demo_runtime.restore_section("goals", 99999)
    with pytest.raises(UnknownSectionError):
        await demo_runtime.section_history("nope")


async def test_the_wizard_endpoints_over_the_real_runtime(demo_runtime: Runtime) -> None:
    await connected(demo_runtime)
    async with client_for(create_app(demo_runtime)) as client:
        role = await client.get("/api/v1/entities", params={"role": "pv_power"})
        templates = await client.get("/api/v1/templates", params={"kind": "device"})
        prefs = await client.get("/api/v1/wizard/energy-prefs")
        check = await client.post("/api/v1/wizard/check")
        preview = await client.post(
            "/api/v1/preview", json={"grid": {"power": {"entity_id": "sensor.sim_grid_power"}}}
        )
        put = await client.put("/api/v1/config/goals", json=GoalsConfig().model_dump(mode="json"))
        goals_history = await client.get("/api/v1/config/goals/history")
    assert role.json()[0]["entity_id"] == "sensor.sim_pv_power"
    assert {t["id"] for t in templates.json()["templates"]} >= {"generic.switch"}
    assert prefs.json()["configured"] is True
    assert check.json()["items"]
    assert preview.json()["items"][0]["unit"] == "W"
    assert put.status_code == 200
    assert goals_history.json() == []
    _ = cast("dict[str, Any]", {})
