# Phase 4 – Forecasts, tariffs, planner, battery

Status: **revision 2 approved** by the maintainer on 2026-10-16 (answers in §6). Revision 1 was
approved earlier the same day. Revision 2 merges the concept "Prognose, Puffer und Akkuplanung"
from a separate chat (§0) and splits the work into small stacked PRs (§3).
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §3 (two control layers), §4.1 (battery, weather),
§4.2, §4.3, §4.4, §4.5 (battery rules of the surplus distribution), §4.8, §4.9 (plan view, only
the part Phase 4 needs), §4.10 steps 4–6 and 10, §5, §8, §9, §10;
[`docs/roadmap.md`](../roadmap.md) Phase 4; ADR-0003, 0004, 0006, 0011, 0012;
[`docs/research/integrations.md`](../research/integrations.md),
[`kostal-plenticore-modbus.md`](../research/kostal-plenticore-modbus.md),
[`solver-on-aarch64.md`](../research/solver-on-aarch64.md),
[`device-integrations.md`](../research/device-integrations.md); the "Phase 4 follow-ups" and the
`UNVERIFIED` table in [`docs/STATUS.md`](../STATUS.md).
Branches: `claude/zealous-mayer-j5oda9` (PR 4-A), then stacked `phase-4/<letter>-<topic>` (§3).

Phase 4 gives SunCore foresight. Adapters read PV forecasts and prices from the integrations the
user already runs, a load forecast is learnt from Home Assistant's statistics, a risk model learns
how wrong those forecasts are, and a planner (HiGHS LP/MILP with a heuristic fallback) computes a
48 h schedule in 15-minute steps. The plan fills the `Plan` contract the Phase 3 controller
already consumes (`DeviceBand`, `BatteryBand`, `grid_import_limit_w`), so the controller keeps the
last word in real time and the safety gate stays the only writer. The battery becomes a
controllable asset through battery adapters, and every plan interval carries structured reasons
that the UI renders as "Why?". **Shadow mode stays the default; the battery starts in shadow like
every device.**

**Goal (from the merged concept):** as much self-sufficiency as possible and as much
self-consumption as is sensible. The battery covers the night and, where possible, bridges a poor
next day. Buffers and limits can be set by every user and are also learnt from history.

