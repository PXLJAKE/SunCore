# Phase 4 – Forecasts, tariffs, planner, battery

Status: **approved** by the maintainer on 2026-10-16 (answers in §6: commit and push after every
WP, Opus takes the Fable roles, battery "as full as possible", grid charging by a price level and
a schedule instead of a fixed night window, calibration charge optional and off; defaults
otherwise)
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §3 (two control layers), §4.1 (battery, weather),
§4.2, §4.3, §4.4, §4.5 (battery rules of the surplus distribution), §4.8, §4.9 (plan view, only
the part Phase 4 needs), §4.10 steps 4–6 and 10, §5, §8, §9, §10;
[`docs/roadmap.md`](../roadmap.md) Phase 4; ADR-0003, 0004, 0006, 0011, 0012;
[`docs/research/integrations.md`](../research/integrations.md),
[`kostal-plenticore-modbus.md`](../research/kostal-plenticore-modbus.md),
[`solver-on-aarch64.md`](../research/solver-on-aarch64.md),
[`device-integrations.md`](../research/device-integrations.md); the "Phase 4 follow-ups" and the
`UNVERIFIED` table in [`docs/STATUS.md`](../STATUS.md).
Branch: `claude/zealous-mayer-j5oda9` (session branch, see §3 and question 1).

Phase 4 gives SunCore foresight. Adapters read PV forecasts and prices from the integrations the
user already runs, a load forecast is learnt from Home Assistant's statistics, and a planner
(HiGHS LP/MILP with a heuristic fallback) computes a 24–48 h schedule in 15-minute steps. The plan
fills the `Plan` contract the Phase 3 controller already consumes (`DeviceBand`, `BatteryBand`,
`grid_import_limit_w`), so the controller keeps the last word in real time and the safety gate
stays the only writer. The battery becomes a controllable asset through battery adapters, and
every plan interval carries structured reasons that the UI renders as "Why?". **Shadow mode stays
the default; the battery starts in shadow like every device.**

Phase 3 is implemented but its acceptance (shadow run and one device to automatic on the
maintainer's system) is still open. Phase 4 is built on top of `main` in parallel; nothing in
Phase 4 changes the Phase 3 write paths of devices (question 2).

## 1. Goals and acceptance criteria

Phase 4 is done when (roadmap Phase 4 milestones):

1. **Forecast adapters:** Solcast (attributes or the read-only query action, never the Solcast
   API), Forecast.Solar (core, via `energy/solar_forecast`), Open-Meteo Solar Forecast (HACS),
   a generic attribute adapter (mapping per template), and the built-in fallback behind an
   explicit opt-in (question 5). Several sources at once are blended by measured skill; a bias
   calibration per hour of day and season is learnt; clipping (inverter limit, export limit) is
   recognised; accuracy (MAE, bias per source and horizon) is stored.
2. **Load forecast:** base-load profile from HA long-term statistics (`recorder/statistics_during_period`),
   weekday/holiday (optional `workday` binary sensor), optional temperature from a `weather`
   entity (`weather.get_forecasts`); exponential smoothing; the draw of controlled devices is
   subtracted (no double counting).
3. **Tariffs:** dynamic import from Tibber, Nord Pool, ENTSO-E, EPEX Spot (and Octopus if its
   source can be verified), fixed and HT/NT (time windows, seasonal); price formula
   (spot → end price with grid fee incl. time-variable grid fees §14a module 3, levies, margin,
   VAT); 15- and 60-minute grids; export fixed, tiered by kWp share (blended rate) and none;
   negative-price region rule (`de`: no remuneration → avoid export).
4. **Planner:** HiGHS model (`highspy`), heuristic fallback (always available, also when the
   solver times out or the model is infeasible, with a UI hint), warm start from the previous
   plan, time budget (default 8 s, MIP gap 1 %), runs every 15 min and on events. Benchmark:
   the maintainer's reference scenario solves in < 10 s on aarch64 (CI arm runner as proxy,
   see R3).
5. **Battery strategies:** self-consumption (default), target SoC by time (P10 PV in the target
   constraint), grid charging on a dynamic tariff only when the spread exceeds efficiency loss +
   degradation + margin (option "night only", already in `goals`), grid charging on a fixed
   tariff only with an HT/NT spread or in an emergency (off by default), discharge lock, reserve
   separate from BMS drift, max-SoC limit, periodic calibration charge, cycle counter,
   degradation cost in currency/kWh.
