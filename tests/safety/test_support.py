# SPDX-License-Identifier: Apache-2.0
"""Rate limiter, runtime model/store and mode providers."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.decisions import OperatingMode, SwitchAction
from suncore.safety.modes import AllShadow, ModeProvider, StaticModes, view
from suncore.safety.ratelimit import WriteRateLimiter, is_stale_intent
from suncore.safety.runtime import SWITCH_WINDOW, DeviceRuntime, MemoryRuntimeStore, RuntimeStore
from tests.safety.helpers import T0


def test_rate_limiter_try_acquire() -> None:
    rl = WriteRateLimiter(5.0)
    assert rl.min_interval_s == 5.0
    assert rl.next_allowed("a") is None
    assert rl.try_acquire("a", T0)
    assert not rl.try_acquire("a", T0 + timedelta(seconds=4.9))
    assert rl.try_acquire("b", T0)  # per device
    assert rl.next_allowed("a") == T0 + timedelta(seconds=5)
    assert rl.try_acquire("a", T0 + timedelta(seconds=5))
    rl.release_slot("a", T0)
    assert rl.last_write("a") == T0
    rl.release_slot("a", None)
    assert rl.last_write("a") is None
    rl.prime("c", T0)
    assert not rl.can_acquire("c", T0 + timedelta(seconds=1))
    rl.prime("c", None)
    assert rl.can_acquire("c", T0)
    with pytest.raises(ValueError, match="negative"):
        WriteRateLimiter(-1)


@given(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000))
def test_stale_intent(age: int, max_age: int) -> None:
    assert is_stale_intent(T0 - timedelta(seconds=age), T0, max_age) == (age > max_age)


def test_runtime_switch_window_and_pruning() -> None:
    rt = DeviceRuntime(device_id="d")
    rt = rt.with_switch(T0 - timedelta(minutes=70), on=True)
    rt = rt.with_switch(T0 - timedelta(minutes=30), on=False)
    rt = rt.with_switch(T0, on=True)
    assert rt.switches_in_window(T0) == 2
    assert rt.switch_times == (T0 - timedelta(minutes=30), T0)  # pruned on record
    assert rt.last_on_ts == T0
    assert rt.last_off_ts == T0 - timedelta(minutes=30)
    assert rt.pruned(T0) is rt
    later = T0 + SWITCH_WINDOW
    assert rt.pruned(later).switch_times == ()
    assert not rt.under_control
    assert not rt.is_held(T0)
    assert rt.model_copy(update={"override": True}).is_held(T0 + timedelta(days=9))


def test_runtime_round_trips_through_json() -> None:
    rt = DeviceRuntime(
        device_id="d",
        baseline=SwitchAction(on=False),
        last_written=SwitchAction(on=True),
        last_write_ts=T0,
    )
    assert DeviceRuntime.model_validate_json(rt.model_dump_json()) == rt


async def test_memory_store_is_a_runtime_store() -> None:
    store: RuntimeStore = MemoryRuntimeStore()
    assert await store.load_runtime("d") is None
    assert await store.all_runtimes() == ()
    assert await store.load_emergency_stop() is False
    rt = DeviceRuntime(device_id="d")
    await store.save_runtime(rt)
    assert await store.load_runtime("d") == rt
    assert list(await store.all_runtimes()) == [rt]
    await store.save_emergency_stop(True)
    assert await store.load_emergency_stop() is True


def test_mode_providers() -> None:
    shadow: ModeProvider = AllShadow()
    assert shadow.global_mode() is OperatingMode.SHADOW
    assert shadow.device_mode("x") is OperatingMode.SHADOW
    static = StaticModes(OperatingMode.AUTOMATIC, {"a": OperatingMode.ASSISTED})
    assert view(static, "a").effective is OperatingMode.ASSISTED
    assert view(static, "b").effective is OperatingMode.SHADOW  # unknown device = shadow
    static.set_device("b", OperatingMode.AUTOMATIC)
    assert view(static, "b").effective is OperatingMode.AUTOMATIC
    static.set_global(OperatingMode.SHADOW)
    assert view(static, "b").effective is OperatingMode.SHADOW
    assert view(static, "b").global_mode is OperatingMode.SHADOW
    asyncio.run(asyncio.sleep(0))
