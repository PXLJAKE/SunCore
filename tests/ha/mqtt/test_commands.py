# SPDX-License-Identifier: Apache-2.0
"""The command handler: parsing, rate limits, the mode-raise rule, one service call each."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from structlog.testing import capture_logs

from suncore.core.controlstate import OverrideCommand
from suncore.core.decisions import OperatingMode
from suncore.ha.mqtt.catalogue import ALL_ENTITIES
from suncore.ha.mqtt.commands import (
    DEFAULT_BOOST_MINUTES,
    ENTITY_BURST,
    GLOBAL_BURST,
    MAX_PAYLOAD_BYTES,
    CommandHandler,
    TokenBucket,
)
from suncore.ha.mqtt.discovery import Topics
from suncore.ha.mqtt.refusals import (
    OVERFLOW_ENTITY,
    SUMMARY_INTERVAL_S,
    RefusalCount,
    RefusalLogThrottle,
)

DEVICE = "heater"


class FakePort:
    """Records every call; nothing else exists on it."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.global_mode = OperatingMode.SHADOW
        self.device_modes: dict[str, OperatingMode] = {DEVICE: OperatingMode.SHADOW}
        self.fail = False
        self.settled = False
        self.block: asyncio.Event | None = None
        self.stop_block: asyncio.Event | None = None
        """Holds the stop *after* it is recorded (the gate sets its flag first, then releases)."""

    def stored_global_mode(self) -> OperatingMode:
        return self.global_mode

    def stored_device_mode(self, device_id: str) -> OperatingMode | None:
        return self.device_modes.get(device_id)

    def stored_emergency_settled(self) -> bool:
        return self.settled

    async def _wait(self) -> None:
        if self.block is not None:
            await self.block.wait()

    def _record(self, *call: object) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(call)

    async def command_emergency_stop(self) -> None:
        self._record("stop")
        if self.stop_block is not None:
            await self.stop_block.wait()

    async def command_clear_stop(self) -> None:
        self._record("clear")

    async def command_global_mode(self, mode: OperatingMode) -> None:
        await self._wait()
        self._record("global_mode", mode)

    async def command_device_mode(self, device_id: str, mode: OperatingMode) -> None:
        self._record("device_mode", device_id, mode)

    async def command_override(self, device_id: str, command: OverrideCommand) -> None:
        self._record("override", device_id, command)

    async def command_replan(self) -> None:
        await self._wait()
        self._record("replan")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(*, allow_raise: bool = False) -> tuple[CommandHandler, FakePort, Clock, dict[str, str]]:
    port = FakePort()
    clock = Clock()
    handler = CommandHandler(port, allow_mode_raise=allow_raise, clock=clock, timeout_s=0.1)
    ids: dict[str, str] = {}
    entities = []
    for spec in ALL_ENTITIES:
        if not spec.command:
            continue
        if spec.scope.value == "device":
            if spec.kinds is not None and "switch" not in {k.value for k in spec.kinds}:
                continue
            object_id = spec.object_id(DEVICE)
        else:
            object_id = spec.object_id()
        ids[spec.key] = object_id
        entities.append((spec, object_id))
    handler.bind(entities)
    return handler, port, clock, ids


async def send(handler: CommandHandler, object_id: str, payload: str | bytes, retain: bool = False):  # type: ignore[no-untyped-def]
    data = payload.encode() if isinstance(payload, str) else payload
    return await handler.handle(object_id, data, retain=retain)


def test_topic_helpers() -> None:
    topics = Topics("0a1b2c3d")
    assert topics.command_filter == "suncore/0a1b2c3d/+/set"
    assert topics.object_id_of_command("suncore/0a1b2c3d/replan/set") == "replan"
    assert topics.object_id_of_command("suncore/0a1b2c3d/replan/state") is None
    assert topics.object_id_of_command("suncore/0a1b2c3d/a/b/set") is None
    assert topics.object_id_of_command("suncore/other/replan/set") is None
    assert topics.object_id_of_command("suncore/0a1b2c3d//set") is None


async def test_each_command_is_exactly_one_port_call() -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    cases = [
        ("emergency_stop_button", "PRESS", [("stop",)]),
        ("emergency_stop_clear", "PRESS", [("clear",)]),
        ("replan", "PRESS", [("replan",)]),
        ("auto", "ON", [("global_mode", OperatingMode.AUTOMATIC)]),
        ("device_auto", "ON", [("device_mode", DEVICE, OperatingMode.AUTOMATIC)]),
        (
            "device_boost",
            "PRESS",
            [("override", DEVICE, OverrideCommand(action="boost", minutes=DEFAULT_BOOST_MINUTES))],
        ),
        ("device_pause", "PRESS", [("override", DEVICE, OverrideCommand(action="pause"))]),
        ("device_resume", "PRESS", [("override", DEVICE, OverrideCommand(action="resume"))]),
    ]
    for key, payload, expected in cases:
        port.calls.clear()
        result = await send(handler, ids[key], payload)
        assert result.accepted, key
        assert result.reason.key == "mqtt.command.accepted"
        assert port.calls == expected, key


