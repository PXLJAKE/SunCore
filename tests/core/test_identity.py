# SPDX-License-Identifier: Apache-2.0
"""Device identity rule (STATUS F6/A2): id + template_id + entities."""

from __future__ import annotations

from suncore.core.devices import DeviceConfig, DeviceKind, OnOffControl, PowerModel, PowerModelKind
from suncore.core.identity import device_identity, is_new_device, new_device_ids
from suncore.core.site import EntityRef


def device(
    device_id: str = "pool",
    template: str = "generic.switch",
    entity: str = "switch.pool",
    **kw: object,
) -> DeviceConfig:
    fields: dict[str, object] = {"name": "Pool", **kw}
    return DeviceConfig(
        id=device_id,
        template_id=template,
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id=entity)},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=1000),
        control=OnOffControl(kind="on_off", role="switch"),
        **fields,  # type: ignore[arg-type]
    )


def test_same_identity_survives_setting_changes() -> None:
    before = device()
    after = device(priority=5, min_run_s=300, name="Pool pump")
    assert device_identity(before) == device_identity(after)
    assert not is_new_device(after, before)


def test_invert_sign_is_a_setting_not_identity() -> None:
    inverted = device().model_copy(
        update={"entities": {"switch": EntityRef(entity_id="switch.pool", invert_sign=True)}}
    )
    assert not is_new_device(inverted, device())


def test_template_or_entity_change_makes_a_new_device() -> None:
    assert is_new_device(device(template="generic.climate"), device())
    assert is_new_device(device(entity="switch.other"), device())
    assert is_new_device(device(), None)


def test_new_device_ids_across_documents() -> None:
    previous = [device("a"), device("b", entity="switch.b")]
    current = [device("a"), device("b", entity="switch.b2"), device("c", entity="switch.c")]
    assert new_device_ids(previous, current) == frozenset({"b", "c"})
    assert new_device_ids(current, current) == frozenset()
