# ADR-0014: Analytics, baselines and backtesting

- **Status:** Accepted (2026-11-23, by delegation: the maintainer asked to proceed with the
  plan's defaults while away)
- **Date:** 2026-11-23
- **Deciders:** maintainer, lead engineer (`architect`)
- **Research:** plan [`phase-5.md`](../plans/phase-5.md) §0 decisions 1, 2, 3, 6, 7, 8;
  related [ADR-0002](0002-frontend-framework-and-charts.md),
  [ADR-0004](0004-data-storage.md), [ADR-0011](0011-two-control-layers.md)

## Context

Phase 4 leaves the time series, plan snapshots and decision log, but no savings figure, no
baseline, no daily aggregates, no autarky or self-consumption numbers and no way to run the
planner on past data. Raw retention is short (`measurement_1m` 30 days, `price` 400 days), so
costs must not change when the tariff is edited later, and a year view must not scan about
35 000 quarter hours per signal on a Pi 4. Phase 5 adds no device write path; the new code only
reads.

## Options considered

### Option A – record KPIs live, re-implement the planner for what-if

Simple to start, but recorded numbers drift from the data, cannot be recomputed, and a second
planner implementation diverges from the real one.

### Option B – compute KPIs from 15-minute energy, freeze per day, replay the real planner

One pure module, reproducible numbers, a year view from small rows, and what-if results that
are about the planner users actually run.

## Decision

Option B.

1. **Pure `suncore/analysis/` package.** Depends on `core` only, `mypy --strict`, same boundary
   rule as `planner/`. It computes from 15-minute energy per signal: autarky,
   self-consumption, Sankey flows, battery equivalent full cycles, cost and savings.
2. **Sankey attribution rule (fixed).** PV serves the house first, then charges the battery,
   then is exported; the grid charges the battery only for what PV cannot.
3. **Two baselines.** *No PV, no battery* (every kWh of load from the grid) is what the plant
   adds. *PV without battery and without SunCore control* (house load as measured, no battery
   flows, surplus exported) is the honest "what SunCore adds" figure. The UI shows both and the
   difference as attribution. Device shifting is not undone, because the unshifted device load
   is unknown; the UI says so. Standing charges and one-off fees are not part of cost.
4. **Daily frozen `day_summary`.** One row per local day in `timeseries.db` (energies, flows,
   cost, baselines, cycles, per-source forecast MAE, coverage %, config fingerprint of the
   tariff used, `computed_at`), written by the existing 03:30 job, kept unlimited (about 365
   small rows a year). Year and month views read it; today and the last two days are computed
   live. A day with coverage below 80 % is marked incomplete and excluded from averages. It
   needs a `timeseries.db` migration with its own `user_version`; `suncore.db` is unchanged.
5. **Backtest replays the real planner** in `suncore/sim/backtest.py` (composition layer, may
   import `planner` and `analysis`; the planner stays `core`-only). Per replan instant
   (default hourly) it builds `PlannerInputs` from what was known then
   (`latest_forecast_before(issue)`, stored prices, measured SoC), runs the planner with the
   alternative parameters, and executes the first hour against actual PV and load with a simple
   battery model (efficiencies, limits, reserve). The controller and the gate are not in the
   loop; results are labelled "plan with perfect execution". Runs shown: *actual*, *what-if*
   and optionally *perfect foresight* (the upper bound for better forecasts). A run is a
   cancellable background job in a worker thread, one at a time, default window 14 days (cap
   30), memory and CPU bounded.
6. **Chart and bundle budget (ADR-0002 addendum).** One `echarts-core` plus per-view chunks
   (`echarts-plan` with `custom` and `dataZoom`; `echarts-history` with `sankey`, `heatmap`,
   `visualMap`, `bar`). Budget: first paint 125 kB gzip (unchanged), everything but ECharts
   250 kB (unchanged), each view's chart chunks together at most 240 kB gzip, gated by the
   existing CI bundle check.
7. **Exports from the backend.** CSV comes from the same query functions as the JSON
   (`?format=csv`); PNG is rendered in the browser from the chart instance with title and
   period burnt in. CSV is UTF-8, `,` separator, `.` decimal, ISO 8601 local time with offset,
   units in the header.
8. **Bounded reads.** Point cap of 2 000 per series, server-side resolution by span (1 min up
   to 2 days, 15 min up to 31 days, daily above), SQLite queries in a worker thread, response
   test on a year-sized synthetic database (target under 300 ms on amd64, CI budget 1 s).

Not decided here: the UI layout of the history views and the exact API paths (plan WPs).

## Consequences

- Positive: reproducible KPIs, costs immune to later tariff edits, cheap year views, what-if
  results about the real planner, no new write path.
- Negative: the backtest ignores controller and gate effects and unshifted device load, so it
  is an upper-bound style estimate; this is stated in the UI. `day_summary` is a second source
  of truth for past days (mitigated by the stored fingerprint and `computed_at`). A larger
  chart bundle (mitigated by the budget and per-view chunks).
- Follow-ups: `backend-engineer` (analysis, `day_summary` migration, backtest, endpoints),
  `optimizer-engineer` (planner reuse in the backtest), `frontend-engineer` (chart chunks,
  exports), `qa-engineer` (year-sized read test, bundle gate).

## Recommendation to the maintainer

Accept as is.
