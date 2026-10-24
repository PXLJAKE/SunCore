# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-24 · **Phase:** 4 in progress – PR 4-A contracts, 4-B storage, 4-C
tariffs, 4-D PV forecast, 4-E load forecast and risk model, 4-F planner and runtime (WP8, WP9;
battery control WP10 next); Phase 3 (3-A
`phase-3/a-safety`, 3-B `phase-3/b-controller`, 3-C `phase-3/c-ui`) implemented, waiting for the
maintainer's shadow test · **Branch:** `phase-4/f-planner` (stacked on `phase-4/e-load-risk`, merged
with `phase-4/c-tariff`)

## Done

- **Phase 4 – PR 4-A (plan, WP1, WP2; branch `claude/zealous-mayer-j5oda9`)**:
  - Plan `docs/plans/phase-4.md` **revision 2 approved** (2026-10-16), incl. the merged concept
    "Prognose, Puffer und Akkuplanung" (§0) and the split into stacked PRs 4-A…4-I.
  - WP1 research: Phase 4 interfaces verified from source with pinned refs (HA core `dev`
    `5cfe9aad`, Solcast `v4.6.1`, Open-Meteo Solar Forecast `0.1.33`, ENTSO-E `v0.7.5`, EPEX Spot
    `4.1.0`, Octopus `v19.2.1`, Open-Meteo `01ceed5b`); summary table at the end of
    `research/integrations.md`, Modbus/Kostal write path in `research/kostal-plenticore-modbus.md`.
  - WP2 contracts (`core`, additive): `series.py` (`TimeSeries`, `QuantileSeries`, `PriceSeries`,
    15-min grid resampling, DST-safe), `forecast.py` (`ForecastSource`/`PriceSource`,
    `AccuracyRecord`), `tariff.py` (`WeeklySchedule`, `PriceFormula` with VAT, HT/NT, blended
    export tiers), `battery.py` (`BatteryModel`, `BatteryStrategy`, `RiskLevel`,
    `GridChargePolicy`, `BatterySettings` = section `battery`), `BatteryAction` in `decisions.py`
    (**rejected by the gate** with `safety.battery_not_supported`, ADR-0011 addendum), `Plan` v2
    (`IntervalFlows`, `IntervalPrices`, per-interval reasons, `source`, `SolverStats`,
    `BatteryBand.priority_until_soc_pct`; v1 still loads); config `TariffConfig` v3,
    `ForecastConfig` v2, `goals` v2; template schema v1 additions (kind `battery`, `series`,
    HT/NT).
  - ADR-0013 "Battery as a controlled asset" **proposed** (prerequisite of WP10).
  - Review fixes: in progress (`fix(...)` commits on the branch).

- **Phase 4 – PR 4-B (WP3, storage and statistics; branch `phase-4/b-storage`, stacked on 4-A)**:
  - `/data/timeseries.db` (own migrations and `user_version`; excluded from backups together with
    its WAL/SHM and `timeseries.db.corrupt*`): `measurement` 48 h and `measurement_1m` 30 days
    (primary key `(ts, key)`: about 5x fewer WAL bytes per flush than `(key, ts)`), `measurement_15m`
    unlimited with a `derived` flag, `forecast` versioned by issue time (kept 45 days after the
    slot), `price` 400 days, `meta`. `TimeseriesStore`: batched appends, series round trips,
    `latest_forecast_before`, nightly job 03:30 local (wakes at most hourly, follows the time
    zone): downsample (1-minute tier only for complete quarter hours), retention, `VACUUM` only
    when worth it, `size_report`.
  - Corruption handling: only "not a database"/"malformed"/a failing `quick_check` move the file
    aside (with WAL/SHM, `timeseries.db.corrupt-<UTC>`, newest two kept); locked, full, I/O or
    permission errors and a file with a newer schema run SunCore without time series, nothing is
    moved or deleted.
  - Rebuild of 28 days of site history from `recorder/statistics_during_period` (period `hour`,
    explicit units) as `derived` quarter hours (`suncore/ha/statistics.py`,
    `suncore/app/history.py`), until it succeeded once (marker in `timeseries.db`), back-off 1, 5,
    15, then 60 min; `unknown_command` is final for the process.
  - Controller measurements through a batched writer (flush every 60 s and at shutdown, only
    OK-quality values; the tick hook cannot disturb control).
  - `suncore.db` migration 0004 (`plan_snapshot` zlib JSON, 200 rows / 14 days; `learned_param`):
    **DB schema 4, earlier versions refuse it – export before updating (release-risk note).**
  - `ActionRateLimiter` per `domain.service[#sub-key]` for `call_action_response` (default 30 s,
    `ActionRateLimitedError`, follow-up A3 done); boundary rule 7 admits only
    `suncore.app.adapters`.
  - Fixed: a cancelled `Database.transaction` could leave a transaction open on the shared
    connection.
  - Follow-ups: `size_report` is not on a diagnostics page yet; a too-new `timeseries.db` is only
    logged (a UI hint follows in 4-H).

- **Phase 4 – PR 4-D (WP5, PV forecast; branch `phase-4/d-pv-forecast`, stacked on 4-B)**:
  - Part A – adapters: template-driven sources (`suncore/forecast/pv/`) for Solcast
    (`detailedForecast` attributes, fallback read-only action `solcast_solar.query_forecast_data`,
    never the Solcast API), Forecast.Solar (core, `energy/solar_forecast`, `config_entry_id` when
    several entries are linked), Open-Meteo Solar Forecast (`wh_period_15m`) and the generic
    attribute template; pure parsing onto the source's native grid (DST-safe, unknown slots stay
    `None`). Refresh service: every 30 min and on entity changes (debounced), keeps the last good
    data, bounded gap filling, stores issues in `timeseries.db` hourly or on a material change
    (≥ 15 %). Nightly accuracy bookkeeping (MAE and bias = actual − forecast per source and
    horizon bucket 0–6/6–24/24–48 h over 14 days, daylight quarter hours only, clipped ones
    excluded, `learned_param` `forecast_accuracy`).
    `GET /api/v1/forecast/pv` and `/forecast/accuracy`; the demo publishes three deterministic
    biased sources.
  - Part B – blending, calibration, clipping: `blend.py` (weights per horizon bucket from the
    inverse 14-day MAE, minimum weight 0.1, manual weights as shares, equal weights until every
    auto source has ≥ 96 scored quarter hours; renormalised per slot; P50 weighted, P10/P90 from
    the sources with quantiles widened by the sources' disagreement); `calibration.py`
    (multiplicative factor per local hour × month-group season × weather class, bounded 0.5–1.5,
    shrunk with `n/(n+16)`, learnt nightly per source from the day-start issue without clipped
    slots, applied only after `min_history_days` = 30 days with data, stored in `forecast_bias`);
    `clearsky.py` (weather class = clear-sky index of the forecast itself: sun position + Haurwitz
    clear sky on the configured arrays with HA's `latitude`/`longitude` from `get_config`;
    fallback without location or `kwp`: the day's forecast peak against the 95th percentile of
    the stored daily peaks);
    `clipping.py` (grid export at the export limit while the forecast is higher; an inverter
    plateau derived from the data – no AC-rating field exists, none was invented); `learning.py`
    (nightly order clipping → accuracy incl. `blended` → bias tables). The service blends on the
    15-min grid for now → +48 h, stores the blend as source `blended`, and the API shows the
    weights, the calibration state per source and the blend without and with the learnt
    calibration. The demo site now declares its 8 kWp horizontal plant.
  - Deviation: the built-in PV fallback (Open-Meteo API, question 5) is **not shipped**; it moves
    to Phase 6 with the price fallbacks (Open-Meteo terms and live azimuth convention
    `UNVERIFIED`, plan §"Deviations during implementation").
  - Zero-export plants (`export_limit_w` 0 or 0 %): clipping by the export limit is not detected
    (grid ≈ 0 W also without curtailment); only the inverter-plateau rule applies.
  - Follow-ups: the weights come from the *uncalibrated* sources' MAE (a calibrated source is
    under-weighted until a calibrated accuracy is scored); no UI yet (4-H).