async def test_boost_minutes_is_a_parameter_not_a_call() -> None:
    handler, port, _clock, ids = make()
    result = await send(handler, ids["device_boost_minutes"], "15")
    assert result.accepted
    assert port.calls == []
    assert handler.local_values()[ids["device_boost_minutes"]] == 15
    await send(handler, ids["device_boost"], "PRESS")
    assert port.calls == [("override", DEVICE, OverrideCommand(action="boost", minutes=15))]


async def test_default_boost_minutes_is_published() -> None:
    handler, _port, _clock, ids = make()
    assert handler.local_values() == {ids["device_boost_minutes"]: DEFAULT_BOOST_MINUTES}


async def test_raising_a_mode_is_refused_by_default() -> None:
    handler, port, _clock, ids = make()
    for key in ("auto", "device_auto"):
        result = await send(handler, ids[key], "ON")
        assert result.refused
        assert result.reason.key == "safety.mqtt_mode_raise_disabled"
    assert port.calls == []


async def test_clearing_the_emergency_stop_is_refused_by_default() -> None:
    handler, port, _clock, ids = make()
    result = await send(handler, ids["emergency_stop_clear"], "PRESS")
    assert result.refused
    assert result.reason.key == "safety.mqtt_mode_raise_disabled"
    assert port.calls == []


async def test_lowering_always_works_and_goes_to_shadow() -> None:
    handler, port, _clock, ids = make()  # opt-in off
    port.global_mode = OperatingMode.AUTOMATIC
    port.device_modes[DEVICE] = OperatingMode.ASSISTED
    assert (await send(handler, ids["auto"], "OFF")).accepted
    assert (await send(handler, ids["device_auto"], "OFF")).accepted
    assert port.calls == [
        ("global_mode", OperatingMode.SHADOW),
        ("device_mode", DEVICE, OperatingMode.SHADOW),
    ]


async def test_a_switch_already_in_the_state_changes_nothing() -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    port.global_mode = OperatingMode.AUTOMATIC
    result = await send(handler, ids["auto"], "ON")
    assert result.accepted
    assert result.reason.key == "mqtt.command.unchanged"
    assert port.calls == []


async def test_emergency_stop_and_resume_work_with_the_opt_in_off() -> None:
    handler, port, _clock, ids = make()
    assert (await send(handler, ids["emergency_stop_button"], "PRESS")).accepted
    assert (await send(handler, ids["device_resume"], "PRESS")).accepted
    assert (await send(handler, ids["device_pause"], "PRESS")).accepted
    assert len(port.calls) == 3


async def test_retained_commands_are_ignored() -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    result = await send(handler, ids["emergency_stop_button"], "PRESS", retain=True)
    assert result.refused
    assert result.reason.key == "mqtt.command.retained_ignored"
    assert port.calls == []


@pytest.mark.parametrize(
    ("key", "payload"),
    [
        ("auto", "on"),
        ("auto", "1"),
        ("auto", ""),
        ("auto", "ON; DROP"),
        ("replan", "press"),
        ("replan", "ON"),
        ("device_boost_minutes", "abc"),
        ("device_boost_minutes", "nan"),
        ("device_boost_minutes", "inf"),
    ],
)
async def test_invalid_payloads_are_refused(key: str, payload: str) -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    result = await send(handler, ids[key], payload)
    assert result.refused
    assert result.reason.key == "mqtt.command.invalid_payload"
    assert port.calls == []


async def test_binary_and_oversized_payloads_are_refused() -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    assert (await send(handler, ids["replan"], b"\xff\xfe")).refused
    assert (await send(handler, ids["replan"], b"PRESS" + b" " * MAX_PAYLOAD_BYTES)).refused
    assert port.calls == []


@pytest.mark.parametrize("payload", ["0", "1441", "-5", "2.5"])
async def test_numbers_outside_the_range_are_refused(payload: str) -> None:
    handler, _port, _clock, ids = make()
    result = await send(handler, ids["device_boost_minutes"], payload)
    assert result.refused
    assert result.reason.key == "mqtt.command.out_of_range"
    assert handler.local_values()[ids["device_boost_minutes"]] == DEFAULT_BOOST_MINUTES


