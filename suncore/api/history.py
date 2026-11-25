# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/history/*``, ``/plan/at``, ``/plan/compare``, ``/decisions/{id}/explain`` (WP4)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Final, Literal

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse, Response

from suncore.analysis.period import Granularity
from suncore.api import csvout, history_plans, history_queries
from suncore.api.history_models import (
    CyclesOut,
    DecisionExplainOut,
    DeviceHistoryOut,
    ForecastSkillOut,
    HeatmapOut,
    PlanAtOut,
    PlanCompareOut,
    SankeyOut,
    SeriesResolution,
    SeriesResponse,
    SummaryOut,
)
from suncore.api.models import ErrorOut
from suncore.api.services import HistorySources, Services
from suncore.core.decisions import Decision, DecisionKind, Origin
from suncore.core.measurements import SITE_HOUSE_W
from suncore.storage.decisions import DecisionFilter

API_PREFIX: Final = "/api/v1"
ERR_HISTORY_UNAVAILABLE: Final = history_queries.ERR_HISTORY_UNAVAILABLE

PeriodQ = Annotated[Granularity, Query(description="day, week, month or year containing ``date``")]
Format = Annotated[Literal["json", "csv"], Query(description="``csv``: UTF-8, comma, dot")]

_CSV: Final[dict[int | str, dict[str, Any]]] = {
    200: {"content": {"text/csv": {"schema": {"type": "string"}}}, "description": "JSON or CSV"},
    422: {"model": ErrorOut, "description": "Invalid range, key or period"},
    503: {"model": ErrorOut, "description": "No time-series database"},
}


def sources_of(services: Services) -> HistorySources:
    found = services.history_sources()
    if found is None:
        raise history_queries.fail(503, ERR_HISTORY_UNAVAILABLE)
    return found


def _summary_csv(out: SummaryOut, name: str) -> Response:
    header = [
        "bucket", "start", "end", "days_with_data", "complete", "live",
        "pv_wh", "house_wh", "import_wh", "export_wh", "charge_wh", "discharge_wh",
        "autarky_ratio", "self_consumption_ratio", "equivalent_full_cycles", "currency",
        "import_cost", "export_revenue", "cost_net", "baseline_no_pv_cost",
        "baseline_pv_only_cost", "savings_vs_no_pv", "savings_vs_pv_only",
    ]  # fmt: skip
    rows = [
        [
            b.key, b.start, b.end, b.days_with_data, b.complete, b.live,
            b.energy.pv_wh, b.energy.house_wh, b.energy.import_wh, b.energy.export_wh,
            b.energy.charge_wh, b.energy.discharge_wh,
            b.kpis.autarky, b.kpis.self_consumption, b.kpis.equivalent_full_cycles,
            b.cost.currency, b.cost.import_cost, b.cost.export_revenue, b.cost.cost_net,
            b.cost.baseline_no_pv_cost, b.cost.baseline_pv_only_cost,
            b.cost.savings_vs_no_pv, b.cost.savings_vs_pv_only,
        ]
        for b in out.buckets
    ]  # fmt: skip
    return csvout.csv_response(name, header, rows)