- **Phase 4 – PR 4-F, part WP8 planner (branch `phase-4/f-planner`, on 4-E merged with 4-C)**:
  - `suncore/planner/` (depends on `core` only; boundary test extended; `mypy --strict`):
    `inputs.py` (`build_inputs`: pure snapshot on the 15-minute grid now → +48 h from the
    tariff, PV and base-load forecasts, the risk model's morning buffer and evening target,
    `goals.battery_target` as a timed goal on every day, devices with window masks; `spot_only`
    prices without a formula are refused → `price_mode = flat` + `planner.fallback.spot_without_formula`;
    missing prices filled with the median and reported), `model.py` (sparse LP for HiGHS behind
    the `SolverBackend` protocol, `HighsBackend`; PV serves the base load first, devices draw only
    PV surplus - "no import for devices" holds structurally; soft targets with penalties; no
    binaries in Phase 4), `heuristic.py` (rule-based fallback on the same flows), `explain.py`
    (reasons per interval from binding targets and price comparisons), `run.py` (`plan()`/`run()`:
    heuristic first as fallback and yardstick, solver within the budget, warm start from the
    previous plan, fallback chain timeout/infeasible/error/invalid/solver missing → heuristic).
    `highspy` 1.15.1 (+ NumPy 2.5.4) are runtime dependencies now.
  - Plan v2, additive: `BatteryBand.grid_charge_max_w` / `discharge_lock` (ADR-0013; set by the
    planner, the controller still ignores them until 4-G - tested), `IntervalFlows.devices_w` /
    `curtail_w` (so the expected flows balance).
  - Device budget: before every evening/user target devices may only take what a battery-first
    day on P50 could spare plus the PV overflow it cannot absorb (review D4).
  - Tests: hypothesis properties (balance, SoC corridor, bounds, grid charging only inside the
    policy, no export at a negative export price, devices ≤ PV surplus, objective LP ≤ heuristic
    under one cost function, missed targets only when infeasible - checked by a max-SoC LP, every
    interval has catalogued reasons), scenarios `sunny_day`, `mixed_clouds`, `two_poor_days`,
    `forecast_too_optimistic` (rolling replay), `negative_prices`, `price_spike`,
    `grid_charge_price_level`, `prefer_full`, `reference_house` (`suncore/sim/planner_scenarios.py`),
    fallback chain with fake backends; planner coverage 95 %.
  - Benchmark `scripts/bench_planner.py` (reference house: 17 kWp east-west, 29.6 kWh/10 kW,
    13.5 kW export limit, Tibber-like 48 h, blended EEG, two climate devices; 193 slots, 2318
    variables, 413 rows, 0 binaries): session container amd64 (CPython 3.13) median of 7 runs -
    HiGHS solve 4 ms cold (244 simplex iterations) / 4 ms warm (141), heuristic 1 ms, whole
    `run()` incl. explanations 29 ms, inputs incl. risk targets 14 ms. After the review fixes
    (2026-10-24): solve 3.8 ms cold (284 iterations) / 4.0 ms warm (207), heuristic 0.9 ms,
    `run()` 24 ms, inputs 10 ms. Slow test: < 3 s on CI. aarch64 not measured yet (`UNVERIFIED`).
  - Review fixes (2026-10-24, lead decisions D1-D4 and the reviewer/`control-safety` findings,
    plan §"Deviations" "PR 4-F review"): target floor under a plan in the controller, no grid
    charging without a known price, curtailment in negative-rule slots, device budget on P50 plus
    overflow, early-charge tie-breaker, full evening target at the evening deficit, buffer reason
    and priority bounded to the buffer's day, flat 500 W without a load forecast,
    `IntervalPrices.estimated`, optional `PlanInterval.battery`, band hysteresis, 409 for
    `GET /plan` and `/plan/status`, plan age from the hand-over, flapping-band and shadow tests,
    GIL test, flaky 0.1 s timeout test fixed, NumPy declared.
  - The runtime, scheduler and API followed in WP9 (next entry).

- **Phase 4 – PR 4-F, part WP9 planner runtime (branch `phase-4/f-planner`)**:
  - `suncore/app/planning.py` `PlanningService` (time injected twice: `clock`, `sleep`; polled every
    5 s): first run once prices and forecasts exist (or `first_run_grace_s` = 300 s after the first
    connection, the gaps are plan reasons; without the battery SoC no plan at all), then every
    quarter hour + 20 s, and on events - new prices (tariff listener), forecast update (new
    listeners on the PV and load services), configuration change (`site`, `region`, `tariff`,
    `forecast`, `battery`, `goals`, `devices`), user override, manual replan, **deviation**: live
    SoC differs from the plan's SoC at `now` (linear inside the interval) by > 10 %-points, or the
    grid power differs from the planned net power by > 1500 W for 30 min (10-minute exponential
    average, so a cloud does not count; a new plan restarts the count). Event runs are debounced
    (>= 60 s between starts, merged into one run), single flight, the solve runs in a **daemon
    thread** with budget 8 s (`SUNCORE_PLANNER_BUDGET_S`, `SUNCORE_PLANNER_MIP_GAP`), warm start
    from the previous plan, a run is abandoned after budget + 30 s (no second thread starts while
    the first lives). Inputs: `Runtime._collect_planning` -> `build_inputs` (tariff prices, blended
    PV, base-load forecast, `RiskView` buffer/target/`evening_at`, battery section, live SoC from
    the flows, `export_limit_w(site)`, `grid.import_limit_w`, devices). A plan snapshot is stored
    per run (`PlanSnapshotStore`).
  - Hand-over: `ControlStack(plan_provider=planning.active_plan)` (`set_plan` for static use),
    read once per tick; the provider survives stack rebuilds. A failed or skipped run keeps the last
    good plan; **after 2 h it is stale** (flagged in the API) **and the controller gets
    `Plan.default()`**, as it does when every interval is in the past.
  - Controller (for the `control-safety` review): `battery_share_available` judges
    `BatteryBand.priority_until_soc_pct` against the live SoC every tick (battery first while the
    SoC is below it or unknown, before `charge_from_surplus`); a band applies only while a plan
    interval covers `now` (before: a non-default plan outside its intervals used the empty default
    band). Nothing else changed: device bands only restrict (`allowed`, `max_power_w`),
    `preference` is ignored, no `BatteryAction` is ever proposed and the gate still rejects it.
    Tests: `tests/controller/test_plan_handover.py` (hypothesis: bands never raise a device above
    the plan-less controller, below the priority no more surplus for consumers),
    `tests/sim/test_planner_shadow.py` (scenario `planner_in_shadow`: plan every quarter hour, bands
    followed, nothing written, gate rejects `BatteryAction` with a plan present).
  - API: `GET /api/v1/plan?hours=` (`PlanView`: plan, `stale`, `expired`, `in_use`, `fallback`),
    `GET /api/v1/plan/status` (`PlanStatusView`), `POST /api/v1/plan/replan` (202, `ReplanAck`,
    409 `api.error.planning_unavailable`), live message `{type: "plan"}` (summary only). New
    catalogue keys `planner.status.*` and `api.error.planning_unavailable` (en/de); OpenAPI and
    frontend types regenerated, no UI (4-H).
  - Runtime: created in `prepare()`, started after the stack, cancelled with the storage tasks and
    awaited **after** `stack.shutdown()` (a hanging planner never delays the release - tested).
    Demo: the planner runs on the simulated Tibber prices, Solcast/Open-Meteo/Forecast.Solar and the
    cold-start load profile (`/api/v1/plan` shows a real plan; the same inputs give the same plan).
  - Not done / follow-ups: the stored snapshots are not read back at start (the first run after a
    restart plans cold); device `max_w` comes from the configuration, not the learned power models;
    `UNVERIFIED`: planner run time on aarch64 and under the full demo load.

