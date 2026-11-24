# SPDX-License-Identifier: Apache-2.0
"""PR 6-J: the evcc driver over a fake MQTT bus (topics, payloads, read-back, echo release)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from suncore.core.decisions import ChargeAction, LoadpointAction, ReleaseAction
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    LoadpointControl,
    PowerModel,
    PowerModelKind,
)
from suncore.core.signals import Quality
from suncore.devices.drivers import create_driver
from suncore.devices.drivers.base import (
    ActionNotSupportedError,
    DriverError,
    WriteRefusedError,
)
from suncore.devices.evcc import EvccDriver, loadpoint_topic
from tests.devices.drivers.fakes import FakeHA, intent
from tests.safety.helpers import T0

PREFIX = "evcc"
CONTROL = LoadpointControl(kind="loadpoint", loadpoint_id=2, topic_prefix=PREFIX)


def device() -> DeviceConfig:
    return DeviceConfig(
        id="lp",
        template_id="evcc.loadpoint",
        name="evcc",
        kind=DeviceKind.WALLBOX,
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040),
        control=CONTROL,
    )


class FakeBus:
    """The three methods of ``EvccBus``; ``echo`` makes evcc show what was written."""

    def __init__(self, *, alive: bool = True, echo: bool = True) -> None:
        self.values: dict[str, str] = {}
        self.sent: list[tuple[str, str]] = []
        self.up = alive
        self.echo = echo
        self.accept = True

    def set(self, key: str, value: str) -> None:
        self.values[loadpoint_topic(CONTROL, key)] = value

    def value(self, topic: str) -> str | None:
        return self.values.get(topic)

    def alive(self, prefix: str) -> bool:
        return self.up and prefix == PREFIX

    async def publish_set(self, topic: str, payload: str) -> bool:
        if not self.accept:
            return False
        self.sent.append((topic, payload))
        if self.echo:
            assert topic.endswith("/set")
            self.values[topic.removesuffix("/set")] = payload
        return True


def idle_bus(**kwargs: bool) -> FakeBus:
    bus = FakeBus(**kwargs)
    for key, value in {
        "mode": "off",
        "maxCurrent": "10",
        "minCurrent": "6",
        "limitSoc": "80",
        "connected": "true",
        "charging": "false",
        "chargePower": "0",
        "vehicleSoc": "42.5",
        "phasesActive": "3",
    }.items():
        bus.set(key, value)
    return bus


async def sleep_none(_: float) -> None:
    return None


def driver(bus: FakeBus | None) -> EvccDriver:
    return EvccDriver(device(), bus=bus, sleep=sleep_none)


async def test_read_back_and_state_come_from_the_retained_topics() -> None:
    d = driver(idle_bus())
    assert await d.read_back() == LoadpointAction(mode="off", max_current_a=10.0, limit_soc_pct=80)
    state = d.device_state()
    assert state.quality is Quality.OK
    assert (state.mode, state.connected, state.phases, state.soc_pct) == ("off", True, 3, 42.5)
    assert state.max_current_a == 10.0
    assert state.limit_soc_pct == 80
    assert d.min_current_a() == 6.0


async def test_a_vehicle_soc_of_zero_is_not_a_reading() -> None:
    bus = idle_bus()
    bus.set("connected", "false")
    bus.set("vehicleSoc", "0")
    state = driver(bus).device_state()
    assert state.soc_pct is None
    assert state.connected is False


@pytest.mark.parametrize("bus", [None, FakeBus(alive=False)])
async def test_a_silent_or_missing_evcc_is_unavailable(bus: FakeBus | None) -> None:
    d = driver(bus)
    assert await d.read_back() is None
    assert d.device_state().quality is Quality.UNAVAILABLE
    assert d.min_current_a() is None
    with pytest.raises(WriteRefusedError):
        await d.apply(intent(LoadpointAction(mode="smart", max_current_a=10.0), "lp"))


async def test_an_unknown_mode_is_unavailable() -> None:
    bus = idle_bus()
    bus.set("mode", "pv")  # deprecated alias: SunCore does not interpret it
    d = driver(bus)
    assert await d.read_back() is None
    assert d.device_state().quality is Quality.UNAVAILABLE


async def test_apply_writes_only_what_differs_to_the_set_topics() -> None:
    bus = idle_bus()
    d = driver(bus)
    await d.apply(intent(LoadpointAction(mode="smart", max_current_a=16.0, limit_soc_pct=80), "lp"))
    assert bus.sent == [
        ("evcc/loadpoints/2/maxCurrent/set", "16"),
        ("evcc/loadpoints/2/mode/set", "smart"),
    ]


async def test_off_is_written_first_so_the_car_stops_before_the_rest() -> None:
    bus = idle_bus()
    bus.set("mode", "now")
    await driver(bus).apply(intent(LoadpointAction(mode="off", max_current_a=8.0), "lp"))
    assert [t.split("/")[-2] for t, _ in bus.sent] == ["mode", "maxCurrent"]


async def test_nothing_is_written_when_evcc_already_shows_the_values() -> None:
    bus = idle_bus()
    await driver(bus).apply(intent(LoadpointAction(mode="off", max_current_a=10.0), "lp"))
    assert bus.sent == []


async def test_a_limit_soc_is_an_integer_and_a_missing_one_is_not_written() -> None:
    bus = idle_bus()
    await driver(bus).apply(intent(LoadpointAction(mode="off", limit_soc_pct=90.4), "lp"))
    assert bus.sent == [("evcc/loadpoints/2/limitSoc/set", "90")]
    bus = idle_bus()
    del bus.values[loadpoint_topic(CONTROL, "limitSoc")]
    with pytest.raises(WriteRefusedError):
        await driver(bus).apply(intent(LoadpointAction(mode="off", limit_soc_pct=90), "lp"))


async def test_max_current_below_evccs_minimum_is_refused() -> None:
    bus = idle_bus()
    with pytest.raises(WriteRefusedError):
        await driver(bus).apply(intent(LoadpointAction(mode="smart", max_current_a=5.0), "lp"))
    assert bus.sent == []


async def test_a_plan_is_refused() -> None:
    bus = idle_bus()
    action = LoadpointAction(
        mode="smart", plan_soc_pct=80.0, plan_time=T0 + timedelta(hours=6), max_current_a=10.0
    )
    with pytest.raises(WriteRefusedError):
        await driver(bus).apply(intent(action, "lp"))
    assert bus.sent == []


async def test_only_loadpoint_actions_are_applied() -> None:
    with pytest.raises(ActionNotSupportedError):
        await driver(idle_bus()).apply(intent(ChargeAction(current_a=10), "lp"))


async def test_a_refused_publish_is_a_driver_error() -> None:
    bus = idle_bus()
    bus.accept = False
    with pytest.raises(DriverError):
        await driver(bus).apply(intent(LoadpointAction(mode="smart", max_current_a=12.0), "lp"))


async def test_never_a_site_or_a_foreign_topic() -> None:
    bus = idle_bus()
    await driver(bus).apply(
        intent(LoadpointAction(mode="now", max_current_a=16.0, limit_soc_pct=100), "lp")
    )
    assert bus.sent
    for topic, _ in bus.sent:
        assert topic.startswith("evcc/loadpoints/2/")
        assert topic.endswith("/set")
        assert "/site/" not in topic


async def test_release_restores_the_baseline_and_waits_for_the_echo() -> None:
    bus = idle_bus()
    bus.set("mode", "now")
    bus.set("maxCurrent", "16")
    baseline = LoadpointAction(mode="off", max_current_a=10.0, limit_soc_pct=80)
    await driver(bus).release(baseline)
    assert {t.split("/")[-2] for t, _ in bus.sent} == {"mode", "maxCurrent"}
    assert bus.values[loadpoint_topic(CONTROL, "mode")] == "off"


async def test_release_without_a_baseline_writes_nothing() -> None:
    bus = idle_bus()
    await driver(bus).release(None)
    assert bus.sent == []


async def test_release_fails_while_evcc_is_silent() -> None:
    bus = idle_bus(alive=False)
    with pytest.raises(WriteRefusedError):
        await driver(bus).release(LoadpointAction(mode="off", max_current_a=10.0))
    assert bus.sent == []


async def test_release_fails_when_evcc_never_shows_the_baseline() -> None:
    bus = idle_bus(echo=False)
    bus.set("mode", "now")
    with pytest.raises(DriverError):
        await driver(bus).release(LoadpointAction(mode="off", max_current_a=10.0))


async def test_release_through_the_gate_action_is_not_an_apply() -> None:
    with pytest.raises(ActionNotSupportedError):
        await driver(idle_bus()).apply(intent(ReleaseAction(), "lp"))


def test_the_factory_builds_the_evcc_driver_with_the_bus() -> None:
    ha = FakeHA()
    bus = idle_bus()
    d = create_driver(device(), write=ha.write, states=ha.lookup, evcc=bus)
    assert isinstance(d, EvccDriver)
    assert ha.calls == []


async def test_a1_release_works_without_a_limit_soc_topic() -> None:
    """Emergency stop: evcc shows no limitSoc and is on now/16 A; the mode must be restored."""
    bus = idle_bus()
    del bus.values[loadpoint_topic(CONTROL, "limitSoc")]
    bus.set("mode", "now")
    bus.set("maxCurrent", "16")
    baseline = LoadpointAction(mode="off", max_current_a=10.0, limit_soc_pct=90)
    await driver(bus).release(baseline)
    assert bus.values[loadpoint_topic(CONTROL, "mode")] == "off"
    assert bus.values[loadpoint_topic(CONTROL, "maxCurrent")] == "10"
    assert all("limitSoc" not in t for t, _ in bus.sent)


async def test_a1_release_clamps_the_current_up_to_evccs_minimum() -> None:
    bus = idle_bus()
    bus.set("minCurrent", "8")
    bus.set("mode", "now")
    bus.set("maxCurrent", "16")
    await driver(bus).release(LoadpointAction(mode="off", max_current_a=6.0))
    assert bus.values[loadpoint_topic(CONTROL, "maxCurrent")] == "8"
    assert bus.values[loadpoint_topic(CONTROL, "mode")] == "off"


async def test_a1_release_writes_limit_soc_only_when_evcc_shows_it() -> None:
    bus = idle_bus()
    bus.set("limitSoc", "100")
    await driver(bus).release(LoadpointAction(mode="off", max_current_a=10.0, limit_soc_pct=90))
    assert bus.values[loadpoint_topic(CONTROL, "limitSoc")] == "90"