def register_history(app: FastAPI, services: Services) -> None:

    @app.get(
        f"{API_PREFIX}/history/summary",
        response_model=SummaryOut,
        operation_id="history_summary",
        responses=_CSV,
    )
    async def history_summary(
        period: PeriodQ = Granularity.DAY,
        date_: Annotated[date | None, Query(alias="date")] = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | SummaryOut:
        """Energy, flows, autarky, self-consumption, cost and savings against both baselines of
        a period, summed from the frozen days (the current day is computed live), with one bar
        per day (week, month) or month (year). CSV: the bars."""
        src = sources_of(services)
        day = date_ or history_queries.today_of(src)
        out = await history_queries.summary(src, period, day)
        return out if format == "json" else _summary_csv(out, f"summary-{out.key}")

    @app.get(
        f"{API_PREFIX}/history/sankey",
        response_model=SankeyOut,
        operation_id="history_sankey",
        responses=_CSV,
    )
    async def history_sankey(
        period: PeriodQ = Granularity.DAY,
        date_: Annotated[date | None, Query(alias="date")] = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | SankeyOut:
        """The attributed energy flows of a period as Sankey nodes and links (Wh)."""
        src = sources_of(services)
        out = await history_queries.sankey(src, period, date_ or history_queries.today_of(src))
        if format == "json":
            return out
        return csvout.csv_response(
            f"sankey-{out.key}",
            ["source", "target", "wh"],
            [[link.source, link.target, link.wh] for link in out.links],
        )

    @app.get(
        f"{API_PREFIX}/history/series",
        response_model=SeriesResponse,
        operation_id="history_series",
        responses=_CSV,
    )
    async def history_series(
        key: Annotated[list[str], Query(max_length=history_queries.SERIES_MAX_KEYS)],
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        resolution: SeriesResolution | None = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | SeriesResponse:
        """Measured means of up to 8 keys (``site.pv_w``, ``site.grid_w``, ``site.battery_w``,
        ``site.house_w``, ``site.soc_pct``, ``weather.temperature_c``,
        ``device.<id>.power_w``, ``device.<id>.on``). The resolution follows the span (1 min up
        to 2 days, 15 min up to 31 days, daily above) and is coarsened to at most 2000 points per
        series. Times without offset are site time; default range: the last 24 hours."""
        src = sources_of(services)
        now = src.clock()
        to = end or now
        out = await history_queries.series(
            src, key, start or to - timedelta(days=1), to, resolution
        )
        if format == "json":
            return out
        return csvout.csv_response(
            f"series-{out.start:%Y%m%d}-{out.end:%Y%m%d}",
            ["time", *(f"{s.key}_{s.unit}" for s in out.series)],
            [[t, *(s.values[i] for s in out.series)] for i, t in enumerate(out.times)],
        )

    @app.get(
        f"{API_PREFIX}/history/heatmap",
        response_model=HeatmapOut,
        operation_id="history_heatmap",
        responses=_CSV,
    )
    async def history_heatmap(
        key: str = SITE_HOUSE_W,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | HeatmapOut:
        """Mean of a key (default the house consumption) by local weekday x hour of day over
        the range (default: the last 28 days, at most 400)."""
        src = sources_of(services)
        to = end or src.clock()
        out = await history_queries.heatmap(src, key, start or to - timedelta(days=28), to)
        if format == "json":
            return out
        return csvout.csv_response(
            f"heatmap-{out.key}",
            ["weekday", "hour", f"mean_{out.unit}", "samples"],
            [
                [out.weekdays[d], h, out.values[d][h], out.counts[d][h]]
                for d in range(7)
                for h in range(24)
            ],
        )

    @app.get(
        f"{API_PREFIX}/history/cycles",
        response_model=CyclesOut,
        operation_id="history_cycles",
        responses=_CSV,
    )
    async def history_cycles(
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | CyclesOut:
        """Equivalent full cycles, charged and discharged energy and the state-of-charge range
        per local day (default: the last 30 days, at most 400)."""
        src = sources_of(services)
        last = end or history_queries.today_of(src)
        out = await history_queries.cycles(src, start or last - timedelta(days=29), last)
        if format == "json":
            return out
        return csvout.csv_response(
            f"cycles-{out.start}-{out.end}",
            ["day", "equivalent_full_cycles", "charge_wh", "discharge_wh", "soc_min_pct",
             "soc_max_pct", "complete"],
            [
                [d.day, d.equivalent_full_cycles, d.charge_wh, d.discharge_wh, d.soc_min_pct,
                 d.soc_max_pct, d.complete]
                for d in out.days
            ],
        )  # fmt: skip

    @app.get(
        f"{API_PREFIX}/history/forecast-skill",
        response_model=ForecastSkillOut,
        operation_id="history_forecast_skill",
        responses=_CSV,
    )
    async def history_forecast_skill(
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
        source: Annotated[str | None, Query(max_length=128)] = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | ForecastSkillOut:
        """Daily forecast error (MAE, bias = actual - forecast) per source, kind and horizon
        bucket, with the n-weighted mean over the range (default: the last 30 days)."""
        src = sources_of(services)
        last = end or history_queries.today_of(src)
        out = await history_queries.forecast_skill(
            src, start or last - timedelta(days=29), last, source
        )
        if format == "json":
            return out
        return csvout.csv_response(
            f"forecast-skill-{out.start}-{out.end}",
            ["source", "kind", "horizon", "day", "mae_w", "bias_w", "n"],
            [
                [s.source_id, s.kind, s.horizon, p.day, p.mae_w, p.bias_w, p.n]
                for s in out.series
                for p in s.points
            ],
        )

    @app.get(
        f"{API_PREFIX}/history/devices",
        response_model=DeviceHistoryOut,
        operation_id="history_devices",
        responses=_CSV,
    )
    async def history_devices(
        period: PeriodQ = Granularity.DAY,
        date_: Annotated[date | None, Query(alias="date")] = None,
        format: Format = "json",  # noqa: A002
    ) -> Response | DeviceHistoryOut:
        """Run time, estimated energy and starts of every controlled device in a period."""
        src = sources_of(services)
        out = await history_queries.devices(src, period, date_ or history_queries.today_of(src))
        if format == "json":
            return out
        return csvout.csv_response(
            f"devices-{out.key}",
            ["device_id", "name", "day", "run_hours", "energy_wh"],
            [
                [d.device_id, d.name, x.day, x.run_hours, x.energy_wh]
                for d in out.devices
                for x in d.days
            ],
        )

    _register_plan_history(app, services)


def _register_plan_history(app: FastAPI, services: Services) -> None:
    not_found: dict[int | str, dict[str, Any]] = {
        404: {"model": ErrorOut, "description": "No snapshot stored for that time (pruned)"},
        **_CSV,
    }

    @app.get(
        f"{API_PREFIX}/plan/at",
        response_model=PlanAtOut,
        operation_id="plan_at",
        responses=not_found,
    )
    async def plan_at(
        ts: datetime,
        format: Format = "json",  # noqa: A002
    ) -> Response | PlanAtOut:
        """The plan snapshot that was valid at ``ts`` (the newest one created at or before it)
        and the index of the interval covering ``ts``. 404 when none is stored (snapshots are
        kept 14 days / 200). CSV: the intervals."""
        out = await history_plans.plan_at(sources_of(services), ts)
        if format == "json":
            return out
        rows = [
            [
                i.start, i.end,
                i.expected and i.expected.pv_p50_w, i.expected and i.expected.load_w,
                i.expected and i.expected.import_w, i.expected and i.expected.export_w,
                i.expected and i.expected.battery_w, i.expected and i.expected.soc_pct,
                i.expected and i.expected.devices_w,
                i.prices and i.prices.import_price, i.prices and i.prices.export_price,
            ]
            for i in out.plan.intervals
        ]  # fmt: skip
        return csvout.csv_response(
            f"plan-{out.snapshot_id}",
            ["start", "end", "pv_p50_w", "load_w", "import_w", "export_w", "battery_w",
             "soc_pct", "devices_w", "import_price", "export_price"],
            rows,
        )  # fmt: skip

    @app.get(
        f"{API_PREFIX}/plan/compare",
        response_model=PlanCompareOut,
        operation_id="plan_compare",
        responses=not_found,
    )
    async def plan_compare(
        a: Annotated[str, Query(min_length=1, max_length=64)],
        b: Annotated[str, Query(min_length=1, max_length=64)],
        format: Format = "json",  # noqa: A002
    ) -> Response | PlanCompareOut:
        """Interval differences of two snapshots; ``a`` and ``b`` are a snapshot id or a time
        (the snapshot valid then). Only intervals that differ or exist on one side."""
        out = await history_plans.plan_compare(sources_of(services), a, b)
        if format == "json":
            return out
        return csvout.csv_response(
            f"plan-compare-{out.a.snapshot_id}-{out.b.snapshot_id}",
            ["start", "end", "only_in", "battery_w_delta", "soc_pct_delta", "import_w_delta",
             "export_w_delta", "devices_w_delta", "import_price_delta", "devices_changed"],
            [
                [d.start, d.end, d.only_in, d.battery_w_delta, d.soc_pct_delta, d.import_w_delta,
                 d.export_w_delta, d.devices_w_delta, d.import_price_delta,
                 ";".join(d.devices_changed)]
                for d in out.intervals
            ],
        )  # fmt: skip

    @app.get(
        f"{API_PREFIX}/decisions/{{decision_id}}/explain",
        response_model=DecisionExplainOut,
        operation_id="decision_explain",
        responses={
            404: {"model": ErrorOut, "description": "The decision was pruned or does not exist"},
            503: {"model": ErrorOut, "description": "No database"},
        },
    )
    async def decision_explain(
        decision_id: Annotated[int, Path(ge=1, le=2**62)],
    ) -> DecisionExplainOut:
        """The "Why?" of one decision: the decision with its reasons, the same device's
        decisions before and after, the plan snapshot valid then (with the interval) and the
        site readings of that moment."""
        return await history_plans.explain(sources_of(services), decision_id)


def register_decisions(app: FastAPI, services: Services) -> None:
    """The decision log with its filters (Phase 5 WP4)."""

    @app.get(
        f"{API_PREFIX}/decisions",
        response_model=list[Decision],
        responses={
            200: {
                "content": {"text/csv": {"schema": {"type": "string"}}},
                "headers": {
                    "X-Total-Count": {
                        "description": "Matching decisions before limit and offset",
                        "schema": {"type": "integer"},
                    },
                    "X-Decision-Ids": {
                        "description": "Row ids of the returned decisions, comma separated, "
                        "in the order of the body (the key of /decisions/{id}/explain)",
                        "schema": {"type": "string"},
                    },
                },
            },
            503: {"model": ErrorOut, "description": "Filters need the decision database"},
        },
    )
    async def decisions(  # noqa: PLR0917
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        device_id: str | None = Query(default=None, max_length=128),
        kind: Annotated[list[DecisionKind] | None, Query()] = None,
        since: Annotated[datetime | None, Query(alias="from")] = None,
        until: Annotated[datetime | None, Query(alias="to")] = None,
        layer: Origin | None = None,
        reason: Annotated[
            str | None, Query(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
        ] = None,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        format: Annotated[Literal["json", "csv"], Query()] = "json",  # noqa: A002
    ) -> Response | list[Decision]:
        """The decision log, newest first. Filters: ``device_id``, ``kind`` (repeatable;
        ``shadow``, ``accepted``, ``proposed`` ...), ``from`` (inclusive) and ``to`` (exclusive),
        ``layer`` (the layer that produced the intent), ``reason`` (a reason key, or a dotted
        prefix of it) and ``q`` (case-insensitive text in the device id, reason keys and
        arguments). ``offset`` pages; the headers ``X-Total-Count`` and ``X-Decision-Ids``
        accompany the list. CSV: one row per decision."""
        sources = services.history_sources()
        if sources is None:
            if offset or since or until or layer or reason or q or format == "csv":
                raise HTTPException(status_code=503, detail=ERR_HISTORY_UNAVAILABLE)
            if device_id is None and not kind:
                return await services.decisions(limit)
            return await services.decisions(limit, device_id=device_id, kinds=kind)
        tz = sources.context.tz
        flt = DecisionFilter(
            device_id=device_id,
            kinds=frozenset(kind or ()),
            since=None if since is None else since if since.tzinfo else since.replace(tzinfo=tz),
            until=None if until is None else until if until.tzinfo else until.replace(tzinfo=tz),
            layer=None if layer is None else layer.value,
            reason=reason,
            text=q,
        )
        found = await sources.decision_log.query(flt, limit=limit, offset=offset)
        headers = {
            "X-Total-Count": str(await sources.decision_log.count_matching(flt)),
            "X-Decision-Ids": ",".join(str(i) for i, _ in found),
        }
        if format == "csv":
            response = csvout.csv_response(
                "decisions",
                ["id", "time", "device_id", "layer", "kind", "mode", "action", "reasons"],
                [
                    [
                        i,
                        d.ts.astimezone(tz),
                        d.intent.asset_id,
                        d.intent.origin.value,
                        d.kind.value,
                        d.mode.value,
                        d.intent.action.kind.value,
                        ";".join(r.key for r in d.reasons),
                    ]
                    for i, d in found
                ],
            )
            response.headers.update(headers)
            return response
        return JSONResponse([d.model_dump(mode="json") for _, d in found], headers=headers)
