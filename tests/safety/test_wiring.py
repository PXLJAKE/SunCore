# SPDX-License-Identifier: Apache-2.0
"""``safety.wiring``: the write capability goes from the connection to the drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from suncore.core.devices import DeviceConfig, DeviceKind, OnOffControl, PowerModel, PowerModelKind
from suncore.core.site import EntityRef
from suncore.safety.wiring import NoWriteCapabilityError, WriteSource, build_actuators, writer_of


@dataclass
class Result:
    context_id: str | None = "ctx"


class Caller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []

    async def write(
        self, domain: str, service: str, *, service_data: Any = None, target: Any = None
    ) -> Result:
        self.calls.append((domain, service, service_data, target))
        return Result()


class Source:
    def __init__(self) -> None:
        self.caller = Caller()
        self.asked = 0

    def service_caller(self) -> Caller:
        self.asked += 1
        return self.caller


def device(device_id: str) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="generic.switch",
        name=device_id,
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id=f"switch.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=100),
        control=OnOffControl(kind="on_off", role="switch"),
    )


async def test_the_factory_gets_the_connections_writer_for_every_device() -> None:
    source = Source()
    assert isinstance(source, WriteSource)
    made: dict[str, Any] = {}

    def make(d: DeviceConfig, write: Any) -> str:
        made[d.id] = write
        return d.id

    actuators = build_actuators(source, [device("a"), device("b")], make)  # type: ignore[type-var]
    assert actuators == {"a": "a", "b": "b"}
    assert source.asked == 1  # one capability object for all
    await made["a"]("switch", "turn_on", target={"entity_id": "switch.a"})
    assert source.caller.calls == [("switch", "turn_on", None, {"entity_id": "switch.a"})]


async def test_a_connection_without_the_capability_gets_a_failing_writer() -> None:
    class ReadOnly:
        pass

    assert not isinstance(ReadOnly(), WriteSource)
    write = writer_of(ReadOnly())
    with pytest.raises(NoWriteCapabilityError):
        await write("switch", "turn_on", target={"entity_id": "switch.a"})