Phase 3 is implemented but its acceptance (shadow run and one device to automatic on the
maintainer's system) is still open. Phase 4 is built in parallel; nothing in Phase 4 changes the
Phase 3 write paths of devices (question 2).

## 0. Merged concept – decisions

Where the concept and revision 1 overlapped or disagreed, the better solution was kept:

| Topic | Revision 1 | Concept | Kept, and why |
|---|---|---|---|
| Uncertainty | P10 PV in target constraints, flat "safety discount" | learnt error distribution per weather class, risk level cautious/balanced/bold → P10/P25/P50 | **Concept**, refined: the quantile is taken from the learnt distribution of the **net** energy error (PV error − load error) per weather class, not P10 PV and P90 load separately – taking both extremes at once double-counts caution and would keep the battery needlessly full. Solcast's own P10/P90 are used until 30 days of history exist. The flat discount is dropped. |
| Risk default | – | open question | **cautious** (maintainer, 2026-10-16): targets met in 90 % of cases. |
| "Battery first" | `prefer_full` strategy for the maintainer, controller rule from Phase 3 | morning buffer "battery first until X %", X rises with uncertainty; consumers before battery while the evening target is still reached; battery wins when the target gets tight | **Both combined:** the plan sends `BatteryBand.priority_until_soc_pct` = X. The controller compares it with the **live** SoC every tick (robust when the day deviates from the plan); above X, surplus goes to consumers while the plan says the evening target is reachable; when it is not, the plan sets the device bands to `allowed=False`/`max_power_w`. `prefer_full` = X is the max SoC (always battery first) – the maintainer's "always full when possible". |
| Next day | not planned | raise the evening target up to 100 % when the 48 h horizon shows a poor next day | **Concept.** Done as a computed lower SoC bound at the evening time (night load + the next day's deficit at the chosen risk quantile), plus a terminal value for energy left at the horizon end so the LP never empties the battery just because the horizon stops. |
| Objective | cost (+ degradation, switching) | cost + optional self-sufficiency weight | **Concept**; default weight **0.01 currency/kWh** of import: it only decides between options whose cost differs by less than 1 ct/kWh (so it never costs real money) and removes the LP's many equal-cost solutions on flat tariffs. 0 = pure cost; expert parameter. |
| Device value | `DeviceBand` preference only | consumers need a utility value, otherwise export is as good as consumption | **Concept.** `DeviceConfig.planner_value_per_kwh`; automatic = just above the export price (so consumption beats export) and never above the import price (SunCore never imports for a device – Phase 3 answer 6). Comfort bands and the water heater's tank are not modelled thermally in Phase 4 (Phase 6). |
| Grid charging | price-level + schedule (answer 2026-10-16) | – | kept unchanged. |
| Load forecast | profiles + exponential smoothing, no ML | ML (gradient boosting/KNN) as an optional layer, only if measurably better | **Concept, with the maintainer's answer:** profiles stay the default and work from day 1; an ML model (KNN on analogue days, implemented with NumPy only – no scikit-learn, whose musl/aarch64 wheels are `UNVERIFIED`) runs **in parallel in shadow** and is scored next to the profiles. It is used only in mode `auto` and only while it beats the profiles; selectable after 30 days of history. |
| Calibration start | not fixed | 14 or 60 days? | **30 days** (maintainer). Before that statistics are only collected; the load forecast can start earlier because HA's long-term statistics already hold history (rebuilt on first start). |
| Re-planning | every 15 min + events | rolling horizon every 15 min | same; kept. |
| Calibration charge | off by default (answer 11) | – | kept. |

## 1. Goals and acceptance criteria

Phase 4 is done when (roadmap Phase 4 milestones plus the concept):

1. **Forecast adapters:** Solcast (P10/P50/P90 from the attributes or the read-only query action,
   never the Solcast API), Forecast.Solar (core, via `energy/solar_forecast`), Open-Meteo Solar
   Forecast (HACS), a generic attribute adapter (mapping per template), and the built-in fallback
   behind an explicit opt-in (question 5). Several sources are blended by measured skill; a bias
   calibration per hour of day × season × weather class (cloudiness) is learnt; clipping
   (inverter limit, export limit) is recognised and excluded from the statistics; accuracy (MAE,
   bias per source and horizon) is stored and shown.
2. **Load forecast:** base load = house − controlled devices, profiles from HA long-term
   statistics by weekday/holiday (optional `workday` sensor) and outside temperature (optional
   `weather` entity), exponential smoothing; controlled devices modelled separately; optional
   NumPy KNN model in shadow, used only when better (§0).
3. **Risk model and buffer:** learnt distribution of the net forecast error per weather class and
   horizon; risk level cautious/balanced/bold (default cautious) → quantile 10/25/50 %; the morning
   buffer and the evening target are computed from it so a target is met with the chosen
   probability; hard limits (reserve, max SoC, target SoC + time, morning buffer floor) always
   manual and always win.
4. **Tariffs:** dynamic import from Tibber, Nord Pool, ENTSO-E, EPEX Spot (and Octopus if its
   source can be verified), fixed and HT/NT (time windows, seasonal); price formula
   (spot → end price with grid fee incl. time-variable grid fees §14a module 3, levies, margin,
   VAT); 15- and 60-minute grids; export fixed, tiered by kWp share (blended rate) and none;
   negative-price region rule (`de`: no remuneration → avoid export).
5. **Planner:** HiGHS (`highspy`), 48 h, 15 min, rolling every 15 min and on events, warm start,
   time budget (default 8 s, MIP gap 1 %), heuristic fallback with a UI hint. Objective cost +
   degradation + switching + self-sufficiency weight + device values. Benchmark < 10 s on
   aarch64 (R3).
6. **Battery strategies:** self-consumption (default), `prefer_full` (maintainer), target SoC by
   time, next-day bridging, grid charging by price level + schedule (+ spread rule on dynamic
   tariffs), discharge lock, reserve separate from BMS drift, max-SoC limit, calibration charge
   (off by default), cycle counter, degradation cost.
7. **Battery adapters:** generic HA entities (template kind `battery`), Kostal Plenticore through
   the core `kostal_plenticore` integration (min SoC as discharge lock/reserve), Kostal Modbus §3.5
   limits through HA's `modbus.write_register` (question 3, shadow/assisted until the fallback
   timer is measured). Read-back, write only on change, release = inverter self-control.
8. **Explanations:** every plan interval carries `Reason(key, args)`, e.g.
   `planner.battery.morning_buffer` {soc_pct: 40, cause: forecast_uncertain}; forecast accuracy
   and bias per source in the UI.
9. **`control-safety` review** of every change to `safety/`, the controller's battery path and the
   battery adapters' write paths, documented in the PR (invariant table).

Exit (roadmap): plan with battery strategy and "Why?" on the maintainer's system, battery
control in shadow mode, then assisted.

## 2. Work packages

Owner = subagent that implements it. Budget rule (answer 2026-10-16): **Opus takes the Fable
roles** – `optimizer-engineer` (planner, forecast mathematics, risk model, battery economics),
`control-safety` (controller battery path, safety, battery write paths), `reviewer` and advisor;
Sonnet for adapters, backend, simulator, frontend and docs; Haiku for catalogue sync. At most two
subagents in parallel. **Every WP ends with a commit and a push.**

### WP1 – Verify interfaces · `ha-integration` (skills `verify-ha-api`, `new-adapter`)

From the GitHub sources (doc sites unreachable), recorded with commit and path in
`research/integrations.md` / `research/ha-websocket-api.md`: `recorder/statistics_during_period`,
`energy/solar_forecast`, `weather.get_forecasts`, `workday`, `number.set_value`,
`select.select_option`, `modbus.write_register`, `nordpool.get_prices_for_date`,
`tibber.get_prices`, `kostal_plenticore` battery entities; Solcast, Open-Meteo Solar Forecast,
ENTSO-E, EPEX Spot, Octopus attributes; Open-Meteo API and Energy-Charts/aWATTar and their terms.
What cannot be read stays `UNVERIFIED`.

### WP2 – Contracts · `optimizer-engineer` (Opus), reviewed by `architect`

- `core/series.py`: `TimeSeries`, `QuantileSeries`, `PriceSeries`; resampling onto the 15-minute
  grid (energy-preserving for power/energy, step-hold for prices, mixed 15/60 min, DST-safe).
- `core/forecast.py`: `ForecastSource`/`PriceSource` protocols, `AccuracyRecord`;
  `core/tariff.py`: `PriceFormula`, weekly windows (HT/NT and the grid-charge schedule), blended
  export rate; `core/battery.py`: `BatteryModel`, `BatteryStrategy`, `RiskLevel`,
  `GridChargePolicy`; `BatteryAction` (**reviewed by `control-safety`**).
- `Plan` v2 (additive): per interval expected flows, prices, reasons; `Plan.source`, `Plan.stats`;
  `BatteryBand.priority_until_soc_pct`; `DeviceConfig.planner_value_per_kwh`.
- Config (additive, migrated): `ForecastConfig` v2 (several sources, weights auto/manual,
  learning: `min_history_days` 30, `load_ml_mode` off|shadow|auto = shadow), `TariffConfig` v3
  (formula, HT/NT), section `battery` (adapter, model, strategy, risk = cautious,
  morning-buffer floor, `bridge_next_day`, self-sufficiency weight, grid-charge policy,
  calibration), `goals` v2 (`grid_charge_night_only` → schedule 22:00–06:00, charging still off).
- Template schema v1 additions (schema v1 changelog): kind `battery`, generic forecast `series`
  mapping, tariff HT/NT.

### WP3 – Storage and statistics · `backend-engineer`

`/data/timeseries.db` (ADR-0004, `backup_exclude`): measurements (48 h raw, 1-min 30 days,
15-min unlimited), forecasts versioned by issue time, prices, nightly downsample + vacuum,
rebuild of 15-min history from `recorder/statistics_during_period`. `suncore.db` migration
`0004`: `plan_snapshot`, learnt parameters (bias tables, error distributions, load profiles,
blending weights, ML state). Rate limiter per (domain, service) with try-acquire/deadline for
`call_action_response` (follow-up A3).

### WP4 – Tariff adapters · `backend-engineer` (skill `new-adapter`), maths by `optimizer-engineer`

`suncore/tariff/`: Tibber, Nord Pool, ENTSO-E, EPEX Spot, Octopus (only if verified), generic
price list, fixed and HT/NT; price formula; export fixed/tiered/none; negative-price rule from the
region; fixtures from verified shapes; templates with `source` and a conformance scenario.

### WP5 – PV forecast · `device-adapters` (adapters), `optimizer-engineer` (Opus, blending/calibration)

`suncore/forecast/pv/`: Solcast, Forecast.Solar, Open-Meteo Solar Forecast, generic attribute,
built-in fallback (question 5). Blending by inverse MAE over 14 days with a minimum weight and a
manual override; bias calibration per hour × season × weather class (clear-sky index buckets
from the forecast itself), bounded and shrinking to 1 with little data, active after
`min_history_days`; clipping detection; accuracy in `timeseries.db`.

### WP6 – Load forecast · `optimizer-engineer` (Opus)

`suncore/forecast/load.py`: base load = house − controlled devices; profile per day type
(workday/weekend/holiday) × 15-min slot with exponential smoothing; temperature term with a
mapped weather entity; residual quantiles; cold start from HA statistics, else a flat profile
with a wide band and a UI hint. Controlled devices are forecast separately (their Phase 3 power
models and the plan), never in the base load.

### WP7 – Risk model and buffer · `optimizer-engineer` (Opus) *(new, from the concept)*

`suncore/forecast/risk.py`: empirical distribution of the net energy error (actual − forecast,
PV minus load) per weather class and horizon window (rest of today, night, next day), from the
stored forecasts and actuals; until `min_history_days` the source quantiles (Solcast P10/P90) and
the load residuals are used, labelled as such. Functions the planner calls:
`net_energy_quantile(window, risk)`, `morning_buffer_pct(...)` (higher when the PV forecast is
uncertain), `evening_target_pct(...)` (night load + next-day deficit at the risk quantile, capped
at max SoC, never below the user's target). Hypothesis tests: monotone in risk level and in
uncertainty, never below the manual limits.

### WP8 – Planner · `optimizer-engineer` (Opus)

`suncore/planner/`: `model.py` (sparse matrices for `highspy`, backend protocol), `heuristic.py`,
`explain.py`, `run.py`.

- Variables per interval: import, export, battery charge/discharge (separate efficiencies), SoC,
  device energy within its band, PV curtailment where an export limit or negative prices make it
  useful (informational until the export-limit chain in Phase 6).
- Constraints: energy balance, SoC bounds with reserve + BMS-drift margin, battery limits, import
  limit, static export limit, morning buffer and evening target from WP7 (soft with a high
  penalty, so the plan stays feasible and explains the shortfall), grid charging only inside the
  price level and schedule (and the spread rule on dynamic tariffs), discharge lock where it
  pays; binaries only where needed.
- Objective: import cost − export revenue (negative prices and the region rule) + degradation +
  switching + self-sufficiency weight − device values − terminal SoC value; PV at the risk
  quantile in the target constraints, P50 in the objective.
- Warm start, time budget, MIP gap, fallback chain incumbent → relaxed targets → heuristic.
- `explain.py`: binding constraints and price comparisons → `Reason`s.
- Outputs `Plan` v2: `BatteryBand` (power limits, SoC corridor, `priority_until_soc_pct`,
  `charge_from_surplus`) and `DeviceBand` (allowed, `max_power_w`, preference: throttle or switch
  off consumers when the target gets tight).
- Property tests: balance, SoC bounds, never worse than the heuristic, no grid charging outside
  the policy, no avoidable export at negative prices, targets met when the actual lies inside the
  planned quantile.
- `scripts/bench_planner.py`: the maintainer's reference scenario (17 kWp east-west, 29.6 kWh,
  Tibber 15-min prices, export limit 13.5 kW, tiered EEG).

### WP9 – Runtime and scheduler · `backend-engineer`

Planner every 15 min (rolling horizon with live SoC) and on events (new prices, forecast update,
deviation above threshold, config change, override), in a worker thread with the budget, one run
at a time; atomic plan hand-over to the controller; plan snapshots; `/health` unaffected. API:
`GET /api/v1/plan`, `POST /api/v1/plan/replan`, `GET /api/v1/forecast/accuracy` (incl. ML vs
profile), `GET /api/v1/prices`; live feed `plan`. Demo with simulator prices and forecasts.

### WP10 – Battery control · `control-safety` (Opus), adapters by `device-adapters`

**Prerequisite:** [ADR-0013 "Battery as a controlled asset"](../adr/0013-battery-as-controlled-asset.md)
is **accepted before WP10 starts** (proposed in PR 4-A). Its scope:

- action shape (limits vs set-points), sign convention and the adapters' conversion;
- one source of truth for the reserve, `max(site battery min_soc, BatteryModel reserve +
  BMS-drift margin)`, used by planner and gate;
- the explicit exception to "no SunCore-caused import" for planned grid charging and its bounds
  (import limit, price level/schedule, a plan interval allowing it);
- heartbeat/fallback-timer and release semantics;
- battery identity and mode rules (the battery is not covered by the device identity fingerprint
  today);
- invariants to add: `min_soc` never below the reserve; `grid_charge_w` bounded by battery,
  inverter and import limits; `max_discharge_w = 0` must not cause grid import against the
  device rule unless planned.

Until then the gate rejects every `BatteryAction` with `safety.battery_not_supported` (ADR-0011
addendum 2026-10-16); removing it needs the `control-safety` sign-off.

- Controller: `BatteryBand` → `BatteryAction` intents (on change, hysteresis); evaluates
  `priority_until_soc_pct` against the live SoC; device bands from the plan.
- Safety invariants (property-tested): SunCore never discharges below reserve + drift margin;
  grid charging only from a plan interval that allows it, within the import limit, price level
  and schedule; limits within battery/inverter limits; write rate, write-on-change; release =
  restore the adapter's baseline; stale SoC/battery power while the grid meter moves pauses
  battery control; hung inverter → release and stop writing. "No SunCore-caused import" keeps
  holding for devices.
- Adapters `suncore/devices/battery/`: `generic`, `kostal_plenticore`, `kostal_modbus` (§3.5 via
  `modbus.write_register`, question 3).
- Simulator: battery with BMS drift, inverter self-consumption as fallback, Kostal-like fallback
  timer. Scenarios: `sunny_day`, `mixed_clouds`, `two_poor_days`, `forecast_too_optimistic`,
  `negative_prices`, `price_spike`, `grid_charge_price_level`, `inverter_hang_battery`,
  `reference_house`.

### WP11 – UI · `frontend-engineer`

Plan view `#/plan` (minimal, the full chart is Phase 5): ECharts on demand, 24/48 h, price, PV
P10–P90 + P50, load, planned SoC (with morning buffer and evening target lines), battery power,
import/export; reasons per interval; solver/heuristic badge. Forecast accuracy and bias per source
(incl. ML vs profile). Now view: next planned actions with "Why?", battery card. Wizard: steps 4
(several sources), 5 (adapter, limits, strategy, risk level), 6 (formula, HT/NT), 8 (grid-charge
price level and schedule, self-sufficiency weight), 10 (first plan preview). Vitest, Playwright,
axe, screenshots `plan-*` from the demo.

### WP12 – Optional ML load model · `optimizer-engineer` (Opus)

NumPy KNN on analogue days (features: day type, slot, temperature, recent load); mode `shadow`
by default: scored next to the profiles, shown in the accuracy view, used only in `auto` while
better over the last 14 days. Benchmark on aarch64 (training + prediction < 1 s).

### WP13 – Docs, translations, STATUS · `docs-writer`, `i18n-sync` (Haiku)

User guide `docs/user/<lang>/planning-and-battery.md` (forecasts, risk level and buffers,
tariffs, how the planner decides, strategies, adapters and enabling external battery control,
shadow → assisted walk-through, limitations), quick start, app `DOCS.md`, architecture §planner,
compatibility list, all keys en/de, STATUS, roadmap, `UNVERIFIED` table.

## 3. Pull requests and order

Small stacked PRs (maintainer, 2026-10-16). 4-A uses the session branch; the others are pushed as
`phase-4/<letter>-<topic>`, each based on the previous one. Every PR gets the `reviewer` agent
(Opus) before it is opened, its findings fixed in `fix(...)` commits; PRs with safety-relevant
code additionally the `control-safety` sign-off.

| PR | Content | Depends on |
|---|---|---|
| 4-A `feat(core)` contracts | plan, WP1, WP2 | – |
| 4-B `feat(storage)` time series | WP3 | 4-A |
| 4-C `feat(tariff)` | WP4 | 4-B |
| 4-D `feat(forecast)` PV | WP5 | 4-B |
| 4-E `feat(forecast)` load + risk | WP6, WP7 | 4-D |
| 4-F `feat(planner,app)` | WP8, WP9 | 4-C, 4-E |
| 4-G `feat(safety,controller,devices)` battery | WP10 | 4-F |
| 4-H `feat(frontend),docs` | WP11, WP13 | 4-G |
| 4-I `feat(forecast)` ML load (optional) | WP12 | 4-E |

```text
WP1 ─► WP2 ─► WP3 ─┬► WP4 ───────────────┐
                   └► WP5 ─► WP6 ─► WP7 ─┴► WP8 ─► WP9 ─► WP10 ─► WP11 ─► WP13
                                    └► WP12
```

I integrate, run `./scripts/check.sh` and the frontend checks after every WP, commit and push per
WP (Conventional Commits; CHANGELOG via release-please, no hand edits). Open questions go into
each PR under "Offene Fragen an den Maintainer".

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Series/contracts | pytest, hypothesis | resampling preserves energy, quantiles monotone, mixed 15/60 grids, DST days |
| Adapters | pytest with fixtures from verified shapes | every forecast and price adapter; missing/partial data |
| Blending/calibration/load | pytest, hypothesis | weights sum to 1, bias bounded, clipping excluded, controlled loads subtracted |
| Risk model | hypothesis | buffer/target monotone in risk and uncertainty, never below manual limits |
| Planner | hypothesis + reference solver | balance, SoC bounds, objective ≤ heuristic, grid-charge policy, negative prices, fallback, budget; targets met when actuals lie inside the planned quantile |
| Benchmark | `scripts/bench_planner.py`, CI job on `ubuntu-24.04-arm` | reference scenario 48 h (R3); ML training/prediction |
| Safety | hypothesis | the battery never falls below the reserve through SunCore; no write in shadow/assisted without confirm; release paths |
| Simulator | `-m simulator` | scenarios of WP10 incl. sunny day, mixed clouds, two poor days, too optimistic forecast; 7-day run planner + controller |
| Storage | pytest | migration 0004 + timeseries.db, rebuild, retention |
| Boundary | AST test | planner imports `core` only; battery `apply()` only via the gate; actions only through the rate limiter |
| API, Frontend | httpx ASGI; Vitest, Playwright, axe | plan, replan, accuracy, prices; plan view, battery card, wizard |
| Coverage | pytest-cov | ≥ 80 % core/safety/planner/controller (aim ≥ 90 % planner and safety) |

No hardware and no Docker daemon in the session: image and smoke test run in CI; the maintainer's
shadow and assisted run is the acceptance test.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | A battery write empties the battery or charges from the grid at the wrong time | money, backup reserve | battery starts in shadow, assisted needs confirmation, invariants property-tested, reserve + drift margin, `control-safety` sign-off, §3.5 fallback timer as hardware watchdog |
| R2 | Kostal Modbus timeout and enabling unverified | adapter does nothing or does not fall back | §3.5 limits first; §3.4 out of scope; measurement before automatic |
| R3 | Solve time on a Pi 4 > 10 s | stale plans | LP first, few binaries, warm start, budget 8 s + heuristic; CI arm runner as proxy (budget 3 s); the maintainer runs amd64, a Pi 4 needs a volunteer |
| R4 | `highspy` + NumPy enlarge image and memory | RSS budget 300 MB | musllinux wheels verified; smoke-test memory gate; solver in a thread |
| R5 | Integration attributes change between versions | missing plan | tolerant parsers, `tested_with`, fallback to other sources or a flat profile |
| R6 | Built-in fallbacks need internet and unverified terms | terms breach, cloud dependency | opt-in, off, attribution, only after WP1 verified the terms (question 5) |
| R7 | Little history at the start | poor buffers | HA statistics rebuild for load; source quantiles for PV until 30 days; cautious default |
| R8 | Learnt buffer too high | battery always full, less self-consumption | bounded by max SoC, shown with its reason, risk level and manual floor adjustable |
| R9 | DB schema 4 blocks a downgrade | no rollback | release note, export before update |
| R10 | Phase 3 live test finds controller issues | rework | Phase 4 touches the controller only in the battery path and the plan input; fixes first |
| R11 | ECharts breaks the bundle budget | slow first paint | lazy chunk |
| R12 | Nine stacked PRs drift when an early one changes | rebase work | merge in order, small PRs, merge commits (no history rewrite) |

## 6. Questions and answers

1. PR structure: ~~one PR~~ **small stacked PRs** (maintainer, 2026-10-16, §3). May I push the
   extra branches `phase-4/b…i`? (Needed for the stacked PRs.)
2. Phase 3 not yet accepted: **build in parallel; merge after the Phase 3 acceptance**.
3. Kostal battery control path: **(a) generic + `kostal_plenticore`; Modbus §3.5 via HA's
   `modbus.write_register`, shadow/assisted until the fallback timer is measured** – still open:
   is "external battery management via Modbus" enabled in your inverter?
4. Model budget: **Opus takes the Fable roles** (answered).
5. Built-in fallbacks: **only if verifiable, then opt-in and off; else Phase 6**.
6. Battery economics defaults: **95 %/95 %, 0.04 €/kWh degradation, 0.03 €/kWh margin, drift
   margin 5 %**; BYD HVM figures welcome.
7. Grid charging: **price level + schedule** (answered); off until set.
8. EEG tiers: **you enter the two rates**.
9. Tibber `price`: **all-in end price**.
10. Plan view: **minimal in Phase 4**.
11. Calibration charge: **built, off by default** (answered).
12. Concept: history needed for calibration and ML: **30 days** (answered); ML **runs in
    parallel in shadow for comparison** (answered).
13. Concept: risk level default: **cautious** (answered).
14. Concept: self-sufficiency weight: **on with 0.01 currency/kWh** (my choice, §0; maintainer
    left it open).

### Answers (2026-10-16)

- Every WP ends with a commit **and a push**, so nothing is lost when the session's usage quota
  runs out.
- **Opus takes the Fable roles** (`optimizer-engineer`, `control-safety`, `reviewer`, advisor).
- Calibration charge: built for other users, **off by default**.
- Battery of the maintainer: **"as full as possible"** → strategy `prefer_full` (§0).
- Grid charging by **a price level and a schedule** instead of a fixed night window; the spread
  rule still applies on dynamic tariffs; `goals.grid_charge_night_only` is migrated to a schedule
  22:00–06:00; grid charging stays off until the user sets a level or a schedule.
- Concept: 30 days of history, ML in parallel for comparison, risk level cautious.
- Stacked branches `phase-4/b…i` may be pushed.
- External battery management via Modbus is **not enabled** on the maintainer's inverter yet; he
  enables it when he wants grid charging. Consequence: the Modbus adapter is built and tested in
  the simulator but stays `UNVERIFIED` on hardware; without it SunCore controls the battery only
  through `kostal_plenticore` (min SoC: reserve and discharge lock). Charging from PV needs no
  write at all (the inverter's own self-consumption logic does it).
- Self-sufficiency weight: explained to the maintainer, default 0.01 currency/kWh kept.

### Offene Fragen aus PR 4-A (an den Maintainer)

1. **Doppelter Netzladeplan:** `goals.grid_charge_night_only` wird zu einem Zeitplan migriert,
   und `battery.grid_charge` hat einen eigenen Zeitplan. Aufgelöst durch
   `effective_grid_charge_policy`: die Akku-Richtlinie (`battery.grid_charge`) gewinnt, der
   Zeitplan aus `goals` gilt nur, solange die Akku-Richtlinie keinen eigenen setzt. Einverstanden?
2. **Mehrwertsteuer auf negative Spotpreise:** Die Preisformel wendet die MwSt. auf die ganze
   Summe an, auch wenn der Spotpreis negativ ist (Endpreis kann dadurch negativ werden). Das ist
   eine Annahme (`UNVERIFIED`); bitte mit einer Tibber- oder Lieferantenrechnung bestätigen.
3. **Einspeise-Stufen:** Stufen, die die Anlagengröße nicht vollständig abdecken (z. B. nur
   „bis 10 kWp“ bei 17 kWp), werden abgelehnt – SunCore rät keinen Satz. Bitte beide EEG-Sätze
   inklusive der Stufe über 10 kWp eintragen (Frage 8).
4. **Netzladen auf deinem Kostal (ADR-0013):** §3.5 begrenzt nur, die REST-API hat keinen
   Lade-Sollwert. Netzladen wäre in Phase 4 nur geplant und im Schatten-/Assistenzmodus sichtbar,
   nicht ausführbar. Reicht das, oder soll eine generische „Zwangsladen“-Entität bzw. später ein
   eigener §3.4-ADR kommen?

### Offene Fragen aus PR 4-C (an den Maintainer)

1. **Zusätzliche Tarif-Einstellungen:** Damit Nord Pool (`config_entry`, `areas`), Octopus (zwei
   Ereignis-Entitäten), mehrere Tibber-Häuser und ENTSO-E mit eigener Preisformel funktionieren,
   bekommt `tariff.import` vier optionale Felder: `role_entities`, `action_params`, `series_key`,
   `price_includes` (Aussage des Nutzers, ob die Quelle schon Steuern/Netzentgelte enthält).
   Alte Dokumente bleiben gültig. Einverstanden?
2. **Nord Pool `config_entry`:** Die ID des Konfigurationseintrags muss der Nutzer eintragen
   (Entwicklerwerkzeuge → Aktionen zeigt sie). Ob SunCore sie aus der Entity-Registry lesen kann,
   ist nicht verifiziert (`UNVERIFIED`). Reicht die manuelle Eingabe für Phase 4?
3. **Negativpreis-Regel bei All-in-Quellen (Tibber):** Der Spotpreis ist nicht bekannt; als Ersatz
   gilt der Endpreis (Import) < 0. Das markiert weniger Viertelstunden als die echten
   Negativ-Spot-Stunden, die Einspeisung wird dann dort noch vergütet gerechnet (`UNVERIFIED`
   gegen die Praxis). Alternative: zusätzlich die Spot-Quelle (z. B. EPEX Spot Marktpreis) als
   Referenz konfigurieren (Phase 5). Einverstanden mit dem Ersatzwert?
4. **`curtailment_required`:** ändert in Phase 4 keinen Preis (Abregelung ist Sache von
   Planer/Regler, nicht des Preises). Nur `no_remuneration` setzt den Einspeisepreis auf 0.
5. **Dynamische Einspeisepreise:** keine der verifizierten Quellen liefert welche; der Einspeisepreis
   kommt aus `tariff.export` (fest, gestaffelt, keiner). Octopus-Export-Entitäten sind nicht
   angebunden.
6. **HT/NT-Preis:** pro Viertelstunde zum Beginn der Viertelstunde; Fensterkanten, die nicht auf
   :00/:15/:30/:45 liegen, wirken ab der nächsten Viertelstunde.
7. **Veröffentlichung der Preise für morgen:** Standard 13:00 Ortszeit, danach alle 15 min bis die
   Preise da sind (`UNVERIFIED`; Experten-Umgebungsvariablen `SUNCORE_TARIFF_TOMORROW_AFTER`,
   `SUNCORE_TARIFF_TOMORROW_POLL_S`, `SUNCORE_TARIFF_REFRESH_INTERVAL_S`, keine App-Optionen,
   dokumentiert in beiden `DOCS.md`). Bitte an deinem System beobachten.
8. **§51 EEG und Inbetriebnahmedatum:** Ob bei negativen Preisen vergütet wird, hängt vom
   Inbetriebnahmedatum der Anlage ab. Die Regel kommt heute nur aus dem Regionsprofil (`de`:
   `no_remuneration`). Soll es später (Phase 5) pro Anlage eine Abschaltmöglichkeit geben
   („diese Anlage wird auch bei negativen Preisen vergütet“)?
9. **Namensabgrenzung:** `PriceFormula.apply` heißt für Aufrufer jetzt `end_price` (der Grenztest
   reserviert `.apply(` für den Geräteschreibpfad); `apply` bleibt als Alias.

## Deviations during implementation

- Phase 4 is split into the stacked PRs 4-A…4-I (§3) instead of one PR.
- The built-in price fallbacks (Energy-Charts, aWATTar) are **not shipped in Phase 4**: their APIs
  and terms could not be read from the session (question 5 default: only if verifiable, else
  Phase 6).
- Open-Meteo API terms are known only from the repository README (free for open-source and
  non-commercial use, fair use, 10 000 requests/day, CC BY 4.0 attribution); the separate terms
  page was unreachable. The built-in PV fallback stays opt-in and off.
- `BatteryAction` is part of the 4-A contracts but rejected by the gate until WP10
  (ADR-0011 addendum); its semantics are proposed in ADR-0013.
- WP4: ENTSO-E and EPEX Spot get two templates each where the unit or the price content differs
  (`entsoe.price`/`_mwh`, `epex_spot.total_price`/`market_price`): a template holds one value unit
  and one `price_includes`. Octopus is included (its source was verified in 4-A).

## 7. Out of scope for Phase 4

Full plan chart with device Gantt, zoom/pan, history, savings vs baselines, what-if and
backtesting (Phase 5); thermal models for climate and water heater in the planner, export limiting
through the inverter, peak shaving with load shedding, §14a control signal, backup-power
awareness, wallbox/EV, heat pump, shiftable programs, MQTT entities and notifications (Phase 6);
Kostal §3.4 set-points and MDC; V2H/V2G; ML models beyond the NumPy KNN.
