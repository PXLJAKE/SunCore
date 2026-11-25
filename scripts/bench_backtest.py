#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Benchmark of the backtest engine on the deterministic demo history (Phase 5 WP6, risk R2).

Seeds ``--days`` + 1 days of demo history into a throw-away ``timeseries.db``, then replays the
last ``--days`` days (hourly replans, default 14) without and with perfect foresight. Prints the
wall time of each run, the replans, the slowest replan and the peak RSS of the process.

Plan budget (§0 item 7): one hourly replan ≤ 10 s on a Raspberry Pi 4; RSS < 300 MB including a
running backtest. aarch64 figures stay ``UNVERIFIED`` until measured on the Pi.

Usage: ``uv run python scripts/bench_backtest.py [--days 14] [--json]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import resource
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from suncore.core.battery import BatteryModel, BatterySettings
from suncore.sim.backtest import BacktestSite, run_backtest
from suncore.sim.backtest_models import MAX_DAYS, BacktestRequest
from suncore.sim.demo_history import (
    BATTERY_W,
    BATTERY_WH,
    EXPORT_PRICE,
    PRICE_SOURCE,
    PV_SOURCES,
    seed_demo_history,
)
from suncore.storage import open_timeseries
from suncore.storage.timeseries import TimeseriesStore

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Berlin")


def _site() -> BacktestSite:
    return BacktestSite(
        tz=TZ,
        tz_name="Europe/Berlin",
        battery=BatterySettings(
            model=BatteryModel(
                capacity_wh=BATTERY_WH,
                max_charge_w=BATTERY_W,
                max_discharge_w=BATTERY_W,
                reserve_pct=10.0,
                bms_drift_margin_pct=0.0,
            )
        ),
        price_source_id=PRICE_SOURCE,
        export_fallback=EXPORT_PRICE,
        currency="EUR",
        pv_source_ids=tuple(s for s, _, _ in PV_SOURCES),
    )


async def bench(days: int) -> dict[str, float | int | str]:
    today = NOW.astimezone(TZ).date()
    out: dict[str, float | int | str] = {
        "machine": platform.machine(),
        "python": platform.python_version(),
        "days": days,
    }
    with tempfile.TemporaryDirectory(prefix="suncore-bench-") as tmp:
        async with await open_timeseries(Path(tmp) / "t.db") as db:
            store = TimeseriesStore(db)
            await seed_demo_history(store, now=NOW, days=days + 1)
            for label, perfect in (("whatif", False), ("perfect", True)):
                request = BacktestRequest(
                    start=today - timedelta(days=days),
                    end=today - timedelta(days=1),
                    perfect_foresight=perfect,
                )
                started = time.perf_counter()
                result = await run_backtest(store, _site(), request, today=today)
                out[f"{label}_wall_s"] = round(time.perf_counter() - started, 2)
                out[f"{label}_replans"] = result.stats.replans
                out[f"{label}_replan_max_s"] = result.stats.solve_s_max
                out[f"{label}_skipped_days"] = len(result.skipped)
    out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, choices=range(1, MAX_DAYS + 1))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = asyncio.run(bench(args.days))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f"{key:>24}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
