# Phase 5 – Visualisation, analysis, expert mode

Status: **approved by delegation** (2026-11-23). The maintainer is away for several days and asked to
merge Phase 4 and continue with the remaining phases; the work proceeds on the recommended default of
every question in §6, and he can still change any of them on his return.
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §4.8 (explainability, what-if and backtesting), §4.9
(plan, history and analysis, decision log, export), §4.12 (expert mode), §4.14 (simulator and
backtesting); [`docs/roadmap.md`](../roadmap.md) Phase 5; ADR-0002 (ECharts), ADR-0003, ADR-0004,
ADR-0007, ADR-0011; [`docs/architecture.md`](../architecture.md) §7 (storage), §9 (frontend);
[`docs/plans/phase-4.md`](phase-4.md) (what Phase 4 built, incl. "Deviations during
implementation"); the Phase 5 items in [`docs/STATUS.md`](../STATUS.md).
Branches: stacked `phase-5/<letter>-<topic>` (§3), based on `main` (Phase 4 merged on 2026-11-23).

Phase 5 lets the user **see and judge** what SunCore does. Phase 4 produced plans, forecasts,
accuracy scores and a minimal plan view; Phase 3 produced the decision log. Phase 5 adds the full
plan chart with the device Gantt, a history and analysis area (autarky, self-consumption, cost and
savings against baselines, Sankey, heatmap, battery cycles, forecast skill), a decision log that
answers "why did X switch at Y", an expert mode that exposes every parameter with help, defaults,
ranges and reset (plus a parameter reference generated from the code), what-if runs and backtesting
against the recorded history, and CSV/PNG export. **Nothing in Phase 5 adds a write path to a
device**: the new backend code only reads the time series, the plan snapshots and the decision log.
The one exception is the expert mode, which writes configuration, never device set-points (§0,
decision 5).

Phase 4 was merged on 2026-11-23 (#25–#36); its acceptance on the maintainer's system (and the
Phase 3 live write) is still open and does not block Phase 5.

## 0. Context and decisions

What Phase 4 left for Phase 5 (verified in the Phase 4 tree on 2026-11-23):

- `timeseries.db`: `measurement` (48 h), `measurement_1m` (30 days), `measurement_15m` (unlimited,
  `derived` flag for the rebuild from HA statistics, 28 days back), `forecast` (versioned by issue
  time, 45 days), `price` (400 days). Keys in `suncore/core/measurements.py`: `site.grid_w`,
  `site.pv_w`, `site.battery_w`, `site.soc_pct`, `site.house_w`, `weather.temperature_c`,
  `device_power_key(id)`.
- `suncore.db`: `plan_snapshot` (zlib JSON, 200 rows / 14 days), `learned_param` (accuracy, bias,
  load comparison), `decision` (30 days / 200 000 rows, versioned payload with reasons).
- API: `/plan`, `/plan/status`, `/plan/replan`, `/prices`, `/forecast/pv`, `/forecast/load`,
  `/forecast/accuracy`, `/decisions`, `/control/state`.
- Frontend: `#/plan` with ECharts 5.6 (line and bar only, own chunk 177 kB gzip), `#/decisions`
  with device and outcome filters, Settings with config history. First-paint budget 125 kB gzip
  (119 kB used), everything except ECharts 250 kB (182 kB used).
- **Not there:** no savings figure, no baseline, no daily aggregates, no autarky/self-consumption
  numbers, no expert mode (the router has a reserved name only), no stored accuracy history beyond
  14 days, no way to run the planner on past data.

Decisions (each with its ADR in PR 5-A where it is architectural):

1. **KPIs and baselines are computed, not recorded, and frozen per day** (ADR-0014). A pure module
   `suncore/analysis/` (depends on `core` only, `mypy --strict`, same boundary rule as `planner/`)
   computes from 15-minute energy per signal: autarky, self-consumption, flows for the Sankey
   (fixed attribution rule: PV serves the house first, then charges the battery, then exports; grid
   charges the battery only for what PV cannot), battery equivalent full cycles, cost, and savings
   against **two baselines**: *no PV, no battery* (every kWh of load from the grid) and *PV without
   battery and without SunCore control* (house load as measured, no battery flows, surplus
   exported). The second is the honest "what SunCore adds" number, the first the "what the plant
   adds" number; the UI shows both and the difference as attribution. Device shifting is **not**
   undone (the unshifted device load is unknown); the UI says so. Standing charges and one-off fees
   are not part of cost (question 1).
2. **A nightly `day_summary` table** in `timeseries.db` (one row per local day: energies, flows,
   cost, baselines, cycles, per-source forecast MAE, coverage %, config fingerprint of the tariff
   used, `computed_at`) is written by the existing 03:30 job and kept unlimited (about 365 small
   rows a year). Year and month views read it; today and the last two days are computed live.
   Reason: `price` is kept 400 days and `measurement_1m` 30 days; costs must not change when the
   tariff configuration is edited later, and a year view must not scan 35 000 quarter hours per
   signal on a Pi 4. A day with coverage < 80 % is marked incomplete and excluded from averages.
   `day_summary` needs a `timeseries.db` migration (own `user_version`), no `suncore.db` change.
3. **Backtesting replays the real planner, not a re-implementation** (ADR-0014). The orchestrator
   lives in `suncore/sim/backtest.py` (composition layer, may import `planner` and `analysis`; the
   planner stays `core`-only). For each replan instant (default hourly) it builds `PlannerInputs`
   from what was *known then* (`latest_forecast_before(issue)`, stored prices, measured SoC), runs
   the planner with the alternative parameters, then **executes the first hour against the actual
   PV and load** with a simple battery model (efficiencies, limits, reserve), so a plan is judged
   on realised numbers, not on its own promises. The controller and the gate are **not** in the
   loop (the real devices' loads stay as measured); the result is "what the plan would have done
   with perfect execution" and is labelled that way. Three runs are shown together: *actual*,
   *what-if* and optionally *perfect foresight* (actual PV and load as forecast; the upper bound
   that shows what better forecasts could be worth). A run is a cancellable background job in a
   worker thread, one at a time, default window 14 days (cap 30), memory and CPU bounded (§5, R2).
4. **One parameter registry feeds the expert UI, the validation and the documentation**
   (ADR-0015). Every tunable has a `ParamSpec` (key, config path, type, unit, default, range, tags
   `expert|safety|restart|whatif`, help key `param.<key>.help` in the ICU catalogue, optional
   `see` link). The registry is generated from the Pydantic models in `core` (field metadata in
   `json_schema_extra`, same mechanism as the template JSON Schema, ADR-0006) plus a small explicit
   table for parameters that are not config fields today (controller tick, smoothing, hysteresis,
   solver budget and MIP gap, write rates; today partly environment variables or constants).
   `scripts/gen_params.py` writes `docs/reference/parameters.md` and `parameters.de.md` from it
   (`--check` in `check.sh` and CI, like `gen_compat.py`); a test fails when a registry entry has
   no catalogue text in both languages or a default outside its range.
5. **Expert mode never weakens the safety layer** (ADR-0015). Parameters tagged `safety` (min run /
   pause, max switches per hour, write rates, import limit, reserve guards) are shown, but their
   range is the hard bound of the invariants in `safety/` (the registry reads the same constants),
   changes need a confirmation dialog, land in the config history, and take effect through the
   same document validation and `gate.handover` path as a normal device edit. Raising a device
   mode stays exclusively with `PUT /operation/...` (Phase 3). `control-safety` reviews PR 5-E.
6. **Charts: one ECharts core, per-view modules** (ADR-0002 addendum inside ADR-0014). The plan
   view needs `custom` (Gantt) and `dataZoom`; the history needs `sankey`, `heatmap`, `visualMap`
   and `bar`; PNG export uses `getDataURL`. They are registered in per-view chunks
   (`echarts-plan`, `echarts-history`) on top of a shared `echarts-core`; the budget is
   **first paint 125 kB (unchanged), everything but ECharts 250 kB (unchanged), each view's chart
   chunks together ≤ 240 kB gzip** (plan view today 177 kB). Gated in CI by the existing bundle
   check (question 7).
7. **Exports come from the backend.** CSV is produced by the same query functions as the JSON
   (`?format=csv`), so a chart and its file always agree; PNG is rendered in the browser from the
   chart instance (canvas, with the title and the period burnt in by a small helper, no extra
   dependency). CSV: UTF-8, `,` separator, `.` decimal, ISO 8601 local time with offset, units in
   the header (W, Wh, currency/kWh) – question 3.
8. **Read cost is bounded**: every history endpoint has a point cap (≤ 2 000 points per series,
   server-side resolution by span: 1 min up to 2 days, 15 min up to 31 days, daily above), runs
   its SQLite query in a worker thread (not on the event loop), and has a response-time test on a
   year-sized synthetic database (target < 300 ms on amd64, CI budget 1 s).
9. **The demo gets 90 days of deterministic history** (simulator-generated `measurement_15m`,
   `price`, `forecast`, `day_summary`, a few plan snapshots and decisions), seeded only into the
   demo's throw-away database, so Playwright and the screenshots show real charts. Never `/data`.

## 1. Goals and acceptance criteria

Phase 5 is done when (roadmap Phase 5 table):

1. **Plan chart:** price (colour band and step line), PV P10–P90 band with P50 and the actual,
   load forecast and actual, planned and actual SoC, stacked import/export areas, battery power,
   **device Gantt rows** (planned allowed windows and levels from `DeviceBand`, actual on/off or
   level from the recorded device power), 24/48 h and a free range, **zoom and pan** (inside and
   slider, keyboard operable), **tooltips with the reasons** of the interval; "now" marker;
   comparison with an earlier plan snapshot (optional, 14 days); text alternative kept; axe clean.
2. **History:** period selector day/week/month/year with previous/next and a custom range; tiles
   for autarky, self-consumption, import, export, PV, consumption, cost, savings against both
   baselines (with attribution), battery cycles; charts: energy bars per bucket, cost vs baselines,
   **Sankey** per period, **heatmap** (consumption by hour × weekday), cycles and SoC range,
   **forecast skill per source over time**, device run times. Incomplete days are visible as such.
3. **Decision log UI:** filters by time range, device, layer, outcome, reason key and a free search
   of the rendered text; pagination; a "Why did X switch at Y?" panel for any entry: the decision
   with its reasons, the plan snapshot valid at that time (if still stored), the controller inputs
   (surplus, SoC, grid), the neighbouring decisions of the same device, and a jump to the plan
   chart at that instant; deep links from the Gantt and the device card.
4. **Expert mode:** a header toggle (persisted per browser) reveals every registered parameter in
   Settings, grouped by area (controller, safety limits, planner and solver, forecast and blending,
   risk and buffers, battery economics, tariff, storage and retention, debug), each with help text,
   unit, default, valid range, "modified" marker, per-parameter and per-group **reset to default**,
   validation with catalogue messages, search; debug views (raw signals, plan JSON, solver
   statistics, planner and forecast timings). The **generated parameter reference** exists in
   en and de and is checked in CI.
5. **What-if and backtesting:** the user picks a period (default last 14 days, max 30), changes
   parameters from the registry (risk level, buffers, battery limits and economics, grid-charge
   policy, self-sufficiency weight) and/or the tariff (fixed price, HT/NT, a price formula),
   starts a job, sees progress, can cancel, and gets: cost and autarky actual vs what-if, daily
   table, SoC and flow comparison chart, the "perfect foresight" bound on request, and an honest
   list of what the replay does not model. Results of the last 5 runs are kept (`suncore.db`,
   small JSON). A test shows that the replay with the *actual* parameters reproduces the recorded
   cost of a simulator history within a stated tolerance.
6. **Export:** CSV for every history, plan and decision-log table and chart; PNG for every chart;
   filenames carry the view and period.
7. **Cross-cutting:** all UI text in the en/de catalogues (lint, `check_reasons.py`); new reason
   keys for plan/analysis/backtest messages; bundle budgets of decision 6 hold; Pi 4 budgets hold
   (RSS < 300 MB incl. a running backtest, history endpoints as in decision 8, one hourly replan of
   the backtest ≤ 10 s on the reference scenario, benchmarked); `reviewer` sign-off per PR and a
   final review; `control-safety` sign-off for the safety-tagged parameters.

Exit (maintainer): looks through his own plan, history and decision log in the panel, can
answer "why did the battery not charge last night?" from the UI, runs one backtest of his last
two weeks and reads the result.

## 2. Work packages

Owner = subagent that implements it. Budget (maintainer's instruction): **Sonnet** for routine
backend, frontend and docs, **Haiku** for catalogue sync and mechanical work, a **stronger model**
only for the analytics and backtesting mathematics (`optimizer-engineer`) and the final
`reviewer` pass. At most two subagents in parallel; every WP ends with a commit and a push.

### WP1 – Contracts and ADRs · `architect`, reviewed by `reviewer`

- ADR-0014 "History analytics, baselines and backtesting" (decisions 1–3, 6, 8; options: compute
  live vs nightly summaries vs a recorded savings counter; backtest by replaying the planner vs a
  separate simplified model vs the full controller in the loop).
- ADR-0015 "Parameter registry and expert mode" (decisions 4, 5; options: hand-written table vs
  generated from Pydantic metadata vs JSON Schema only).
- Boundary test extended: `suncore.analysis` imports `core` only; `sim/backtest` may import
  `planner` and `analysis`; `api` calls them only through `app`.

### WP2 – Analytics core · `optimizer-engineer` (stronger model) for the maths, `backend-engineer` (Sonnet) for the plumbing

`suncore/analysis/`: `energy.py` (quarter-hour energies from means, sign conventions, coverage),
`flows.py` (the attribution rule, conservation property), `kpi.py` (autarky, self-consumption,
cycles via `BatteryModel`), `baselines.py` (the two baselines on a `PriceSeries` and the export
price), `cost.py`, `period.py` (day/week/month/year buckets in the region's time zone, DST days
of 23/25 h, ISO weeks). Pure, typed, hypothesis-tested: flows balance, autarky and self-consumption
in [0, 1], savings = baseline − actual, the PV-only baseline never exceeds the no-PV baseline when
prices are positive, energy-preserving bucketing across DST.

### WP3 – Storage and recording · `backend-engineer` (Sonnet)

`timeseries.db` migration: `day_summary`, `accuracy_day` (per source and day MAE/bias, fed by the
nightly accuracy job so forecast skill has a history longer than 14 days); nightly job extension
(compute yesterday, repair the last 7 days when coverage changed, recompute after a rebuild);
backfill of up to 365 days of site history from `recorder/statistics_during_period` (hourly,
`derived`, extends the 28-day rebuild of 4-B; cost only where prices were stored); verify that the
controller writes `device_power_key` for every controlled device and the device state, add
`device.<id>.on` (0/1) if missing; `suncore.db` migration for `backtest_run` (last 5 results).
Retention and size report extended; demo history seeding (decision 9).

### WP4 – History API · `backend-engineer` (Sonnet)

`GET /api/v1/history/summary?period=day|week|month|year&date=`, `/history/series` (keys, from, to,
resolution rules of decision 8), `/history/sankey`, `/history/heatmap`, `/history/cycles`,
`/history/forecast-skill`, `/history/devices`; each with `format=csv`; `GET /api/v1/plan/at?ts=`
(snapshot valid at a time, 404 when pruned), `GET /api/v1/plan/compare?a=&b=` (two snapshots,
interval diffs); `GET /api/v1/decisions` gets `from`, `to`, `layer`, `reason`, `q`, `offset`,
`GET /api/v1/decisions/{id}/explain`; all errors `api.error.*` catalogue keys; OpenAPI and
frontend types regenerated.

### WP5 – Parameter registry and expert API · `backend-engineer` (Sonnet), safety tags by `control-safety`

`suncore/core/params.py` (`ParamSpec`, registry, range check), metadata on the config models, the
explicit table for non-config parameters and their wiring through a new optional config section
`expert` (additive, own schema version, migrated; carries controller/planner/solver overrides that
today are constants or environment variables, defaults identical to today so nothing changes
unless set); `GET /api/v1/params` (specs + current + default + modified), `PUT /api/v1/params`
(validated against the specs, safety-tagged keys need `confirm_safety: true`),
`POST /api/v1/params/reset` (key or group); `scripts/gen_params.py` + `--check`; the §51 EEG
per-plant switch (`tariff.export.remunerate_negative`, additive, Phase 4 question 8) and the
optional reference spot source for the negative-price rule (Phase 4 question 3) as registry
parameters. Hot-apply where the owning service already rebuilds on config change (`Runtime`
rebuild path); the rest is tagged `restart` and the UI says so.

### WP6 – Backtest engine · `optimizer-engineer` (stronger model)

`suncore/sim/backtest.py`: input assembly from stored history (decision 3), rolling replan with
parameter and tariff overrides, execution against actuals (battery model with efficiencies, power
and SoC limits, reserve; grid and export limits), perfect-foresight mode, per-day results through
`analysis`, cancellation, progress, deterministic. Skips and reports days without stored forecasts
or prices. `scripts/bench_backtest.py`. Property tests: with perfect foresight and an unlimited
battery the cost is ≤ the baseline's; energy balance of the replay; same input → same output;
override-free replay on a simulator history reproduces the recorded cost within a tolerance that
is measured and written into the test and the user guide, not guessed. `backend-engineer` wires
the job runner (`app`: one job at a time, worker thread, memory guard, `POST /api/v1/backtests`,
`GET /backtests`, `GET /backtests/{id}`, `DELETE /backtests/{id}`).

### WP7 – Plan chart v2 · `frontend-engineer` (Sonnet)

Evolve `PlanChart.svelte`: actual overlays from `/history/series`, Gantt rows (custom series, one
row per controlled device and the battery's charge/discharge/lock bands), dataZoom synced across
the three panels, range presets, keyboard zoom and pan (arrow keys, `+`/`-`), tooltips with the
interval reasons through `ReasonList`, snapshot comparison, PNG/CSV buttons, extended text
alternative. Bundle split of decision 6. Vitest, Playwright, axe, screenshots `plan-*`.

### WP8 – History view · `frontend-engineer` (Sonnet)

`#/history`: period navigation, tiles, charts (bar, cost vs baselines, Sankey, heatmap, cycles,
forecast skill, device run times), incomplete-day marking, empty and collecting states, export
buttons, deep link to the plan chart for a day. Vitest, Playwright, axe, screenshots `history-*`.

### WP9 – Decision log UI · `frontend-engineer` (Sonnet)

Filters, URL-serialised state (shareable `#/decisions?device=…&from=…`), the "why" panel from
WP4's explain endpoint, links to plan chart and device card, CSV export, keyboard and
screen-reader behaviour of the table. Vitest, Playwright, axe, screenshots `decisions-*`
refreshed.

### WP10 – Expert mode UI · `frontend-engineer` (Sonnet)

Header toggle, Settings "Expert" area generated from `/api/v1/params` (no hand-written form per
parameter), search, grouping, modified markers, reset, safety confirmation, restart hint, debug
views (raw signals, plan JSON viewer with copy, solver statistics). Vitest, Playwright, axe.

### WP11 – What-if UI · `frontend-engineer` (Sonnet)

`#/whatif`: period picker, parameter overrides picked from the registry (only parameters tagged
`whatif`), tariff alternatives, run/cancel/progress, result comparison charts and table, list of
earlier runs, the "what is not modelled" box, CSV/PNG export. Vitest, Playwright, axe.

### WP12 – Docs, translations, STATUS · `docs-writer` (Sonnet), `i18n-sync` (Haiku)

User guides `docs/user/<lang>/history-and-analysis.md` (reading the numbers, baselines and what
they mean, Sankey rule, export), `expert-mode.md` (links into the generated reference),
`what-if-and-backtesting.md` (what is replayed, limitations, tolerance); quick start and both app
`DOCS.md` updated, architecture §7 (new tables) and §9 (routes, chunks), generated reference
published, all catalogue keys en/de (`sync-translations`), STATUS and roadmap, `UNVERIFIED` table,
screenshots from the demo.

## 3. Pull requests and order

Small stacked PRs, one topic each, `phase-5/<letter>-<topic>`, each based on the previous one.
Every PR gets the `reviewer` agent before it is opened (findings as `fix(...)` commits); 5-E
additionally `control-safety`. Open questions go into each PR under "Offene Fragen an den
Maintainer".

| PR | Content | Depends on |
|---|---|---|
| 5-A `docs(adr)` plan and ADRs | this plan, ADR-0014, ADR-0015, boundary test rule | Phase 4 |
| 5-B `feat(analysis)` analytics core | WP2 | 5-A |
| 5-C `feat(storage,app)` summaries and recording | WP3, demo history seeding | 5-B |
| 5-D `feat(api)` history, plan-at, decision explain | WP4 | 5-C |
| 5-E `feat(params)` registry, expert API, generator | WP5 (`control-safety` review) | 5-A only |
| 5-F `feat(frontend)` plan chart v2 | WP7 | 5-D |
| 5-G `feat(frontend)` history view and export | WP8 | 5-D |
| 5-H `feat(frontend)` decision log and "why" | WP9 | 5-D |
| 5-I `feat(frontend)` expert mode | WP10 | 5-E |
| 5-J `feat(sim,app)` backtest engine and job API | WP6 | 5-C, 5-E |
| 5-K `feat(frontend)` what-if | WP11 | 5-J, 5-I |
| 5-L `docs` guides, reference, STATUS, final review | WP12 | all |

```text
5-A ─► 5-B ─► 5-C ─► 5-D ─┬► 5-F
  │                       ├► 5-G
  │                       └► 5-H
  └──────► 5-E ─┬► 5-I ───────────┐
                └► 5-J (needs 5-C) ─► 5-K ─► 5-L
```

5-E has no dependency on 5-B…5-D and may be built in parallel (at most two subagents). To keep
rebasing simple the branch order is A, B, C, D, E, F, G, H, I, J, K, L; the arrows only say what a
PR needs. Merge in that order. After every WP: `./scripts/check.sh`, frontend
`npm run lint|test|build`, Playwright where the UI changed, commit and push.

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Analytics | pytest, hypothesis | flow balance, KPI bounds, baseline ordering, DST day buckets (23/25 h), ISO weeks, energy-preserving bucketing, coverage handling |
| Storage | pytest | `day_summary` and `accuracy_day` migrations, nightly compute and repair, backfill of 365 days, retention, a config change does not alter frozen days |
| History API | httpx ASGI | every endpoint, point caps, CSV equals JSON numbers, 404 for pruned snapshots, response time on a year-sized synthetic database (marked `slow`) |
| Parameter registry | pytest, hypothesis | every config field with `expert` metadata is registered; default within range; en and de help text present; `--check` of the generator; safety-tagged ranges equal the `safety/` constants; PUT validates and rejects out of range; reset restores defaults; defaults reproduce today's behaviour (golden test of `ControlSettings` and planner settings) |
| Backtest | pytest, hypothesis, `-m simulator` | determinism, balance, perfect foresight ≤ baseline, reproduction of the recorded cost within the measured tolerance, skipped days reported, cancel, one job at a time, memory guard |
| Benchmark | `scripts/bench_backtest.py`, CI `ubuntu-24.04-arm` as proxy | hourly replan on the reference scenario, 14/30-day job time and peak RSS, history endpoints; aarch64 stays `UNVERIFIED` until measured |
| Boundary | AST test | `analysis` imports `core` only; planner stays `core`-only; no `apply()`/`call_service` in Phase 5 code; the expert API never raises a mode |
| Frontend | Vitest | stores, query serialisation, chart option builders (pure functions tested without a canvas), registry form, CSV/PNG helpers, ICU messages |
| E2E | Playwright against the demo (desktop, phone, tablet; axe WCAG 2.1 AA) | zoom/pan by keyboard, Gantt tooltip reasons, history period navigation and export download, decision filters and "why", expert toggle/modify/reset/safety confirm, what-if run/cancel |
| i18n | `check_reasons.py`, ESLint rule, ICU check | no hardcoded strings, same keys en/de |
| Bundle | existing CI check | first paint ≤ 125 kB, rest ≤ 250 kB, chart chunks per view ≤ 240 kB gzip |
| Docs | markdownlint, Mermaid parse, `gen_params.py --check`, `gen_compat.py --check` | guides, reference |

No hardware in the session: the maintainer's own data (his Phase 4 history) is the acceptance test
for the numbers; the simulator history is the acceptance test for correctness.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Savings numbers mislead (baseline choice, unshifted device load unknown, standing charges missing, export tiers changed) | wrong trust in SunCore | two baselines with attribution, labels and a "how this is computed" panel, frozen daily cost with the tariff fingerprint, incomplete days marked, no hidden extrapolation (question 1) |
| R2 | Backtest overloads the Pi (CPU, RSS > 300 MB, delays the planner or the controller) | missed ticks, OOM | one job at a time, worker thread, one day of data in memory, hourly replans, default 14 days (cap 30), refuses to start while a plan run is active or the controller loop is late, short solve budgets, memory guard aborts above 200 MB RSS, benchmark in CI |
| R3 | The replay is not the real system (controller and gate absent, forecasts stored only 45 days, prices 400 days) | over-optimistic results | labelled "perfect execution", days without stored forecasts skipped and listed, perfect-foresight bound shown separately, tolerance test against a known history, list of what is not modelled |
| R4 | Expert parameters used to break safety or detune the controller | oscillation, import, wear | safety-tagged ranges = invariant constants, confirmation, config history and reset, defaults golden-tested, `control-safety` review, expert mode off by default |
| R5 | Registry drifts from the code | docs wrong, silent no-op parameters | generated from the models, tests tie every entry to a config field or a wired setting, `--check` in CI |
| R6 | ECharts additions (custom, Sankey, heatmap, dataZoom) exceed the budget | slow first paint on tablets | per-view chunks, shared core, tree-shaken registration, CI gate; fall back to a table plus a lighter chart if a chart does not fit |
| R7 | Year view and heatmap slow on a Pi | UI lag | `day_summary`, point caps, worker thread, response-time tests, lazy load per tile |
| R8 | Little history at the start (28-day rebuild, hourly data) | empty or coarse views | backfill to 365 days (hourly, `derived`), "collecting" states, coverage shown |
| R9 | `timeseries.db` migration blocks a downgrade | no rollback | release note, export before update (as in Phase 4); the tables are additive and an older version reads a newer file as "no time series" (Phase 4 rule) |
| R10 | Phase 4 findings from the live test change the data model | rework in 5-B…5-D | Phase 5 reads through the Phase 4 stores and API only; small PRs; rebase as in Phase 4 (R12) |
| R11 | Tooltips and Gantt unusable on phones and for keyboard users | accessibility failure | text alternative, keyboard zoom/pan, Playwright on phone and tablet, axe |
| R12 | Twelve stacked PRs drift | rebase work | merge in order, small PRs, no history rewrite |

## 6. Questions for the maintainer (Fragen an den Maintainer)

Der Maintainer ist einige Tage abwesend; **bis zur Antwort gilt jeweils der empfohlene Standard**.

1. **Ersparnis-Baseline:** Es gibt keine eindeutige „Ersparnis“. Vorschlag: zwei Baselines
   anzeigen – „ohne PV und Akku“ (alles aus dem Netz) und „PV ohne Akku und ohne
   SunCore-Steuerung“ – mit der Differenz als Aufteilung (Anlage vs. SunCore). Grundgebühren und
   Einmalkosten zählen nicht zu den Kosten; die Verschiebung von Geräten wird nicht
   herausgerechnet (unbekannt). **Empfehlung:** so umsetzen; die Hauptzahl ist „SunCore-Anteil“
   (zweite Baseline), mit Erklärungstext.
2. **Expertenparameter mit Sicherheitsbezug** (Mindestlauf-/Pausenzeit, Schaltspiele pro Stunde,
   Schreibraten, Reserve): sollen sie im Expertenmodus änderbar sein? **Empfehlung:** ja, aber nur
   innerhalb der harten Grenzen der Sicherheitsschicht, mit Bestätigungsdialog und Eintrag in der
   Konfigurationshistorie; der Expertenmodus ist standardmäßig aus.
3. **CSV-Format:** **Empfehlung:** ein Format (UTF-8, Komma, Punkt als Dezimaltrenner,
   ISO-8601-Zeit mit Offset, Einheiten im Kopf); eine Excel-DE-Variante (Semikolon,
   Dezimalkomma) erst auf Wunsch.
4. **Backtest-Grenzen auf dem Pi:** **Empfehlung:** Start nur von Hand, ein Lauf gleichzeitig,
   Standard 14 Tage, maximal 30 Tage, stündliches Neuplanen, Abbruch bei hohem
   Speicherverbrauch; die letzten 5 Ergebnisse bleiben gespeichert.
5. **Backtest ohne gespeicherte Prognosen:** Prognosen werden 45 Tage gespeichert, Preise 400
   Tage. **Empfehlung:** Tage ohne gespeicherte Prognose werden übersprungen und aufgelistet;
   optional („Perfekte Voraussicht“) wird die Obergrenze mit den tatsächlichen Werten berechnet.
6. **Historie über 28 Tage hinaus:** **Empfehlung:** beim ersten Start bis zu 365 Tage stündlich
   aus den HA-Langzeitstatistiken nachladen (als „abgeleitet“ markiert); Kosten nur dort, wo
   SunCore Preise gespeichert hat – kein Schätzen aus der heutigen Tarifkonfiguration.
7. **Diagramm-Budget:** ECharts mit Gantt, Sankey, Heatmap und Zoom braucht mehr als die
   heutigen 177 kB. **Empfehlung:** Budget je Ansicht auf 240 kB gzip (nachgeladen), die
   Erstanzeige bleibt bei 125 kB.
8. **Phase-4-Reste, die in Phase 5 passen:** (a) Schalter pro Anlage „wird auch bei negativen
   Preisen vergütet“ (§51 EEG, Frage 8 aus 4-C), (b) optionale Spot-Referenzquelle für die
   Negativpreis-Regel bei All-in-Tarifen (Frage 3 aus 4-C). **Empfehlung:** beide als
   Experten-Parameter in 5-E einbauen (Standard unverändert); die Kalibrierladung (nur
   Konfigurationsfeld, der Planer nutzt sie nicht) bleibt draußen und geht nach Phase 6.
9. **Demo-Daten für Screenshots:** **Empfehlung:** der Demo-Modus erzeugt deterministisch 90 Tage
   Historie in seiner Wegwerf-Datenbank (nie `/data`).
10. **Modellwahl:** wie angewiesen Sonnet für Backend/Frontend/Doku, Haiku für Übersetzungen und
    Mechanisches, ein stärkeres Modell nur für die Analyse- und Backtest-Mathematik (5-B, 5-J)
    und die Abschlussprüfung. **Empfehlung:** so; ist ein stärkeres Modell nicht verfügbar, 5-B
    und 5-J mit Sonnet und doppelter Review-Runde.

## 7. Out of scope for Phase 5

Anything that writes to devices or adds control logic (Phase 6: wallbox/EV, heat pump, shiftable
programs, peak shaving, export limiting, §14a, backup power, MQTT entities, notifications); the
Lovelace cards and the docs site (Phase 7); running the controller and the safety gate inside the
backtest (the replay is plan-level, decision 3); thermal models of climate and water heater in
the planner; calibration-charge planning (Phase 6); CO₂ weighting and CO₂ history; standing
charges and tax-return reports; dynamic export prices; sharing or uploading results; per-user
accounts or roles for expert mode; ML beyond what Phase 4 shipped; a diagnostics page for
`timeseries.db` size and the too-new-database hint (small Phase 4 follow-ups, taken into 5-C only
if cheap); built-in price and PV fallbacks (Phase 6).

## 8. Deviations during implementation

- **5-B:** a seventh module `suncore/analysis/summary.py` (`PeriodSummary`: flows, KPIs, savings and
  coverage of one bucket, DST-aware expected slots) bundles the pieces for the nightly job and the
  history API. The attribution rule also covers battery discharge: it serves the house first, any
  remainder is exported (`battery_to_grid`). Cost is the grid exchange only; battery degradation is
  not part of it.
- **5-C:** `accuracy_day` has one row per source, kind (`pv`/`load`), day **and horizon bucket**
  (not only per source and day), so the forecast-skill view can show the 0-6 h error and still
  weight by `n`. A frozen day is rewritten only when more slots were measured than before (never
  because of a tariff edit); cost columns are NULL for days without stored prices. The backfill runs
  in windows of 60 days (a series is limited to 20 000 points) and, for installs that rebuilt
  before, once more on the next start (marker `history_backfilled_at`). The demo's plan snapshots
  come from the planner's reference scenario (the maintainer's house), not from the demo's own
  devices. `suncore.storage.backtests` also ships the small repository (start/finish/prune to 5) so
  5-J only adds the engine.
- **5-D:** decision 8 asks for 1 min up to 2 days, 15 min up to 31 days and at most 2 000 points;
  both cannot hold (31 days of 15 min = 2 976 points). The series endpoint takes the finest
  resolution the span allows that stays within 2 000 points, with an hourly step in between
  (`1m` -> `15m` -> `1h` -> `1d`; `resolution_reason` says why), and `1m` only within the 30 days
  the 1-minute table is kept. `layer` of the decision filter is the intent's origin (planner,
  controller, user, safety); the `layer` column of the table is always `safety`. `q` searches the
  device id and the stored decision (reason keys and arguments), not rendered text. Row ids and the
  total of `/decisions` travel in headers so the list body keeps its shape. `plan/at` returns the
  newest snapshot created at or before `ts` even when its intervals have run out
  (`interval_index` is then `null`). The explain inputs are the raw site readings (grid, PV, house,
  battery, SoC, the device's power and state), not a derived surplus.
- **5-G:** no free custom range (`/history/summary` takes a period and a day only); the date field
  jumps to any day's period instead. The heatmap shows at least the last 28 days, cycles and
  forecast skill the 14 days up to a single day (the caption names the range). The deep link to
  the plan chart is `#/plan?day=YYYY-MM-DD`; the plan view reads it in 5-F. The shared chunks
  `echarts-core`/`echarts-history` of decision 6 are not split yet: the history charts register
  into the one existing `echarts` chunk (194 kB of 200 kB). First paint is at 124.9 of 125 kB
  because the catalogues are part of it; the next PR with new strings needs lazy per-view or
  per-locale catalogues. The router has a `history` route and parses the hash query. The
  mobile navigation wraps (five links no longer fit 375 px).

- **5-H:** the plan chart does not read `#/plan?at=<ts>` yet (5-F); the "Why?" panel links to it and
  shows the plan interval itself, so the deep link only becomes a highlight once 5-F lands. The
  date filter sends a bare date (the server reads it in the site zone); `to` is inclusive in the UI
  and sent as the next day (exclusive on the server). The log reloads on the live feed only on the
  first page. The old `controlApi.decisions` stays for other callers; the log uses
  `decisionPage`. First paint is 124.7 kB of the 125 kB budget: both language catalogues sit in
  the first-paint chunk, so every new key counts; 5-G/5-I will exceed it unless the catalogue is
  split per language or per view (not done here).
- **5-H (i18n loading):** English stays eager (typed key source and fallback, so no raw key can
  show); German is its own chunk (`catalogue-de`), fetched before the first render when the browser
  asks for German and otherwise when `region.language` or the wizard switches (English shows until
  it arrived). First paint fell from 124.7 to 98.8 kB. `Translator.set()` adds a catalogue; lazy
  extras such as `params.<lang>.json` (5-E) compose through the same `set`/`extend` idea and keep
  their own chunks (`vite.config.ts` only names the `en`/`de` main files).
