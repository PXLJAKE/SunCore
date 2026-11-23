# SPDX-License-Identifier: Apache-2.0
"""``day_summary`` and ``accuracy_day`` of ``timeseries.db`` (Phase 5 WP3, ADR-0014).

``day_summary`` holds one frozen row per local day (energies, attributed flows, KPIs, grid cost
and both baselines, coverage), computed by :mod:`suncore.app.summaries` with
:mod:`suncore.analysis`. ``accuracy_day`` holds the daily forecast error per source and horizon
bucket. Both are kept for ever. Timestamps are integer UTC seconds; days are local ISO dates.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import UTC, date, datetime
from typing import Final, Literal

from suncore.storage.db import Database, Row, SqlParam


def _epoch(moment: datetime) -> int:
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = "timestamps must be timezone-aware"
        raise ValueError(msg)
    return int(moment.timestamp())


def _from_epoch(seconds: object) -> datetime:
    return datetime.fromtimestamp(float(str(seconds)), UTC)


def _opt(value: object) -> float | None:
    return None if value is None else float(str(value))


@dataclass(frozen=True, slots=True)
class DaySummaryRow:
    """One local day, frozen. ``None`` costs mean no slot of the day had a stored price."""

    day: date
    start: datetime
    end: datetime
    tz: str
    slots: int
    coverage: float
    complete: bool
    derived_slots: int
    pv_wh: float
    house_wh: float
    import_wh: float
    export_wh: float
    charge_wh: float
    discharge_wh: float
    pv_to_house_wh: float
    pv_to_battery_wh: float
    pv_to_grid_wh: float
    battery_to_house_wh: float
    battery_to_grid_wh: float
    grid_to_house_wh: float
    grid_to_battery_wh: float
    residual_wh: float
    autarky: float | None
    self_consumption: float | None
    equivalent_full_cycles: float | None
    priced_slots: int
    import_cost: float | None
    export_revenue: float | None
    cost_net: float | None
    baseline_no_pv_cost: float | None
    baseline_pv_only_cost: float | None
    currency: str | None
    tariff_fingerprint: str | None
    computed_at: datetime

    @property
    def savings_vs_no_pv(self) -> float | None:
        """What the plant adds: ``baseline_no_pv_cost - cost_net``."""
        if self.cost_net is None or self.baseline_no_pv_cost is None:
            return None
        return self.baseline_no_pv_cost - self.cost_net

    @property
    def savings_vs_pv_only(self) -> float | None:
        """What SunCore adds on top of the plant: ``baseline_pv_only_cost - cost_net``."""
        if self.cost_net is None or self.baseline_pv_only_cost is None:
            return None
        return self.baseline_pv_only_cost - self.cost_net


_DAY_COLUMNS: Final = (
    "day, start_ts, end_ts, tz, slots, coverage, complete, derived_slots, pv_wh, house_wh, "
    "import_wh, export_wh, charge_wh, discharge_wh, pv_to_house_wh, pv_to_battery_wh, "
    "pv_to_grid_wh, battery_to_house_wh, battery_to_grid_wh, grid_to_house_wh, "
    "grid_to_battery_wh, residual_wh, autarky, self_consumption, equivalent_full_cycles, "
    "priced_slots, import_cost, export_revenue, cost_net, baseline_no_pv_cost, "
    "baseline_pv_only_cost, currency, tariff_fingerprint, computed_at"
)
assert len(_DAY_COLUMNS.split(",")) == len(fields(DaySummaryRow))


def _day_params(row: DaySummaryRow) -> tuple[SqlParam, ...]:
    values = astuple(row)
    out: list[SqlParam] = []
    for value in values:
        if isinstance(value, datetime):
            out.append(_epoch(value))
        elif isinstance(value, date):
            out.append(value.isoformat())
        elif isinstance(value, bool):
            out.append(int(value))
        else:
            out.append(value)
    return tuple(out)


def _day_row(r: Row) -> DaySummaryRow:
    return DaySummaryRow(
        day=date.fromisoformat(str(r[0])),
        start=_from_epoch(r[1]),
        end=_from_epoch(r[2]),
        tz=str(r[3]),
        slots=int(str(r[4])),
        coverage=float(str(r[5])),
        complete=bool(r[6]),
        derived_slots=int(str(r[7])),
        pv_wh=float(str(r[8])),
        house_wh=float(str(r[9])),
        import_wh=float(str(r[10])),
        export_wh=float(str(r[11])),
        charge_wh=float(str(r[12])),
        discharge_wh=float(str(r[13])),
        pv_to_house_wh=float(str(r[14])),
        pv_to_battery_wh=float(str(r[15])),
        pv_to_grid_wh=float(str(r[16])),
        battery_to_house_wh=float(str(r[17])),
        battery_to_grid_wh=float(str(r[18])),
        grid_to_house_wh=float(str(r[19])),
        grid_to_battery_wh=float(str(r[20])),
        residual_wh=float(str(r[21])),
        autarky=_opt(r[22]),
        self_consumption=_opt(r[23]),
        equivalent_full_cycles=_opt(r[24]),
        priced_slots=int(str(r[25])),
        import_cost=_opt(r[26]),
        export_revenue=_opt(r[27]),
        cost_net=_opt(r[28]),
        baseline_no_pv_cost=_opt(r[29]),
        baseline_pv_only_cost=_opt(r[30]),
        currency=None if r[31] is None else str(r[31]),
        tariff_fingerprint=None if r[32] is None else str(r[32]),
        computed_at=_from_epoch(r[33]),
    )


class DaySummaryStore:
    """Repository over ``day_summary``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def put(self, row: DaySummaryRow) -> None:
        """Insert or replace the row of ``row.day``."""
        marks = ", ".join("?" * len(fields(DaySummaryRow)))
        await self._db.execute(
            f"INSERT OR REPLACE INTO day_summary ({_DAY_COLUMNS}) VALUES ({marks})",
            _day_params(row),
        )

    async def get(self, day: date) -> DaySummaryRow | None:
        found = await self._db.fetchone(
            f"SELECT {_DAY_COLUMNS} FROM day_summary WHERE day = ?",
            (day.isoformat(),),
        )
        return None if found is None else _day_row(found)

    async def between(self, first: date, last: date) -> list[DaySummaryRow]:
        """The rows of the local days ``first..last`` (inclusive), oldest first."""
        rows = await self._db.fetchall(
            f"SELECT {_DAY_COLUMNS} FROM day_summary WHERE day >= ? AND day <= ? ORDER BY day",
            (first.isoformat(), last.isoformat()),
        )
        return [_day_row(r) for r in rows]

    async def slot_counts(self, first: date, last: date) -> dict[date, int]:
        """Measured slots of the stored days (to see whether more data arrived since)."""
        rows = await self._db.fetchall(
            "SELECT day, slots FROM day_summary WHERE day >= ? AND day <= ?",
            (first.isoformat(), last.isoformat()),
        )
        return {date.fromisoformat(str(r[0])): int(str(r[1])) for r in rows}

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) FROM day_summary")
        return 0 if row is None else int(str(row[0]))


