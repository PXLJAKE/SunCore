# SPDX-License-Identifier: Apache-2.0
"""Hypothesis properties of the downsampling: energy, idempotence, bucket boundaries."""

from __future__ import annotations

import asyncio
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from suncore.storage.timeseries import TimeseriesStore, open_timeseries

T0 = datetime(2026, 10, 1, tzinfo=UTC)
T0_S = int(T0.timestamp())

samples = st.lists(
    st.tuples(
        st.sampled_from(["a", "b"]),
        st.integers(min_value=0, max_value=4 * 3600),  # random gaps: sparse and clustered
        st.integers(min_value=-500, max_value=3000),
    ),
    max_size=120,
    unique_by=lambda s: (s[0], s[1]),
)
job_offsets = st.integers(min_value=0, max_value=6 * 3600)


async def _run(rows: list[tuple[str, int, int]], jobs: list[int]) -> tuple[list, list, list]:
    with tempfile.TemporaryDirectory() as tmp:
        async with await open_timeseries(Path(tmp) / "t.db") as db:
            store = TimeseriesStore(db)
            await store.append_measurements(
                [(k, T0 + timedelta(seconds=o), float(v)) for k, o, v in rows]
            )
            for job in jobs:
                await store.downsample(T0 + timedelta(seconds=job))
            minute = await db.fetchall("SELECT key, ts, mean, min, max, n FROM measurement_1m")
            quarter = await db.fetchall("SELECT key, ts, mean, min, max, n FROM measurement_15m")
            snapshot = [await db.fetchall("SELECT * FROM measurement_15m ORDER BY key, ts")]
            for job in jobs:  # the same jobs again: nothing may change
                await store.downsample(T0 + timedelta(seconds=job))
            again = await db.fetchall("SELECT * FROM measurement_15m ORDER BY key, ts")
            return minute, quarter, [snapshot[0], again]


def _go(rows: list[tuple[str, int, int]], jobs: list[int]) -> tuple[list, list, list]:
    return asyncio.run(_run(rows, jobs))


FAST = settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


@FAST
@given(rows=samples, job=job_offsets)
def test_energy_is_preserved_per_complete_quarter(rows: list, job: int) -> None:
    minute, quarter, _ = _go(rows, [job])
    horizon = (T0_S + job) // 900 * 900  # complete quarter hours end at or before this
    by_quarter: dict[tuple[str, int], list[int]] = defaultdict(list)
    for key, offset, value in rows:
        bucket = (T0_S + offset) // 900 * 900
        if bucket < horizon:
            by_quarter[(key, bucket)].append(value)
    stored = {(str(r[0]), int(r[1])): r for r in quarter}
    assert set(stored) == set(by_quarter)
    for bucket, values in by_quarter.items():
        _key, _ts, mean, low, high, n = stored[bucket]
        assert n == len(values)
        assert abs(mean * n - sum(values)) < 1e-6 * max(1.0, abs(sum(values)))
        assert (low, high) == (min(values), max(values))
    # the 1-minute tier adds up to the same energy
    total = float(sum(sum(v) for v in by_quarter.values()))
    minutes_total = sum(float(r[2]) * int(r[5]) for r in minute)
    assert abs(minutes_total - total) < 1e-6 * max(1.0, abs(total))


@FAST
@given(rows=samples, jobs=st.lists(job_offsets, min_size=1, max_size=3))
def test_downsampling_is_idempotent_for_any_job_times(rows: list, jobs: list[int]) -> None:
    _, _, (first, again) = _go(rows, jobs)
    assert first == again


@FAST
@given(rows=samples, job=job_offsets)
def test_rows_sit_on_bucket_boundaries_and_never_in_the_future(rows: list, job: int) -> None:
    minute, quarter, _ = _go(rows, [job])
    horizon = (T0_S + job) // 900 * 900
    assert all(int(r[1]) % 60 == 0 and int(r[1]) < horizon for r in minute)
    assert all(int(r[1]) % 900 == 0 and int(r[1]) < horizon for r in quarter)
    assert all(int(r[5]) > 0 for r in minute + quarter)