6. **Battery adapters:** generic HA entities (template kind `battery`: number/select/switch/
   actions), Kostal Plenticore through the core `kostal_plenticore` integration (min SoC as
   discharge lock/reserve), Kostal Modbus §3.5 limits only with the verified register map and
   behind question 3. Read-back after every write, write only on change, release = hand the
   battery back to the inverter's own control.
7. **Explanations:** every plan interval carries `Reason(key, args)` (price, forecast, target,
   limit, rule); the UI renders them in a plan view and in the Now view's "next actions".
8. **`control-safety` review** of every change to `safety/`, the controller's battery path and
   the battery adapters' write paths, documented in the PR (invariant table).

Exit (roadmap): plan with battery strategy and "Why?" on the maintainer's system, battery
control in shadow mode, then assisted.

## 2. Work packages

Owner = subagent that implements it. Budget rule (CLAUDE.md, your instruction): **Fable only for
`optimizer-engineer` (planner, forecast mathematics, battery economics), `control-safety`
(controller battery path, safety, battery write paths) and the final `reviewer`**; Sonnet for
adapters, backend, simulator, frontend and docs; Haiku for catalogue sync. At most two subagents
in parallel.

### WP1 – Verify interfaces · `ha-integration` (skills `verify-ha-api`, `new-adapter`)

Read from the GitHub sources (the doc sites are unreachable, see `research/README.md`) and record
in `research/integrations.md` / `research/ha-websocket-api.md`, each fact with commit and path:

- HA: `recorder/statistics_during_period` (request/response, `period`, `types`, units),
  `energy/solar_forecast` (response shape, `wh_hours`), `weather.get_forecasts` (response,
  `hourly`), `workday` binary sensor, `number.set_value`, `select.select_option`,
  `modbus.write_register` (fields, multi-register values) for question 3.
- Integrations: Forecast.Solar `energy.py`; Open-Meteo Solar Forecast (domain, attributes);
  Nord Pool `get_prices_for_date`; ENTSO-E and EPEX Spot attributes; Octopus `rates`;
  `kostal_plenticore` `Battery:MinSoc`/charging mode entities (translation keys, ranges).
- Built-in fallbacks: Open-Meteo `global_tilted_irradiance`/`tilt`/`azimuth` and its terms;
  Energy-Charts `/price` and aWATTar `marketdata` and their terms. If unreachable they stay
  `UNVERIFIED` and are not shipped (question 5).

### WP2 – Contracts · `optimizer-engineer` (Fable), reviewed by `architect`

- `core/series.py`: `TimeSeries` (aware start/step, values, unit), `QuantileSeries`
  (P10/P50/P90, monotone), `PriceSeries` (import/export per interval, resolution 15/60 min,
  source, `price_includes`), resampling to the 15-minute planning grid (energy-preserving for
  power/energy, step-hold for prices).
- `core/forecast.py`: `ForecastSource` protocol (`fetch(window) -> QuantileSeries`), accuracy
  record; `core/tariff.py`: `PriceSource` protocol, `PriceFormula`, HT/NT windows;
  `core/battery.py`: `BatteryModel` (capacity, efficiencies, limits, reserve, BMS-drift margin,
  degradation cost), `BatteryAction` for the gate (charge/discharge limits, min SoC, grid charge
  power) – **`BatteryAction` is reviewed by `control-safety`**.
- `Plan` v2 (additive to `core/control.py`): per interval the expected flows (PV P10/P50/P90,
  load, import, export, battery power, SoC), prices, the battery band, reasons; `Plan.source`
  (`solver`/`heuristic`/`default`) and solver statistics. The controller keeps reading
  `DeviceBand`/`BatteryBand`/`grid_import_limit_w` unchanged.
- Config documents (additive, migrated): `ForecastConfig` v2 (several sources, weights auto/
  manual, safety discount), `TariffConfig` v3 (formula components, HT/NT windows, seasonal),
  section `battery` (adapter template id, entities, model parameters, strategy options),
  `goals` v2 (night window for grid charging, calibration interval, conservative planning on/off).
- Template schema v1 additions (additive, schema v1 changelog): kind `battery` (ADR-0012 already
  names it), forecast `series` mapping for the generic attribute adapter, tariff HT/NT.