ForecastKind = Literal["pv", "load"]


@dataclass(frozen=True, slots=True)
class AccuracyDayRow:
    """Error of one source on one local day in one horizon bucket (error = actual - forecast)."""

    source_id: str
    kind: ForecastKind
    day: date
    horizon: str
    mae_w: float
    bias_w: float
    n: int
    computed_at: datetime


class AccuracyDayStore:
    """Repository over ``accuracy_day``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def put_many(self, rows: Iterable[AccuracyDayRow]) -> int:
        batch: list[Sequence[SqlParam]] = [
            (
                r.source_id,
                r.kind,
                r.day.isoformat(),
                r.horizon,
                r.mae_w,
                r.bias_w,
                r.n,
                _epoch(r.computed_at),
            )
            for r in rows
        ]
        if not batch:
            return 0
        await self._db.executemany(
            "INSERT OR REPLACE INTO accuracy_day "
            "(source_id, kind, day, horizon, mae_w, bias_w, n, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        return len(batch)

    async def between(
        self, first: date, last: date, *, source_id: str | None = None
    ) -> list[AccuracyDayRow]:
        sql = (
            "SELECT source_id, kind, day, horizon, mae_w, bias_w, n, computed_at "
            "FROM accuracy_day WHERE day >= ? AND day <= ?"
        )
        params: list[SqlParam] = [first.isoformat(), last.isoformat()]
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        rows = await self._db.fetchall(sql + " ORDER BY day, source_id, kind, horizon", params)
        return [
            AccuracyDayRow(
                source_id=str(r[0]),
                kind="pv" if str(r[1]) == "pv" else "load",
                day=date.fromisoformat(str(r[2])),
                horizon=str(r[3]),
                mae_w=float(str(r[4])),
                bias_w=float(str(r[5])),
                n=int(str(r[6])),
                computed_at=_from_epoch(r[7]),
            )
            for r in rows
        ]

    async def days_with_rows(
        self, source_id: str, kind: ForecastKind, first: date, last: date
    ) -> set[date]:
        """The days of ``first..last`` for which the source already has a row."""
        rows = await self._db.fetchall(
            "SELECT DISTINCT day FROM accuracy_day "
            "WHERE source_id = ? AND kind = ? AND day >= ? AND day <= ?",
            (source_id, kind, first.isoformat(), last.isoformat()),
        )
        return {date.fromisoformat(str(r[0])) for r in rows}


__all__ = [
    "AccuracyDayRow",
    "AccuracyDayStore",
    "DaySummaryRow",
    "DaySummaryStore",
    "ForecastKind",
]
