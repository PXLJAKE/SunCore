# SPDX-License-Identifier: Apache-2.0
"""HiGHS releases the GIL while it solves (control-safety review m5).

The runtime (WP9) solves in a daemon thread; if ``Highs.run()`` kept the GIL, the event loop
(controller ticks every 5 s, the websocket) would stall for the whole solve. highspy's own
``Highs.solve`` runs ``run()`` in a thread and polls it from the main thread with timeouts,
which only works with the GIL released; this test measures it with an LP that takes long
enough to notice: the event loop's largest gap must stay far below the solve time.
``UNVERIFIED`` on aarch64 (same wheel build, not run on a Pi).
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from suncore.planner.model import HighsBackend, LinearProblem


def _large_lp(n: int, m: int) -> LinearProblem:
    rng = np.random.default_rng(0)
    starts, index, value = [0], [], []
    dense = rng.random((m, n)) * (rng.random((m, n)) < 0.05)
    for row in dense:
        nz = np.nonzero(row)[0]
        index.extend(nz.tolist())
        value.extend(row[nz].tolist())
        starts.append(len(index))
    return LinearProblem(
        cost=-rng.random(n),
        col_lower=np.zeros(n),
        col_upper=np.ones(n),
        row_lower=np.full(m, -np.inf),
        row_upper=dense.sum(axis=1) * 0.3,
        a_start=np.array(starts, dtype=np.int64),
        a_index=np.array(index, dtype=np.int64),
        a_value=np.array(value, dtype=np.float64),
    )


@pytest.mark.slow
async def test_the_event_loop_keeps_ticking_while_highs_solves() -> None:
    problem = _large_lp(1500, 750)
    gaps: list[float] = []
    done = asyncio.Event()

    async def ticker() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    began = time.perf_counter()
    result = await asyncio.to_thread(HighsBackend().solve, problem, time_limit_s=30, mip_gap=0.0)
    solve_s = time.perf_counter() - began
    done.set()
    await task
    assert result.status in ("optimal", "time_limit")
    assert solve_s > 0.3, "the LP must take long enough to measure the loop"
    assert max(gaps) < min(0.25, solve_s / 2)
