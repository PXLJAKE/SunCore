# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from suncore.ha.mqtt import StateItem, StatePublisher, Topics


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, int, bool]] = []
        self.ok = True

    async def __call__(self, topic: str, payload: str, qos: int, retain: bool) -> bool:
        if self.ok:
            self.sent.append((topic, payload, qos, retain))
        return self.ok


def _power(value: float) -> StateItem:
    return StateItem("heater_power", f"{value:.0f}", value, deadband_abs=10.0, deadband_rel=0.01)


def _publisher(sink: Sink, clock: list[float]) -> StatePublisher:
    return StatePublisher(Topics("abcd1234"), sink, clock=lambda: clock[0])


async def test_first_value_is_published_retained_qos0() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    assert await pub.sync([_power(500)]) == 1
    assert sink.sent == [("suncore/abcd1234/heater_power/state", "500", 0, True)]


async def test_unchanged_value_is_not_published_again() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    await pub.sync([_power(500)])
    clock[0] = 60.0
    assert await pub.sync([_power(500)]) == 0


async def test_deadband_absolute_and_relative() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    await pub.sync([_power(5000)])
    clock[0] = 100.0
    # 1 % of 5000 W = 50 W is larger than 10 W: 45 W is inside, 55 W is outside.
    assert await pub.sync([_power(5045)]) == 0
    assert await pub.sync([_power(5055)]) == 1
    clock[0] = 200.0
    await pub.sync([_power(100)])
    clock[0] = 300.0
    assert await pub.sync([_power(105)]) == 0  # below the absolute 10 W
    assert await pub.sync([_power(112)]) == 1


async def test_deadband_is_measured_against_the_last_published_value() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    await pub.sync([_power(1000)])
    for step, value in enumerate((1004, 1008, 1012), start=1):
        clock[0] = 10.0 * step
        sent = await pub.sync([_power(value)])
        assert sent == (1 if value == 1012 else 0)


async def test_rate_limit_holds_back_and_flags_pending() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    await pub.sync([_power(1000)])
    clock[0] = 2.0
    assert await pub.sync([_power(2000)]) == 0
    assert pub.pending is True
    clock[0] = 5.1
    assert await pub.sync([_power(2000)]) == 1
    assert pub.pending is False


async def test_text_states_publish_on_any_change() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    await pub.sync([StateItem("mode", "Shadow")])
    clock[0] = 10.0
    assert await pub.sync([StateItem("mode", "Shadow")]) == 0
    assert await pub.sync([StateItem("mode", "Automatic")]) == 1


async def test_reset_republishes_everything() -> None:
    sink, clock = Sink(), [0.0]
    pub = _publisher(sink, clock)
    await pub.sync([StateItem("mode", "Shadow")])
    pub.reset()
    assert await pub.sync([StateItem("mode", "Shadow")]) == 1


async def test_failed_delivery_is_retried() -> None:
    sink, clock = Sink(), [0.0]
    sink.ok = False
    pub = _publisher(sink, clock)
    assert await pub.sync([StateItem("mode", "Shadow")]) == 0
    assert pub.pending is True
    sink.ok = True
    assert await pub.sync([StateItem("mode", "Shadow")]) == 1
