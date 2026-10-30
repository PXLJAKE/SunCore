# SPDX-License-Identifier: Apache-2.0
"""Run-time budget of the KNN load model (plan WP12; ``scripts/bench_load_ml.py``).

Training (building the samples) plus the prediction of 48 h must stay below 1 s for 30 to 90
days of 15-minute history. Measured on CI's x86 runner; aarch64 is ``UNVERIFIED``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bench_load_ml.py"
BUDGET_S = 1.0


@pytest.mark.slow
def test_knn_training_and_prediction_stay_within_the_budget() -> None:
    spec = importlib.util.spec_from_file_location("bench_load_ml", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.bench(3, [30, 90])
    for days in (30, 90):
        row = result[f"{days}d"]
        assert row["samples"] == days * 96
        assert row["slots"] == 193
        assert row["total_s"] < BUDGET_S