### WP3 – Storage and statistics · `backend-engineer`

- `/data/timeseries.db` (ADR-0004; `backup_exclude` in both `config.yaml`, via `ha-integration`):
  own migrations; tables for measurements (controller resolution 48 h, 1-min 30 days, 15-min
  unlimited), forecasts versioned by issue time, prices; nightly downsample + vacuum; rebuild of
  15-min history from `recorder/statistics_during_period` when the file is missing.
- `suncore.db` migration `0004`: `plan_snapshot` (compressed JSON, last N plans + retention),
  learnt parameters (forecast bias tables, load profiles, blending weights). Schema 4: 0.4.x
  refuses newer databases → export before update (release note).
- Rate limiter per (domain, service) with try-acquire/deadline for `call_action_response`
  (follow-up A3 from Phase 1) before the first adapter calls an action; boundary rule 7 admits
  the adapter wiring.

### WP4 – Tariff adapters · `backend-engineer` (skill `new-adapter`), maths by `optimizer-engineer`

`suncore/tariff/`: adapters Tibber (`tibber.get_prices`), Nord Pool, ENTSO-E, EPEX Spot,
Octopus (only if WP1 verifies it), generic price-list attribute, fixed and HT/NT; price formula;
export fixed/tiered (blended rate by kWp share)/none; negative-price rule from the region
template; refresh on the integration's update, never more than the rate limiter allows. Fixtures
copied from the verified source shapes. New templates per adapter with `source` and a conformance
scenario (`new-device-template` skill).

### WP5 – PV forecast · `device-adapters` (adapters), `optimizer-engineer` (Fable, blending/calibration)

`suncore/forecast/pv/`: Solcast (`detailedForecast` attributes, `query_forecast_data` as
fallback), Forecast.Solar, Open-Meteo Solar Forecast, generic attribute, built-in fallback (only
after question 5); blending weighted by recent skill (inverse MAE over 14 days, minimum weight,
manual override); bias calibration per hour of day × season × clear-sky index bucket (bounded,
shrinks to 1 with little data); clipping detection (inverter/export limit plateaus excluded from
the error statistics); accuracy bookkeeping in `timeseries.db`.

### WP6 – Load forecast · `optimizer-engineer` (Fable)

`suncore/forecast/load.py`: house base load = house − controlled devices (their estimates from
the Phase 3 power models); profile per weekday type (workday/weekend/holiday) × 15-min slot with
exponential smoothing; temperature regression term when a weather entity is mapped; P10/P90 from
residuals; cold start from HA statistics (WP3), else a flat profile from the current mean with a
wide band and a UI hint. No ML model in Phase 4 (aarch64 footprint).

### WP7 – Planner · `optimizer-engineer` (Fable)

`suncore/planner/`: `model.py` (sparse matrices for `highspy`, Protocol for the backend so tests
can use a reference solver), `heuristic.py`, `explain.py`, `run.py`.

- Variables per interval: import, export, battery charge/discharge (separate efficiencies), SoC,
  PV curtailment (only where an export limit or negative prices make it useful, informational
  until the export-limit chain in Phase 6), controllable device energy (band per device).
- Constraints: energy balance, SoC bounds incl. reserve + BMS-drift margin, battery power limits,
  grid import limit, static export limit (13.5 kW case), target SoC by time on **P10** PV,
  grid charging only in allowed windows (night window, fixed tariff off by default), discharge
  lock where it pays; binaries only where needed (no simultaneous charge/discharge only if the
  LP relaxation exploits it with negative prices; device min run later).
- Objective: import cost − export revenue (with negative prices and the region rule) + degradation
  + switching penalty + soft target penalties; P50 in the objective.
- Warm start, time budget, MIP gap; on timeout use the incumbent if feasible, else the heuristic;
  infeasible → relax soft targets, then heuristic, reason `planner.fallback.*`.
- `explain.py`: per interval the binding constraints and price comparisons become `Reason`s, e.g.
  `planner.battery.grid_charge` {from, to, price, later_price, later_time, pv_p10_kwh}.
- Outputs `Plan` v2 with `BatteryBand` (min/max power, SoC corridor, `charge_from_surplus`) and
  `DeviceBand` preferences; the controller's surplus rule "battery first / consumers first if the
  forecast still reaches the target" now follows the plan.