async def test_unknown_object_ids_are_refused() -> None:
    handler, port, _clock, _ids = make(allow_raise=True)
    for object_id in ("nope", "status", "ghost_pause", "mode"):
        result = await send(handler, object_id, "PRESS")
        assert result.reason.key == "mqtt.command.unknown_entity"
    assert port.calls == []


async def test_an_unknown_device_is_refused() -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    del port.device_modes[DEVICE]
    result = await send(handler, ids["device_auto"], "ON")
    assert result.refused
    assert result.reason.key == "mqtt.command.unknown_entity"


async def test_a_failing_service_call_is_a_refusal_not_an_exception() -> None:
    handler, port, _clock, ids = make()
    port.fail = True
    result = await send(handler, ids["replan"], "PRESS")
    assert result.refused
    assert result.reason.key == "mqtt.command.failed"
    assert result.reason.args == {"error": "RuntimeError"}


async def test_flood_is_rate_limited_per_entity() -> None:
    handler, port, clock, ids = make()
    results = [await send(handler, ids["replan"], "PRESS") for _ in range(50)]
    assert sum(r.accepted for r in results) == ENTITY_BURST
    assert len(port.calls) == ENTITY_BURST
    assert {r.reason.key for r in results if r.refused} == {"mqtt.command.rate_limited"}
    clock.now += 1.0  # one token per second
    assert (await send(handler, ids["replan"], "PRESS")).accepted
    assert not (await send(handler, ids["replan"], "PRESS")).accepted


async def test_flood_over_many_entities_is_capped_globally() -> None:
    handler, port, _clock, _ids = make()
    pause = next(s for s in ALL_ENTITIES if s.key == "device_pause")
    names = [f"dev{i}" for i in range(GLOBAL_BURST * 2)]
    handler.bind([(pause, pause.object_id(n)) for n in names])
    results = [await send(handler, pause.object_id(n), "PRESS") for n in names]
    assert len(port.calls) == GLOBAL_BURST
    assert sum(r.accepted for r in results) == GLOBAL_BURST


async def test_emergency_stop_is_never_rate_limited() -> None:
    handler, port, _clock, ids = make()
    for _ in range(GLOBAL_BURST + 30):  # exhaust everything else first
        await send(handler, ids["replan"], "PRESS")
    results = [await send(handler, ids["emergency_stop_button"], "PRESS") for _ in range(30)]
    assert all(r.accepted for r in results)
    assert sum(1 for c in port.calls if c == ("stop",)) == 30


def test_token_bucket_refills_up_to_the_burst() -> None:
    clock = Clock()
    bucket = TokenBucket(1.0, 3, clock)
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]
    clock.now += 100.0
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]


def test_bind_replaces_the_command_set() -> None:
    handler, _port, _clock, ids = make()
    assert ids["replan"] in handler._targets
    handler.bind([])
    assert handler.local_values() == {}


async def test_withdrawing_the_opt_in_takes_effect_at_once() -> None:
    handler, port, _clock, ids = make(allow_raise=True)
    handler.revoke_raise()
    for key in ("auto", "device_auto"):
        result = await send(handler, ids[key], "ON")
        assert result.reason.key == "safety.mqtt_mode_raise_disabled"
    assert (await send(handler, ids["emergency_stop_clear"], "PRESS")).refused
    assert port.calls == []


async def test_a_blocked_service_times_out_but_the_emergency_stop_does_not_wait() -> None:
    handler, port, _clock, ids = make()
    port.block = asyncio.Event()  # e.g. the operation lock is held by a devices put
    slow = await send(handler, ids["replan"], "PRESS")
    assert slow.refused
    assert slow.reason.key == "mqtt.command.failed"
    assert slow.reason.args == {"error": "timeout"}
    stop = await asyncio.wait_for(send(handler, ids["emergency_stop_button"], "PRESS"), 1.0)
    assert stop.accepted
    assert port.calls == [("stop",)]


async def test_a_settled_emergency_stop_is_acknowledged_without_a_call() -> None:
    handler, port, _clock, ids = make()
    port.settled = True
    result = await send(handler, ids["emergency_stop_button"], "PRESS")
    assert result.accepted
    assert result.reason.key == "mqtt.command.unchanged"
    assert port.calls == []


@pytest.mark.parametrize("payload", ["1e3", "+5", "0x10", "\u0665", " ", "5.", ".5", "1_0"])
async def test_numbers_must_be_plain_decimals(payload: str) -> None:
    handler, _port, _clock, ids = make()
    result = await send(handler, ids["device_boost_minutes"], payload)
    assert result.reason.key == "mqtt.command.invalid_payload"


