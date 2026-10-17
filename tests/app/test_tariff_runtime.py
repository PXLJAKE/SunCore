# SPDX-License-Identifier: Apache-2.0
"""The price service in the runtime: demo wiring, refresh on events, API, rebuild on change."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from suncore.api import create_app
from suncore.app.demo import DemoClock
from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.core.config import ExportTariff, ImportTariff, TariffConfig
from suncore.core.site import EntityRef
from suncore.ha.connection import ConnectionState, EntityState, EntityUpdate
from suncore.sim.demo_devices import PRICE_ID, sim_price
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import load_scenario
from suncore.tariff import TariffService
from tests.api.conftest import client_for
from tests.app.conftest import demo_settings

SPEED = 600.0
START_HOUR = 14
SIM_ZONE = timezone(timedelta(hours=2))  # the scenario's offset


class Subscribed(FakeHAConnection):
    def __init__(self, scenario_name: str, clock: DemoClock) -> None:
        super().__init__(
            load_scenario(scenario_name),
            clock=clock,
            speed=SPEED,
            sim_clock=clock,
            start_offset_s=START_HOUR * 3600,
        )
        self.subscriptions: list[frozenset[str]] = []

    async def subscribe_entities(self, entity_ids: object) -> None:
        self.subscriptions.append(frozenset(entity_ids))  # type: ignore[call-overload]
        await super().subscribe_entities(entity_ids)  # type: ignore[arg-type]


async def until(predicate: Callable[[], bool], timeout_s: float = 10.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def start_runtime(tmp_path: Path, scenario_name: str) -> Runtime:
    scenario = load_scenario(scenario_name)
    clock = DemoClock(
        scenario.start.replace(hour=START_HOUR), SPEED
    )  # tomorrow's prices are published
    connection = Subscribed(scenario_name, clock)
    runtime = await build_runtime(
        demo_settings(tmp_path, demo_speed=SPEED), env={}, connection=connection, clock=clock
    )
    runtime.control_sleep = clock.sleep  # the price loop polls in simulated time, like the demo
    await runtime.start()
    return runtime


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    started = await start_runtime(tmp_path, "summer_day")
    try:
        yield started
    finally:
        await started.stop()


def service_of(runtime: Runtime) -> TariffService:
    assert runtime.tariff is not None
    return runtime.tariff


async def fetched(runtime: Runtime) -> TariffService:
    service = service_of(runtime)
    await until(lambda: service.report(24).freshness.fetched_at is not None)
    return service


async def test_demo_tariff_goes_through_the_tibber_template_and_the_fake_action(
    runtime: Runtime,
) -> None:
    service = await fetched(runtime)
    report = service.report(24)
    assert report.source.import_source_id == "tibber.price"
    assert report.source.import_kind == "dynamic"
    assert report.source.native_step_min == 15
    assert report.source.price_includes == "all_in"
    assert not report.source.formula_applied
    assert report.freshness.status == "fresh"
    assert report.quality.known_slots == report.quality.slots
    assert report.quality.has_tomorrow
    assert report.quality.slots > 24 * 4
    slot = report.slots[0]
    assert slot.import_price == pytest.approx(sim_price(slot.start.astimezone(SIM_ZONE)))
    assert slot.export_price == 0.08  # the demo's fixed feed-in remuneration


async def test_the_prices_reach_timeseries_db(runtime: Runtime) -> None:
    await fetched(runtime)
    assert runtime.timeseries is not None
    from suncore.core.series import TimeRange  # noqa: PLC0415

    now = runtime.clock()
    stored = await runtime.timeseries.read_prices(
        "tibber.price", TimeRange(start=now, end=now + timedelta(hours=24))
    )
    assert stored
    assert stored[0].step == timedelta(minutes=15)
    assert stored[0].export_price is not None


async def test_negative_prices_scenario_zeroes_export_in_the_slots_below_zero(
    tmp_path: Path,
) -> None:
    runtime = await start_runtime(tmp_path, "negative_prices")
    try:
        service = await fetched(runtime)
        report = service.report(24)
        negative = [s for s in report.slots if s.import_price is not None and s.import_price < 0]
        assert negative, "the scenario has a negative midday"
        assert all(s.export_price == 0.0 for s in negative)
        others = [s for s in report.slots if s.import_price is not None and s.import_price >= 0]
        assert all(s.export_price == 0.08 for s in others)
        assert report.source.negative_price_rule == "no_remuneration"  # region template `de`
        assert report.source.export_zeroed_slots == len(negative)
    finally:
        await runtime.stop()


async def test_the_api_serves_the_report(runtime: Runtime) -> None:
    await fetched(runtime)
    async with client_for(create_app(runtime)) as client:
        r = await client.get("/api/v1/prices", params={"hours": 12})
    assert r.status_code == 200
    body = r.json()
    assert body["freshness"]["status"] == "fresh"
    assert 12 * 4 <= len(body["slots"]) <= 24 * 4 + 1  # at least 24 h are kept ... capped at 12 h
    assert len(body["slots"]) <= 12 * 4 + 1


async def test_the_price_entity_is_subscribed_and_a_change_marks_the_service(
    runtime: Runtime,
) -> None:
    service = await fetched(runtime)
    conn = runtime.connection
    assert isinstance(conn, Subscribed)
    assert any(PRICE_ID in ids for ids in conn.subscriptions)
    assert not service._state.dirty
    runtime._on_entities(
        EntityUpdate(changed={PRICE_ID: EntityState(PRICE_ID, "0.3", {})}, full=False)
    )
    assert service._state.dirty
    service._state.dirty = False
    runtime._on_entities(
        EntityUpdate(changed={"sensor.other": EntityState("sensor.other", "1", {})})
    )
    assert not service._state.dirty


async def test_reconnecting_clears_the_back_off(runtime: Runtime) -> None:
    service = await fetched(runtime)
    service._state.failures = 3
    runtime._on_state(ConnectionState.CONNECTED)
    assert service._state.failures == 0


async def test_putting_a_fixed_tariff_rebuilds_the_service_and_serves_static_prices(
    runtime: Runtime,
) -> None:
    old = service_of(runtime)
    await runtime.put_section(
        "tariff",
        TariffConfig(
            import_=ImportTariff(kind="fixed", fixed_price=0.33),
            export=ExportTariff(kind="none"),
        ),
    )
    new = service_of(runtime)
    assert new is not old
    await until(lambda: new.report(24).freshness.fetched_at is not None)
    report = new.report(24)
    assert report.freshness.status == "static"
    assert {s.import_price for s in report.slots} == {0.33}
    assert {s.export_price for s in report.slots} == {0.0}
    assert (await runtime.prices(24)).source.import_source_id == "fixed"


async def test_putting_a_price_entity_only_tariff_uses_the_current_slot(runtime: Runtime) -> None:
    await runtime.put_section(
        "tariff",
        TariffConfig(
            import_=ImportTariff(kind="dynamic", price_entity=EntityRef(entity_id=PRICE_ID)),
            export=ExportTariff(kind="fixed", fixed_price=0.08),
        ),
    )
    service = service_of(runtime)
    await until(lambda: service.report(24).freshness.fetched_at is not None)
    assert service.report(24).source.import_source_id == "entity"


async def test_an_unusable_template_leaves_the_service_off_and_the_api_says_so(
    runtime: Runtime,
) -> None:
    await runtime.put_section(
        "tariff",
        TariffConfig(
            import_=ImportTariff(kind="dynamic", template_id="no.such_template"),
            export=ExportTariff(kind="none"),
        ),
    )
    assert runtime.tariff is None
    async with client_for(create_app(runtime)) as client:
        r = await client.get("/api/v1/prices")
    assert r.status_code == 404


async def test_a_tiered_export_without_the_plant_size_leaves_the_service_off(
    runtime: Runtime,
) -> None:
    from suncore.core.config import ExportTier  # noqa: PLC0415

    await runtime.put_section(
        "tariff",
        TariffConfig(
            import_=ImportTariff(kind="fixed", fixed_price=0.3),
            export=ExportTariff(kind="tiered", tiers=(ExportTier(price=0.08),)),
        ),
    )
    assert runtime.tariff is None  # the demo site has no kWp: no rate is guessed


async def test_stop_cancels_the_price_loop(tmp_path: Path) -> None:
    runtime = await start_runtime(tmp_path, "summer_day")
    task = runtime._tariff_task
    assert task is not None
    await runtime.stop()
    assert task.done()
    assert runtime._tariff_task is None


async def test_the_system_check_warns_about_spot_prices_without_a_formula(runtime: Runtime) -> None:
    keys = [i.reason.key for i in await runtime.system_check(None)]
    assert "wizard.check.tariff_spot_without_formula" not in keys  # Tibber is all-in
    await runtime.put_section(
        "tariff",
        TariffConfig(
            import_=ImportTariff(
                kind="dynamic", template_id="tibber.price", price_includes="spot_only"
            ),
            export=ExportTariff(kind="none"),
        ),
    )
    items = await runtime.system_check(None)
    (warning,) = [i for i in items if i.reason.key == "wizard.check.tariff_spot_without_formula"]
    assert warning.severity == "warn"