- **Phase 4 – PR 4-E (WP6 load forecast, WP7 risk model; branch `phase-4/e-load-risk`, stacked on
  4-D)**:
  - WP6 `suncore/forecast/load.py` + `load_service.py`: base load = `site.house_w` minus every
    configured device's `device.<id>.power_w` (the history rebuild also fetches the devices' power
    sensors; unmeasured quarter hours count 0 W, are reported per device and are left out of a day
    type's profile once it has 7 fully measured days, else kept and flagged; negative rests
    clipped); profiles per day type (workday/weekend/holiday) × 96 wall-clock quarter hours,
    exponential smoothing (half-life 21 days, 56 days look-back), clipped z-score (3 × max(MAD
    scale, ½ median)), shrinkage of thin day types (holiday → weekend → all non-holiday days),
    kernel smoothing; optional temperature term (heating/cooling degrees 15/22 °C, bounded 0–300
    W/K, active after 14 days with a recorded temperature, offset ≤ 50 % of the day's mean, only for
    forecast days with ≥ 20 h of temperatures); residual P10/P90 per time-of-day block; cold start
    (< 3 days: flat at the 7-day mean, P10 0.5×, P90 2×), `no_history` without data. Day types: the
    `workday` sensor's state for today + `days_offset`, `workday.check_date(d − days_offset)` for
    other dates (Home Assistant adds the offset; ≤ 12 calls per refresh, answers cached in
    `learned_param` `load_profile/day_types` with a fingerprint of entity, `workdays`, `excludes`
    and offset, dropped when it changes), else the weekday rule. Refresh every 15 min, refit
    hourly/after the history rebuild/nightly, stored hourly as kind `load` source `load_profile`,
    accuracy (all quarter hours) in the nightly job; `GET /api/v1/forecast/load?hours=48`; the
    controlled devices' histories are kept for 4-F. Benchmark (n5): a refit (read 56 days of house,
    3 devices and temperature from SQLite + fit) takes 118–163 ms on the session's amd64 container
    (4 vCPU), the forecast itself about 1 ms; aarch64 not measured yet.
  - Config: `forecast.load.workday` / `forecast.load.weather` (additive in `forecast` v2, domain
    checked; the wizard form carries them through, UI in 4-H). `workday.check_date` joined
    `READ_ONLY_ACTIONS`. The weather entity's `temperature` is recorded as
    `weather.temperature_c` (HA keeps no statistics of weather entities).
  - WP7 `suncore/forecast/risk.py` + `risk_learning.py`: windows `rest_of_night` (before
    sunrise), `rest_of_today`, `night`, `next_day`, `bridge` (night + next day, one quantile) and
    `day_and_night` (coming daytime + the night after it) anchored on today's sunset from the PV
    forecast; weather class from the PV clear-sky index; net energy quantile `learned` (relative
    errors per window × class, pooled per window when thin) from 30 days with samples, else
    `source_quantiles` (two-piece normal, PV and load independent, slot quantiles summed) or
    `default_band` (PV forecast without a band: default relative band per weather class);
    `evening_target_pct`, `morning_buffer_pct` (one quantile over `day_and_night` against the
    median night need), floor `core.battery.effective_reserve_pct` (ADR-0013), `prefer_full` =
    max SoC, manual limits and max SoC always win; `goals.battery_target` is a floor of the
    evening target, its time is ignored there (timed constraint in 4-F; maintainer 2026-10-22).
    Reasons `planner.battery.morning_buffer` / `.evening_target` (en/de, naming the risk level).
    Nightly samples from the issues at sunrise + 1 h and sunset − 1 h against measured PV and
    base load, only for windows both forecasts cover, without clipped daytime quarter hours,
    kept 365 days (`learned_param` `net_error`). `GET /api/v1/forecast/risk`. Hypothesis tests
    (monotone in risk level, in PV and load uncertainty and on the learned path, limits incl.
    the site min SoC) and the four scenarios.
  - Review fixes (M1–M5, m2–m8, n1–n6) on 2026-10-22/23, see the plan's deviations.
  - Demo: deterministic workday sensor and weather entity (`binary_sensor.sim_workday_sensor`,
    `weather.sim_home`, both actions answered), battery model 10 kWh / 5 kW (still observe only).
  - Decisions and deviations: plan §"Deviations during implementation" (PR 4-E).
  - Follow-ups: the planner (4-F) consumes `morning_buffer_pct`/`evening_target_pct` and the
    device histories; no UI yet (4-H); ML load model in shadow (4-I).

