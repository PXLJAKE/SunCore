# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-11-25 (Phase 6 complete: PRs 6-A to 6-M, 6-N docs, 6-O gaps; Phase 7: 7-B
and 7-N part 1, release readiness) ·
**Phase:** 6 built and waiting for review and merge (plan `docs/plans/phase-6.md`); Phase 7 next.
PR 7-B has merged the Phase 5 tail (5-E, 5-I, 5-J, 5-K, 5-L) into this line. PR 7-C adds the demo
flexible loads (`SUNCORE_DEMO_FLEX=1`: EV on a wallbox, washing machine, heat pump; shadow).
Bundle budget split pending maintainer decision; option A implemented (standard path 250 kB,
expert extras 30 kB; 7-B measured 236.9 and 25.4 kB, first paint 113.2 kB).
Phases 3 and 4 are merged; their acceptance on the maintainer's system (two stages, see "Next
steps") and the acceptance of everything Phase 6 added are still open, because no PV surplus, wallbox,
heat pump, §14a box or backup switch was available in the sessions ·
**Branch:** `phase-7/c-demo-flex` (on `phase-7/b-phase5-tail`)

## Phase 6 at a glance

What exists (all behind shadow mode, none of it has run on hardware):

- **HA surface:** MQTT discovery with a command path (modes, emergency stop, overrides, replan;
  raising refused by default), read-only views of grid, battery, EV, heat pump and programs, and
  notifications (persistent, push, confirm and reject buttons, quiet hours, daily cap, island,
  §14a and EV target at risk); backend ICU catalogue en/de.
- **Grid:** one resolver for the import limit, export limit and §14a budget; peak shaving with a
  monthly peak and a shed order; §14a and island caps; export chain with an inverter limit (capped
  at assisted until `limit_verified`, which has no panel path yet).
- **Devices:** wallbox/EV (modes `off`, `now`, `min_pv`, `pv`, `target`, phase switching), evcc as
  actor, heat pump (SG-Ready 2/3, state 4 opt-in, set-point lift, RC model and COP), shiftable
  programs (plug, remote switch, notify).
- **Safety:** invariant families G1-G6, X1-X6, E1-E9, L1-L5, H1-H5, P1-P6 with property tests and
  scenarios; `NOT_YET_SUPPORTED_KINDS` is empty; every PR has a `control-safety` sign-off table.
- **Docs:** user guides en/de for entities, notifications, grid limits, EV and evcc, heat pump
  and programs; architecture updated for Phases 5 and 6; acceptance table in plan §9.

Carried to Phase 7: see "Open - Phase 7".

## Done

- **Phase 7 - 7-N part 1, release readiness (branch `phase-7/n-release`, on 7-B;
  `qa-engineer`, `ha-integration`)** - details and deviations: plan section 8 (7-N):
  - Frozen `suncore_v1.sql` and the 0.2.0 export, generated from the tag by
    `scripts/gen_release_fixtures.py` (only `v0.2.0` exists; v2 to v6 and the time-series
    database were never released, so no dumps for them); upgrade chain, export import, refusal
    of newer databases, hypothesis tests for the four section migrations
    (`tests/storage/test_release_upgrade.py`).
  - CI: `scripts/upgrade_test.sh` (0.2.0 image by digest, seeded through its API, candidate on
    the same volume) and `LOADED=1 scripts/smoke_image.sh` (pinned Mosquitto, 10 min) as steps of
    `build.yml`; `bench-arm.yml` on `ubuntu-24.04-arm`; `SUNCORE_DEMO_MQTT=1`; `beta_version.py`
    reads `release-as`. None of the three CI jobs has run yet (no Docker in the session).
  - **Memory, measured locally** (full app, demo with flexible loads, battery control, MQTT to the
    test stub broker, 75 entities, a replan per minute for 10 min, `/proc` RSS, x86_64, CPython
    3.13, merge of 7-N and 7-C in a scratch tree): 132.3 MiB after the broker connected, 136.4 MiB
    after 10 min (peak = final, growth about 0.4 MiB/min early, flat from minute 6); limit 300.
    Docker's cgroup figure will be higher (page cache, s6); the loaded smoke test in CI is the
    authoritative reading, also on aarch64.

- **Phase 6 - PR 6-O, gaps of Phase 6 (backend; branch `phase-6/o-gaps`, on 6-N;
  `ha-integration`, `backend-engineer`)** - nothing in `safety/` or `controller/` changed:
  - Notifications wired: island and §14a from the grid monitor (`grid_flags`; an unreadable
    source is "no information"), start at once, end after 120 s without the signal (flicker
    gives one start and one end); "EV target at risk" from the newest plan's
    `planner.ev.target_shortfall` (plan made after the target was set, held 10 min, once per
    target). Quiet hours and the daily cap as before; safety ignores both.
  - `notify.quiet_hint` (en, de) now says what the rules do: proposals, charging targets, cheap
    windows, "finished" and "did not start" are dropped in quiet hours; errors, safety, "start
    now" and the summary are not.
  - HA entities, read only: month peak, grid state, §14a, island, battery action and lease, job
    state, planned start, plus new sensors for the EV (mode, target, plug, phases, vehicle SoC)
    and heat pump (SG state, lift). The table's EV, program and comfort *controls* are not
    announced (no command path, plan section 8, 6-O).
  - Tests: rules, service, limiter text, runtime sources, discovery payloads (golden files),
    and simulator closed loops (`tests/sim/test_notify_scenarios.py`).
  - Deviations, `UNVERIFIED` and questions: plan section 8 (6-O).
- **Phase 6 - PR 6-N, WP14 docs, translations, STATUS (branch `phase-6/n-docs`, on 6-M;
  `docs-writer`)** - docs only, no code and no safety path changed:
  - User guides en and de: `home-assistant-entities.md`, `notifications.md`, `grid-limits.md`,
    `ev-charging.md` (with evcc), `heat-pump.md`, `programs.md`. Each covers the setup in the
    wizard, what SunCore does and never does, shadow and assisted behaviour, hand-back and the
    emergency stop, and the known limits and `UNVERIFIED` items, including the residual risks of
    the reviews (evcc `smart` reacts to the import limit only after about a minute, the program
    plug's safe state is ON and the HA automation for a plug stuck off, evcc liveness caveats,
    register 533 without a timeout).
  - Existing pages extended, not duplicated: quick start (grid step, gallery, "cannot do yet"),
    surplus control and planning (links, schema 6 and 3 instead of 4, stale "no wallbox"
    statements), both `DOCS.md` (the MQTT service needs no `hassio_api`).
  - `docs/architecture.md` brought up to date: Phase 5 and 6 modules, dependency rules (notify
    and MQTT never write), grid chain and flexible loads, the HA surface, storage (schema 6 and
    3, `flex_state`, `grid_event`, `grid_peak`), the process model (the planner runs in a daemon
    thread, not a process pool), the frontend routes.
  - Catalogues: frontend en/de (2214 keys) and backend en/de (683 keys) have the same keys and
    placeholders; `check_reasons` passes; the addon translations cover every option of both app
    folders. Four unused frontend keys removed (`plan.status.solve`, `plan.next.more`,
    `fc.accuracy.title`, `bat.settings_title`). The generators (`gen_schemas`, `gen_compat`,
    `gen_backend_catalogue`) produce no diff.
  - Plan `docs/plans/phase-6.md`: acceptance table with evidence (section 9) and one consolidated
    German list of the open questions (section 10).
  - Not done: screenshots of the new views (they need the demo with a browser; carried to
    Phase 7), a click-through of the new how-tos on a real Home Assistant.
- **Phase 6 - PR 6-M, WP13 frontend (branch `phase-6/m-frontend`, on 6-L, 6-J, 6-K, 6-G;
  `frontend-engineer`)** - no safety path changed, shadow stays the default:
  - Backend (commit scope `api`, `suncore/api/flex.py`): `GET /devices/{id}/flex`,
    `PUT /devices/{id}/ev` (mode + target into `flex_state`, the controller reads it),
    `POST /devices/{id}/program/arm` and `/cancel` (through `ProgramJobBook`; a cancelled
    plug job is released by the controller, never written by the API),
    `GET /grid/status`, `GET /grid/events?days=`; errors are Reason keys (`api.error.*`).
    New entity role `backup_state` (detect.py) for the island entity picker.
  - Wizard step 3: peak shaving, export chain (zero export, 0 W at negative prices), §14a
    signal kind and limit, backup entity with live state preview and island states. Step 5:
    inverter limit block, disabled with a reason (no verification endpoint exists yet).
    Step 7: wallbox, evcc, heat pump and program templates in the gallery (13 templates),
    evcc loadpoint id/prefix, program run time/idle power/peak placeholder, resume
    confirmation, "behaviour confirmed" checkbox where the backend caps the mode. The device
    form now carries `ev`/`program`/`thermal` from the template (they were dropped before).
  - Now: device cards for EV (mode, target, plug, phases, vehicle SoC), heat pump (SG state,
    lift, COP range, learned-model error), program (arm "finished by", job state, planned
    start, cancel); a warning dialog before cancelling a job and before "back to normal" of a
    plug program (the plug switches ON, the machine starts at once); badges island, §14a,
    month peak. Settings: "Home Assistant entities" (status, broker hint, republish,
    mode-raise opt-in) and "Notifications" (categories, targets, quiet hours, cap, test).
    Plan Gantt: program start slots; History: grid events and monthly peaks.
  - Bundle: `scripts/bundle-size.mjs` counts one language catalogue (the largest) because the
    app loads only the active language: 228.6 kB of 250 kB, first paint 123.5 kB of 125 kB.
  - Tests: Vitest 378, Playwright `phase6.spec.ts` (10 tests) plus the updated gallery count.
  - Deviations, `UNVERIFIED` and questions: plan section 8 (6-M).
