# SPDX-License-Identifier: Apache-2.0
"""A backtest can never write to a device (Phase 5 WP6): structural checks.

1. The backtest modules (engine, models, job runner, API) import nothing that can reach a
   device or Home Assistant: ``safety``, ``ha``, ``devices``, ``controller``, ``app.control``,
   the simulator's fake Home Assistant and closed-loop stack, and no storage writer of the
   configuration.
2. They never name a write call (``call_service``, ``apply``, ``release``, ``arm_fallback``,
   ``keep_alive``, ``put_section``, ``put_site``) or the gate.
3. The job runner hands the engine a :class:`ReadOnlyHistory`, which exposes only four read
   methods (slots, no ``__dict__``). The bound methods still belong to the store, so this is an
   interface restriction, not a capability boundary; rules 1 and 2 are the structural guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from suncore.app.backtests import ReadOnlyHistory
from suncore.storage import open_timeseries
from suncore.storage.timeseries import TimeseriesStore

ROOT = Path(__file__).resolve().parents[2] / "suncore"
MODULES = (
    ROOT / "sim" / "backtest.py",
    ROOT / "sim" / "backtest_models.py",
    ROOT / "app" / "backtests.py",
    ROOT / "api" / "backtests.py",
)
FORBIDDEN_IMPORTS = (
    "suncore.safety",
    "suncore.ha",
    "suncore.devices",
    "suncore.controller",
    "suncore.app.control",
    "suncore.app.runtime",
    "suncore.sim.fake_ha",
    "suncore.sim.stack",
    "suncore.sim.runner",
    "suncore.storage.config_store",
)
FORBIDDEN_NAMES = frozenset(
    {
        "call_service",
        "apply",
        "release",
        "arm_fallback",
        "keep_alive",
        "put_section",
        "put_site",
        "gate",
        "SafetyGate",
    }
)


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_backtest_modules_cannot_reach_a_device(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = sorted(
        name
        for name in _imports(tree)
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_IMPORTS)
    )
    assert not bad, f"{path.name} imports {bad}"
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & FORBIDDEN_NAMES, f"{path.name} names {sorted(names & FORBIDDEN_NAMES)}"


async def test_read_only_history_exposes_reads_only(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db)
        view = ReadOnlyHistory(store)
        public = {n for n in dir(view) if not n.startswith("__")}
        assert public == {
            "_forecast",
            "_prices",
            "_series",
            "_sources",
            "latest_forecast_before",
            "price_source_ids",
            "read_prices",
            "read_series",
        }
        # The held callables are bound read methods of the store, nothing else.
        held = [view._forecast, view._prices, view._series, view._sources]
        assert [m.__name__ for m in held] == [
            "latest_forecast_before",
            "read_prices",
            "read_series",
            "price_source_ids",
        ]
        assert not hasattr(view, "__dict__")  # no attribute can be added later
        for name in ("store_prices", "append_measurements", "database", "store_forecast"):
            assert not hasattr(view, name)
