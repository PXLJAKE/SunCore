# SPDX-License-Identifier: Apache-2.0
"""Battery adapters (WP10): generic, kostal_plenticore, kostal_modbus - exact service calls."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.actions import READ_ONLY_ACTIONS
from suncore.core.battery import (
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatterySettings,
    FieldRange,
    ModbusByteOrder,
    ModbusLink,
)
from suncore.core.battery_adapter import BatteryAdapterSpec
from suncore.core.decisions import ReleaseAction
from suncore.core.site import EntityRef
from suncore.devices.battery import (
    GenericBatteryDriver,
    KostalModbusDriver,
    KostalPlenticoreDriver,
    create_battery_driver,
)
from suncore.devices.battery.modbus_codec import (
    float32_words,
    uint32_words,
    words_float32,
    words_uint32,
)
from suncore.devices.drivers.base import ActionNotSupportedError, WriteRefusedError
from suncore.templates import BatteryTemplate, load_templates
from tests.devices.drivers.fakes import FakeHA, intent

ROOT = Path(__file__).resolve().parents[3]
CATALOG = load_templates(ROOT / "templates")
MODEL = BatteryModel(capacity_wh=10_000, max_charge_w=5000, max_discharge_w=6000)


def template(template_id: str) -> BatteryAdapterSpec:
    found = CATALOG.get(template_id)
    assert isinstance(found, BatteryTemplate)
    return found.adapter_spec()


def template_model(template_id: str) -> BatteryTemplate:
    found = CATALOG.get(template_id)
    assert isinstance(found, BatteryTemplate)
    return found


def settings(template_id: str, entities: dict[str, str], **kw: Any) -> BatterySettings:
    return BatterySettings(
        adapter_template_id=template_id,
        entities={r: EntityRef(entity_id=e) for r, e in entities.items()},
        model=MODEL,
        **kw,
    )


def battery_intent(**fields: float) -> Any:
    return intent(BatteryAction(**fields), asset="battery")


# --- generic ---------------------------------------------------------------------------------

GENERIC_ENTITIES = {
    "min_soc_number": "number.min_soc",
    "max_charge_number": "number.max_charge",
    "max_discharge_number": "number.max_discharge",
    "grid_charge_number": "number.grid_charge",
}


def generic(ha: FakeHA, entities: dict[str, str] | None = None) -> GenericBatteryDriver:
    ha.set("number.min_soc", "10", min=5, max=100, step=5, unit_of_measurement="%")
    ha.set("number.max_charge", "5000", min=0, max=8000, step=1, unit_of_measurement="W")
    ha.set("number.max_discharge", "6000", min=0, max=6000, unit_of_measurement="W")
    ha.set("number.grid_charge", "0", min=0, max=4000, unit_of_measurement="W")
    driver = create_battery_driver(
        template("generic.battery"),
        settings("generic.battery", entities or GENERIC_ENTITIES),
        write=ha.write,
        states=ha.lookup,
    )
    assert isinstance(driver, GenericBatteryDriver)
    return driver


async def test_generic_capabilities_follow_the_mapped_roles() -> None:
    ha = FakeHA()
    assert generic(ha).capabilities == frozenset(
        {
            BatteryCapability.MIN_SOC,
            BatteryCapability.CHARGE_LIMIT,
            BatteryCapability.DISCHARGE_LIMIT,
            BatteryCapability.GRID_CHARGE,
        }
    )
    only_floor = generic(FakeHA(), {"min_soc_number": "number.min_soc"})
    assert only_floor.capabilities == frozenset({BatteryCapability.MIN_SOC})
    assert only_floor.heartbeat_s is None
    assert only_floor.prepared()
    assert only_floor.refusal() is None


async def test_generic_ranges_intersect_template_and_entity() -> None:
    ranges = generic(FakeHA()).ranges()
    assert ranges.min_soc_pct == FieldRange(5, 100, 5)
    assert ranges.max_charge_w == FieldRange(0, 8000, 1)
    assert ranges.grid_charge_w == FieldRange(0, 4000, None)


async def test_generic_read_back_and_writes_only_changed_numbers() -> None:
    ha = FakeHA()
    driver = generic(ha)
    assert await driver.read_back() == BatteryAction(
        max_charge_w=5000, max_discharge_w=6000, min_soc_pct=10, grid_charge_w=0
    )
    context = await driver.apply(
        battery_intent(max_charge_w=5000, max_discharge_w=0, min_soc_pct=10, grid_charge_w=0)
    )
    assert ha.sent == [
        ("number", "set_value", {"entity_id": "number.max_discharge"}, {"value": 0.0}),
    ]
    assert context == "ctx-1"
    assert driver.applied is not None


async def test_generic_release_restores_the_baseline_numbers() -> None:
    ha = FakeHA()
    driver = generic(ha)
    ha.set("number.max_discharge", "0", min=0, max=6000, unit_of_measurement="W")
    await driver.release(BatteryAction(max_discharge_w=6000, min_soc_pct=10))
    assert ha.sent == [
        ("number", "set_value", {"entity_id": "number.max_discharge"}, {"value": 6000.0}),
    ]
    assert driver.applied is None


async def test_generic_refuses_other_actions_and_missing_capabilities() -> None:
    driver = generic(FakeHA(), {"min_soc_number": "number.min_soc"})
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(ReleaseAction(), asset="battery"))
    with pytest.raises(WriteRefusedError):
        await driver.apply(battery_intent(grid_charge_w=1000))


async def test_generic_unavailable_entity_reads_unknown_and_refuses() -> None:
    ha = FakeHA()
    driver = generic(ha)
    ha.set("number.max_discharge", "unavailable")
    assert await driver.read_back() is None
    assert driver.ranges().max_discharge_w is None
    with pytest.raises(WriteRefusedError):
        await driver.apply(battery_intent(max_discharge_w=0))


def _with_controls(controls: dict[str, Any], roles: dict[str, Any]) -> BatteryAdapterSpec:
    base = template_model("generic.battery").model_dump(mode="json", exclude_none=True)
    base.update({"id": "acme.battery", "name": "template.acme.battery.name"})
    base["roles"] = roles
    base["controls"] = controls
    base["capabilities"] = []
    return BatteryTemplate.model_validate(base).adapter_spec()


async def test_select_based_lock_and_force_charge() -> None:
    role = {"matcher": {"domain": "select"}, "description": "template.acme.battery.role.mode"}
    tpl = _with_controls(
        {
            "mode": {
                "kind": "mode",
                "options": [
                    {"value": "Auto", "meaning": "auto"},
                    {"value": "Hold", "meaning": "no_discharge"},
                    {"value": "Grid", "meaning": "force_charge"},
                    {"value": "Service", "meaning": "other"},
                ],
            }
        },
        {"mode": role},
    )
    ha = FakeHA()
    ha.set("select.mode", "Auto")
    driver = GenericBatteryDriver(
        tpl,
        settings("acme.battery", {"mode": "select.mode"}),
        write=ha.write,
        states=ha.lookup,
    )
    assert driver.capabilities == frozenset(
        {BatteryCapability.DISCHARGE_LIMIT, BatteryCapability.GRID_CHARGE}
    )
    assert driver.ranges().max_discharge_w == FieldRange(0, 0)
    assert driver.ranges().grid_charge_w == FieldRange(5000, 5000)
    assert await driver.read_back() == BatteryAction(max_discharge_w=6000, grid_charge_w=0)
    await driver.apply(battery_intent(max_discharge_w=0))
    assert ha.sent[-1] == (
        "select",
        "select_option",
        {"entity_id": "select.mode"},
        {"option": "Hold"},
    )
    ha.set("select.mode", "Hold")
    assert await driver.read_back() == BatteryAction(max_discharge_w=0, grid_charge_w=0)
    await driver.apply(battery_intent(grid_charge_w=5000))
    assert ha.sent[-1][3] == {"option": "Grid"}
    ha.set("select.mode", "Grid")
    await driver.release(None)
    assert ha.sent[-1][3] == {"option": "Auto"}
    # The user's own option stays when SunCore restricts nothing.
    ha.set("select.mode", "Service")
    calls = len(ha.calls)
    await driver.apply(battery_intent(max_discharge_w=6000))
    assert len(ha.calls) == calls


async def test_switch_based_force_charge() -> None:
    role = {"matcher": {"domain": "switch"}, "description": "template.acme.battery.role.force"}
    tpl = _with_controls({"force": {"kind": "force_charge"}}, {"force": role})
    ha = FakeHA()
    ha.set("switch.force", "off")
    driver = GenericBatteryDriver(
        tpl, settings("acme.battery", {"force": "switch.force"}), write=ha.write, states=ha.lookup
    )
    assert driver.capabilities == frozenset({BatteryCapability.GRID_CHARGE})
    await driver.apply(battery_intent(grid_charge_w=5000))
    assert ha.sent == [("switch", "turn_on", {"entity_id": "switch.force"}, None)]
    ha.set("switch.force", "on")
    assert await driver.read_back() == BatteryAction(grid_charge_w=5000)
    await driver.release(None)
    assert ha.sent[-1] == ("switch", "turn_off", {"entity_id": "switch.force"}, None)


async def test_percent_and_kw_numbers_are_converted() -> None:
    roles = {
        "charge": {"matcher": {"domain": "number"}, "description": "template.acme.battery.role.c"},
        "discharge": {
            "matcher": {"domain": "number"},
            "description": "template.acme.battery.role.d",
        },
    }
    tpl = _with_controls(
        {
            "charge": {"kind": "max_charge", "unit": "kW", "min": 0, "max": 10},
            "discharge": {"kind": "max_discharge", "unit": "%", "min": 0, "max": 100},
        },
        roles,
    )
    ha = FakeHA()
    ha.set("number.charge", "2.5")
    ha.set("number.discharge", "50")
    driver = GenericBatteryDriver(
        tpl,
        settings("acme.battery", {"charge": "number.charge", "discharge": "number.discharge"}),
        write=ha.write,
        states=ha.lookup,
    )
    assert await driver.read_back() == BatteryAction(max_charge_w=2500, max_discharge_w=3000)
    assert driver.ranges().max_discharge_w == FieldRange(0, 6000, None)
    await driver.apply(battery_intent(max_charge_w=1000, max_discharge_w=0))
    assert [c[3] for c in ha.sent] == [{"value": 1.0}, {"value": 0.0}]


# --- kostal_plenticore ------------------------------------------------------------------------


def kostal_rest(ha: FakeHA) -> KostalPlenticoreDriver:
    ha.set("number.scb_battery_min_soc", "5", min=5, max=100, step=5, unit_of_measurement="%")
    ha.set("select.scb_battery_charging_usage_mode", "None")
    driver = create_battery_driver(
        template("kostal.plenticore"),
        settings(
            "kostal.plenticore",
            {
                "min_soc_number": "number.scb_battery_min_soc",
                "charge_mode": "select.scb_battery_charging_usage_mode",
            },
        ),
        write=ha.write,
        states=ha.lookup,
    )
    assert isinstance(driver, KostalPlenticoreDriver)
    return driver


async def test_kostal_rest_is_min_soc_only_with_a_lag_aware_settle() -> None:
    ha = FakeHA()
    driver = kostal_rest(ha)
    assert driver.capabilities == frozenset({BatteryCapability.MIN_SOC})
    assert driver.settle_s >= 35
    assert driver.charge_mode() == "None"
    assert driver.ranges().min_soc_pct == FieldRange(5, 100, 5)
    assert driver.ranges().grid_charge_w is None
    await driver.apply(battery_intent(min_soc_pct=45))
    assert ha.sent == [
        ("number", "set_value", {"entity_id": "number.scb_battery_min_soc"}, {"value": 45.0})
    ]
    # The REST write failed silently: the entity still shows 5 - the read-back says so.
    assert await driver.read_back() == BatteryAction(min_soc_pct=5)
    with pytest.raises(WriteRefusedError):
        await driver.apply(battery_intent(grid_charge_w=1000))  # no charge set-point over REST


def test_kostal_rest_template_must_have_only_a_min_soc_control() -> None:
    base = template_model("kostal.plenticore").model_dump(mode="json", exclude_none=True)
    base["controls"]["min_soc_number"] = {"kind": "max_charge", "unit": "W", "min": 0, "max": 1}
    with pytest.raises(ValueError, match="min_soc"):
        BatteryTemplate.model_validate(base)


# --- kostal_modbus ----------------------------------------------------------------------------

MODBUS_ENTITIES = {
    "external_control": "sensor.k_1080",
    "charge_limit": "sensor.k_1280",
    "discharge_limit": "sensor.k_1282",
    "fallback_charge_limit": "sensor.k_1284",
    "fallback_discharge_limit": "sensor.k_1286",
    "fallback_timeout": "sensor.k_1288",
    "own_charge_limit": "sensor.k_1076",
    "own_discharge_limit": "sensor.k_1078",
}
LINK = ModbusLink(hub="kostal", slave=71, byte_order=ModbusByteOrder.LITTLE)


def modbus(ha: FakeHA, *, external: str = "2", prepared: bool = False) -> KostalModbusDriver:
    ha.set("sensor.k_1080", external)
    ha.set("sensor.k_1280", "0")
    ha.set("sensor.k_1282", "0")
    ha.set("sensor.k_1284", "4800" if prepared else "0")
    ha.set("sensor.k_1286", "5500" if prepared else "0")
    ha.set("sensor.k_1288", "60" if prepared else "0")
    ha.set("sensor.k_1076", "4800")
    ha.set("sensor.k_1078", "5500")
    driver = create_battery_driver(
        template("kostal.plenticore_g3_modbus"),
        settings("kostal.plenticore_g3_modbus", MODBUS_ENTITIES, modbus=LINK),
        write=ha.write,
        states=ha.lookup,
    )
    assert isinstance(driver, KostalModbusDriver)
    return driver


def register_writes(ha: FakeHA) -> list[tuple[int, list[int]]]:
    out = []
    for domain, service, target, data in ha.sent:
        assert (domain, service, target) == ("modbus", "write_register", {})
        assert data is not None
        assert data["hub"] == "kostal"
        assert data["slave"] == 71  # the action would default to 1: always sent
        out.append((data["address"], data["value"]))
    return out


async def test_modbus_refuses_without_external_battery_management() -> None:
    ha = FakeHA()
    driver = modbus(ha, external="0")
    refusal = driver.refusal()
    assert refusal is not None
    assert refusal.key == "safety.battery_external_control_off"
    with pytest.raises(WriteRefusedError):
        await driver.arm_fallback()
    with pytest.raises(WriteRefusedError):
        await driver.apply(battery_intent(max_discharge_w=0))
    await driver.release(None)
    assert ha.calls == []


async def test_modbus_arms_the_fallback_first_and_refuses_limits_until_confirmed() -> None:
    ha = FakeHA()
    driver = modbus(ha)
    assert driver.capabilities == frozenset(
        {
            BatteryCapability.CHARGE_LIMIT,
            BatteryCapability.DISCHARGE_LIMIT,
            BatteryCapability.HARDWARE_FALLBACK,
        }
    )
    assert not driver.prepared()
    with pytest.raises(WriteRefusedError):
        await driver.apply(battery_intent(max_discharge_w=0))
    await driver.arm_fallback()
    order = ModbusByteOrder.LITTLE
    assert register_writes(ha) == [
        (1284, float32_words(4800, order)),
        (1286, float32_words(5500, order)),
        (1288, uint32_words(60, order)),
    ]
    ha.set("sensor.k_1284", "4800")
    ha.set("sensor.k_1286", "5500")
    ha.set("sensor.k_1288", "60")
    assert driver.prepared()


async def test_modbus_limits_heartbeat_and_release() -> None:
    ha = FakeHA()
    driver = modbus(ha, prepared=True)
    assert driver.heartbeat_s == 20
    assert driver.ranges().max_discharge_w == FieldRange(0, 5500)
    await driver.apply(battery_intent(max_charge_w=4800, max_discharge_w=0))
    order = ModbusByteOrder.LITTLE
    lock = [(1280, float32_words(4800, order)), (1282, float32_words(0, order))]
    assert register_writes(ha) == lock
    await driver.keep_alive()
    assert register_writes(ha) == lock + lock
    await driver.release(BatteryAction(max_charge_w=0, max_discharge_w=0))
    own = [(1280, float32_words(4800, order)), (1282, float32_words(5500, order))]
    assert register_writes(ha) == lock + lock + own  # own limits, not the takeover read-back
    await driver.keep_alive()  # the heartbeat stopped with the release
    assert len(ha.calls) == 6


async def test_modbus_read_back_reports_the_limit_registers() -> None:
    ha = FakeHA()
    driver = modbus(ha, prepared=True)
    ha.set("sensor.k_1282", "0")
    ha.set("sensor.k_1280", "4800")
    assert await driver.read_back() == BatteryAction(max_charge_w=4800, max_discharge_w=0)
    ha.set("sensor.k_1282", "unavailable")
    assert await driver.read_back() is None


def test_modbus_needs_its_link_and_roles() -> None:
    ha = FakeHA()
    with pytest.raises(ValueError, match=r"battery\.modbus"):
        create_battery_driver(
            template("kostal.plenticore_g3_modbus"),
            settings("kostal.plenticore_g3_modbus", MODBUS_ENTITIES),
            write=ha.write,
            states=ha.lookup,
        )
    with pytest.raises(ValueError, match="unmapped"):
        create_battery_driver(
            template("kostal.plenticore_g3_modbus"),
            settings("kostal.plenticore_g3_modbus", {"charge_limit": "sensor.x"}, modbus=LINK),
            write=ha.write,
            states=ha.lookup,
        )


@given(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, width=32),
    st.sampled_from(list(ModbusByteOrder)),
)
def test_float32_words_round_trip(value: float, order: ModbusByteOrder) -> None:
    words = float32_words(value, order)
    assert all(0 <= w <= 0xFFFF for w in words)
    assert words_float32(words, order) == value


def test_float32_word_order_follows_register_5() -> None:
    # 1000.0 = 0x447A0000: ABCD (big) high word first, CDAB (little, Kostal default) low first.
    assert float32_words(1000.0, ModbusByteOrder.BIG) == [0x447A, 0x0000]
    assert float32_words(1000.0, ModbusByteOrder.LITTLE) == [0x0000, 0x447A]


@given(st.integers(0, 0xFFFFFFFF), st.sampled_from(list(ModbusByteOrder)))
def test_uint32_words_round_trip(value: int, order: ModbusByteOrder) -> None:
    assert words_uint32(uint32_words(value, order), order) == value


def test_write_register_is_a_write_of_the_modbus_adapter_only() -> None:
    """``modbus.write_register`` is never a read-only action and only the ``kostal_modbus``
    adapter (and the simulator, which is the fake Home Assistant) names it."""
    assert not any(a.startswith("modbus.") for a in READ_ONLY_ACTIONS)
    users = []
    for path in sorted((ROOT / "suncore").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Constant) and n.value == "write_register" for n in ast.walk(tree)):
            users.append(path.relative_to(ROOT).as_posix())
    assert [u for u in users if not u.startswith("suncore/sim/")] == [
        "suncore/devices/battery/kostal_modbus.py"
    ]


async def test_a_release_with_an_unavailable_entity_fails_loudly() -> None:
    """Review 4-G B1 (probe): a REST release to an unavailable number must not report success."""
    ha = FakeHA()
    driver = kostal_rest(ha)
    ha.set("number.scb_battery_min_soc", "unavailable")
    with pytest.raises(WriteRefusedError):
        await driver.release(BatteryAction(min_soc_pct=10))
    assert ha.calls == []
    gone = FakeHA()
    generic_driver = generic(gone)
    gone.set("number.max_discharge", "unavailable")
    with pytest.raises(WriteRefusedError):
        await generic_driver.release(BatteryAction(max_discharge_w=6000))


async def test_release_restores_the_users_own_select_option_and_switch_state() -> None:
    role = {"matcher": {"domain": "select"}, "description": "template.acme.battery.role.mode"}
    tpl = _with_controls(
        {
            "mode": {
                "kind": "mode",
                "options": [
                    {"value": "Auto", "meaning": "auto"},
                    {"value": "Hold", "meaning": "no_discharge"},
                    {"value": "Eco", "meaning": "other"},
                ],
            }
        },
        {"mode": role},
    )
    ha = FakeHA()
    ha.set("select.mode", "Eco")
    driver = GenericBatteryDriver(
        tpl, settings("acme.battery", {"mode": "select.mode"}), write=ha.write, states=ha.lookup
    )
    await driver.apply(battery_intent(max_discharge_w=0))
    ha.set("select.mode", "Hold")
    await driver.release(None)
    assert ha.sent[-1][3] == {"option": "Eco"}

    switch_role = {"matcher": {"domain": "switch"}, "description": "template.acme.battery.role.f"}
    tpl2 = _with_controls({"force": {"kind": "force_charge"}}, {"force": switch_role})
    ha2 = FakeHA()
    ha2.set("switch.force", "on")  # the user force-charges on their own
    driver2 = GenericBatteryDriver(
        tpl2,
        settings("acme.battery", {"force": "switch.force"}),
        write=ha2.write,
        states=ha2.lookup,
    )
    await driver2.apply(battery_intent(grid_charge_w=5000))
    ha2.set("switch.force", "off")
    await driver2.release(None)
    assert ha2.sent[-1] == ("switch", "turn_on", {"entity_id": "switch.force"}, None)