- **Phase 6 - PR 6-L, WP12 shiftable programs (branch `phase-6/l-programs`, on 6-I + 6-E;
  `control-safety`)** - shadow stays the default, every write still passes the gate:
  - Control kind `program` is judged now (no longer rejected). `safety/programs.py`: P1-P5 as
    pure functions (start only for an armed job with a fresh measured power; never off while
    the job runs, the appliance draws `idle_w` or more, its power is unknown or within run
    time + 15 min of SunCore's switch-on; off only within 600 s of arming; a deadline-bound
    start may import up to the program's peak power, the import limit holds). The plug's
    takeover baseline is on (P3); a release never switches a possibly running program off; the
    G2 fuse waiver skips running programs (6-F review H3); programs have no surplus levels, so
    the deficit back-off never touches them. P6 = the existing behaviour cap.
  - New control kind `notify` (`NotifyControl`, only with `start_method: notify`): nothing is
    ever written, the gate rejects every action but a release.
  - `devices/drivers/program.py`: plug (`switch`/`input_boolean`), remote start (switch, never
    switched off, release writes nothing), notify (no entity).
  - `controller/programs.py`: armed -> scheduled (fresh plan `start` band) -> running (start
    accepted, or power >= `idle_w` for 30 s: manual start) -> done (run time + 5 min idle);
    start at the planned window or at the latest start that meets the deadline; missed
    deadline = cancelled + notification; no start on unknown power, untrusted site inputs, a
    blocked device or in island; one release after a job SunCore wrote for.
  - `app/programs.py` `ProgramJobBook` (open jobs, persisted to `flex_state`), notifications
    `notify.program.start_now/done/missed` through `NotificationService.post`; the runtime
    passes the open jobs to the planner (`FlexRequest.jobs`). No API/MQTT/panel to arm a job
    yet (6-D/6-M, like the EV mode in 6-I).
  - Templates `generic.program_plug`, `generic.program_notify`, `generic.program_remote`
    (+ conformance scenarios), en/de keys; simulator `SimProgram` and scenarios
    `program_deadline_plug` (with and without the real planner, cloud flicker),
    `program_notify_start`, `program_crash_recovery`; e2e gallery count 10.
  - Deviations, safety self-review and questions: plan section 8 (6-L).
  - Review conditions of PR #62 done (H1, M1, M2, L1-L4): a release after a job is retried
    until settled, nothing in flight blocks it (timeout, submit errors), docs recommend an HA
    automation "plug off for hours -> on", `start_now`/`missed` pass the daily push cap, the
    "start now" request is persisted (`notified_at`), `idle_w > 0`, an unseen run is reported
    as "status unknown"; e2e wizard search adjusted for the new plug template.
- **Phase 6 - PR 6-J, WP10 evcc as actor (branch `phase-6/j-evcc`, on 6-I + 6-C;
  `control-safety`)** - shadow stays the default, every write still passes the gate:
  - `loadpoint` is a judged action kind now (`NOT_YET_SUPPORTED_KINDS` = inverter limit only).
    `safety/evcc.py`: L1-L5 as pure functions (values in range, no vehicle plan, `now` only with
    an allowance and the E6 battery lock, no raise on unknown evcc state, potential power
    `maxCurrent x V x phases` under G1-G6); a loadpoint takes only `loadpoint` actions and a
    release, only it takes them.
  - `devices/evcc.py`: dumb driver over evcc's MQTT API (read from the evcc source, 2026-11-24):
    mode `off`/`smart`/`now`, `maxCurrent`, `limitSoc` through `<prefix>/loadpoints/<id>/<key>/set`
    (not retained); never `site/*`, `minCurrent`, `phasesConfigured` or a plan; read-back from
    the retained topics; the release restores the takeover baseline and waits for the echo.
  - `app/evcc_link.py`: second MQTT connection to the broker of the `mqtt` section, heartbeat
    `<prefix>/updated` (alive only while younger than 120 s), the one `publish_set` write path.
  - `controller/evcc.py` `EvccIntents`: runtime EV mode -> `smart`/`now`/`off` with a
    `maxCurrent` that fits the allowance, import limit and §14a budget; no raise on anything
    unknown; debounced raises, immediate lowering; asks the battery controller for the E6 lock.
    `min_pv` maps to `smart` (no floor, evcc has no minimum-power mode).
  - Template `evcc.loadpoint` (no roles; conformance scenario), simulator `SimLoadpoint` on an
    in-memory `FakeEvccBus`, scenario `evcc_actor` (+ silent evcc, emergency stop, manual mode
    change in evcc's app); en/de keys; e2e gallery count 8.
  - Review conditions of PR #61 done (A1, A2, B1, B2, C1-C3): best-effort release (mode and
    `maxCurrent` only in the echo), G1/peak check for a raise to `smart` plus an import-cut
    path in the controller (scenario `evcc_plan`), release retries with back-off, the link
    starts for a loadpoint added after start-up, liveness and E6 warnings in template and docs.
  - Re-review: import cut with hysteresis (off after 60 s over, back on after 300 s with 1 A
    margin; loadpoint off/on counts as a switch, off never blocked; scenario `evcc_flicker`),
    failed emergency-stop/recovery releases retried by `poll` with back-off. Residual risk:
    the smart import check is reactive (overshoot up to about 1 min).
  - C4: the over-limit clock restarts only after 30 s under the limit (noise around a mean
    above the limit is cut within about 60-90 s); above the fuse limit `off` at once.
    Residual risk: G1 for `smart` is reactive, about 1 min.
  - Deviations, safety self-review and questions: plan section 8 (6-J).

- **Phase 6 - PR 6-I, WP9 wallbox/EV direct control (branch `phase-6/i-ev`, on 6-F + 6-H;
  `control-safety`)** - shadow stays the default, every write still passes the gate:
  - `charge` is a judged action kind now (`NOT_YET_SUPPORTED_KINDS` = loadpoint, inverter
    limit). `safety/flex.py`: E1-E6 as pure functions (current range incl. the live entity
    range, phase change only paused/rarely/never in `now`, the bounded import exception of
    ADR-0017 decision 4, the battery lock, no increase unplugged or in `off`); a charger takes
    only `charge` and a release, only a charger takes `charge`. The gate reads the EV mode
    (runtime `flex_state`, unreadable = `off`) and the fresh plan band (`target`).
  - E6: grid power for the car only without a home battery or while SunCore holds a confirmed
    discharge lock; the battery controller asks for that lock (`controller.battery.ev_lock`,
    B4 extended) while a charger has an allowance, also without a fresh plan.
  - `devices/drivers/charger.py`: current number (snapped to the entity step), pause switch,
    optional phase select/switch, vehicle SoC and plug sensor; write on change; read-back and
    baseline are the full state (current, phases, switch), so a release restores all three.
  - `controller/ev.py`: levels 0 (pause) and min..max A in 1 A steps; floors for `now`,
    `min_pv` and the plan's `grid_power_max_w` (`target`); cap 0 for `off`/unplugged/phase
    switch; fail-safe cap at the minimum while the site inputs are not trusted; deficit
    back-off down to the floor only; 1/3-phase switching (5 min dwell, +500 W to go up, pause
    around the switch, max switches per hour, never in `now`).
  - Runtime: `ControlStack(ev_states=...)` from `flex_state`, `FlexRequest` (EV states, SoC,
    plug) for the planner; `GridRequest` still not wired.
  - Template `generic.wallbox` (+ conformance scenario), en/de keys; simulator `SimWallbox`
    and scenarios `ev_pv_only_flicker`, `ev_min_pv`, `ev_phase_switching`, `ev_arrives_late`
    (real planner, battery lock), `ev_para14a`.
  - Deviations, safety self-review and questions: plan section 8 (6-I).
  - Review conditions of PR #59 done (A1, A2, M1-M3, H1-H3): staged charger release (pause
    first, phases/§14a later), immediate shed of a battery-fed car when the lock is lost,
    one lock tolerance in `core.battery`, gate-observed idle time for E2, unit check on the
    current entity; e2e gallery count updated for `generic.wallbox`.
