# SPDX-License-Identifier: Apache-2.0
"""A minimal fake of Home Assistant's recorder statistics for the simulator (Phase 4 WP3).

Answers ``recorder/statistics_during_period`` with ``period: "hour"`` in the verified shape
(``docs/research/ha-websocket-api.md``): rows of ``{start, end, mean}`` with epoch
**milliseconds**, no unit, ids without data absent from the result. The history is the
simulated house run *before* the scenario start at 15-minute steps (clear sky, no devices),
aggregated to hourly means - a plausible week for the rebuild, not a replay of the scenario.
Tests can replace the rows of any id with :meth:`set_rows`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from suncore.ha.connection import CommandError
from suncore.sim.house import House, HouseState
from suncore.sim.scenario import Scenario

HISTORY_STEP_S: Final = 900
HOUR_MS: Final = 3_600_000


class SimRecorder:
    """Hourly statistics of the simulated house.

    Args:
        scenario: supplies the house parameters and the end of the history (its start).
        series: statistic id -> reader of the value from a house state (the sim sensors).
        history_hours: how far back the history reaches.
    """

    def __init__(
        self,
        scenario: Scenario,
        series: Mapping[str, Callable[[HouseState], float]],
        *,
        history_hours: int = 72,
    ) -> None:
        self._scenario = scenario
        self._series = dict(series)
        self._history_hours = history_hours
        self._rows: dict[str, list[dict[str, Any]]] | None = None
        self._overrides: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[dict[str, Any]] = []
        """Every query seen (evidence for the chunking tests)."""

    def set_rows(self, statistic_id: str, rows: Sequence[Mapping[str, Any]]) -> None:
        """Replace (or add) the rows of one statistic; an empty list removes its data."""
        self._overrides[statistic_id] = [dict(r) for r in rows]

    def _build(self) -> dict[str, list[dict[str, Any]]]:
        end = self._scenario.start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        begin = end - timedelta(hours=self._history_hours)
        house = House(self._scenario.house, begin, HISTORY_STEP_S)
        steps_per_hour = 3600 // HISTORY_STEP_S
        rows: dict[str, list[dict[str, Any]]] = {sid: [] for sid in self._series}
        for hour in range(self._history_hours):
            states = [house.step() for _ in range(steps_per_hour)]
            start_ms = int((begin + timedelta(hours=hour)).timestamp() * 1000)
            for statistic_id, read in self._series.items():
                mean = sum(read(s) for s in states) / len(states)
                rows[statistic_id].append(
                    {"start": start_ms, "end": start_ms + HOUR_MS, "mean": mean}
                )
        return rows

    def query(
        self,
        statistic_ids: Sequence[str],
        start: datetime,
        end: datetime | None,
        period: str,
        types: Sequence[str] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        """The ``result`` object of ``recorder/statistics_during_period``."""
        self.calls.append(
            {"ids": list(statistic_ids), "start": start, "end": end, "period": period}
        )
        if period != "hour":
            raise CommandError("not_found", f"period {period!r} is not simulated")
        if self._rows is None:
            self._rows = self._build()
        lo = int(start.timestamp() * 1000)
        hi = None if end is None else int(end.timestamp() * 1000)
        wanted = tuple(types) if types is not None else ("mean",)
        result: dict[str, list[dict[str, Any]]] = {}
        for statistic_id in statistic_ids:
            source = self._overrides.get(statistic_id, self._rows.get(statistic_id, []))
            picked = [
                {k: v for k, v in row.items() if k in ("start", "end", *wanted)}
                for row in source
                if row["start"] >= lo and (hi is None or row["start"] < hi)
            ]
            if picked:
                result[statistic_id] = picked
        return result