- Property tests (hypothesis): balance holds, SoC within bounds, never worse than the heuristic,
  no grid charging outside allowed windows, no export at negative prices when avoidable.
- Benchmark script `scripts/bench_planner.py` with the maintainer's reference scenario
  (17 kWp east-west, 29.6 kWh, Tibber 15-min prices, export limit 13.5 kW, tiered EEG).

### WP8 – Runtime and scheduler · `backend-engineer`

Planner run every 15 min and on events (new prices, forecast update, deviation above threshold,
config change, override), in a worker thread with the time budget, at most one run at a time;
the controller gets the new plan atomically; plan snapshots stored; `/health` unaffected by
planner failures (status in the API). API: `GET /api/v1/plan` (current plan with reasons and
statistics), `POST /api/v1/plan/replan`, `GET /api/v1/forecast/accuracy`, `GET /api/v1/prices`;
live feed `plan` message. OpenAPI and frontend types regenerated. Demo: simulator prices and
forecasts so the demo shows a real plan.

### WP9 – Battery control · `control-safety` (Fable), adapters by `device-adapters`

- Controller: translates the plan's `BatteryBand` into `BatteryAction` intents (on change only,
  hysteresis on power levels); the existing surplus share follows `charge_from_surplus`.
- Safety: new invariants (property-tested): never discharge below reserve + drift margin by
  SunCore; grid charging only from a plan interval that allows it, within the import limit and
  the night window; charge/discharge limits within battery and inverter limits; write rate and
  write-on-change; battery release = restore the adapter's baseline (inverter self-control);
  watchdog: stale SoC/battery power while the grid meter moves pauses battery control; the
  hung-inverter case (BRIEF §9) releases and stops writing. "No SunCore-caused import" keeps
  holding for devices; the only allowed import is planned grid charging of the battery.