- **Fix after the first live test (2026-10-16, branch `fix/device-freshness`)**: on the maintainer's
  HA the SunSpec battery SoC (84 % for 25 min) and the `smartthinq_sensors` power sensors (5 W for a
  day) never advance `last_reported` for an unchanged value, so SunCore marked them "stale" and the
  site watchdog paused all devices. Now only the grid meter is a heartbeat
  (`EntityCache.set_heartbeats`, set by `ControlStack`); other entities stay valid while available.
  The watchdog follows the cache flag; a frozen inverter is still caught by `unavailable`, frozen PV
  in daylight and a silent grid meter. The measured power model counts the age of the information:
  an unchanged value of an unchanged device is current, after a state change the age runs from the
  change. Scenario `sensor_dropout` now silences the grid meter (trips) and the SoC (does not).
  Safety review fixes: a sample from before a state change reports its true age (the controller
  keeps the level's power until a slow sensor catches up); grid import always counts as a deficit
  in the back-off (a frozen battery reading cannot hide it); an import/export pair is one
  heartbeat group. Not detected directly: a battery/SoC/device reading frozen while available
  (bounded by the grid-measured back-off). `UNVERIFIED`: whether a hung Plenticore shows
  `unavailable` or frozen SunSpec registers.

- **Phase 3 – PR 3-C (WP10 UI, WP11 docs, branch `phase-3/c-ui`)**, stacked on 3-B:
  - UI: *Now* view with a **Surplus** card (free surplus, deficit, allocated) and a card per
    device (state, estimate with uncertainty, mode switch with a confirmation and the device's
    identity token for automatic, held/handing-back/paused badges, **Why?**, **Take over
    manually**: hold N minutes, pause until/resume, **Resume now**); the **Emergency stop** in
    the header of every view with a banner and a confirmed **Back to normal**; assisted proposals
    (confirm/dismiss, expiry); the **Decision log** view with device and outcome filters; the
    operating mode step sets only the overall mode (device modes live on the Now view); the
    device editor offers switch-on opt-in, override policy and `power_update_s`. Vitest and
    Playwright E2E (emergency stop, mode switch, proposals, decision log, device editor), axe.
  - Screenshots from the demo: `now-*`, `decisions-*`, `estop-*` (light/dark, phone/tablet, en/de).
  - Docs: user guide `docs/user/<lang>/surplus-control.md` (en/de: how surplus control works,
    modes, climate, water heater, manual changes, emergency stop, fail-safe, "Why?", a "first
    device to automatic" walk-through, known limitations); quick start en/de, app `DOCS.md`
    (stable and beta), README, compatibility intro (generator text) updated; architecture §4–§6
    rewritten to the implementation (per-tick order, gate order, release paths, observed-baseline
    rule, handover/recover; all 8 Mermaid diagrams parse); plan "Deviations during
    implementation".
  - The goals note `goals.stored_note` now says the controller already uses the battery target
    and "climate before export".

- **Phase 3 – WP2 contracts v2 and WP3 safety layer** (branch `phase-3/a-safety`, signed off
  by `control-safety`):
  - `core`: actions `SetpointAction` (absolute °C + base), `StepAction`, `ModeAction`,
    `ReleaseAction`; `Decision.schema_version = 2`, kinds `proposed`/`held`/`failed`,
    fields `expires_at`/`held_until`/`observed`; `Origin.SAFETY` for release intents;
    `decision_from_payload` loads Phase 1 v1 rows (`DecisionLog.latest` skips newer or
    corrupt rows). `core/control.py`: `PowerEstimate`, `DeviceState`, `ControlSettings`
    (tick 5 s, smoothing 60 s, deficit 30 s / 50 Wh, proposal TTL 120 s, release deadline
    10 s), minimal `Plan` with `Plan.default()` and per-interval `DeviceBand`/`BatteryBand`.
    `DeviceConfig` additive: `allow_switch_on`, `switch_on_hvac_mode`, `override_policy`
    (`next_day` 04:00 | `hours` 1–48). `core/identity.py`: device identity (id +
    `template_id` + entities) for WP4's shadow fallback. `effective_mode = min(global, device)`.
  - `safety`: `invariants.py` (pure, hypothesis-tested: no SunCore-caused import, import
    limit, comfort band, min run/pause, max switches per sliding hour, write rate, write
    only on change, battery reserve guard, climate set-point while off needs
    `allow_switch_on`); `gate.py` with an injected `ModeProvider` (default all shadow),
    `DeviceLookup`, `StateReader`, `InputsProvider`, `RuntimeStore`; shadow → `shadow` (with
    the invariants that would have fired), assisted → `proposed` + `confirm` as
    `Origin.USER`, automatic → invariants → try-acquire → takeover baseline → `apply()` →
    settle → read-back → `accepted`/`failed` (3 failures trip the device);
    `overrides.py` (value-based, context id only as positive evidence, hold until next day /
    N hours, boost/pause/resume); `watchdog.py` (stale with cadence allowance, unavailable,
    invalid unit, jumps, frozen PV in daylight, balance, connection loss; site faults pause
    all, device faults one; SafeState → cool-down → normal); release on emergency stop
    (persisted through `RuntimeStore`), shutdown (deadline), mode → shadow, site trip,
    reconnect and crash recovery (`recover()`); `Actuator` gained `release(baseline)` and
    `apply()` returns the HA context id. Boundary tests: only `safety/gate.py` calls
    `apply()`/`release()`, drivers importable by `safety`/`app` only. 43 `safety.*` reason
    keys in en/de. Coverage safety ≥ 96 %.
  - App: `Runtime.start()` calls `gate.recover()`, `Runtime.stop()` calls `gate.shutdown()`
    before closing the HA connection; ~~the gate still has no actuators and the all-shadow
    provider until WP9~~ (done in WP9: real drivers, `ConfigModes`, recover after the first
    connect). OpenAPI and frontend API types regenerated; the decision list knows
    the new kinds; the device editor carries the Phase 3 fields through (UI in WP10).
  - Decisions: the watchdog judges a slow sensor's staleness as
    `age > cadence × stale_cadence_factor` (2) because `subscribe_entities` has no
    `last_reported`; ~~the app sets cadences per entity (WP9)~~ (done, `suncore/app/cadence.py`).
    `release()` bypasses min
    run/pause (restoring the user's state is the safe direction; noted as a risk).

- **Phase 3 – WP4 storage** (branch `phase-3/a-safety`): migration `0003_runtime.sql` (DB
  schema 3: `device_runtime`, `safety_state`, `power_model_state`; `decision(ts)` was already
  indexed in 0001). **0.3.x cannot open a schema-3 database (it refuses newer ones): export the
  configuration before updating.** `storage/runtime.py`: `SqliteRuntimeStore` (the gate's
  `RuntimeStore`; an unparseable payload is logged and counts as missing, an unreadable
  emergency-stop flag counts as stopped) and `PowerModelStateStore` (JSON per device, for WP5).
  `DecisionLog.prune(now, max_age_days=30, max_rows=200_000)`, run hourly by
  `Runtime` (time and sleep injected). Device identity rule replaces the id-only rule in
  `ConfigStore`: put/import/restore of `devices` drop the `operation` mode of devices whose id,
  template or entities changed; restore of `operation` drops modes of devices whose identity
  differs from the one recorded when the mode was set (`safety_state.mode_identities`; no record
  = shadow); an import always resets the global mode to shadow. The gate uses
  `SqliteRuntimeStore`; the demo uses it on its throw-away database, never `/data`.
  ~~Known gap: a plain `operation` put is trusted (a stale UI document could re-add a mode for a
  device that was just re-mapped).~~ Closed in WP9: the full document may keep or lower modes
  only (`409 api.error.mode_raise_rejected`); raising goes through `PUT /api/v1/operation/mode`
  and `PUT /api/v1/operation/devices/{id}`, the latter with the device's identity token.

- **Phase 2 completion from the live LG check (PR #18, 2026-10-14)**: the maintainer runs the
  same AC through `lg_thinq` and HACS `smartthinq_sensors`. (1) Climate templates
  (`lg.thinq_climate`, `generic.climate`) have an optional `power` role without
  `same_device_as` (sensor of another device); mapping it
  preselects the `measured` power model (switchable), candidates of a device with the same
  name rank first (`name_affinity`, query parameter `device_name`, no schema change). The
  `energy_current` sensor polls every 5 minutes, so Phase 3 must treat it as slow (noted in
  the templates). (2) System check warning `wizard.check.duplicate_unit` (same control entity
  twice, or climate entities of different registry devices on different platforms with equal
  device names; a renamed device silences it) and a hint in the device editor; the check never
  blocks. The demo has a second, smartthinq-like
  device. `UNVERIFIED`: the `smartthinq_sensors` source was not read, only its entities on
  the maintainer's HA.

- **Phase 2 merged** (#13, #14, #15, #16, 2026-10-13). First automatic beta
  bump on `main` worked (`0.3.0-beta.50`, pushed with `RELEASE_PLEASE_TOKEN`).
  The maintainer installed it on HA OS 18.3 / core 2026.10.1 (amd64, KVM),
  completed the wizard and sees plausible live flows; the panel follows the HA
  theme accent colour. App memory 65 MiB, CPU < 0.1 % idle. LG ThinQ facts
  checked live (see `research/device-integrations.md`, "Live check").

- **Phase 1 accepted** (2026-10-13): the maintainer installed the beta on
  HA OS, mapped grid, PV, battery (SunSpec Modbus) and house sensors and
  sees live flows. Finding from the test fixed in #9: sensors without a
  `device_class` (e.g. SunSpec state of charge, unit `%`) were missing in
  the picker; the picker is now a searchable combobox filtered by unit.
  Beta app version `0.2.0-beta.1`.

- **Phase 1 merged** (#3, #4, #5, #6, 2026-10-13). The beta image
  `ghcr.io/pxljake/suncore-beta:0.1.0-beta.0` is published for amd64 and
  aarch64; the smoke test on `main` passed on both (`/health` 200 after
  2–3 s, 49.9 MiB container memory after 60 s idle, clean SIGTERM stop).
  `hassio_api` removed until MQTT needs it (Phase 6).

- Phase 0 merged (PR #1): brief, research, ADRs 0001–0011 accepted,
  architecture, roadmap, scaffold, CI, agent team.
- Phase 1 plan `docs/plans/phase-1.md` approved (defaults of its §6: four
  stacked PRs 1-A…1-D, `sensor.sim_*` demo entities, browser language,
  repository stays private).
- **PR 1-A** (WP1, WP2, WP4):
  - `core`: units and HA unit parsing (unknown unit → `invalid`, never
    guessed), signals with quality, `SiteConfig`, energy balance
    (`house = pv + grid − battery`, worst input quality propagates).
  - `core.decisions` + `safety`: `Reason`/`Intent`/`Decision` contracts,
    `SafetyGate` structurally fixed to shadow mode, global emergency stop,
    `Actuator` protocol without implementations; AST boundary test
    (single writer, `call_service` only in `ha`/`safety`, dependency rule).
    Signed off by `control-safety`.
  - `storage`: `/data/suncore.db` with SQL migrations (`PRAGMA
    user_version`), refusal of newer DBs, `ConfigStore` with per-section
    document migrations, atomic export/import, decision log.
  - Coverage gate ≥ 80 % for core/safety/planner/controller in
    `check.sh` and CI.
- **PR 1-B** (WP3, WP5, WP6), stacked on 1-A:
  - `ha`: WebSocket connector (Supervisor and direct transport), auth,
    compressed `subscribe_entities`, heartbeat, reconnect with jittered
    back-off and resync, entity cache with staleness, token redaction,
    picker candidates, rate-limited `ServiceCaller` reserved for `safety`.
    Message shapes verified against HA core (research note updated).
  - `api` + `app`: FastAPI with `/health` (liveness: 503 only for DB
    failure, `auth_failed`, `closed`), site config, entities, export/import
    (1 MiB limit), decisions, live WebSocket (≤ 2 flow updates/s); peer
    check (Supervisor → `172.30.32.2` only, otherwise loopback unless
    `SUNCORE_ALLOW_ANY_PEER=1`); composition root with graceful SIGTERM.
  - `sim`: virtual house, scenarios `summer_day`, `winter_fog`,
    `sensor_dropout`, `FakeHAConnection` with fault injection; demo mode
    (`demo_mode` option / `SUNCORE_DEMO=1`) runs without HA and never
    touches `/data`.
  - Image installs the hash-checked set from `uv.lock` from PyPI only
    (the base image's HA wheels index lagged behind, see PR #3).
- **PR 1-C** (WP7, WP8), stacked on 1-B:
  - `frontend/`: Svelte 5 + Vite + TypeScript, hash routing and relative
    URLs (ingress base path), "Now" view with animated SVG energy flow,
    quality states, shadow-mode badge, connection indicator, demo banner;
    temporary settings page for grid/PV/battery/house entities with sign
    inversion and live preview; theme tokens named after HA variables with
    `prefers-color-scheme` defaults. Bundle 40 kB gzip (budget 250 kB).
  - i18n: `intl-messageformat`, `en.json`/`de.json` (same keys, ICU
    checked), ESLint rule against hardcoded UI text,
    `scripts/check_reasons.py` keeps backend reason/error keys and
    catalogues in sync.
  - Tests: Vitest, Playwright E2E against the real demo backend (desktop,
    phone, tablet; axe WCAG 2.1 AA), CI job `frontend`.
  - Demo starts at `SUNCORE_DEMO_START_TIME` (default 10:00); screenshots
    in `docs/assets/screenshots/` come from the real demo.
- **PR 1-D** (WP9, WP10), stacked on 1-C:
  - Image: Node stage builds the SPA (`node:22-alpine` pinned by digest),
    Python stage installs the locked set from PyPI and serves
    `frontend/dist`; s6 service (`/etc/services.d/suncore`) execs
    `python3 -m suncore`; `HEALTHCHECK` polls `/health` over loopback.
  - Smoke test (`scripts/smoke_image.sh`): demo mode, `/health` within
    90 s, idle container memory ≤ 300 MiB after 60 s, clean stop (exit 0,
    `app.stopped`); PRs on amd64, `main` per architecture after the push.
  - App option `demo_mode` (en/de). Verified against Supervisor source:
    `panel_admin` and `ingress_port` defaults, ingress gateway
    `172.30.32.2`, ingress path always has a trailing slash.
  - Docs: app `DOCS.md`, quick start en/de with screenshots,
    `CONTRIBUTING.md` (frontend, local stack), README status.
- R1 cleared: every planned Python dependency has cp313 musllinux wheels
  for aarch64 and x86_64 or is pure Python.

- **Phase 2 – PR 2-A** (WP1–WP4, branch `phase-2/a-foundation`):
  - Research (WP1): HA interfaces verified against `home-assistant/core` dev
    `14f093e` (registry list commands and shapes, registry update events,
    `energy/get_prefs`, entity id regex), LG ThinQ, Tibber, Solcast; notes in
    `research/ha-websocket-api.md` and `research/device-integrations.md`.
  - ADR-0012 (accepted): template schema v1 reviewed before freeze
    (compatibility policy and "Schema v1 changelog" in `templates/README.md`).
  - Device model (`suncore/core/devices.py`) and template schema v1
    (`suncore/templates/`: models, loader, matcher; JSON Schemas generated
    into `templates/schema/`). Templates are plain YAML, anchors/aliases are
    rejected. `DeviceConfig.mode` removed: the `operation` section is the
    only store of the mode of operation.
  - 8 templates: `lg.thinq_climate`, `generic.switch`, `generic.climate`,
    `generic.number_load`, `tibber.price`, `solcast.pv`, regions `de` and
    `generic`; each with a conformance scenario in `tests/scenarios/`.
  - `sim`: registry (entity, device, area, Energy-dashboard data) and demo
    devices (LG-like AC with hourly Wh counter, pool plug, Tibber-like price,
    Solcast-like forecast) served by `FakeHAConnection`.
  - Storage (WP4): site v2 (`PvArray`, v1 → v2 migration), 7 config sections
    (`site`, `region`, `tariff`, `devices`, `goals`, `operation`, `wizard`),
    `config_history` (migration 0002) → DB schema version 2: 0.2.0 refuses a
    newer DB; `ConfigStore.get_section`/`put_section`; `TariffConfig.import_`
    is stored as `import` (old key still loads).
  - Review fixes: PV rows keep their array in the settings form, YAML alias
    rejection, template sources re-fetched from GitHub mirrors, one shared
    region id pattern.
  - `UNVERIFIED`: all three integration templates come from source reading
    only (no live HA or hardware); see the table below.

- **Phase 2 WP5/WP6 (PR 2-B, branch `phase-2/b-api`)**: `HAConnection` registry list commands,
  `energy/get_prefs`, registry update events and the read-only `call_action_response`
  (allowlist `READ_ONLY_ACTIONS`); `EntityCatalogue`, role detection (`ha/detect.py`), Energy
  dashboard import, config section/history/template/wizard endpoints, `operation` stored only
  (boundary tests keep the gate away from it). `UNVERIFIED`: `unknown_command` when the
  energy integration is not loaded; the older grid prefs format on a real older core.

- **Phase 2 – PR 2-C (WP7–WP9, branch `phase-2/c-wizard`)**, stacked on 2-B:
  - Frontend: setup wizard at `#/setup[/<step>]` (10 steps: region, Energy dashboard import, grid,
    solar, battery, tariff, devices, priorities and goals, operating mode, summary with system
    check), resumable via `wizard.completed_steps`; first start redirects once into it (Phase 1
    users with a site but no wizard section land in the pre-filled wizard). `RolePicker`
    (ranked candidates with the criteria that matched, live value, optional "also hidden and
    diagnostic entities") and `SignCheck` (live plausibility question, one-click sign
    inversion). Device gallery/editor from templates, drag-and-drop and keyboard priorities.
    Settings overview with per-step edit links, config history/restore and export/import;
    "Run setup again" restarts the wizard. Unsaved edits are guarded (leave confirmation,
    Settings never reloads over them, `beforeunload`).
  - Review fixes (this commit series): site role `price` (dynamic tariff with only a price
    sensor); template protection defaults (`settle_s`, hysteresis, `max_switches_per_hour`)
    carried into new devices; new device priority = highest + 1; "Finish" disabled while the
    check reports an error; German wording; catalogue/ICU split into own chunks.
  - Decisions: (1) the demo starts fully set up (region `de`, dynamic Tibber-style tariff,
    forecast, three devices instantiated from `lg.thinq_climate`/`generic.switch`/
    `generic.number_load`, goals, shadow, wizard completed); the wizard stays reachable.
    (2) Phase 1 users are redirected once into the pre-filled wizard. (3) The forecast source
    is its own optional section `forecast` (tariff is schema v2; tariff v1 forecast fields are
    moved on start-up and on import); the system check only notes a missing forecast.
    (4) `RolePicker` can include hidden/diagnostic entities.
  - Bundle: 107.5 kB gzip in total (budget 250 kB); the first-paint chunks are `index`
    11 kB, `icu` 10 kB, `catalogues` 22 kB (gzip), the wizard steps load on demand.
  - `UNVERIFIED`: the theme bridge (copying HA theme variables from the ingress parent document)
    is unit-tested with fake parent windows only, not on a real HA (table below).

- **Phase 2 – PR 2-D (WP10, branch `phase-2/d-docs`)**, stacked on 2-C:
  - Docs: quick start en/de rewritten around the setup wizard (one section and
    screenshot per step, shadow mode, settings/history/export-import,
    troubleshooting, honest "cannot do yet"); "Contribute a device template"
    guide en/de (`docs/user/<lang>/device-templates.md`); app `DOCS.md` (stable
    and beta), README status and `templates/README.md` updated.
  - Generated compatibility list `docs/user/compatibility.md` from the template
    loader (`scripts/gen_compat.py`, `--check` in `scripts/check.sh` and the CI
    python job, tested in `tests/test_gen_compat.py`).
  - Beta version derivation in `build.yml` and the E2E/screenshot work were done
    in PR 2-D, see Open – Phase 1.

## Open – Phase 4

- PR order (stacked, each on the previous, `phase-4/<letter>-<topic>`): 4-B storage/time series
  (WP3) → 4-C tariffs (WP4) and 4-D PV forecast (WP5) → 4-E load + risk (WP6, WP7) → 4-F planner and
  runtime (WP8, WP9) → 4-G battery control (WP10) → 4-H frontend + docs (WP11, WP13); 4-I ML load
  (WP12, optional, after 4-E). Merge only after the Phase 3 acceptance.
- **ADR-0013 must be accepted before WP10** (4-G). Its contract changes `BatteryBand.grid_charge_max_w`
  / `discharge_lock` landed with WP8 (additive, planner-only, the controller ignores them);
  `BatteryAction` `None` = baseline and `battery_identity` land at the start of 4-G.
- **WP9 done** (4-F, see Done); next: 4-G battery control (WP10, ADR-0013 first).
- **PR 4-C (WP4, branch `phase-4/c-tariff`)**: `suncore/tariff/` - a template-driven price adapter
  (`engine.py`: action or attribute source, list or mapping shape, per-MWh conversion, native grid
  from `end - start` or the timestamp spacing, 15/60-minute mixes as segments, gaps `None`, DST
  safe; a kWh/MWh unit mismatch between sensor and template is refused), fixed and HT/NT sources,
  `PriceFormula` on spot-only sources only, export fixed/tiered/none, the region's
  `no_remuneration` rule (export price 0 in slots with a negative spot price; for all-in sources the
  import end price is the proxy), `TariffService` (refresh hourly, on entity change for attribute
  sources, every 15 min from 13:00 local until tomorrow's prices arrive, back-off after failures,
  throttled calls are not failures, good data is never replaced by an empty or failed fetch,
  prices stored in `timeseries.db`) and `GET /api/v1/prices?hours=48`. Templates `tibber.price`
  (updated), `nordpool.price`, `entsoe.price` (+`_mwh`), `epex_spot.total_price`/`market_price`,
  `octopus_energy.rates`, `generic.price_list`; schema changes are additive (templates README
  changelog). Wiring in `suncore/app/adapters.py`; demo and simulator: the fake answers
  `tibber.get_prices` through the same template path, scenario `negative_prices`.
  `PriceFormula.end_price` is the 4-A `apply` under a name the actuator boundary test does not
  reserve. `UNVERIFIED`: see the price rows in the table below.
  Review fixes: a failing attribute source backs off; a fetch without a known price fails and keeps
  the old data (unknown slots are carried over, `quality.carried_slots`); the old tail never overlaps
  new data; Nord Pool `date` is the CET delivery day (`date_tz`, research note in
  `research/integrations.md`) and "tomorrow covered" ends at the market's day end; a source in
  another currency than the region is refused; `segments_from_points` (4-A) treats a multiple-step
  hole at the list end as a gap. A spot-only price without a formula is a system-check warning and
  `GET /api/v1/prices` says `formula_missing: true` with the top-level `price_includes`.
  **For 4-F: the planner must refuse `spot_only` import prices without a formula (it must not
  plan with bare market prices).** Known: the error argument `available` of
  `tariff.error.ambiguous_series` carries the home names/areas of the response (shown to the user,
  they are needed to choose one).
- Maintainer questions from 4-A: plan §6 "Offene Fragen aus PR 4-A" (grid-charge schedule
  precedence, VAT on negative spot prices, export tiers, grid charging on the Kostal).

## Open – Phase 3

- **WP5/WP6 (branch `phase-3/b-controller`)** done: `suncore/devices/power/` (measured,
  counter-derived, learned RLS, nominal, grid-balance validator, `create_power_model`) and
  `suncore/devices/drivers/` (`OnOffDriver`, `ClimateDriver`, `StepsDriver` incl. the new
  `relays` control kind, `NumberDriver`, `ModeDriver`, `create_driver`; not wired before WP9).
  `ServiceCaller.write()` and `ServiceResult.context_id` carry the HA context id; the connection
  flood floor is 0.25 s. Templates `generic.water_heater_steps` and `generic.water_heater_relays`
  (simulator only, `UNVERIFIED`: the maintainer has no water heater). For WP7/WP9: the controller
  should round set-points with `suncore.devices.setpoint.snap_setpoint` (device step from the
  `target_temp_step` attribute) so the written value equals the intent; the composition root
  passes `ServiceCaller.write` and an entity-cache lookup to `create_driver` (boundary rule 3 forbids
  `service_caller()` outside `ha`/`safety`, so the wiring needs a small accessor in `safety`/`ha`).
  ~~Wiring~~ done in WP9: `suncore/safety/wiring.py` (`build_actuators`) takes the writer out of
  the connection; rule 3 is unchanged.

- **WP7 controller (branch `phase-3/b-controller`)** done: `suncore/controller/` (pure core,
  time injected, depends on `core` only - now enforced by rule 4 of `tests/safety/test_boundary.py`):
  `surplus` (available `a = export + battery share + real draw of the controllable devices`,
  median over `smoothing_s`, uncertainty = max(robust sigma, median - P10) so two-level cloud
  flicker widens the band; the window is re-based when a device joins/leaves the controlled
  set), `levels` (one level table per control kind; climate shifts snapped with
  `core.setpoint.snap_setpoint`, `CallableQuantiser` for the driver wiring), `hysteresis`
  (per-device machine idle/steady/pending/in_flight/retry, intents only on change),
  `allocation` (priority allocation, committed draws, the gate's rules mirrored incl. the
  per-tick free-surplus budget, deficit back-off per plan §6 incl. budget, reserve and import
  limit) and `loop` (`Controller.step` + `ControllerLoop`; the per-tick call order for WP8/WP9
  is in the `loop` module docstring). `suncore/devices/conversion.py` moved to
  `suncore/core/conversion.py` (re-export shim kept). 26 `controller.*` reason keys (en/de),
  `scripts/check_reasons.py` checks `suncore/controller/reasons.py`. Battery share without any
  goal: *not* available (battery first) - `UNVERIFIED` against the maintainer's intent.
  Tests: `tests/controller/` (hypothesis allocation invariants, cloud flicker switch counts,
  measured vs counter-only climate, deficit back-off, real gate and real drivers).
  The gate now claims every accepted load increase for its `GateInputs` snapshot (`ts`) and
  subtracts the claims from the free surplus for later intents of that snapshot, incl.
  confirmed assisted proposals; failed/rejected writes return their claim
  (`tests/safety/test_claims.py`, review 3-B G1).
- **WP8 simulator (branch `phase-3/b-controller`)** done: `suncore/sim/devices.py` (climate with
  RC room model, inverter compressor, LG hourly integer-Wh counter and 5 min polled power sensor,
  `int()` truncation, 2 s sleep and delayed cloud push; stepped water heater in the `select`,
  `relays` and `water_heater` variants with a tank model and thermostat cut-off; switch and
  number loads), `FakeHAConnection` executes the verified write services with HA's error shapes,
  contexts (call context within 5 s, else an id-only one), faults (unavailable, stale, disconnect,
  frozen) and manual user events; the device draw is part of the house balance. Ten scenario files
  (`cloud_flicker`, `climate_min_run`, `climate_counter_only`, `climate_measured_slow`,
  `sensor_dropout`, `inverter_hang`, `ha_disconnect`, `manual_override`, `water_heater_steps`,
  `grid_limit`), `suncore/sim/stack.py` (`ControlStack`: cache, real drivers, real power models,
  controller, real gate, watchdog - the reference composition; `demo_stack` for the demo) and
  `suncore/sim/runner.py` (`ScenarioRunner` in virtual time: every sleep waits on simulated
  time). Tests `-m simulator`: per-scenario bounds on switches, hold times and SunCore-caused
  import (at most 3 % of the energy moved to the devices), fail-safe paths (shutdown, crash and
  restart, emergency stop, mode change, disconnect, watchdog) end in the baseline, measured vs
  counter-only equivalence, a 7-day run (`slow`, runs in CI). Demo devices are physical now (the
  default demo has a demo user that runs the unit and the pump by the clock); the controller on
  them in shadow mode is `ScenarioRunner(..., configs=demo_devices(catalog).devices)` /
  `demo_stack(...)`. Findings fixed in separate commits (gate: switch history judged before the
  write, baseline after an override; controller: base set-point with a late state, switched-on
  flag, level 1 of a running unit). `EntityState.context` (`StateContext`) is new; ~~the WebSocket
  client does not parse the `c` key yet (only the simulator fills it)~~ (parsed in WP9).
  ~~Still open for WP9: set the power-model cadence of idle sensors~~ (done: 60 s default,
  `power_model.power_update_s`, counters `counter_update_s` or 300 s), ~~a failed release
  while HA is unreachable is retried every tick by `check_device`~~ (done: the stack skips its
  ticks while the connection is lost). Still open: the controller does not know about the
  gate's releases (the first intent after one can meet `safety.min_pause`).
- **WP9 runtime and API (branch `phase-3/b-controller`)** done. `control-safety` review of
  3-B: four `fix(...)` commits (handover on config changes, switches recorded when sent, release
  gives back unjudged manual changes, level 1 with a base outside the comfort band, stale
  CONNECTED handler, lazy runtime load race, pause releases, holds never shortened). Final
  review: orphaned runtimes of removed/re-mapped devices are released with the old actuator
  (`predecessor` gate) or reported (`safety.orphan_dropped`); a water heater's `max_c` is
  enforced through its tank probe (`core.control.heat_capped`, controller cap + gate invariant
  `check_heat_limit`); time zone learnt before the first recover; `app.control` is mypy-strict.
  *Composition*: `suncore/app/control.py` `ControlStack` is the one control composition (the
  simulator's WP8 stack was generalised, not duplicated; `sim/stack.py` re-exports it): drivers
  via `suncore/safety/wiring.py` `build_actuators(connection, devices, make)` (the connection's
  writer never appears outside `ha`/`safety`/`sim`; boundary tests: only `app.control` builds the
  `SafetyGate`, only `ha`/`safety.wiring`/`sim` name `service_caller`), power models restored from
  and saved (5 min, shutdown) to `PowerModelStateStore`, `ConfigModes` (the `operation` section,
  `gate.sync_modes()` after every change), gate tz = region time zone (else HA's `time_zone`,
  else UTC with a warning), `ControllerLoop` ticking every `controller_interval_s` in the
  documented order. `Runtime` owns one stack and rebuilds it when `devices`, `site` or `region`
  change (the old stack hands its devices over: `gate.handover(keep)` releases devices with an
  unchanged identity and control without breaking min run/pause - the new stack executes the
  deferred release - and changed devices at once; the emergency stop and the device runtime
  survive in the store). Life cycle: `recover()` after
  the first successful connect (never before; observations are ignored until then - every
  release judges the observed state itself, so a change made meanwhile is given back),
  `connection_lost`/`connection_restored` from the state listener, ticks are skipped while the
  connection is lost, stop = tick loop, `loop.close()`, `gate.shutdown()`, save models, then
  close the connection.
  *Demo*: the same stack on `FakeHAConnection` with `DemoClock` (accelerated scenario time shared
  by cache, gate and controller; tick = 0.1 s of real time), shadow by default, never `/data`.
  Automatic in the demo works but the fake delivers the device push with the next house step, so
  the first read-back of a write is a `failed` `readback_mismatch` followed by `late_confirmed`.
  *HA client*: `c` (string/dict, diff-merged) and `get_states` `context` are parsed into
  `EntityState.context`; no second periodic `get_states` (the 60 s resync is the refresh,
  research note). Cadences: `suncore/app/cadence.py`; new optional `power_model.power_update_s`
  (the demo AC needs none: the simulator's heartbeats). The watchdog only watches the entities
  the power model reads (an informational `energy_yesterday` cannot trip a device).
  *API* (all errors are `api.error.*` / `api.validation.*` catalogue keys, en/de added):
  `GET /api/v1/control/state` (`ControlState`: `ts`, `active`, `emergency_stop`, `global_mode`,
  `site` {available/total/free surplus, allocated draw, deficit, grid, battery, SoC}, `devices`
  [{state, estimate value/uncertainty, three modes, controller phase/level, `identity`,
  `held`/`held_until`, `release_pending`, `paused`, `reasons`, `last_decision`}], `proposals`),
  `POST /control/emergency-stop` and `/clear` (`{active}`), `POST /devices/{id}/override`
  (`{action: boost|pause|resume, minutes?, until?}`; boost is a timed hold, SunCore keeps its
  hands off and drives nothing; pause also releases a device SunCore controls, respecting min
  run/pause; a pause `until` the past is 422 `api.validation.override_until_past`),
  `GET /proposals`, `POST /proposals/{intent_id}/confirm`
  (the gate's decision) and `/dismiss` (204), `GET /decisions?limit&device_id&kind=..`,
  `PUT /operation/mode` `{mode}`, `PUT /operation/devices/{id}` `{mode, identity?}` (identity
  required above shadow, 409 `device_identity_changed`), `PUT /config/operation` may not raise
  a mode (409 `mode_raise_rejected`). Live feed: `snapshot.control` and `{type: "control"}`
  messages. Frontend: types regenerated, `LiveStore.control` (UI is WP10). The backend has no
  "active from a later version" hint (it is the frontend string `mode.later`, WP10).
  Open: boost does not drive the device (maintainer question); `tz` falls back to UTC without
  region and HA config (the stack recovers only after HA's zone was asked for).
- From the PR 3-A review (fixed in `fix(safety)`: per-device lock and re-checks before
  `apply()`, late confirmation of slow echoes, free-surplus semantics with input age, deferred
  release). ~~Still for WP9: (2) the gate's `tz` from the site/region time zone; (8) the
  `operation` put trust gap; (10) wiring of `gate.recover()`, `connection_lost()`/
  `connection_restored()`, `gate.poll()` every tick and `gate.observe()`~~ all done in WP9
  (below). Maintainer question (PR 3-A): non-urgent releases (mode change to shadow,
  device watchdog trip, reconnect) wait for the device's minimum run/pause time; emergency
  stop and shutdown release at once.

## Open – Phase 1

- Still to report by the maintainer: menu names in the HA UI. Memory on
  amd64 HA OS: 65 MiB (2026-10-14); the panel follows the HA theme. The GHCR packages
  `suncore`, `aarch64-suncore`, `amd64-suncore` and `suncore-beta` are public
  (anonymous pull verified on 2026-10-13).
- ~~Beta app version bumped by hand~~ Done (Phase 2, WP10): `build.yml`
  derives `<next>-beta.<n>` and the `bump-beta` job commits it to
  `addon/suncore_beta/config.yaml` on `main` (ADR-0009 addendum). Needs
  push permission to `main`; the job uses the `RELEASE_PLEASE_TOKEN` secret
  (set by the maintainer) before `GITHUB_TOKEN`; verified by the first run
  on `main` (`0.3.0-beta.50`).
- Done in PR 2-B: 422 issues are rendered as catalogue keys (`api.validation.*`),
  and `/api/v1/entities` is served from the entity catalogue (registry snapshot
  plus a state copy refreshed at most every 30 s, never a `get_states` per
  request). The theme bridge follows the HA theme on the maintainer's HA
  (accent colour confirmed 2026-10-14).
- Python ICU library decision (ADR-0007 follow-up) deferred to Phase 6:
  in Phase 1 the backend only emits `Reason(key, args)`.
- Phase 3 follow-ups: ~~boundary rule 2 for drivers, removal of
  `test_phase_1_has_no_apply_call_anywhere`, versioned decision payload, per-device rate
  limits and max switches/hour in `safety/`, read-back from the cache after a settle
  delay, try-acquire rate limiter, persisted emergency stop~~ done in WP2/WP3. Still open:
  retention for the `decision` table and the `RuntimeStore` implementation (WP4); the
  connection flood floor is 0.25 s now (WP6); tables
  `plan_snapshot`/`template_override` and `timeseries.db` (Phase 4); an `AuthError` on
  reconnect needs a restart (document in the UI).
- Phase 3 follow-up (review F6, A2): `core/identity.py` provides the rule; WP4 applies it
  in put/import/restore of `devices` **and** put/restore of `operation`, and an import
  resets the global mode to shadow (plan §6.8).
- Phase 4 follow-up (A3): per-(domain, service) rate limiter with try-acquire/deadline for
  `call_action_response` before the first adapter calls it (boundary rule 7 then admits `app`
  together with the adapter wiring).
- Carried over from Phase 0: deleting old tags `v0.1.0`–`v0.3.0`. The
  repository is public now, CodeQL runs; `RELEASE_PLEASE_TOKEN` is set.

## Next steps

1. Maintainer: install the beta (export a configuration backup first: schema 3 cannot be opened
   by earlier versions), keep everything in **shadow mode**, read `docs/user/en/surplus-control.md`
   (or `de`) and check the **Decision log** after a day with sun: are the "would have" entries
   plausible? Check the summary system check at noon.
2. ~~Answer the open questions~~ Answered 2026-10-16: keep everything as built (safe and simple),
   see the plan's "Answers after implementation".
3. Phase 3 exit: switch **one** device to automatic following the guide's walk-through and report
   the first live writes (the climate unit's counter-only estimate and the cloud echo delay are
   the interesting parts). Then merge 3-A, 3-B, 3-C and release.
4. Release the next release-please PR for the stable app after acceptance.
5. Phase 4: finish the 4-A review fixes, open PR 4-A; maintainer decides on ADR-0013 and the 4-A
   questions; continue with 4-B (storage/time series).

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Energy-Charts and aWATTar APIs and terms (not shipped in Phase 4) | `research/integrations.md` | session with network access (Phase 6) |
| Open-Meteo azimuth convention in a live response (code: 0 = south, OpenAPI text contradicts); separate terms page (README only) | `research/integrations.md` | live request; maintainer reads the terms page |
| Kostal REST battery/grid power sign; `ActivePowerLimitation` semantics; live entity ids | `research/integrations.md` | maintainer's HA (enable the entities, compare with the inverter display) |
| Kostal Modbus external battery management not enabled on the maintainer's inverter; how it is enabled; §3.5 fallback timer behaviour | `research/kostal-plenticore-modbus.md`, ADR-0013 | maintainer enables it and measures; `kostal_modbus` capped at assisted until then |
| Creating a modbus hub from the HA UI (else YAML) | `research/ha-websocket-api.md` | maintainer's HA |
| Supervisor proxy passes `recorder/*` and `energy/*` commands | `research/ha-websocket-api.md` | first Phase 4 run on HA OS |
| Supervisor proxy forwards `recorder/statistics_during_period` (the 4-B rebuild logs a warning and retries) | `suncore/ha/statistics.py` | first run on HA OS |
| `change`/`sum` rows of real energy counters (the rebuild reads them as verified from source, not tested against live counters); `backup_exclude` accepts the glob `timeseries.db.corrupt*` | `suncore/ha/statistics.py`, `addon/*/config.yaml` | maintainer's HA with an energy counter; a backup on HA OS |
| Publication time of next-day prices (Tibber, Nord Pool, ENTSO-E, EPEX) | `research/integrations.md` | observe on the maintainer's HA |
| Nord Pool `config_entry` id source, sensor translation keys, publication time; Octopus/ENTSO-E/EPEX entity matchers (platform and unit only, no translation keys); `start`/`end` sent to `tibber.get_prices` as ISO with offset; negative-price rule proxy for all-in sources | `templates/tariff/*.yaml`, `suncore/tariff/pricing.py` | live check on a system with the integration |
| VAT applied to negative spot prices | `suncore/core/tariff.py` | maintainer's bill / supplier terms |
| KNN load model performance on aarch64 | plan WP12 | CI arm runner benchmark |
| Energy colour variable names (`--energy-*`) | `frontend/src/app.css` | read HA frontend source |
| HA UI menu paths in the user docs (Settings → Apps → App store, Repositories, Show in sidebar; German labels) | `addon/*/DOCS.md`, `README.md`, `docs/user/*/quick-start.md` | maintainer checks on HA OS during the beta install |
| Supervisor Core proxy passes `supported_features` through | `suncore/ha/websocket.py` | first run on HA OS |
| Registry and energy-prefs shapes on cores older than dev (single `config_entry_id` on devices, unified grid source); `unknown_command` code without the `energy` integration; Supervisor proxy passes registry commands | `research/ha-websocket-api.md` | first run on HA OS; parsers tolerate both shapes |
| Section 51 EEG 2023 text could not be fetched; region `de` feed-in tiers are empty until the official BNetzA table is cited | `templates/region/de.yaml` | maintainer provides the table or a session with access to gesetze-im-internet.de |
| Whether Tibber's `total` includes grid fees (country dependent) | `templates/tariff/tibber.price.yaml` | live check on the maintainer's Tibber account |
| LG translation keys (inferred from source and unique ids; LG entity ids, modes and units checked live on 2026-10-14), other LG models' modes (Solcast default day-sensor count verified from source in 4-A; Tibber publication time: see the price row above) | `research/device-integrations.md` | maintainer's HA with LG ThinQ, Tibber, Solcast (Phase 2 wizard test) |
| Wording of the quick start steps and the HA menu names in the new docs (written from the UI catalogues, not clicked through on a real HA); the surplus control guide is written from the code and the demo, its walk-through has not been followed on a real system | `docs/user/*/quick-start.md`, `docs/user/*/surplus-control.md` | maintainer follows the quick start and the first-device walk-through on HA OS |
| Which Home Assistant tool lets users place files in the app's `/data/templates` | `docs/user/*/device-templates.md` | maintainer checks on HA OS |
| Water heater templates (`generic.water_heater_steps`, `_relays`), drivers for select/number/relays/mode, `ClimateDriver` paths other than `lg_thinq` source reading: simulator only, no hardware; `lg_thinq` `step is None` `TypeError` on `set_temperature`, push latency after a command, `climate.turn_on` fallback for modes not in `hvac_modes` | `templates/device/generic.water_heater_*.yaml`, `suncore/devices/drivers/`, `research/device-integrations.md` | first limited live write on the maintainer's AC; a user with a water heater |
| Default sensor cadences (power sensor 60 s = the client resync, energy counter without `counter_update_s` 300 s); whether a quiet plug re-reports an unchanged value so that `last_reported` moves | `suncore/app/cadence.py`, `research/ha-websocket-api.md` | shadow-mode run on the maintainer's system; set `power_update_s`/`counter_update_s` where a sensor is slower (`power_update_s` is in the device editor since WP10) |
| Controller tuning (deficit delay/budget, downside spread of the smoothing, counter-only climate needs `nominal + span + hysteresis` free surplus to shift; battery share without a goal = battery first) | `suncore/controller/`, `tests/controller/test_climate_equivalence.py` | shadow-mode run on the maintainer's system (plan exit criterion) |
| evcc REST API paths | `research/competitors.md` | Phase 6, MQTT API is verified and sufficient |
| Solcast day sensors d3/d4: translation keys `total_kwh_forecast_d3`/`_d4` from the research notes; the date-time format the fallback action `query_forecast_data` accepts for `start_date_time`/`end_date_time` (SunCore sends ISO 8601 with offset) | `templates/forecast/solcast.pv.yaml`, `suncore/forecast/pv/adapter.py` | maintainer's HA with Solcast |
| Open-Meteo Solar Forecast: translation key assumed equal to the description key; CC BY 4.0 attribution still to be shown in the UI (4-H) | `templates/forecast/open_meteo_solar_forecast.pv.yaml` | a running integration; PR 4-H |
| Forecast.Solar `wh_hours`/`watt_hours_period` keys read as period *start* (vendor API unreachable) | `templates/forecast/forecast_solar.pv.yaml`, `suncore/forecast/pv/parsing.py` | live comparison with the integration's sensors |
| Supervisor proxy forwards `energy/solar_forecast` | `suncore/ha/` (`fetch_solar_forecast`) | first run on HA OS |
| PV calibration constants (classes kt 0.3/0.65, shrink k = 16, decay 0.98, factor 0.5–1.5) and the clipping thresholds (plateau ±0.3 % for ≥ 1 h on ≥ 2 days; export within max(3 %, 50 W)) are reasoned, not fitted to real data | `suncore/forecast/pv/` | 30+ days of the maintainer's data |
| Planner solve times on aarch64 (Raspberry Pi 4, HA Green); amd64: 4 ms solve, 24 ms whole run for the reference house; HiGHS releasing the GIL on aarch64 (measured on amd64) | ADR-0003, `scripts/bench_planner.py`, `tests/planner/test_gil.py` | run the benchmark and the slow tests on a Pi / the CI arm runner |
| Negative-price rule slots in the planner use a proxy (export price ≤ 0 and import price < 0); a spot-only source with a formula whose end price stays positive is not detected | `suncore/planner/inputs.py` | tariff service exposes the rule's slots (Phase 5) |
| Planner weights and rules: target penalties (10 × max price + 1, buffer 2 × + 0.5), terminal value (median import × discharge efficiency − degradation/2), device budget on P50 plus overflow (a day far below P50 can miss the evening target, review D4), flat 500 W base load without a load forecast, automatic device value export + 0.01 | `suncore/planner/model.py`, `inputs.py` | shadow plans on the maintainer's system |
| `weather.get_forecasts` `hourly` support of the maintainer's weather integration (the `temperature_unit` attribute and the conversion of the forecast to it are verified from source, `research/ha-websocket-api.md`) | `suncore/app/adapters.py` (`HaLoadReader`) | maintainer's HA |
| `workday.check_date` over the Supervisor proxy and with the maintainer's Workday configuration (the `days_offset` handling is verified from source; a changed country/holiday list is not visible in the attributes, so the day-type cache only resets on a changed entity, `workdays`, `excludes` or offset) | `suncore/app/adapters.py`, `suncore/forecast/load_service.py` | maintainer maps a Workday sensor |
| Load and risk constants (half-life 21 days, outlier z 3, shrink 3 days, 7 clean days, degree-day bases 15/22 °C, coverage 75 %/90 %, 20 h temperature days, anchors sunrise + 1 h / sunset − 1 h, sunrise threshold 2 %/20 W) and the default PV bands per weather class (clear 0.85/1.10, mixed 0.55/1.35, overcast 0.35/1.70) are reasoned, not fitted | `suncore/forecast/load.py`, `suncore/forecast/risk.py`, `risk_learning.py` | 30+ days of the maintainer's data |
| Known limitation (n6): the load history and the risk samples are not re-derived when the device list changes - a device added later is only subtracted from the base load from the day its power is recorded (or rebuilt), and older risk samples keep the old base load | `suncore/forecast/load_service.py`, `risk_learning.py` | accepted for Phase 4; revisit with 4-I |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