- **Phase 6 - PR 6-K, WP11 heat pump (branch `phase-6/k-heatpump`, on 6-F + 6-H;
  `control-safety`)** - shadow stays the default, every write passes the gate:
  - SG-Ready is supported: `core/sgready.py` (step <-> state <-> contacts, `contact_path` that
    never passes the block pattern, fail-safe causes), `devices/drivers/sgready.py` (two contact
    switches, read-back of unknown pairs as a mode, release to the baseline or state 2 - never
    state 1), `safety/heatpump.py` (H1-H5: no state 1, state 4 only with `allow_forced`, dwell
    600 s and switch limit, probe and comfort band, fail-safe to state 2 that waives dwell, min
    run and the switch limit). `safety.sg_ready_not_supported` is gone.
  - Hot-water lift: `devices/drivers/water_heater.py` (`water_heater.set_temperature`, snapped
    and clamped to the entity range, never while `off`, release to the user's set-point).
  - Controller: SG levels (state 2 = 0 W, state 3/4 nominal), fail-safe back-off also with an
    unknown surplus, dwell wait, restart hysteresis 3 K below `comfort.max_c`.
  - `forecast/thermal.py`: RC least squares on 15-minute data with a MAD outlier filter, quality
    = NRMSE of a 24 h replay, stored in `learned_param` (`thermal_model`), refitted daily; the
    planner runtime builds the thermal `FlexRequest` (probe temperatures, outdoor forecast,
    learned models). Probe temperatures are recorded as `device.<id>.temperature_c`.
  - Templates `generic.heat_pump_sg_ready`, `generic.heat_pump_dhw_setpoint` (sources,
    `unverified`, en/de); simulator `SimHeatPump`; scenarios `heat_pump_sg_ready_surplus`,
    `heat_pump_dhw_lift` and a planner-level history -> fit -> plan test. Deviations and the
    safety self-review: plan section 8 (6-K).
  - Review conditions A1-A5 (PR #60): release never lifts a foreign SG lock, the lift is
    capped at assisted until confirmed, H4 checks against the user's baseline set-point.
- **Phase 6 - PR 6-G, WP7 export chain and inverter limit (branch `phase-6/g-export`, on 6-F;
  `control-safety`)** - shadow stays the default (`operation.inverter_mode`), every write
  passes the gate:
  - `safety/inverter.py`: invariants X1-X6 as pure functions (never above the baseline or the
    static limit; an AC limit, or a not yet verified export limit, leaves house + battery
    charging + 500 W; below the current export only with an exhausted chain; heartbeat and
    read-back; island/stale inputs never write and end the lease; at most assisted until
    `limit_verified`). `safety/gate.py` is still the only caller of `apply()`/`release()`; the
    inverter has a lease, a heartbeat for volatile limits, reset detection, release to the
    user's own value, crash recovery and an orphan release after an adapter change.
  - `controller/export.py`: the chain surplus -> devices -> battery (SunCore's own charge cap
    lifted, `controller.export.battery_cap_lifted`) -> inverter limit, with a curtail delay
    (120 s), no tightening while devices can still rise, loosening at once, pacing and a 3 %
    AC hysteresis (no oscillation under cloud flicker).
  - Drivers `generic_number`, `kostal_rest` (`Inverter:ActivePowerLimitation`) and
    `kostal_modbus` (register 533 via `modbus.write_register`, int, volatile); template kind
    `inverter` with the role `limit` and three templates; `PUT /operation/inverter` with the
    adapter's identity; a changed adapter resets `limit_verified` and the inverter mode.
  - Simulator: inverter limiter (static limit, the three adapters, register resets); scenarios
    `export_limit_zero_export`, `negative_price_curtail`, `inverter_limit_heartbeat_lost`
    (reset, lost read-back, crash and restart in the test).
    Deviations, safety self-review and questions: plan section 8 (6-G).
  - Review conditions (PR #58): the lease checks every limit that may be in force and the
    heartbeat re-writes only the confirmed one; takeover only from the register default or an
    export limit not below the static limit (modbus release = 100 %); `nominal_ac_w`
    plausibility; no write in an unknown unit. Residual risk: an AC limit (533) stays after a
    crash or connection loss (no documented timeout) - plan question 6.

- **Phase 6 - PR 6-F, WP6 import side, §14a, island (branch `phase-6/f-grid-import`, on 6-B;
  `control-safety`)** - shadow stays the default, every write still passes the gate:
  - `safety/grid.py`: invariants G1-G6 as pure functions, wired into the device invariants
    (`check_grid_situation`, the G2 fuse waiver in min run and the switch limit) and the battery
    invariants (no grid charging under §14a or in a cool-down, nothing import-causing in island;
    the lease ends with them). The gate keeps an island/§14a memory (`core.grid.GridMemory`) for
    the cool-down (`safe_state_cooldown_s`) and sums the other §14a devices' upper estimates.
  - `controller/grid.py`: one resolver per tick through `core.grid` (effective import limit =
    min(fuse, peak), §14a budget, grid state, warnings); level caps for island (shed all but the
    keep list, keep frozen), cool-down, §14a (budget shared in priority order, keep at `<=`,
    rise with hysteresis to spare) and unknown §14a (frozen); the back-off above the limit uses
    the shedding order (continuous first, `shed_rank`, reverse priority) and waives min run of
    non-thermal kinds only above the fuse limit; battery support lifts a discharge lock near the
    limit, island releases the battery, §14a blocks grid charging. `GateInputs` gains
    `fuse_limit_w` and `para14a_devices` (additive).
  - `app/grid.py` `GridMonitor`: monthly peak from 15-minute means of the tick values (reset at
    local midnight of the 1st, seeded from `grid_peak`), grid events (island, §14a, new peak,
    import above the fuse) persisted to `timeseries.db`; the controller ignores the plan in
    island.
  - Simulator: island (grid 0, battery covers, PV curtailed) and §14a signals in the fake HA;
    scenarios `import_limit_fuse_shed`, `peak_shaving_month`, `para14a_dimming` (with cloud
    flicker), `grid_outage_island`. Behaviour change: with only a fuse limit the import-limit
    back-off now reports `controller.grid.fuse_shed` and skips min run of non-thermal kinds.
    Deviations, safety self-review and questions: plan section 8 (6-F).
  - Review conditions (PR #54): no G2 waiver on a stale snapshot; the §14a sum counts writes
    in flight; the cool-down memory is restored from `grid_event` at start; G1 rejects load
    increases of unknown size (`safety.power_unknown`). H3 (running programs) is due in 6-L.

- **Phase 6 - PR 6-H, WP8 planner for flexible loads and grid (branch `phase-6/h-planner-flex`,
  on 6-B; `optimizer-engineer`)** - plans and bands only, no device writes, no runtime wiring:
  - `planner/flex.py` (inputs from the 6-B contracts: `build_flex`, `build_grid`),
    `planner/flexmodel.py` (LP/MILP columns and rows), `planner/flexheuristic.py` (fallback).
    Without flexible loads or grid limits the model is exactly Phase 4 (same columns and rows,
    no integrality; test).
  - EV: modes off/min/pv/min_pv/target, charging window from arrival/departure, 6 A floor,
    on/off binaries only in the first 24 h (LP beyond), energy target counted on the P10
    surplus, never fed by the home battery. Program jobs: one binary per candidate start before
    the deadline, warm start keeps the previous start. Heat pump: exact RC discretisation, COP
    curve, comfort band with soft slacks, surplus pre-heat; grid pre-heat only with
    `grid_preheat`.
  - Grid: per-slot import/export limits from `core.grid`, monthly peak variable with the
    capacity price (local month bounds), §14a sum of flagged loads within the budget for the
    hold time, grid charging 0 while dimmed; a demanded load above the import limit uses a
    penalised slack (`planner.grid.import_limit_exceeded`).
  - Bands: `max_power_w`, `grid_power_max_w` (EV target, grid pre-heat), `start` (jobs); 18 new
    reason keys (`planner.ev.*`, `planner.job.*`, `planner.thermal.*`, `planner.grid.*`, en/de).
  - Simulator: `sim/planner_flex.py` with nine scenarios (full house, late EV, PV-only EV,
    program deadline (feasible/infeasible), heat pump surplus/grid pre-heat, peak shaving,
    §14a). Hypothesis: random flexible loads and grid limits, both schedules feasible, solver
    never worse than the heuristic.
  - Benchmark (`scripts/bench_planner.py --scenario full_house`, x86_64, 5 runs): 5931 columns,
    2414 rows, 234 binaries, solve 0.22 s cold / 0.21 s warm, whole run 0.26 s, MIP gap 0;
    heuristic objective -0.37 vs solver -12.88. Reference house unchanged (5 ms solve, 31 ms
    run). Slow CI test: full house < 5 s. aarch64 not measured (`UNVERIFIED`).
  - Deviations: plan section 8 (6-H).
- **Phase 6 - PR 6-E, WP5 notifications (branch `phase-6/e-notify`, on 6-D;
  `backend-engineer`, `ha-integration`, `control-safety` review pending for the confirm path)** -
  SunCore tells the user what matters and lets them confirm an assisted proposal from the phone:
  - `HAConnection.notify(domain, service, data, target)` with an allowlist
    (`persistent_notification.create|dismiss`, `notify.send_message` to `notify.*` entities,
    `notify.<name>` only for configured names), its own 1 s limiter, no payload in logs;
    `subscribe_notification_actions` (event `mobile_app_notification_action`, only the `action`
    string passes). WebSocket client and `sim` fake both implement it.
  - `suncore/notify/`: `rules.py` (categories: error, safety, proposal, program, EV target,
    cheap window, daily summary; first observation = baseline), `ratelimit.py` (dedupe key,
    interval, quiet hours 22:00-07:00, 20 push a day), `actions.py` (random single-use tokens
    bound to a proposal and its expiry), `sink.py`, `render.py` (backend ICU catalogue),
    `service.py` (observe, deliver, confirm path).
  - Confirm path: a tap is honoured only after the event subscription succeeded; token valid and
    unused, proposal still open, not expired, device still `assisted`; then one
    `Runtime.confirm_proposal` (the panel's `gate.confirm`, `Origin.USER`). Reject calls nothing.
    The port cannot raise a mode or clear the emergency stop (boundary tests).
  - API `GET/PUT /api/v1/notifications/settings`, `GET .../status`, `POST .../test`; MQTT
    mode-raise refusals (6-D) now raise a persistent notification.
  - Not in this PR: grid events (island, section 14a) are consumed from `Observation` once 6-F
    delivers them; program and EV events come with 6-L and 6-I (`NotificationService.post`);
    settings UI is 6-M. Review conditions of #57 (safety bypasses the daily cap,
    `gate.confirm` re-checks the current mode, a lost subscription drops buttons) are in.
    Deviations, safety self-review and questions: plan section 8 (6-E).
- **Phase 6 - PR 6-D, WP4 MQTT commands (branch `phase-6/d-mqtt-commands`, on 6-C;
  `ha-integration`, `control-safety` review pending)** - Home Assistant can now command SunCore,
  only through the services layer:
  - `suncore/ha/mqtt/commands.py`: parse (`ON`/`OFF`, `PRESS`, finite numbers in range, at most
    64 bytes), rate limit (1/s per entity, burst 3, 20 per 10 s over all; the emergency stop is
    exempt), the mode-raise rule, one `CommandPort` call per command, a `Reason` per outcome in the
    log; retained commands are ignored and removed from the broker; the true state is republished
    after every command (also after a refusal).
  - `app/ha_entities.py` `RuntimeCommandPort`: forwards to `Runtime.set_global_mode`,
    `set_device_mode`, `emergency_stop`, `clear_emergency_stop`, `override_device`, `replan` (the
    methods of the REST API). Never `apply()`, never the gate directly (boundary tests).
  - Announced command entities: `auto`, `replan`, `emergency_stop_button`,
    `emergency_stop_clear` (only with `mqtt.allow_mode_raise`), per device `<dev>_auto`,
    `<dev>_boost`, `<dev>_pause`, `<dev>_resume`, `<dev>_boost_minutes`.
  - Raising (`auto` ON, clearing the emergency stop) is refused unless `mqtt.allow_mode_raise`
    (default off): `safety.mqtt_mode_raise_disabled`. `auto` OFF lowers to shadow; stop, pause,
    resume, boost and replan always work.
  - Not in this PR (no config path or values yet): battery strategy/reserve/evening target, EV,
    comfort and program commands, the `mqtt` config author, the persistent notification (6-E).
    Review conditions A1, A2, N1-N3 are in (opt-in withdrawn at once, emergency stop in its own
    task, 5 s timeout, explicit payloads). Deviations and open questions: plan section 8 (6-D).

- **Phase 6 - PR 6-C, WP3 MQTT base and backend ICU (branch `phase-6/c-mqtt`, on 6-B;
  `ha-integration`, `backend-engineer`)** - read-only HA entities, no commands, nothing writes:
  - `suncore/i18n/`: minimal ICU renderer (arguments, `number` with `::.0`/`0` skeletons, `plural`
    with `=n`/`one`/`other`/`offset`, `select`, apostrophes; en/de plural rules and separators,
    `other` fallback), generated `catalogue/{en,de}.json` (`scripts/gen_backend_catalogue.py`,
    `--check` in `check.sh` and CI, prefixes `mqtt. notify. safety. controller. planner. device.
    grid.`), golden file `tests/fixtures/i18n_golden.json` from a Vitest helper with
    `intl-messageformat` (1 200 cases, Python equals it). Closes the ADR-0007 follow-up.
  - `suncore/ha/mqtt/`: `catalogue.py` (the v1 entity catalogue as data), `discovery.py`
    (device-based payloads with `via_device`, origin `SunCore`, topics, install id),
    `states.py` (on change, deadbands, 5 s per entity, retained), `broker.py` (Supervisor
    `GET /services/mqtt` or manual settings; status `disabled|no_broker|connecting|connected|error`
    with a reason key), `client.py` (`aiomqtt` on `paho-mqtt` 2.2: connect, back-off, last will,
    TLS, error classification), `service.py` (session, announce on connect and on the HA birth
    message after 1-5 s, removal of vanished devices and on disable, events), `check.py`
    (system-check item). Composition in `app/ha_entities.py`; runs only outside demo mode.
  - API `GET /api/v1/ha-entities/status` (state, source, entity count, reason key, install hint
    key), `POST /api/v1/ha-entities/republish`; system check "Home Assistant entities";
    `schema.d.ts` regenerated. Frontend settings page for it follows in 6-M.
  - **`hassio_api` stays off** (the Supervisor lets apps call `/services*` without it, verified);
    a boundary test pins `GET /services/mqtt` as the only Supervisor REST URL. In-process stub
    MQTT broker for the tests (no `amqtt`). Deviations and open questions: plan section 8 (6-C)
    and questions 15-16.
  - Announced so far: status, mode, available surplus, healthy, shadow mode, emergency stop,
    event entity; per device mode, power and last decision. Everything else of the catalogue waits
    for the PR that produces its values.

- **Phase 6 - PR 6-B, WP2 contracts and configuration (branch `phase-6/b-contracts`, on 6-A;
  `control-safety`)** - additive, no behaviour yet:
  - Actions `charge`, `loadpoint`, `inverter_limit` in the `Action` union; the gate rejects
    them with `safety.<kind>_not_supported` before mode handling, and every action but a release
    for a device with a `charger`, `loadpoint` or `sg_ready` control (`safety.charge_/
    loadpoint_/sg_ready_not_supported`) or a program spec (`safety.program_not_supported`).
    Property test: every `ActionKind` is judged or rejected; nothing is written, released or
    proposed. Devices of these controls get a placeholder driver that never writes.
  - `core/devices.py`: the three controls, `EvSpec`, `ProgramSpec`, `ThermalSpec`, `shed_rank`,
    `para14a`, `behaviour_confirmed` (identity; Phase 3 tokens unchanged). Phase 6 devices run at
    most assisted until the behaviour is confirmed.
  - `core/grid.py` (effective import/export limit, §14a budget, `GridState`, `BackupConfig`,
    `PeakShaving`, DST-safe month bounds), `core/thermal.py` (RC model, COP curve, generic
    air/water assumption), `core/flex.py` (EV modes/targets, program jobs and transitions);
    `DeviceBand.grid_power_max_w`/`start`, `GateInputs` export limit, §14a budget/status, grid
    state; `GridConfig` additions (peak shaving, dynamic/zero export, §14a limit and signal kind,
    backup).
  - New config sections `mqtt` (password stored, masked in API, export and history; a masked
    value keeps the stored secret), `notifications`, `inverter` (an import resets
    `limit_verified`). Template schema v1: the three controls, `defaults.ev/program/thermal`,
    seven capabilities (`templates/README.md`).
  - Storage: `suncore.db` 5 -> 6 (`flex_state`), `timeseries.db` 2 -> 3 (`grid_event` 400 days,
    `grid_peak` for ever). **Export the configuration before updating** (older versions refuse
    both files).
  - Frontend follows the regenerated types; the site form keeps the grid fields it does not
    edit yet. Deviations: plan section 8 (6-B).

- **Phase 5 – PR 5-L, WP12 docs (branch `phase-5/l-docs`, on 5-K + 5-F; `docs-writer`)**:
  - **Phase 5 in short:** analytics core (`suncore/analysis`, ADR-0014: attribution rule, two
    baselines), frozen `day_summary`/`accuracy_day` with a 365-day backfill, history API with CSV,
    plan-at/compare and decision explain, parameter registry with 32 parameters and the expert API
    (ADR-0015, `control-safety` reviewed), UI: plan chart v2, history, decision log with "Why?",
    expert mode, what-if; backtest engine with job API (`control-safety` reviewed). No new write
    path to devices; the backtest reads through `ReadOnlyHistory` and writes nothing back.
  - Guides en + de: `history-and-analysis.md`, `decision-log.md`, `plan-chart.md`,
    `expert-mode.md`, `what-if-and-backtesting.md` (limitations, the 5 % cost-fidelity tolerance
    as a stated expectation, link to the generated reference); quick start, README, both app
    `DOCS.md` and the roadmap point to them. Screenshots are the existing demo ones. There is no
    `mkdocs.yml` in the repository, so the README documentation list is the index.
  - Not done here: `docs/architecture.md` §7 (new tables) and §9 (routes, chunks) stay as they
    are because it is an engineering document; the architect should add `day_summary`,
    `accuracy_day`, `backtest_run`, and the routes `#/history`, `#/decisions`, `#/whatif`.
    The generated parameter reference (`docs/reference/parameters*.md`) is current from 5-E.
  - Merge of 5-F into 5-K: conflicts in STATUS, plan §8 and the catalogues were unions; router and
    `vite.config.ts` merged without conflict (chunks `echarts-core|plan|history`).
  - **Bundle after the merge (`npm run verify`):** tests and type check green (Vitest 382),
    first paint 106.3 kB (budget 125), ECharts chunks core 182.9 / history 13.3 / plan 9.1 kB
    (each under 200), core + history 196.2 and core + plan 192.0 kB (budget 240). The total
    without ECharts first summed both language catalogues (265.9 kB). Maintainer decision
    (2026-11-24): only the active language counts, because only one catalogue is loaded at
    runtime; the script counts the larger of en/de. **Total now 232.6 kB against 250 kB**;
    the budget stays unchanged.

- **Phase 5 – PR 5-K, WP11 what-if UI (branch `phase-5/k-whatif`, on 5-J + 5-I + 5-H;
  `frontend-engineer`)**:
  - `#/whatif` (lazy chunk `WhatIf`), reachable from the header only while expert mode is on; with
    expert mode off a deep link shows a hint, not the form. `api/backtests.ts` (list, get, start,
    DELETE = cancel or delete), `stores/whatif.svelte.ts` (runs, selection, 1 s polling only while
    a run is active, server date from `history/summary`), pure logic in `whatif/model.ts` (request
    building and checks, change list, comparison rows, CSV), chart options in
    `whatif/chartOptions.ts` (types only from ECharts; registered in the existing `echarts` chunk,
    194.3 of 200 kB, unchanged).
  - Form: period (default last 14 days up to yesterday, max 30, server date), planner parameters
    from `GET /params` filtered to tag `whatif` and numeric type (names and help from the lazy
    param catalogue), battery overlay (strategy, capacity, charge/discharge power, reserve,
    autarky weight, grid charging off/on with a price), flat tariff, perfect foresight. Only
    edited parameters are sent. Progress bar and cancel, a live-region announcement, 409/422/503
    keys through `tDynamic`.
  - Result: verdict (cheaper/dearer/same), "what is not modelled" from `result.limitations`,
    list of changes with `params` vs `params_requested` (budget capped), comparison table
    (actual, what-if, perfect foresight), reference costs (no PV, PV only), charts (net cost per
    day, SoC, grid power) with PNG download, daily table, skipped days with their reason keys,
    CSV of the daily table, run list (last five) with show, delete and cancel.
  - i18n: 5-H lazy main catalogue and 5-E lazy param catalogue compose (`Translator.extend` keeps a
    side catalogue next to the lazy `set`; main keys win). Vitest: model, chart options, store
    (polling, start, cancel, delete, 503), router; Playwright `e2e/whatif.spec.ts` on desktop,
    phone and tablet incl. axe and German; screenshots `whatif-*`.
  - Numbers: first paint 104.9 kB (budget 125), ECharts chunk 194.3 kB (200), total without
    ECharts 251.7 kB with both catalogues counted (the what-if view is 12.6 kB JS + 0.7 kB CSS,
    about 85 catalogue keys); 219.3 kB with only the active language counted (maintainer
    decision, see the 5-F merge entry above).
  - Deviations: see plan §8 (5-K).

- **Phase 5 – PR 5-J, WP6 backtest engine and job API (branch `phase-5/j-backtest`, on 5-D + 5-E;
  `optimizer-engineer`)**:
  - `suncore/sim/backtest.py`: per local day and hour, `PlannerInputs` from what was known then
    (newest stored PV/load forecast issued before the hour, stored prices up to their day-ahead
    publication at 13:00 local, the simulated SoC), the real planner (HiGHS + heuristic, warm
    start), then the hour executed against the actual PV and house load (`execute_slot`: PV
    surplus charges, a deficit discharges, inside the plan's band: SoC corridor, power limits,
    discharge lock, grid charge only where planned; efficiencies of the battery model). Runs
    compared on the same priced slots: actual (measured, equals `day_summary`), what-if and
    optionally perfect foresight; both baselines from `suncore.analysis`. Days with < 80 %
    measured, without prices or without a stored forecast are skipped with a `backtest.skip.*`
    reason; every result lists its `backtest.limit.*` limitations and is labelled
    `plan_with_perfect_execution`.
  - Overlay (`suncore/sim/backtest_models.py`, `core/params.py::check_whatif_overrides`): registry
    parameters of the `planner`/`forecast` groups tagged `whatif` only (`planner.budget_s`, capped
    at 10 s, and `planner.mip_gap` today); anything `safety`, controller or untagged is a 422
    `param_not_whatif`. Plus battery settings (strategy, autarky weight, grid-charge policy,
    capacity, power, reserve) and a flat tariff. Nothing is written back.
  - Jobs (`suncore/app/backtests.py`): one run at a time, refused while the planner solves; stop
    checked before every replan (cancel, 900 s runtime, 280 MB process RSS); results in
    `backtest_run` (last 5); runs left `running` are `failed` at start-up. History reaches the
    engine only through `ReadOnlyHistory`; boundary test: the backtest modules import nothing of
    `safety`, `ha`, `devices`, `controller`, `app.control` and name no write call; a demo-runtime
    test replays a day with zero service calls.
  - API: `POST|GET /api/v1/backtests`, `GET|DELETE /api/v1/backtests/{id}` (DELETE cancels a
    running run, deletes a finished one); new en/de keys; OpenAPI and `schema.d.ts` regenerated.
  - Numbers (amd64, `scripts/bench_backtest.py`, demo history): 14 days 8.9 s (336 replans, slowest
    0.05 s), with perfect foresight 16.6 s; 30 days 22.5 s / 39.2 s; peak RSS 80 MB. Override-free
    replay of the demo's last 14 days: 17.41 EUR vs 17.21 EUR recorded (+1.1 %); the test allows
    5 %. `UNVERIFIED`: run time and RSS on aarch64 (Pi 4).
  - `control-safety` review (PR #47, conditions A1–A10) addressed: the overlay is applied at
    start (422 `api.error.backtest_overlay_invalid|_no_battery`, nothing stored); a battery size,
    power or reserve change without a battery model is refused; the result echoes the effective
    params (`params`, budget capped) next to `params_requested` and lists values above the
    hardware (`backtest.limit.overlay_capped`); replays run in the planner's daemon-thread helper
    at niceness 10 (Linux, ignored elsewhere); cancel and shutdown wait at most 2 s and abandon a
    running solve; no start while shutting down; the loop yields between days and old days read
    `measurement_15m` only. Loop lag while a run replays: 65 ms worst (test bound 250 ms).
  - Deviations: see plan §8 (5-J).
- **Phase 5 - PR 5-F, WP7 plan chart v2 (branch `phase-5/f-plan-chart`, on 5-H;
  `frontend-engineer`)**:
  - `#/plan` shows measured values (`/history/series`, 15 min) as wide faint lines under the
    planned ones, a Gantt per controlled device and for the battery (planned bars outlined,
    measured bars solid, from `device.<id>.on` and `site.battery_w`), dataZoom synced over all
    panels with buttons and keys (arrows, `+`, `-`, `0`), tooltips with the interval reasons
    (`ReasonList`), PNG and CSV (plan, measured values, comparison).
  - Deep links: `#/plan?day=YYYY-MM-DD` (history), `#/plan?at=<time>` (decision log; marks the
    decision's interval), `&compare=<time>` (`/plan/compare`, dashed lines and a difference
    table); a look-back panel sets them. A missing snapshot (pruned) is said, the measured
    values still show.
  - Text alternatives: table with measured columns, device schedule as text, colour key.
  - Bundle: ECharts split into `echarts-core` 183 kB, `-plan` 9 kB, `-history` 13 kB gzip; core +
    view 192/196 of 240 kB; first paint 101.7 of 125 kB; total without ECharts 232 of 250 kB.
    Deviations: plan section 8 (5-F).

- **Phase 5 – PR 5-G, WP8 history view and export (branch `phase-5/g-history`, on 5-D;
  `frontend-engineer`)**:
  - `#/history` (lazy chunk): period day/week/month/year with previous/next/today and a date jump,
    state in the URL (`#/history?period=week&date=2026-06-10`); tiles (autarky, self-consumption,
    PV, consumption, import, export, cost, savings against both baselines, cycles) with the
    attribution box; charts energy bars, cost against the baselines, Sankey, heatmap, cycles with
    SoC range, forecast skill; device run-time table. Every chart has a data table as text
    alternative, a CSV link (`format=csv`) and a PNG button. Incomplete days are dashed and
    marked; collecting, unavailable (503) and per-section error states.
  - ECharts only in the existing chunk (`HistoryChart.svelte`: bar, line, sankey, heatmap,
    visualMap added). `api/history.ts` and the store live in the lazy chunk, not in first paint.
  - Verified: Vitest 307, `npm run verify` green, Playwright `history.spec.ts` 36 passed (desktop,
    phone, tablet; axe light and dark), screenshots `history-*` in `docs/assets/screenshots/`.
  - Bundle: first paint 124.9 kB gzip (budget 125 kB, the catalogues grew by about 90 keys),
    everything but ECharts 202 kB, ECharts chunk 194 kB (budget 200 kB). Deviations: plan §8 (5-G).

- **Phase 5 - PR 5-H, WP9 decision log and "why" (branch `phase-5/h-decisions`, on 5-D;
  `frontend-engineer`)**:
  - `#/decisions` is a table (cards on phones) with filters for device, outcome, layer
    (`intent.origin`), reason key or prefix, free text and a date range. The filters live in the
    hash (`#/decisions?device=..&kind=..&from=..`), so a link restores them; invalid values in a
    pasted link are dropped. Paging of 50 rows by `offset`, total from `X-Total-Count`, CSV export
    as a plain link to `GET /decisions?format=csv` with the same filters.
  - "Why?" per row (button, `aria-expanded`, Escape closes and returns the focus) loads
    `GET /decisions/{id}/explain` (row id from `X-Decision-Ids`): decision, reasons, plan band and
    expected flows of the interval, site readings, neighbouring decisions; links to the plan
    (`#/plan?at=<ts>`), the device card (`#/?device=<id>`, scrolls and focuses the card) and the
    log of that device.
  - Tests: Vitest (filters, store, readings, table/panel), Playwright `decisions.spec.ts`
    (filters, URL, CSV, keyboard, phone width, axe); screenshots `decisions-*` refreshed.
  - Bundle: only the active language's catalogue ships on first paint (English eager, German lazy):
    98.8 kB of 125 kB, see plan section 8 (5-H).

- **Phase 5 – PR 5-D, WP4 history API (branch `phase-5/d-api`, on 5-C; `backend-engineer`)**:
  - `GET /api/v1/history/{summary,series,sankey,heatmap,cycles,forecast-skill,devices}`, each with
    `format=csv` (UTF-8, comma, dot, ISO time with the site offset, units in column names);
    `GET /plan/at?ts=` (404 `api.error.snapshot_not_found` when pruned), `GET /plan/compare?a=&b=`
    (snapshot ids or times), `GET /decisions` with `from`, `to`, `layer`, `reason`, `q`, `offset`,
    `format=csv` and the headers `X-Total-Count` / `X-Decision-Ids` (row ids), and
    `GET /decisions/{id}/explain`. Week/month/year sum the frozen `day_summary` rows; today and
    yesterday (before the nightly job) are computed live (`DaySummaryJob.preview`).
  - Wiring: `Services.history_sources()` returns `HistorySources` (stores, tz, clock, live day);
    `None` -> `api.error.history_unavailable` (503). New keys in en+de; OpenAPI and `schema.d.ts`
    regenerated. Tests: `tests/api/test_history_api.py` (demo history, DST day, empty, pruned
    snapshot, filters, explain, validation, year read < 1 s).
  - Deviations: see plan §8 (5-D).

- **Phase 5 – PR 5-C, WP3 storage and recording (branch `phase-5/c-storage`, on 5-B; `backend-engineer`)**:
  - **Schema bump: `timeseries.db` 1 -> 2 (`day_summary`, `accuracy_day`) and `suncore.db` 4 -> 5
    (`backtest_run`). Earlier versions refuse the newer `suncore.db` and read a newer
    `timeseries.db` as "no time series": export before updating (release-risk note, plan R9).**
  - `day_summary` (one frozen row per local day, 23/25 h days included): flows by the ADR-0014
    attribution rule, KPIs, cost and both baselines (`suncore.analysis`), coverage, `derived_slots`,
    tariff fingerprint and currency. Cost is only computed for slots with a *stored* price (NULL
    without; `priced_slots` says how many). `accuracy_day`: MAE/bias per source, kind, day and
    horizon bucket. Both kept for ever; `size_report` lists them.
  - `suncore/app/summaries.py`: the nightly job (03:30, after downsample) computes yesterday and
    repairs the last 7 days (new row, or a row whose day has more measured slots than before; a
    tariff edit never changes a frozen day). First run on a file and the end of a rebuild/backfill
    walk the whole stored history (`catch_up`). Skipped while the time zone is unknown. The numbers
    are computed in a worker thread; `accuracy_day` is written after the forecast learning.
  - Backfill: `backfill_history` extends the 28-day rebuild to 365 days (hourly, `derived`, windows
    of 60 days because a series is limited to 20 000 points; marker `history_backfilled_at`; for
    installs that already rebuilt it runs once on the next start).
  - Recording: `device.<id>.power_w` was already recorded for every controlled device; added
    `device.<id>.on` (1.0/0.0, only OK-quality driver state). `suncore.storage.backtests`:
    `backtest_run` table and a repository keeping the last 5 (the engine follows in 5-J).
  - Demo: `suncore.sim.demo_history` seeds 90 deterministic days (setting `demo_history_days`,
    env `SUNCORE_DEMO_HISTORY_DAYS`, 0 = none) into the throw-away demo database only (it refuses
    anything under `/data`): measurements, prices, 40 days of forecasts, `day_summary`,
    `accuracy_day`, decisions and three solved plan snapshots; about 2 s at demo start.
  - `UNVERIFIED`: how many days of long-term statistics a given Home Assistant keeps and that the
    Supervisor proxy forwards `recorder/*` for 365-day ranges (days without statistics stay gaps);
    memory and time of the 365-day backfill on aarch64.

- **Phase 5 – PR 5-B, WP2 analytics core (branch `phase-5/b-analysis`, on 5-A; `optimizer-engineer`)**:
  - `suncore/analysis/` (pure, `core` only, `mypy --strict`): `energy.py` (quarter-hour Wh from
    mean-power series, incomplete slots dropped, coverage, 80 % completeness), `flows.py` (the
    ADR-0014 attribution rule per slot, exact balances, measurement residual), `kpi.py` (autarky,
    self-consumption, equivalent full cycles over `BatteryModel.usable_wh`), `cost.py` (grid cost,
    prices per slot, unpriced slots skipped and counted), `baselines.py` (no PV/no battery and
    PV-only, `savings = baseline - actual`), `period.py` (day/week/month/year buckets between local
    midnights, 23/25 h days, ISO weeks), `summary.py` (one period's figures).
  - Boundary test: `analysis` imports `core` only; package added to the strict mypy list and the
    coverage gate.
  - Tests `tests/analysis/` (hypothesis: flow balance, KPI bounds, savings identity, PV-only ≤ no-PV
    baseline for non-negative prices, energy-preserving bucketing across DST in four zones).

- **Phase 5 – PR 5-I, WP10 expert mode UI (branch `phase-5/i-expert-ui`, base `phase-5/e-params`;
  `frontend-engineer`)**: header switch `Expert mode` (`role=switch`, per browser in localStorage,
  default off); Settings "Expert" area is a lazy chunk (`ExpertArea`) generated from
  `GET /api/v1/params` (number/int/bool/enum/entity inputs, unit, default and hard range, search,
  "only modified", modified/unsaved/safety/restart markers, per-key and per-group reset, restart
  hint). A change of safety-tagged keys opens a confirmation dialog (old to new) and is sent with
  `confirm_safety`; client-side range check plus the API's `api.validation.param_*` messages per
  field. Debug views: raw live signals, plan JSON with copy, solver statistics (existing
  endpoints only). The `param.*` help catalogue is a lazy chunk (`params.<locale>.json`, commit
  `perf(i18n)`; `check_reasons.py` reads both). First paint 124.4 kB gzip (budget 125 kB, tight:
  keep new first-paint strings small), total without ECharts 201 kB. Vitest (model, generated
  form) and Playwright `expert.spec.ts` incl. axe light/dark.
- **Phase 5 – PR 5-E, WP5 parameter registry and expert API (branch `phase-5/e-params`;
  `backend-engineer`, safety bounds proposed for `control-safety` review)**:
  - `suncore/core/params.py`: `ParamSpec` registry (key, kind, unit, default, hard range, group,
    tags `expert|safety|restart|whatif`, catalogue keys `param.<key>.name|help`), range check
    (`ParamError` -> `suncore.param_*` validation keys). 32 parameters: 21 controller
    (`ControlSettings`; 16 `safety`), 7 planner/solver, 4 config
    (`tariff.export.remunerate_negative`, `tariff.export.negative_reference_entity`,
    `forecast.learning.min_history_days|load_ml_mode`).
    Config parameters take default/range from the model field and are tagged by
    `json_schema_extra={"param": ...}`; the rest is an explicit table (controller defaults read from
    `ControlSettings`, planner defaults tied to the runtime by a test).
  - Safety: `core/control.py::SAFETY_LIMITS` is the single table of hard bounds (inside the model
    bounds, around the defaults); the registry range of every `safety` key *is* that table, the
    `expert` document model rejects values outside; a *stored* document is repaired at load
    (out-of-range clamped, unknown keys dropped, logged) so a tightened limit cannot block the
    start-up; restore and import stay strict and take effect through `gate.handover`.
    No parameter can switch a check off (every limit has a positive minimum except the deficit
    battery budget, where 0 = no deficit allowed). `PUT /config/expert` refuses a changed safety
    value without `/params` confirmation. `control-safety` review: settle time and write
    interval max 60 s (they pace the import-limit back-off), release deadline 5–10 s,
    staleness factor 1.5–3, balance tolerance max 1000 W / 30 %, and
    `inputs_max_age_ticks × tick_s ≤ 180 s` (`INPUTS_MAX_AGE_LIMIT_S`).
  - New optional section `expert` (`core/expert.py`, schema v1, sparse `overrides` map, absent =
    today's behaviour, golden-tested); stored/exported/history like any section. Hot apply:
    `Runtime._apply_expert` swaps the planner settings in place (`PlanningService.update_settings`)
    and rebuilds the control stack (same hand-over as a device edit) only when a controller value
    changed; no parameter needs `restart`. The demo stores an empty section.
  - Tariff (§51 EEG switch and reference spot, plan question 8): `tariff.export.remunerate_negative`
    (default off) turns the region's `no_remuneration` rule off for the plant;
    `tariff.export.negative_reference_entity` (sensor, default unset) lets the rule judge a real
    spot price for the **current slot** of an all-in source (a price sensor has no curve; later
    slots keep the proxy). Both additive in tariff v3, watched/subscribed like the price entity.
  - API: `GET|PUT /api/v1/params`, `POST /api/v1/params/reset` (`suncore/api/params.py`); PUT is
    all-or-nothing, 422 for unknown key/type/range, 422 `param_confirm_required` for a changed
    safety value without `confirm_safety`, 409 when a config parameter's section does not exist.
    OpenAPI and `schema.d.ts` regenerated.
  - `scripts/gen_params.py` (+ `--check` in `check.sh` and CI) writes
    `docs/reference/parameters.md` and `parameters.de.md`; en/de catalogue keys added
    (`check_reasons` also checks the registry keys).
  - Tests: registry/range/safety (hypothesis: no accepted value leaves `SAFETY_LIMITS`, forced
    out-of-range rows are clamped), API, `expert` section storage/migration, wiring (golden of
    `ControlSettings`/`PlanningSettings`, stack rebuild, negative-price switch), generator.

- **Phase 4 – PR 4-I, WP12 optional ML load model (branch `phase-4/i-ml-load`, stacked on 4-H;
  `optimizer-engineer`)**:
  - `suncore/forecast/load_ml.py` (NumPy only): KNN on analogue days - quarter-hour samples of
    the base load history (`max(91, min_history_days)` days, ≥ 75 % coverage, unmeasured device
    slots dropped after 7 clean days), features day type (holiday ≈ weekend), slot (±2), daily
    mean temperature, 7-day load level, weak age term; K = 24, P50 weighted median, P10/P90
    weighted quantiles, ≥ 0, monotone, deterministic (stable sort). Forecasts after 7 usable days.
  - `forecast.learning.load_ml_mode`: `off` not computed; `shadow` (default) stored hourly with
    the profiles' issue time as source `load_ml`, scored nightly like `load_profile`, shown, never
    used; `auto` used by the planner (and the risk view) only with ≥ `min_history_days` days and a
    fresh (≤ 36 h) paired 14-day comparison on the same issues and quarter hours (≥ 7 days) with
    an MAE ≥ 2 % below the profiles' (left again only when no longer below: hysteresis), else the
    profiles; fitted once a day in a worker thread, stale after a day without a fit; every choice
    has a `forecast.load_model.*` reason, in `auto` also as a plan-level reason.
  - API: `ForecastAccuracyView.load_model` and `LoadForecastView.model` (`LoadModelView`, additive);
    `load_ml` rows in `/forecast/accuracy`. UI: "Machine-learning load model" in the forecast
    panel (mode, model in use, reason, both MAEs, history days); mode select in wizard step 4.
    en/de keys added (`check_reasons` green), user guide §2 "The machine-learning load model".
  - Benchmark `scripts/bench_load_ml.py` (synthetic house, 48 h = 193 slots, median of 5, session
    amd64 container): training + prediction 30 days 30 ms, 60 days 49 ms, 90 days 36 ms (budget
    1 s; slow test `tests/forecast/test_load_ml_bench.py`). Whole service refit (SQLite read of 91
    days, 3 devices, profile + KNN fit) 128 ms vs 82 ms without the KNN model; aarch64 not
    measured (`UNVERIFIED`).
  - Tests: `tests/forecast/test_load_ml.py` (features, DST, hypothesis: quantiles monotone,
    non-negative, on the grid, deterministic, every `auto` rule), `test_load_ml_service.py`
    (shadow/off/auto, fallback, window, nightly bookkeeping, stored scores), API, runtime, planner
    pass-through, Vitest panel and form.
  - Review fixes (M1, M2, minors) complete; `./scripts/check.sh` green (4709 tests). Screenshots
    `plan-tablet-*` and `wizard-pv-tablet-light-*` regenerated with the ML load model panel (the
    phone `plan-*` images are not used by the docs and were left as they were).

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
  - ADR-0013 "Battery as a controlled asset" **proposed** (prerequisite of WP10; accepted 2026-10-19).
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

- **Phase 4 – PR 4-G (WP10, battery control; branch `phase-4/g-battery`, on 4-F; `control-safety`)**:
  - Contracts (ADR-0013): `BatteryAction` = the full desired state, `None` = the adapter's baseline
    (`core.battery.resolve_battery_action`), capabilities per field, live `BatteryRanges`, min SoC
    rounded **up**; `core.identity.battery_identity(_token)` (asset id `battery`, adapter template,
    role entities, the site's SoC/battery power, modbus hub + unit id);
    `operation.battery_mode` (additive): a `battery`/`site` put that changes the identity drops it,
    an import resets it, a restore keeps it only for the recorded identity, a full `operation` put
    cannot raise it, `PUT /api/v1/operation/battery {mode, identity}` (404 observe-only, 409 stale);
    `battery.modbus` (hub, unit id, byte order, heartbeat 20 s, fallback timer 60 s) and
    `battery.fallback_measured` (additive).
  - Adapters `suncore/devices/battery/` (only the gate calls `apply`, `release`, `arm_fallback`,
    `keep_alive`; boundary tests): `generic` (number/select/switch roles), `kostal_plenticore`
    (`Battery:MinSoc` only, settle 35 s for the silent REST write and the 30 s poll, no charge
    set-point), `kostal_modbus` (§3.5 via `modbus.write_register` - a write of this adapter only,
    never a read-only action; float32/U32 words in the configured order; refuses unless 1080 = 2;
    1284/1286/1288 armed and read back before any limit; heartbeat; release writes the own limits).
    Templates `generic.battery`, `kostal.plenticore`, `kostal.plenticore_g3_modbus` (`driver`,
    register map, capability `hardware_fallback`; schema v1 additive) with conformance scenarios.
  - Safety: the blanket `safety.battery_not_supported` stays only without a battery adapter or for
    other asset ids; battery intents pass B1-B9 (`safety/battery.py`, hypothesis property tests),
    mode capped at assisted while the hardware fallback is not measured, mandatory read-back, a
    lease re-judged every tick by `poll()` (heartbeat only while it holds), override detection,
    release check, battery watch (frozen battery power / SoC), every Phase 3 release path. All
    Phase 3 safety tests unchanged and green.
  - Controller: `controller/battery.py` turns the band of a fresh plan interval into intents
    (grid charging, discharge lock as `max_discharge_w` 0 or a min-SoC floor, release), hysteresis,
    one intent per minute, `controller.battery.grid_charge_unsupported` for Kostal REST; a held
    forced grid charge is never surplus for the devices.
  - App: `ControlStack` battery wiring, control state `battery`, devices' reserve guard =
    `effective_reserve_pct` with a battery model; OpenAPI and frontend types regenerated, no UI.
  - Simulator: `sim/inverter.py` (own control as fallback, BMS drift, REST lag and silent failures,
    §3.5 fallback timer, hang); scenarios `modbus_heartbeat_lost`, `battery_reserve_drift`,
    `kostal_rest_reserve`, `inverter_hang_battery`, `grid_charge_price_level` (real planner,
    controller and gate in automatic mode).
  - Decisions and deviations: plan §"Deviations during implementation" (PR 4-G).
  - Review fixes (2026-10-26, reviewer blocker B1, M1-M3, minors 1-8): latched battery release
    with read-back of the original baseline and back-off retries (also under emergency stop),
    B4/B5 on the written state, Kostal §3.5 baseline = own limits and fallback confirmed against
    the written values, register 5 role, lease ends on refusal / plan end, arming outside the
    lock, user's select/switch restored, `fallback_measured` is identity, OK-only SoC, a more
    realistic simulator (REST 30 s, modbus scan lag, stale registers, price jump). ADR-0013 got
    an implementation addendum. Re-check fixes (2026-10-28): own late/partial writes are never
    taken for user values (`DeviceRuntime.attempted`, per-field rule), user values found during a
    release get the override hold, the generic adapter remembers user options only before its
    first write attempt.
    Approval follow-up: when the read-back shows an older own state instead of the held one,
    the gate writes the held state again through the evaluated path (B1-B9, read-back) or
    releases if that is refused. For the 4-H user docs: a user value that happens to equal a
    value SunCore wrote or attempted while in control is taken for SunCore's and reset to the
    baseline at release (accepted by the reviewer).

- **Phase 4 – PR 4-H, WP13 docs, translations, STATUS (branch `phase-4/h-ui-docs`; `docs-writer`)**:
  - User guide `docs/user/<lang>/planning-and-battery.md` (en/de, same structure): the planner
    (48 h, 15 min, every 15 min and on events, solver or rule-based fallback, "Why?"), forecasts
    (sources, blending by measured accuracy, calibration after 30 days, clipping, accuracy panel,
    Open-Meteo CC BY 4.0), load forecast (base load without controlled devices, workday/weather,
    `days_offset`, 28 days from HA statistics), tariffs (price formula for spot prices only, tiered
    feed-in, negative-price rule), risk level, morning buffer, evening target, bridging, strategies,
    the self-sufficiency weight, grid charging, battery control (shadow, assisted, automatic,
    limits only, effective reserve, release and hardware fallback, manual changes until 04:00 with
    the documented corner case, emergency stop), adapters with the step-by-step enabling of the
    Kostal `Battery:MinSoc` entity and a **procedure to measure the Kostal §3.5 fallback timer**
    (with abort criteria), a "first battery to assisted" walk-through, troubleshooting, known
    limitations. Screenshots `plan-*`, `now-battery-*`, `wizard-battery-*` from the demo.
  - Quick start (steps 4, 5, 6, 8, 10, Now view, settings, "cannot do yet"), surplus control
    (the plan sets bands, battery first until the morning buffer, your target is a floor, schema 4),
    both app `DOCS.md` (Phase 4 features, planning section, demo variables, "not there yet"),
    README status, architecture §4 (planner paragraph, deviation events), roadmap Phase 4 table.
  - The generated compatibility list was already current (`scripts/gen_compat.py --check`).
    Translations checked: the frontend catalogues have identical key sets (1311 keys), argument
    names match, `scripts/check_reasons.py` is clean (383 backend keys), the app options
    translations match `schema:` in both apps; no gaps found, nothing changed.
  - Facts found while writing: a normal app stop **releases** the battery first, so measuring the
    Kostal timer needs an abrupt end of the container (`docker kill`); `battery.fallback_measured`
    has no switch in the panel (the guide uses export, edit, import). The gate caps only adapters
    with the `hardware_fallback` capability (Kostal Modbus); the generic and Kostal REST adapters
    are not capped (the card's "generic" cap note cannot appear).
- **Phase 4 – PR 4-H, part WP11 UI (branch `phase-4/h-ui-docs`, on 4-G; `frontend-engineer`)**:
  - Plan view `#/plan` (own lazy chunk; ECharts 5.6 tree-shaken to line and bar series, grid,
    tooltip, legend, markLine, inside zoom and the canvas renderer, its own `echarts` chunk that
    is not reachable from `index.html`): three panels (power with the PV P10-P90 band and P50,
    house load, battery power and grid import/export bars; planned SoC with morning-buffer and
    evening-target lines; import/export price steps), 24/48 h toggle, tooltip with the reasons of
    the interval, solver/heuristic/default and stale badges, "Replan" (debounced, server answer
    `accepted`/earliest start shown), planner status (last run, trigger, next run, failures), a
    text alternative (planned actions with "Why?", hourly table). "Now" is the server's
    hand-over time (the demo runs on a simulated clock).
  - Forecast panel: sources and their freshness, MAE/bias/n per source and horizon (collecting
    chip below `min_samples`), blend weights with their basis, calibration state per source, "ML
    load model: not available yet", Open-Meteo CC BY 4.0 attribution with links when an Open-Meteo
    source is configured.
  - Now view: battery card (mode switch with confirmation for assisted and automatic, identity
    token like a device, SoC/power, held or current limits, paused reason, last decision, the
    assisted cap note while the adapter's hardware fallback is not measured) and "next planned
    actions" with "Why?"; battery proposals read like device proposals (limit parts).
  - Wizard: step 4 several sources with weight mode and share, Forecast.Solar config entry
    (choices from `energy/get_prefs`, additive field `solar_forecast_entries` of the
    energy-prefs answer; manual id when none), workday and weather inputs (new site roles
    `workday`, `weather`); step 5 adapter or observe only, adapter entities, Kostal REST and
    Modbus hints, modbus link, model parameters, strategy, risk level, morning buffer, bridge;
    step 6 HT/NT editor (`tou`, periods with weekdays, midnight windows and months), price
    contents and price formula with time-variable grid fees; step 8 grid-charge policy (price
    level and weekly schedule, off by default) and the self-sufficiency weight; step 10 first plan
    preview. Settings overview shows sources and battery control and offers the battery section's
    history. The tariff and forecast forms no longer drop `role_entities`, `action_params`,
    `series_key`, `price_includes` and `config_entry_id` (round-trip tests).
  - Demo: `SUNCORE_DEMO_BATTERY_CONTROL=1` gives the simulated house a controllable inverter and
    stores a `generic.battery` adapter (used by Playwright and the screenshots); off by default.
  - Not done: the "too-new `timeseries.db`" hint (the API exposes no such state), editing of
    `action_params`/`series_key`, `learning` settings and the periodic calibration charge (carried
    through unchanged), a mini chart in the summary beyond a sparkline.
  - Verified (2026-10-28): `./scripts/check.sh` green (4654 pytest, coverage 96 %, 267 Vitest),
    Playwright against the demo 228 passed on desktop, phone and tablet (axe on plan, Now and the
    wizard steps), markdownlint clean.
  - Bundle: first paint 119 kB gzip (was about 100 kB: the two catalogues grew by about 170
    keys), everything but ECharts 182 kB (budget 250 kB), ECharts chunk 177 kB on demand.

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

## Open - Phase 7 (carried over from Phase 6)

Phase 7 is the UI design pass, the documentation site (`mkdocs.yml`, `mkdocs build`), Weblate,
the Lovelace cards, the companion integration (ADR-0005) and 1.0. What Phase 6 leaves for it:

- **Maintainer acceptance, nothing of Phase 6 has run on hardware:** MQTT with the Mosquitto app
  and one notification on the phone (stage A), then the grid section (static export limit shown,
  §14a "not active", backup entity mapped or skipped), the simulated "car arrives late" and a
  washing-machine job in the demo, later a real wallbox, heat pump, evcc and inverter limit.
  The 16 questions of plan section 10 need answers or confirmation of the defaults.
- **Still not fed or offered (6-O closed the rest):** costs, savings, autarky, forecast error and
  the next action as MQTT entities (nothing produces them); the event types `island_*`,
  `para14a_*`, `program_*`, `cheap_window_*` and `ev_target_at_risk` of the MQTT events entity;
  HA **commands** for EV mode, target, program arming, comfort band and the battery settings
  (with the author `mqtt` in the config history; needs a `control-safety` review, plan section 8,
  6-O); a push for a target the plan meets but the controller later misses.
- **Panel gaps:** inverter verification test as an endpoint with a dialog (without it
  `limit_verified` stays false and no limit is ever written), per-device `para14a` flag,
  `shed_rank` and the island keep list, evcc `loadpoint_id` and `topic_prefix`, a manual MQTT
  broker, COP at the current weather on the heat pump card, bundle budget (first paint 123.5 of
  125 kB: move more wizard steps out).
- **Behaviour decisions deferred:** hard target at any price for the EV (the late-car scenario
  stops at about 77 % of 80 %), grid pre-heating execution, `generic.heat_pump_select`, a
  `button.press` remote start, a plan clear for evcc, resetting evcc's mode at unplugging.
- **Budgets to measure:** resident memory 136 MiB measured locally (7-N); the CI loaded smoke
  and the aarch64 planner job (at most 10 s; amd64: 0.26 s for the full house) still need their
  first run.
- **Docs:** screenshots of the new views through the `demo-screenshots` skill, a followed test of
  every new how-to on Home Assistant OS, the docs site and the Weblate project (the translation
  structure of `docs/user/en` and `docs/user/de` is identical).
- Deferred past 1.0 unless asked: built-in PV and price fallbacks, calibration charging, per-phase
  imbalance limits, V2H, several vehicles per charger, EEBus and the certified §14a path.

## Open – Phase 4

- PR order (stacked, each on the previous, `phase-4/<letter>-<topic>`): 4-B storage/time series
  (WP3) → 4-C tariffs (WP4) and 4-D PV forecast (WP5) → 4-E load + risk (WP6, WP7) → 4-F planner and
  runtime (WP8, WP9) → 4-G battery control (WP10) → 4-H frontend + docs (WP11, WP13); 4-I ML load
  (WP12, optional, after 4-E). Merge only after the Phase 3 acceptance.
- **WP10, WP11 and WP13 done** (4-G and 4-H, see Done). All Phase 4 PRs are open and stacked; merge
  order **#25 → #26 → #27/#29 → #30 → #31 → #33 → 4-H → 4-I** (merge commits are fine; **squash is
  recommended**, one commit per PR on `main`), **only after the Phase 3 acceptance** (plan §6).
  ADR-0013 accepted by the maintainer on 2026-10-19. 4-I (optional ML load model) is implemented on
  `phase-4/i-ml-load`; open are the follow-ups below.
- **Safety note:** the documented fallback-timer measurement lifts the assisted cap (via
  `fallback_measured`); the procedure gets a `control-safety` review (the lead runs it). It needs
  the app's Watchdog switch off, otherwise the Supervisor restarts the killed app and SunCore itself
  releases and writes within the 60 s (false positive); Supervisor behaviour read in the source,
  `UNVERIFIED` on HA OS.
- **Calibration charge:** only a configuration field so far, the planner does not use it (plan §6
  question 11 said "built"; corrected in the plan's deviations). Open.
- **S1 (code, separate PR):** bind `fallback_measured` to the measured `fallback_timeout_s` (store
  the measured timeout, the gate caps unless equal); needs an ADR-0013 addendum and a gate test.
  The `control-safety` review of the measurement procedure approved it with changes R1-R7, applied
  in the guide (2026-10-29).
- Follow-ups from 4-H and the docs: `fallback_timeout_s` changes should drop the battery mode to
  shadow on the server (follows); a switch for `battery.fallback_measured` in the panel (today:
  export, edit, import), a hint for a too-new `timeseries.db` (the API exposes no such state), the
  panel for editing `action_params`/`series_key`/`learning.min_history_days` (the ML mode has a
  select since 4-I).
- Maintainer questions from 4-G (PR description): grid charging on the Kostal (generic
  force-charge entity or a §3.4 ADR); the fallback timer measurement procedure for
  `battery.fallback_measured`. Answered: no permanent reserve floor on the inverter.
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

1. Maintainer: **Phase 3 acceptance in two stages** (decided 2026-11-23 because there is no PV
   surplus in winter; the logic is covered by the simulator scenarios, re-run in shadow and automatic
   on 2026-11-23). *Stage A, now:* export a configuration backup (schema 4 cannot be opened by
   earlier versions), install the current beta, keep everything in **shadow mode** for 1-2 days and
   check that no "paused" or "stale data" banner appears (battery SoC, LG power sensor), that the
   **Decision log** stays (nearly) empty without surplus and that the system check is green.
   *Stage B, on the first day with real surplus:* are the "would have" entries plausible at noon,
   then switch **only the LG climate** to automatic, check the first live writes (`accepted`, not
   `failed`) and the emergency stop. Stage B does not block later phases.
2. Then **Phase 4**, following `docs/user/en/planning-and-battery.md` (or `de`):
   1. Wizard: step 4 (forecast sources, workday and weather inputs), step 5 (battery model,
      strategy, risk level), step 6 (price formula for a spot source, HT/NT), step 8 (grid charging
      stays off), step 10 (first plan).
   2. Look at the **Plan** view in shadow for a day or two: are the reasons and the evening target
      plausible, is the accuracy panel filling up (calibration needs 30 days)?
   3. Enable the disabled Kostal `Battery:MinSoc` number entity in Home Assistant, map it in step 5
      (adapter "Kostal Plenticore, minimum SoC"), compare the signs, then switch the battery to
      **assisted** and confirm the first proposals.
   4. Optional, later: enable external battery management via Modbus on the inverter, add the modbus
      hub, and measure the fallback timer with the documented procedure; then set
      `battery.fallback_measured`.
3. ~~Merge the stacked PRs~~ (done 2026-11-23: #25–#36 and #35 as one merge commit, because
   squashing would have broken the stack). Release the next release-please PR (#17) for the stable
   app only after the acceptance (schema 4: release note "export before updating").
4. The questions of 4-A/4-B/4-C were answered with the defaults on 2026-10-19. Still open: what needs
   the maintainer's system or bills (VAT on negative spot prices, the EEG tiers, the Kostal fallback
   timer measurement, the publication time of next-day prices) and the 4-I questions (defaults built).
5. Phases 5 and 6 were built on the defaults of their plans' §6 while the maintainer was away
   (`docs/plans/phase-5.md`, `docs/plans/phase-6.md`). Review and merge the Phase 6 stack in
   order (6-A ... 6-N), read the consolidated questions in `docs/plans/phase-6.md` section 10, and
   do the acceptance listed under "Open - Phase 7". Then Phase 7 (plan to be written by the
   architect).
6. Phase 5 (#38–#50): review and merge in the order A to L (see the table in
   `docs/plans/phase-5.md` §3); decide the bundle budget first (item 1 below). It was built on the
   defaults of plan §6 while the maintainer was away. After the merge: run a backtest and the
   history on the real system, and a 14-day backtest on a Pi (time and memory).

### Open maintainer questions from PRs #38–#50

Each was built with the proposed default; the answer may change that default.

1. **Bundle budget (5-K, #50): decided 2026-11-24.** Only the active language catalogue counts
   towards the 250 kB total; the budget stays. Total after 5-F + 5-K: 232.6 kB.
2. **Backtest start while the planner solves (#50):** the server answers 409; should the UI retry
   by itself?
3. **Max. switches per hour in expert mode (#42):** hard bound 30, safety review recommends 12.
4. **Negative-price reference (#42):** is a sensor with the current spot price enough for now?
5. **Restore/import with changed safety values (#42):** no confirmation (the UI shows the
   difference first, 5-I). The dialog (#44) does not show the allowed range; add it?
6. **Backtest (#47):** what-if battery and tariff fields as `whatif` registry parameters instead
   of fixed request fields? Memory guard 280 MB (plan said 200 MB)? Prices known from 13:00
   local the day before? HT/NT and price formulas in the what-if form or flat tariff only? State
   the 5 % cost-fidelity tolerance in the user guide (done in 5-L, change if you disagree).
7. **History API (#43):** headers `X-Total-Count` and `X-Decision-Ids` instead of a new response
   model; `layer` filters on `intent.origin`; resolution picks the finest one within 2 000 points
   (plan decision 8 contradicts itself).
8. **Storage (#41):** run the 365-day backfill once on existing installs (default yes); demo
   history on by default (about 2 s longer start, default yes).
9. **Analytics (#40):** equivalent full cycles over the usable capacity (default) or the nominal
   one; cost is grid exchange only, without battery wear and standing charges.
10. **History view (#46):** is "period plus date jump" enough, or should `/history/summary` take a
    free range?
11. **Decision log (#45):** link `#/plan?at=<time>` (done in 5-F); are site readings plus device
    power and state enough in the "Why?" panel?
12. **Plan chart (#48):** no blocking questions; window snapshot per window, measured CSV with at
    most three devices, zoom not in the URL.
13. **Release:** `timeseries.db` 1 to 2 and `suncore.db` 4 to 5 are one-way; the release note must
    say "export before updating" (plan R9). #39 (dependency bump) and #49 (Phase 6 plan) are not
    part of Phase 5.

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Phase 6 residual risk (6-J): for evcc `smart` the import limit (G1) is checked reactively; evcc may import by its own plan until the controller saw the import and the cut was echoed, about 1 min (60-90 s with noisy import, scenario `evcc_plan`: 20 s over the limit); the gate only sees what evcc *may* do. Accepted, the fuse and the operator's limit stay the user's own protection | `suncore/controller/evcc.py`, `suncore/safety/evcc.py`, `docs/user/*/ev-charging.md` | a running evcc with a smart-cost limit or plan |
| Phase 6 residual risk (6-J): evcc liveness is `<prefix>/updated` younger than 120 s; a quiet evcc that publishes less often trips falsely, and an evcc clock running ahead makes an old retained timestamp look fresh | `suncore/app/evcc_link.py`, `templates/device/evcc.loadpoint.yaml` | measure the publication interval of the user's evcc |
| Phase 6 residual risk (6-L): the safe state of a program's plug is ON (a failed power sensor, a crash, "back to normal", a missed job release it and the machine starts early); if SunCore does not run while the plug is off nobody switches it on, so the guides recommend an HA automation "plug off longer than X h -> on" (SunCore does not create it) | `suncore/safety/programs.py`, `docs/user/*/programs.md`, `docs/user/*/surplus-control.md` | maintainer decides whether SunCore should create the automation (plan section 10) |
| Phase 6 residual risk (6-G): a held AC limit in register 533 survives a crash or a lost connection without a documented timeout; when the house load rises above it the difference is imported | `suncore/devices/inverter/`, `docs/user/*/grid-limits.md` | the inverter's communication-timeout setting; until then prefer the REST export limit |
| Budgets: resident memory is 136 MiB after 10 min with flex, MQTT and replans (x86_64, local, 7-N); the cgroup figure in the image and everything on aarch64 are not measured; the planner at most 10 s on aarch64 (amd64: 0.26 s for the full house) | `scripts/smoke_image.sh` (`LOADED=1`), `.github/workflows/bench-arm.yml`, `scripts/bench_planner.py` | first CI run on `main`; a Pi 4 (plan question 14) |
| 7-N CI jobs (upgrade test, loaded smoke, arm benchmark) were written without a Docker daemon, `actionlint` or `shellcheck`; the pieces were rehearsed locally (real 0.2.0 code seeded over HTTP, current code verified, loaded API steps against the real app) | `scripts/upgrade_test.sh`, `scripts/upgrade_probe.py`, `scripts/smoke_image.sh`, `.github/workflows/` | first CI runs |
| Digests pinned 2026-11-25 by lookup: `ghcr.io/pxljake/suncore` 0.2.0 index `sha256:05641188…3b32`; `eclipse-mosquitto:2.0.23` index `sha256:6a332cc7…2ad9` (verified against the manifest body hash) | `scripts/upgrade_test.sh`, `scripts/smoke_image.sh` | refresh at each release (skill `prepare-release`) |
| `release-as` is deprecated in release-please's schema (a `Release-As:` footer replaces it) and not set yet; what `breaking_versions` does for installed versus store config is not verified; `stage` default `stable` verified in Supervisor source | `release-please-config.json`, `scripts/beta_version.py`, plan questions 23, 24 | maintainer decision; 7-N part 2 |
| The Phase 6 user guides (6-N) are written from the code and the catalogues; their set-up steps and the UI names were not followed on a real Home Assistant, and the new views have no screenshots yet | `docs/user/*/home-assistant-entities.md`, `notifications.md`, `grid-limits.md`, `ev-charging.md`, `heat-pump.md`, `programs.md` | maintainer follows them; screenshots through `demo-screenshots` (Phase 7) |
| 6-D MQTT commands: default payloads of the MQTT `switch` (`ON`/`OFF`), `button` (`PRESS`) and the `number` keys `min`, `max`, `step`, `mode` (HA source was not reachable in the session; used as documented); that HA sends commands with retain off | `research/mqtt-discovery.md`, `suncore/ha/mqtt/commands.py`, `discovery.py` | maintainer's HA: press the buttons, set the switches and the boost duration, see the true state come back |
| 6-C MQTT: Home Assistant `get_config` carries the UI language under `language`; a custom HA birth topic is never configured (default `homeassistant/status`); the unit rule of `monetary` sensors; TLS to the Mosquitto app with certificate verification; the Supervisor `GET /services/mqtt` answer on a real HA OS (verified from source only) | `research/mqtt-discovery.md`, `suncore/ha/mqtt/` | maintainer's HA with the Mosquitto app: entities appear, birth republish, language, a restart of the broker |
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Energy-Charts and aWATTar APIs and terms (not shipped in Phase 4) | `research/integrations.md` | session with network access (Phase 6) |
| Open-Meteo azimuth convention in a live response (code: 0 = south, OpenAPI text contradicts); separate terms page (README only) | `research/integrations.md` | live request; maintainer reads the terms page |
| Kostal REST battery/grid power sign; `ActivePowerLimitation` semantics; live entity ids | `research/integrations.md` | maintainer's HA (enable the entities, compare with the inverter display) |
| Kostal Modbus external battery management not enabled on the maintainer's inverter; how it is enabled; §3.5 fallback timer behaviour (the `kostal_modbus` adapter and its fallback are exercised in the simulator only, scenario `modbus_heartbeat_lost`) | `research/kostal-plenticore-modbus.md`, ADR-0013, `suncore/devices/battery/kostal_modbus.py` | maintainer enables it and measures, then sets `battery.fallback_measured`; capped at assisted until then |
| Kostal Modbus U32 word order of register 1288 assumed equal to the float order (register 5); modbus sensor `scan_interval` vs the adapter's 15 s settle time; how 1076/1078 (the battery's own limits) move with temperature and SoC (the fallback gets max(model, own)) | `suncore/devices/battery/modbus_codec.py`, `templates/battery/kostal.plenticore_g3_modbus.yaml` | first Modbus write on the maintainer's inverter |
| Kostal REST: battery/grid power sign (the site's `invert_sign`), the min-SoC read-back timing after the forced refresh (35 s settle assumed), whether out-of-range values are clamped or rejected; the number is disabled by default | `suncore/devices/battery/kostal_plenticore.py`, `templates/battery/kostal.plenticore.yaml` | maintainer enables "Battery min SoC" and watches the first assisted write |
| Creating a modbus hub from the HA UI (else YAML); the hub name and unit id the user configures | `research/ha-websocket-api.md`, `battery.modbus` | maintainer's HA |
| Generic battery adapter (numbers/select/switch) and the battery watch thresholds (frozen 600 s / 50 W / grid 500 W, SoC frozen after 3 % of the capacity moved) | `suncore/devices/battery/generic.py`, `suncore/safety/battery.py` | first real battery integration; shadow run |
| Supervisor proxy passes `recorder/*` and `energy/*` commands | `research/ha-websocket-api.md` | first Phase 4 run on HA OS |
| Supervisor proxy forwards `recorder/statistics_during_period` (the 4-B rebuild logs a warning and retries) | `suncore/ha/statistics.py` | first run on HA OS |
| `change`/`sum` rows of real energy counters (the rebuild reads them as verified from source, not tested against live counters); `backup_exclude` accepts the glob `timeseries.db.corrupt*` | `suncore/ha/statistics.py`, `addon/*/config.yaml` | maintainer's HA with an energy counter; a backup on HA OS |
| Publication time of next-day prices (Tibber, Nord Pool, ENTSO-E, EPEX) | `research/integrations.md` | observe on the maintainer's HA |
| Nord Pool `config_entry` id source, sensor translation keys, publication time; Octopus/ENTSO-E/EPEX entity matchers (platform and unit only, no translation keys); `start`/`end` sent to `tibber.get_prices` as ISO with offset; negative-price rule proxy for all-in sources | `templates/tariff/*.yaml`, `suncore/tariff/pricing.py` | live check on a system with the integration |
| VAT applied to negative spot prices | `suncore/core/tariff.py` | maintainer's bill / supplier terms |
| KNN load model run time on aarch64 (amd64: training + 48 h prediction 30–50 ms for 30–90 days, `scripts/bench_load_ml.py`); its constants (K 24, slot window ±2, scales 4 K / 15 % level / 120 days age, 2 % margin) are reasoned, not fitted | plan WP12, `suncore/forecast/load_ml.py` | CI arm runner or a Pi; 30+ days of the maintainer's data in `shadow` |
| Energy colour variable names (`--energy-*`) | `frontend/src/app.css` | read HA frontend source |
| HA UI menu paths in the user docs (Settings → Apps → App store, Repositories, Show in sidebar; German labels) | `addon/*/DOCS.md`, `README.md`, `docs/user/*/quick-start.md` | maintainer checks on HA OS during the beta install |
| Supervisor Core proxy passes `supported_features` through | `suncore/ha/websocket.py` | first run on HA OS |
| Registry and energy-prefs shapes on cores older than dev (single `config_entry_id` on devices, unified grid source); `unknown_command` code without the `energy` integration; Supervisor proxy passes registry commands | `research/ha-websocket-api.md` | first run on HA OS; parsers tolerate both shapes |
| Section 51 EEG 2023 text could not be fetched; region `de` feed-in tiers are empty until the official BNetzA table is cited | `templates/region/de.yaml` | maintainer provides the table or a session with access to gesetze-im-internet.de |
| Whether Tibber's `total` includes grid fees (country dependent) | `templates/tariff/tibber.price.yaml` | live check on the maintainer's Tibber account |
| LG translation keys (inferred from source and unique ids; LG entity ids, modes and units checked live on 2026-10-14), other LG models' modes (Solcast default day-sensor count verified from source in 4-A; Tibber publication time: see the price row above) | `research/device-integrations.md` | maintainer's HA with LG ThinQ, Tibber, Solcast (Phase 2 wizard test) |
| Wording of the quick start steps and the HA menu names in the new docs (written from the UI catalogues, not clicked through on a real HA); the surplus control and planning guides are written from the code and the demo, their walk-throughs have not been followed on a real system | `docs/user/*/quick-start.md`, `surplus-control.md`, `planning-and-battery.md` | maintainer follows the quick start and the first-device and first-battery walk-throughs on HA OS |
| Steps to enable the disabled Kostal `Battery:MinSoc` entity (HA menu names for disabled entities), the `docker kill` way to end the app without a hand-back, the container name, and the sensor behaviour while measuring the §3.5 timer (what exactly shows the fall-back: battery power and grid power are assumed) | `docs/user/*/planning-and-battery.md` (adapters, "Measuring the fallback timer") | maintainer's HA OS with a Kostal inverter |
| Which Home Assistant tool lets users place files in the app's `/data/templates` | `docs/user/*/device-templates.md` | maintainer checks on HA OS |
| Water heater templates (`generic.water_heater_steps`, `_relays`), drivers for select/number/relays/mode, `ClimateDriver` paths other than `lg_thinq` source reading: simulator only, no hardware; `lg_thinq` `step is None` `TypeError` on `set_temperature`, push latency after a command, `climate.turn_on` fallback for modes not in `hvac_modes` | `templates/device/generic.water_heater_*.yaml`, `suncore/devices/drivers/`, `research/device-integrations.md` | first limited live write on the maintainer's AC; a user with a water heater |
| Default sensor cadences (power sensor 60 s = the client resync, energy counter without `counter_update_s` 300 s); whether a quiet plug re-reports an unchanged value so that `last_reported` moves | `suncore/app/cadence.py`, `research/ha-websocket-api.md` | shadow-mode run on the maintainer's system; set `power_update_s`/`counter_update_s` where a sensor is slower (`power_update_s` is in the device editor since WP10) |
| Controller tuning (deficit delay/budget, downside spread of the smoothing, counter-only climate needs `nominal + span + hysteresis` free surplus to shift; battery share without a goal = battery first) | `suncore/controller/`, `tests/controller/test_climate_equivalence.py` | shadow-mode run on the maintainer's system (plan exit criterion) |
| §14a numeric signal convention (allowed power in W while dimmed, 0 when not dimmed; 0 W cannot be a legal dimming value given the 4.2 kW minimum, a positive value is used as min(value, limit)) and the EMS formula for several devices | `suncore/core/grid.py` (`para14a_budget_w`), ADR-0018, plan section 8 (6-F) | a grid operator's control box / documentation |
| Kostal register 533: readable back (Kostal lists only FC 0x06), whether it includes battery discharge, the reset behaviour (volatile per rev 2.9, re-written every heartbeat); `Inverter:ActivePowerLimitation` semantics (export or AC limit), whether the REST value survives an inverter restart, out-of-range handling | `suncore/devices/inverter/drivers.py`, `templates/inverter/`, `research/kostal-plenticore-modbus.md` | maintainer's inverter: the verification test before `limit_verified` |
| Export chain tuning (curtail delay 120 s, AC hysteresis 3 %, lease at half the 500 W margin, device headroom >= 100 W) and the simulator's 0-8 W regulation jitter below an AC limit (keeps the watchdog's frozen-PV check quiet) | `suncore/controller/export.py`, `suncore/safety/inverter.py`, `suncore/sim/limiter.py` | shadow run on the maintainer's system with surplus |
| Backup-switch island state names (simulator: `island`/`on_grid`; Kostal BackUp Switch G3 entity and states unknown) | `suncore/core/grid.py` `BackupConfig`, `suncore/sim/fake_ha.py` | maintainer maps the entity (question 10) |
| Grid shedding and §14a dimming exercised in the simulator only (no fuse, peak tariff, control box or backup hardware in the session) | `suncore/controller/grid.py`, `suncore/safety/grid.py` | shadow run on a system with the hardware |
| SG-Ready contact mapping per vendor; charger phase-switch pause | `suncore/core/devices.py`, `suncore/core/flex.py`, ADR-0017 | templates with vendor sources (6-I, 6-K) |
| evcc as actor: exercised on the fake evcc only (no running evcc in the session); clearing a vehicle plan over MQTT; how fast evcc applies `maxCurrent` while charging; evcc resetting the mode at vehicle disconnect (read as an override); `smart` with `alwaysCharge` for `min_pv`. Resolved from the evcc source (2026-11-24): loadpoint ids start at 1, `phasesConfigured` 0/1/3, the retained read topics, the heartbeat `<prefix>/updated` | `templates/device/evcc.loadpoint.yaml`, `suncore/devices/evcc.py`, plan section 8 (6-J) | a running evcc with MQTT enabled on the maintainer's broker |
| Wallbox control: exercised in the simulator only (no wallbox in the session); the phase switch pause (120 s default), whether a charger accepts a phase change while paused, min run/pause defaults (300 s) for cloud-polled chargers; no integration template verified from `home-assistant/core` source (only home-assistant.io `wallbox` page, 2026-11-24) | `templates/device/generic.wallbox.yaml`, `suncore/devices/drivers/charger.py`, plan section 8 (6-I) | a real wallbox; a session with access to the core source |
| Shiftable programs (6-L): exercised in the simulator only; whether an appliance resumes its programme after the plug's power returns (per appliance, the user confirms `resume_confirmed`); remote-start switches per integration (a `button` role is not supported yet); placeholder run time 120 min, `idle_w` 5 W and peak power in the templates; appliances that draw more than `idle_w` right after start are treated as running (no delay) | `templates/device/generic.program_*.yaml`, `suncore/devices/drivers/program.py`, plan section 8 (6-L) | a real washing machine/dishwasher behind a plug; an integration with a remote start |
| Notifications (6-E): payload of `mobile_app_notification_action` beyond `data.action`; that the Supervisor token may `subscribe_events` for it (else plain messages); `data.actions` on every legacy `notify.<service>`; `notify.send_message` has no button field | `suncore/ha/connection.py`, `suncore/notify/`, `research/ha-websocket-api.md` | first run on HA OS with the companion app |
| Heat pump (6-K): SG-Ready contact mapping of the generic template (normal 00, intensified 01, forced 11, block 10); which integrations expose a heat pump's hot water as `water_heater` and how fast they apply a target; template defaults (dwell 600 s, min run 1200 s, min pause 600 s, 3/h, comfort 45-55 °C, restart 3 K); grid pre-heat planned but not executed; simulator only, no heat pump in the session | `templates/device/generic.heat_pump_*.yaml`, `suncore/core/sgready.py`, plan section 8 (6-K) | a user's heat pump with its manual; 6-I import mechanism |
| RC learner (6-K): losses fitted against the outdoor series (stores indoors lose to the room); small fast stores miss the quality threshold on 15-minute data and stay energy-only | `suncore/forecast/thermal.py` | 14+ days of a real heat pump's probe, power and weather |
| SG-Ready contact mapping per vendor; charger phase-switch pause; evcc `phasesConfigured` values, loadpoint ids starting at 1, the min+PV mapping to evcc `smart` | `suncore/core/devices.py`, `suncore/core/flex.py`, ADR-0017 | templates with vendor sources (6-I, 6-J, 6-K) |
| evcc REST API paths | `research/competitors.md` | not needed: the MQTT API is verified from source and sufficient |
| Solcast day sensors d3/d4: translation keys `total_kwh_forecast_d3`/`_d4` from the research notes; the date-time format the fallback action `query_forecast_data` accepts for `start_date_time`/`end_date_time` (SunCore sends ISO 8601 with offset) | `templates/forecast/solcast.pv.yaml`, `suncore/forecast/pv/adapter.py` | maintainer's HA with Solcast |
| Open-Meteo Solar Forecast: translation key assumed equal to the description key (the CC BY 4.0 attribution is shown in the plan view since 4-H) | `templates/forecast/open_meteo_solar_forecast.pv.yaml` | a running integration |
| Forecast.Solar `wh_hours`/`watt_hours_period` keys read as period *start* (vendor API unreachable) | `templates/forecast/forecast_solar.pv.yaml`, `suncore/forecast/pv/parsing.py` | live comparison with the integration's sensors |
| Supervisor proxy forwards `energy/solar_forecast` | `suncore/ha/` (`fetch_solar_forecast`) | first run on HA OS |
| PV calibration constants (classes kt 0.3/0.65, shrink k = 16, decay 0.98, factor 0.5–1.5) and the clipping thresholds (plateau ±0.3 % for ≥ 1 h on ≥ 2 days; export within max(3 %, 50 W)) are reasoned, not fitted to real data | `suncore/forecast/pv/` | 30+ days of the maintainer's data |
| Planner solve times on aarch64 (Raspberry Pi 4, HA Green); amd64: 4 ms solve, 24 ms whole run for the reference house, 0.22 s solve / 0.26 s run for the 6-H full house (EV, program, heat pump, 234 binaries); HiGHS releasing the GIL on aarch64 (measured on amd64) | ADR-0003, `scripts/bench_planner.py`, `tests/planner/test_gil.py` | run the benchmark and the slow tests on a Pi / the CI arm runner |
| Negative-price rule slots in the planner use a proxy (export price ≤ 0 and import price < 0); a spot-only source with a formula whose end price stays positive is not detected | `suncore/planner/inputs.py` | tariff service exposes the rule's slots (Phase 5) |
| Planner weights and rules: target penalties (10 × max price + 1, buffer 2 × + 0.5), terminal value (median import × discharge efficiency − degradation/2), device budget on P50 plus overflow (a day far below P50 can miss the evening target, review D4), flat 500 W base load without a load forecast, automatic device value export + 0.01 | `suncore/planner/model.py`, `inputs.py` | shadow plans on the maintainer's system |
| `weather.get_forecasts` `hourly` support of the maintainer's weather integration (the `temperature_unit` attribute and the conversion of the forecast to it are verified from source, `research/ha-websocket-api.md`) | `suncore/app/adapters.py` (`HaLoadReader`) | maintainer's HA |
| `workday.check_date` over the Supervisor proxy and with the maintainer's Workday configuration (the `days_offset` handling is verified from source; a changed country/holiday list is not visible in the attributes, so the day-type cache only resets on a changed entity, `workdays`, `excludes` or offset) | `suncore/app/adapters.py`, `suncore/forecast/load_service.py` | maintainer maps a Workday sensor |
| Load and risk constants (half-life 21 days, outlier z 3, shrink 3 days, 7 clean days, degree-day bases 15/22 °C, coverage 75 %/90 %, 20 h temperature days, anchors sunrise + 1 h / sunset − 1 h, sunrise threshold 2 %/20 W) and the default PV bands per weather class (clear 0.85/1.10, mixed 0.55/1.35, overcast 0.35/1.70) are reasoned, not fitted | `suncore/forecast/load.py`, `suncore/forecast/risk.py`, `risk_learning.py` | 30+ days of the maintainer's data |
| Known limitation (n6): the load history and the risk samples are not re-derived when the device list changes - a device added later is only subtracted from the base load from the day its power is recorded (or rebuilt), and older risk samples keep the old base load | `suncore/forecast/load_service.py`, `risk_learning.py` | accepted for Phase 4; the KNN model (4-I) shares the base load and the limitation; revisit in Phase 5 |
| Backtest run time and peak memory on aarch64 (Pi 4, HA Green); amd64: 14 days 8.9 s, 30 days 22.5 s, RSS 80 MB (`scripts/bench_backtest.py`); the 5 % cost fidelity was measured on the demo history only (+1.1 %), not on real data | `suncore/sim/backtest.py`, `docs/user/*/what-if-and-backtesting.md` | CI arm runner or a Pi; a 14-day override-free run on the maintainer's history |
| Number of days of long-term statistics a Home Assistant keeps and whether the Supervisor proxy forwards `recorder/*` for 365-day ranges; time and memory of the 365-day backfill on aarch64 | `suncore/storage/`, `suncore/app/summaries.py` | first start on HA OS |
| Expert parameter bounds in `SAFETY_LIMITS` (reviewed, not tried on hardware); the "needs restart" marker (no parameter uses it) | `suncore/core/control.py`, `docs/reference/parameters.md` | shadow run; maintainer's decision on the switches-per-hour bound |
| Wording and walk-through of the Phase 5 guides (written from the code, the demo and the catalogues, not followed on a real system) | `docs/user/*/history-and-analysis.md`, `decision-log.md`, `plan-chart.md`, `expert-mode.md`, `what-if-and-backtesting.md` | maintainer follows them on his system |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