# --- SEC-16: refused-command log throttle ---------------------------------------------------


async def test_a_refusal_flood_logs_the_first_line_and_then_a_count() -> None:
    handler, port, clock, ids = make()
    with capture_logs() as logs:
        for _ in range(200):
            await send(handler, ids["replan"], "PRESS")
        for _ in range(50):
            await send(handler, ids["replan"], "garbage")
        clock.now += SUMMARY_INTERVAL_S
        handler.log_refusal_summary()
    refused = [e for e in logs if e["event"] == "mqtt.command" and not e["accepted"]]
    accepted = [e for e in logs if e["event"] == "mqtt.command" and e["accepted"]]
    summaries = [e for e in logs if e["event"] == "mqtt.command_refused_summary"]
    assert len(accepted) == ENTITY_BURST == len(port.calls)  # unchanged: accepted are all logged
    assert [(e["entity"], e["reason"]) for e in refused] == [
        (ids["replan"], "mqtt.command.rate_limited"),
        (ids["replan"], "mqtt.command.invalid_payload"),
    ]
    assert len(summaries) == 1
    assert summaries[0]["total"] == 200 - ENTITY_BURST - 1 + 50 - 1
    assert summaries[0]["log_level"] == "warning"


async def test_the_throttle_changes_no_verdict() -> None:
    """Same messages, same clock: the results are identical with and without throttling."""
    plain, _p1, c1, ids = make()
    throttled, _p2, c2, _ = make()
    plain._refusal_log = RefusalLogThrottle(c1, interval_s=0.0, max_keys=10_000)
    for i in range(120):
        c1.now = c2.now = 1000.0 + i * 0.25
        key = ("replan", "auto", "emergency_stop_button", "device_pause")[i % 4]
        payload = "garbage" if i % 7 == 0 else ("ON" if key == "auto" else "PRESS")
        a = await send(plain, ids[key], payload)
        b = await send(throttled, ids[key], payload)
        assert a == b


def test_throttle_logs_each_pair_once_per_busy_interval() -> None:
    clock = Clock()
    t = RefusalLogThrottle(clock, interval_s=60.0)
    assert t.record("a", "r1") is True
    assert t.record("a", "r1") is False
    assert t.record("a", "r2") is True
    assert t.record("b", "r1") is True
    assert t.summary() is None  # interval not over
    clock.now += 60.0
    assert t.summary() == [RefusalCount("a", "r1", 1)]
    # "a"/"r1" is still flooding: counted, not logged again.
    assert t.record("a", "r1") is False
    # "b"/"r1" was quiet for an interval: forgotten after the next summary.
    clock.now += 60.0
    assert t.summary() == [RefusalCount("a", "r1", 1)]
    assert t.record("b", "r1") is True
    clock.now += 60.0
    assert t.summary() == []
    clock.now += 60.0
    assert t.summary() == []
    assert t.record("a", "r1") is True  # quiet for a whole interval: in full again


def test_throttle_bounds_invented_entities() -> None:
    clock = Clock()
    t = RefusalLogThrottle(clock, max_keys=4)
    full = [t.record(f"x{i}", "mqtt.command.unknown_entity") for i in range(1000)]
    assert sum(full) == 5  # four pairs plus the overflow pair
    clock.now += SUMMARY_INTERVAL_S
    assert t.summary() == [RefusalCount(OVERFLOW_ENTITY, "mqtt.command.unknown_entity", 995)]


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["a", "b", "c", "d", "e"]),
            st.sampled_from(["r1", "r2"]),
            st.floats(min_value=0.0, max_value=90.0),
        ),
        max_size=300,
    )
)
def test_throttle_accounts_for_every_refusal(events: list[tuple[str, str, float]]) -> None:
    """Property: every refusal is either logged in full or counted in exactly one summary,
    and per interval at most one full line per pair (plus the overflow pair) is logged."""
    clock = Clock()
    max_keys = 4
    t = RefusalLogThrottle(clock, interval_s=60.0, max_keys=max_keys)
    full = counted = 0
    full_this_interval = 0
    for entity, reason, dt in events:
        clock.now += dt
        counts = t.summary()
        if counts is not None:
            counted += sum(c.suppressed for c in counts)
            full_this_interval = 0
        if t.record(entity, reason):
            full += 1
            full_this_interval += 1
        assert full_this_interval <= max_keys + 2  # + one overflow pair per reason
    clock.now += 60.0
    counted += sum(c.suppressed for c in t.summary() or [])
    assert full + counted == len(events)