- Adapters `suncore/devices/battery/`: `generic` (template kind `battery`, roles: min-SoC number,
  max charge/discharge numbers, mode select, force-charge switch/action), `kostal_plenticore`
  (core integration: min SoC → discharge lock/reserve, charging mode select; no charge set-point
  over REST), `kostal_modbus` §3.5 limits with fallback timer (question 3; shadow/assisted only
  until the timeout is measured on the maintainer's inverter).
- Simulator: battery with BMS drift, inverter self-consumption logic as the fallback behaviour,
  Kostal-like fallback timer, scenarios `negative_prices`, `price_spike`, `winter_fog_target`,
  `grid_charge_night`, `inverter_hang_battery`, `reference_house` (maintainer's system).

### WP10 – UI · `frontend-engineer`

- Plan view `#/plan` (minimal for Phase 4, the full chart is Phase 5): ECharts loaded on demand
  (own chunk), 24/48 h, price band, PV P10–P90 band + P50, load forecast, planned SoC and battery
  power, import/export; tap/hover on an interval shows its reasons; solver/heuristic badge.
- Now view: "next planned actions" with "Why?"; battery card (mode switch, proposals for
  assisted, release/emergency stop like the devices).
- Wizard: step 4 several forecast sources (auto-detected), step 5 battery adapter or "observe
  only" with model parameters, step 6 price formula and HT/NT, step 8 battery strategy options,
  step 10 first plan as preview. Settings pages follow.
- Vitest, Playwright E2E against the demo, axe; screenshots `plan-*` (light/dark, phone/tablet,
  en/de) via `demo-screenshots`. Bundle budget 250 kB gzip for first paint holds (ECharts lazy).

### WP11 – Docs, translations, STATUS · `docs-writer`, `i18n-sync` (Haiku)

User guide `docs/user/<lang>/planning-and-battery.md` (en/de: forecasts and blending, tariffs and
price formula, how the planner decides, battery strategies, battery adapters and how to enable
external battery control, shadow → assisted walk-through, known limitations), quick start and
app `DOCS.md` updated, architecture §planner, compatibility list regenerated, all new keys en/de
(`sync-translations`), STATUS, roadmap, `UNVERIFIED` table.

## 3. Pull requests and order

Default (question 1): all work on the session branch `claude/zealous-mayer-j5oda9`, one commit
series per WP and **one PR**; if the maintainer allows extra branches, the same commits are split
into stacked PRs:

| PR | Content | Depends on |
|---|---|---|
| 4-A `feat(core,storage,forecast,tariff)` | WP1–WP6 | – |
| 4-B `feat(planner,app)` | WP7, WP8 | 4-A |
| 4-C `feat(safety,controller,devices)` battery | WP9 | 4-B |
| 4-D `feat(frontend),docs` | WP10, WP11 | 4-C |

```text
WP1 ─► WP2 ─┬► WP3 ─► WP4 ─┐
            ├► WP5 ────────┼─► WP7 ─► WP8 ─► WP9 ─► WP10 ─► WP11
            └► WP6 ────────┘
```

I integrate, run `./scripts/check.sh` and the frontend checks after every WP and commit per WP
(Conventional Commits; CHANGELOG via release-please, no hand edits). Before the PR the `reviewer`
agent (Fable) reviews the whole change; its findings are fixed in separate `fix(...)` commits.
`control-safety` signs off WP9 with the invariant table in the PR description.

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Series/contracts | pytest, hypothesis | resampling preserves energy, quantiles monotone, price grid 15/60 mixed |
| Adapters | pytest with fixtures from verified source shapes | Solcast, Forecast.Solar, Open-Meteo SF, generic, Tibber, Nord Pool, ENTSO-E, EPEX, fixed/HT-NT; missing/partial data; DST days (23/25 h) |
| Blending/calibration/load | pytest, hypothesis | weights sum to 1, bias bounded, clipping excluded, controlled loads subtracted |
| Planner | hypothesis + reference solver | balance, SoC bounds, objective ≤ heuristic, grid charging windows, negative prices, infeasible → fallback, time budget respected |
| Benchmark | `scripts/bench_planner.py`, CI job on `ubuntu-24.04-arm` | reference scenario 48 h; budget see R3 |
| Safety | hypothesis | battery invariants, gate never writes the battery in shadow/assisted without confirm, release paths |
| Simulator | `-m simulator` | scenarios of WP9, 7-day run with planner + controller, fail-safe of the battery |
| Storage | pytest | migrations 0004 + timeseries.db, rebuild from statistics, retention |
| Boundary | AST test | planner imports `core` only; battery adapters' `apply()` only via the gate; actions only through the rate limiter |
| API | httpx ASGI | plan, replan, accuracy, prices |
| Frontend | Vitest, Playwright, axe | plan view, reasons, battery card, wizard steps |
| Coverage | pytest-cov | ≥ 80 % core/safety/planner/controller (aim ≥ 90 % planner and safety) |

No hardware and no Docker daemon in the session: image and smoke test run in CI; the maintainer's
shadow and assisted run is the acceptance test.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | A battery write empties the battery or charges from the grid at the wrong time | money, backup reserve | battery starts in shadow, assisted needs confirmation, invariants property-tested, reserve + BMS drift margin, `control-safety` sign-off, inverter fallback timer (§3.5) as hardware watchdog |
| R2 | Kostal Modbus timeout and enabling unverified | adapter does nothing or does not fall back | §3.5 limits with documented fallback first; §3.4 set-points out of scope; measurement by the maintainer before automatic |
| R3 | Solve time on a Pi 4 > 10 s | stale plans | LP first, few binaries, warm start, time budget 8 s + heuristic; CI arm runner is faster than a Pi 4 → CI budget 3 s as a proxy; the maintainer runs amd64 (KVM), a Pi 4 measurement needs a volunteer |
| R4 | `highspy` (+ NumPy) enlarges the image and memory | RSS budget 300 MB | musllinux wheels verified (research); smoke-test memory gate stays; solver in a thread, model freed after the run |
| R5 | Forecast adapters rely on attributes that change between integration versions | missing plan | tolerant parsers, source version in `tested_with`, fallback to other sources or flat profile with UI hint |
| R6 | Built-in fallbacks need internet and unverified terms | licence/terms breach, cloud dependency | opt-in, off by default, attribution shown, only after WP1 verified the terms (question 5) |
| R7 | Load forecast poor at start (little history) | bad battery decisions | rebuild from HA statistics, wide P10/P90, conservative target on P10, UI hint |
| R8 | Phase too large for one review | slow review, missed bugs | WP commits, stacked PRs if allowed (question 1), reviewer per PR |
| R9 | DB schema 4 blocks a downgrade | user cannot roll back | release note, export before update |
| R10 | Phase 3 acceptance finds controller issues while Phase 4 builds on it | rework | Phase 4 changes the controller only in the battery path and the plan input; fixes from the live test are merged first |
| R11 | ECharts breaks the bundle budget | slow first paint | lazy chunk for the plan view only |

## 6. Questions to the maintainer

Bold = default if there is no answer.

1. **PR structure:** the session may only push `claude/zealous-mayer-j5oda9`. **One PR with one
   commit series per WP** – or may I push stacked branches `phase-4/a…d` (four PRs as in
   Phases 1–3)?
2. **Phase 3 not yet accepted:** **build Phase 4 in parallel; merge only after your Phase 3
   acceptance**, fixes from your live test go first.
3. **Kostal battery control path** (you have SunSpec Modbus read-only and the core
   `kostal_plenticore` integration):
   (a) **Phase 4 ships the generic adapter and `kostal_plenticore` (min SoC = discharge
   lock/reserve, no grid charging over REST); the Modbus §3.5 limits are written through Home
   Assistant's `modbus.write_register` action (you add a `modbus` hub in HA), shadow/assisted
   only until the fallback timer is measured on your inverter**;
   (b) SunCore talks Modbus TCP itself (`pymodbus`, opt-in) – the brief's non-goal "no own
   Modbus implementation" speaks against it;
   (c) no Modbus in Phase 4. Is "external battery management via Modbus" enabled in your
   inverter (installer menu)?
4. **Model budget:** in Phase 3 the Fable credits ran out and Opus did the Fable work. **Same
   rule again if Fable is unavailable**?
5. **Built-in fallbacks** (Open-Meteo PV, Energy-Charts/aWATTar prices) need internet from the
   app and their APIs/terms could not be verified from the session: **only ship them if WP1 can
   verify them, then opt-in and off by default; otherwise move them to Phase 6**.
6. **Battery economics defaults** (all expert parameters, shown with their source/explanation):
   **charge and discharge efficiency 95 % each, degradation 0.04 €/kWh throughput, grid-charge
   safety margin 0.03 €/kWh, BMS-drift margin 5 % above the reserve** (your BMS sags from 10 %
   to 6–7 %). Do you know BYD HVM figures you want instead?
7. **Night window for "grid charging only at night":** **22:00–06:00 local**, configurable.
8. **Your EEG tiers** (10 kWp + rest): the region `de` template has no official rates (not
   invented). **You enter the two rates in the tariff step**; or do you give me the BNetzA table
   to cite?
9. **Tibber `price`:** **treated as the all-in end price** (no formula on top). Correct for your
   contract?
10. **Plan view scope:** **minimal plan chart in Phase 4 (price, PV band, load, SoC, battery,
    reasons), the full chart with Gantt/zoom in Phase 5**.
11. **Calibration charge:** **off by default; if on, once every 30 days to 100 %, scheduled into
    a sunny day by the planner**.

### Answers (2026-10-16)

- Every WP ends with a commit **and a push**, so nothing is lost when the session's usage quota
  runs out.
- **Opus takes the Fable roles** (`optimizer-engineer`, `control-safety`, `reviewer`, advisor).
- Question 1 (PR structure), 2, 3, 5, 6, 8, 9, 10: defaults.
- Question 11: the calibration charge is built for other users, **off by default**.
- Battery strategy of the maintainer: **"keep the battery as full as possible"** – a strategy
  option `prefer_full` (target 100 % as early as the P10 forecast allows, PV surplus to the battery
  before consumers until full, discharge only for the house); self-consumption stays the default
  for new installations.
- Question 7: instead of a fixed night window, grid charging is allowed by **a price level**
  (charge only when the import price is at or below a user-set level, currency/kWh) **and a
  schedule** (weekly time windows, default: always allowed inside the price level). The spread rule
  (efficiency + degradation + margin) still applies on dynamic tariffs; the existing
  `goals.grid_charge_night_only` is migrated to a schedule 22:00–06:00. Grid charging stays off
  until the user sets a level or a schedule.

## 7. Out of scope for Phase 4

Full plan chart with device Gantt, zoom/pan, history, savings vs baselines, what-if and
backtesting (Phase 5); export limiting through the inverter, peak shaving with load shedding,
§14a control signal, backup-power awareness, wallbox/EV, heat pump, shiftable programs, MQTT
entities and notifications (Phase 6); Kostal §3.4 set-points and MDC; V2H/V2G; ML load models.
