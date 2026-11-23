#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Benchmark of the optional KNN load model (Phase 4 WP12; plan §4 "ML training/prediction").

A synthetic house (a weekday/weekend profile, a heating term on a daily outdoor temperature,
a level shift after half the history and Gaussian noise; seeded, so every run sees the same data)
with 30, 60 and 90 days of 15-minute history. Prints the median of ``--runs`` runs of the
"training" (:func:`~suncore.forecast.load_ml.fit_knn_model`: building the samples and their
features) and of the prediction of 48 h (:func:`~suncore.forecast.load_ml.forecast_knn`, 193
quarter hours with P10/P50/P90). Budget: training + prediction < 1 s (plan WP12); CI checks it
on x86 (``tests/forecast/test_load_ml_bench.py``), aarch64 is ``UNVERIFIED`` until it is run on
a Pi or the CI arm runner.

Usage: ``uv run python scripts/bench_load_ml.py [--runs 5] [--days 30 60 90] [--json]``.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from suncore.core.series import SeriesUnit, TimeSeries, planning_grid
from suncore.forecast.load_ml import fit_knn_model, forecast_knn

TZ = ZoneInfo("Europe/Berlin")
STEP = timedelta(minutes=15)
END = datetime(2026, 11, 2, 10, 5, tzinfo=TZ).astimezone(UTC)  # a Monday morning
BUDGET_S = 1.0


def synthetic(days: int, seed: int = 7) -> tuple[TimeSeries, TimeSeries, datetime]:
    """Base load (W) and outdoor temperature (°C) of ``days`` complete days before ``END``."""
    rng = random.Random(seed)
    midnight = datetime.combine(END.astimezone(TZ).date(), datetime.min.time(), tzinfo=TZ)
    start = (midnight - timedelta(days=days)).astimezone(UTC)
    count = (midnight.astimezone(UTC) - start) // STEP
    daily_temp: dict[object, float] = {}
    load: list[float | None] = []
    temp: list[float | None] = []
    for i in range(count):
        moment = (start + STEP * i).astimezone(TZ)
        day = moment.date()
        if day not in daily_temp:
            daily_temp[day] = 12.0 + 8.0 * math.sin(i / 96 / 20) + rng.gauss(0, 3)
        hour = moment.hour + moment.minute / 60
        shape = 250.0 + 350.0 * math.exp(-((hour - 19.5) ** 2) / 4)
        if moment.weekday() >= 5:
            shape += 200.0 * math.exp(-((hour - 12.0) ** 2) / 6)
        heating = 25.0 * max(0.0, 15.0 - daily_temp[day])
        shift = 80.0 if i > count // 2 else 0.0
        load.append(max(0.0, shape + heating + shift + rng.gauss(0, 60)))
        temp.append(daily_temp[day] + 4.0 * math.sin((hour - 9) / 24 * 2 * math.pi))
    return (
        TimeSeries(start=start, step=STEP, values=tuple(load), unit=SeriesUnit.W),
        TimeSeries(start=start, step=STEP, values=tuple(temp), unit=SeriesUnit.CELSIUS),
        start,
    )


def bench(runs: int, days_list: list[int]) -> dict[str, object]:
    results: dict[str, object] = {
        "machine": f"{platform.machine()} {platform.python_implementation()} "
        f"{platform.python_version()}",
        "runs": runs,
        "budget_s": BUDGET_S,
    }
    for days in days_list:
        base, temperature, _ = synthetic(days)
        grid = planning_grid(END, timedelta(hours=48))
        fits: list[float] = []
        predictions: list[float] = []
        samples = 0
        for _ in range(runs):
            started = time.perf_counter()
            model = fit_knn_model(
                base, now=END, tz=TZ, day_types={}, temperature=temperature, history_days=days
            )
            fits.append(time.perf_counter() - started)
            started = time.perf_counter()
            forecast = forecast_knn(model, grid, TZ, {}, {END.astimezone(TZ).date(): 10.0})
            predictions.append(time.perf_counter() - started)
            assert forecast is not None
            samples = model.samples
        fit_s = statistics.median(fits)
        predict_s = statistics.median(predictions)
        results[f"{days}d"] = {
            "samples": samples,
            "slots": grid.count,
            "fit_s": round(fit_s, 4),
            "predict_s": round(predict_s, 4),
            "total_s": round(fit_s + predict_s, 4),
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--days", type=int, nargs="+", default=[30, 60, 90])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = bench(max(1, args.runs), args.days)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for key, value in result.items():
            sys.stdout.write(f"{key:>10}: {value}\n")
    totals = [v["total_s"] for v in result.values() if isinstance(v, dict)]
    return 0 if all(t < BUDGET_S for t in totals) else 1


if __name__ == "__main__":
    sys.exit(main())
