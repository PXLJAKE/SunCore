# Phase 3 – Real-time controller and safety

Status: **approved** by the maintainer on 2026-10-14 (answers in §6: water heater built
without hardware to test, climate switch-on per device, manual changes are respected until
the next day or a configurable number of hours, no grid import caused by SunCore; defaults
otherwise)
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §3, §4.5 (common concepts, power without a
power sensor, device types, real-time surplus distribution), §4.11, §4.14, §5, §8, §9, §10;
[`docs/roadmap.md`](../roadmap.md) Phase 3; ADR-0004, 0006, 0011, 0012;
[`docs/architecture.md`](../architecture.md) §4–§6; the "Phase 3 follow-ups" in
[`docs/STATUS.md`](../STATUS.md).
Branches: `phase-3/a-safety`, `phase-3/b-controller`, `phase-3/c-ui` (stacked, see §3).

Phase 3 turns SunCore from an observer into a controller. A deterministic real-time
controller computes the available surplus every few seconds and allocates it to the
configured devices by priority with hysteresis and protection times. Every intent passes the
safety gate, which now knows the three operating modes per device and globally, checks the
hard invariants, detects manual overrides, watches its inputs and releases hardware control
on any doubt. **Shadow mode stays the default for the system and for every new device**; a
device writes only after the user has switched it (and the global mode) to automatic.

## 1. Goals and acceptance criteria

Phase 3 is done when (roadmap Phase 3 milestones):

1. **Controller:** surplus computation (BRIEF §4.5 formula), priority allocation, hysteresis
   with delay, min run/pause, max switches per hour, smoothing against cloud flicker. A
   simulator test with cloud flicker proves bounded switching (switch count ≤ limit, no
   on/off cycle shorter than `min_run_s + min_pause_s`).
2. **Safety:** invariants as pure functions with property-based tests (grid import limit
   where controllable, comfort bounds, min run/pause, max switches/hour, per-device rate
   limit, write only on change, read-back after write, battery reserve as a guard for
   Phase 4); modes shadow/assisted/automatic per device and globally; override detection;
   watchdogs (stale/implausible/frozen inputs, connection loss); fail-safe on shutdown,
   crash recovery, emergency stop.
3. **Power models:** measured, counter-derived (LG ThinQ hourly Wh), learned, nominal – each
   returns a `PowerEstimate` (value + uncertainty + quality); a simulator test shows the same
   allocation behaviour for one climate unit with a power sensor and with only an energy
   counter (within a documented tolerance, wider hysteresis for the counter case).
4. **Devices:** climate (set-point shift inside the comfort band, on/off via HVAC mode, both
   power models), water heater (stepped, 1–3 relays or a select/number), switchable loads,
   continuous number loads (already templated in Phase 2).
5. **Decision log + "Why?":** every decision stored with reasons (versioned payload,
   retention); the UI shows per device the current state, what SunCore would do / did and
   why, the decision history, assisted proposals to confirm, and the global emergency stop.
6. **`control-safety` review** documented in every PR (invariant table pass/fail with
   evidence).

Exit (roadmap): climate surplus control runs in shadow mode on the maintainer's system with
plausible "would have" entries; the maintainer switches one device to automatic.

## 2. Work packages

Owner = subagent that implements it. Budget rule (CLAUDE.md): **Fable only for
`control-safety` (controller and safety) and the final `reviewer`**; Sonnet for drivers,
power models, backend, simulator, frontend and docs; Haiku for catalogue sync.

### WP1 – Verify HA write interfaces · `ha-integration` (skill `verify-ha-api`)

Read from `home-assistant/core` (GitHub, the doc sites are unreachable) and record in
`docs/research/ha-websocket-api.md` / `device-integrations.md`:

- Services and fields: `switch.turn_on/turn_off`, `input_boolean.*`, `climate.set_temperature`
  (`temperature`, `target_temp_low/high`, `hvac_mode`), `climate.set_hvac_mode`,
  `climate.turn_on/turn_off` (and the `ClimateEntityFeature.TURN_ON/OFF` bits),
  `number.set_value`, `select.select_option`, `water_heater.set_temperature` /
  `set_operation_mode`; `call_service` result and error shape (`ServiceValidationError`,
  `return_response`).
- Context ids: does a state change caused by our `call_service` carry our `context.id` (and
  `parent_id`/`user_id`) in `subscribe_entities` updates? This is the basis of override
  detection (R3). `last_reported` vs `last_updated` for polled sensors with unchanged values
  (needed for the 5-minute `smartthinq_sensors` power sensor, R4).
- `lg_thinq` climate: set-point step, `turn_on` delay (2 s seen in source), behaviour of
  `set_temperature` while `off`.
- Everything that cannot be verified stays `UNVERIFIED` in code and PR.

### WP2 – Contracts v2 · `control-safety` (Fable)

`suncore/core/` (pure, `mypy --strict`):

- New action variants next to `SwitchAction` / `PowerSetpointAction`: `SetpointAction`
  (absolute °C, plus the user's base set-point it shifts from), `StepAction` (step index),
  `ModeAction` (mode value), `ReleaseAction` (restore baseline). Device power stays + =
  consumption.
- `Decision` gets `schema_version` and new kinds `proposed` (assisted, awaiting the user),
  `held` (override active), `failed` (write or read-back failed); `DecisionLog.latest`
  deserialises tolerantly (old v1 rows still load). Closes the STATUS follow-up "version the
  decision payload before changing `Decision`".
- `PowerEstimate` (value W, uncertainty W, quality, age) and `DeviceState` (on/off/step/
  set-point/mode as observed, with quality).
- Minimal `Plan` contract (ADR-0011 hands it to Phase 4): per interval and device an
  `allowed`/`preferred` band, plus a `Plan.default()` that allows everything. The controller
  consumes `Plan`; Phase 3 only ever uses the default plan, so the Phase 4 planner plugs in
  without changing the controller.
- `ControlSettings` (tick interval 5 s default, smoothing window, reserve W, default
  hysteresis/delay/settle/max switches per hour where the device leaves them `None`).

### WP3 – Safety layer · `control-safety` (Fable)

`suncore/safety/`:

- `invariants.py`: pure functions over `(Intent, DeviceRuntime, Signals, limits, now)` →
  `Ok | Violation(Reason)`: grid import limit (controllable share: never *add* load that
  pushes import above `site.grid.import_limit_w`), comfort band (set-point never outside
  `comfort`), **no grid import caused by SunCore** (see WP7), min run/pause, max
  switches/hour (sliding hour), per-device write rate limit,
  write-only-on-change, battery reserve (guard only; no battery writes in Phase 3).
  Property tests with hypothesis over random intent sequences and clocks.
- `gate.py`: per-device and global mode from an **injected mode provider** (the app passes
  the `operation` snapshot; `safety` still never imports `storage`/config, boundary test
  stays). Effective mode = minimum(global, device). Shadow → `shadow` decision; assisted →
  `proposed` (expires after `proposal_ttl_s`, default 120 s; confirm via API re-submits with
  `Origin.USER`); automatic → invariants → `apply()` → settle → read-back from the entity
  cache (not `get_states`) → `accepted` or `failed`.
- `overrides.py`: a state change that is not explained by our last write (context id, or
  value differs from the read-back after settle) marks the device `held` (R3). A unit
  switched off by remote control stays off, a changed temperature is kept (it becomes the
  new base set-point). Per device `override_policy`: **`next_day`** (default: SunCore takes
  over again at the configured local time of the next day, default 04:00) or `hours` (1–48 h).
  The UI shows "held until …" and offers "resume now". "Boost N min" / "Pause until" are
  explicit user overrides through the API.
- `watchdog.py`: plausibility per scope – stale (per-entity max age, slow sensors get their
  cadence × 2), unavailable, unit invalid, jumps (> configurable W/s), frozen inverter values
  in daylight (maintainer's Kostal hang, BRIEF §9), balance not closing. Grid/PV/battery
  faults pause **all** devices, a device's own faults pause only that device; HA connection
  loss pauses everything. State machine per architecture §5 (`SafeState` → cool-down →
  back to the previous mode).
- Fail-safe "release": each driver restores its baseline (WP6); triggered by the emergency
  stop, SIGTERM/shutdown (bounded by a 10 s deadline), mode change to shadow, and on start-up
  after a crash (persisted takeover baseline, WP4). Emergency stop is persisted and survives
  a restart.
- Rate limiter with try-acquire/deadline (stale intents are dropped, never queued), per
  device; connection flood floor lowered from 1 s to 0.25 s (STATUS follow-up).
- Device identity rule (STATUS F6/A2): a device whose `template_id` or entities changed
  counts as new and falls back to shadow on put/import/restore of `devices` and
  put/restore of `operation`; **an import always resets the global mode to shadow**.
- Boundary tests: rule 2 extended to `suncore.devices` drivers (only `safety` and
  `suncore.app` may import them); `test_phase_1_has_no_apply_call_anywhere` replaced by
  "only `safety.gate` calls `apply()`".

### WP4 – Storage · `backend-engineer`

- Migration `0003_runtime.sql` (DB schema version 3; 0.3.x refuses it, documented):
  `device_runtime` (last switch times, switches in the last hour, last written action,
  takeover baseline, override until), `safety_state` (emergency stop), decision retention
  (default 30 days and at most 200 000 rows, pruned hourly), `power_model_state` (learned
  coefficients per device).
- `timeseries.db` and `plan_snapshot` stay in Phase 4 (not needed by the controller).
- Tests: migration from a v2 DB fixture, refusal by an older reader, retention pruning,
  restart restores runtime (min run/pause survive a restart; unknown → conservative).

### WP5 – Power models · `device-adapters`

`suncore/devices/power/` (pure, no I/O, time injected):

- `measured`: latest sample; uncertainty grows with age relative to the sensor cadence
  (5-minute `smartthinq_sensors` sensor = slow, R4).
- `counter_derived`: average power from counter deltas, handles hourly quantisation
  (LG ThinQ: 3600 s), midnight reset of "today" counters, counter going backwards, unit
  conversion Wh/kWh; uncertainty from quantisation and age.
- `learned`: online recursive least squares on (on/off, HVAC mode, |set-point − room|,
  outdoor temperature if mapped) → power, trained from counter-derived or measured
  intervals, prior = `nominal_w`, bounded to `[0, 2 × nominal_w]`, falls back to nominal
  until enough samples; state persisted (WP4).
- `nominal`: on → `nominal_w`, steps/modes → their `power_w`, continuous → written value.
- Validation against the grid balance (BRIEF §4.5): when a device switches, the measured
  change of grid/house power confirms or widens the estimate (shared helper, used by
  `learned`).

### WP6 – Drivers · `device-adapters` (write paths reviewed by `control-safety`)

`suncore/devices/drivers/` implementing `Actuator` (+ `release()`), writing only through the
`ServiceCaller` held by safety, verified services from WP1:

- `OnOffDriver` (`switch`, `input_boolean`), `ClimateDriver` (set-point shift relative to the
  user's base set-point inside the comfort band; switching on/off via HVAC mode only when
  `allow_switch_on` is set for the device – per-device opt-in in the device editor, default
  off; the HVAC mode used for switching on (heat/cool) is configured per device; baseline =
  user's set-point and HVAC mode at takeover),
  `StepsDriver` (select option or number value; or 1–3 relays, see question 3),
  `NumberDriver` (W, A, %), `ModeDriver` (select/preset).
- Read-back maps the cached entity state to an `Action`; write only on change; never
  blocks the loop.
- New template `generic.water_heater_steps` (stepped heater with temperature sensor role
  and `max_c` via the comfort band) in two variants: one select/number entity, and 1–3 relay
  switches (`relays` control kind, an additive schema v1 change per ADR-0012 policy). The
  maintainer has no heater: it is tested in the simulator only and marked `UNVERIFIED`
  (`tested_with: []`).
- `DeviceConfig` additions (additive, defaults keep old documents valid): `allow_switch_on`,
  `switch_on_hvac_mode`, `override_policy` (`next_day` + time | `hours`).

### WP7 – Controller · `control-safety` (Fable)

`suncore/controller/` (pure core + a thin async loop, time injected, no sleeps in logic):

- `surplus.py`: `surplus = export + Σ controlled device power (estimates) + battery share −
  reserve`, smoothed (EWMA/median window, configurable), with an uncertainty band from the
  power estimates. Battery share from goals: with a `battery_target` that the current SoC
  has not reached yet, battery charging power is *not* available; with "consumers before
  battery" it is (Phase 4 replaces this rule with the planner's band).
- `allocation.py`: devices in priority order, each gets what it can use from the remaining
  surplus; on/off needs `nominal + hysteresis_w` to switch on and drops below `−hysteresis_w`
  to switch off, both after `hysteresis_delay_s`; hysteresis widened by the estimate's
  uncertainty (counter-only climate); continuous loads follow with a dead band; climate
  shifts the set-point (heat: up to `+shift_max_c`, cool: down) inside the comfort band and
  back to the base when the surplus is gone; time windows and quiet hours; `climate_before_
  export` goal.
- **No grid import caused by SunCore** (maintainer, 2026-10-14): controlled devices may only
  use real surplus. Adding load needs `surplus − uncertainty ≥ need + hysteresis`. When a
  controlled device suddenly draws more (compressor ramp, Δtemp), the battery buffers the
  short deficit; if grid import or battery discharge attributable to controlled devices lasts
  longer than `deficit_delay_s` (default 30 s, shorter for uncertain estimates), the
  controller backs off in steps: set-point back toward the base, then lower steps/power,
  then off (only after `min_run_s`; switch-off is always allowed for devices SunCore switched
  on). A small battery energy budget per deficit (default 50 Wh) bounds the buffering, and
  the battery is never used below the reserve. Grid import is only ever planned for smart
  battery charging (Phase 4).
- Hysteresis state machine per device with explicit states and reasons (`controller.*`
  catalogue keys), emits intents **only on change**, so the decision log does not fill
  every 5 s.
- `loop.py`: tick every `ControlSettings.tick_s`, reads signals from the entity cache,
  submits intents to the gate, reports deviation events (for Phase 4).

### WP8 – Simulator · `qa-engineer`

- `sim/devices.py`: simple physics for climate (room RC model, compressor power by mode and
  Δtemp, hourly Wh counter with the LG quantisation, optional 5-minute power sensor),
  stepped water heater, switch load; `FakeHAConnection` executes the verified services
  against them and emits state changes with context ids.
- Scenarios (BRIEF §4.14): `cloud_flicker`, `climate_min_run`, `climate_counter_only`,
  `climate_measured_slow`, `sensor_dropout` (extended), `inverter_hang`, `ha_disconnect`,
  `manual_override`, `water_heater_steps`, `grid_limit`.
- Tests: switch counts and min run/pause hold in every scenario; measured vs counter-only
  equivalence (R2); fail-safe paths (shutdown, crash + restart, disconnect, emergency stop)
  end in the baseline state; long runs (simulated 7 days) without oscillation.
- Demo mode: the controller runs in shadow mode on the demo devices so the UI shows
  "would have" entries; one demo device is switchable to automatic against the simulated
  house (never against HA).

### WP9 – Runtime and API · `backend-engineer`

- Composition root wires controller loop, gate with mode provider, drivers, runtime
  repository; graceful shutdown calls release before closing the HA connection.
- Endpoints: `GET /api/v1/control/state` (per device: state, estimate, mode, held/override,
  last reasons), `POST /api/v1/control/emergency-stop` and `/release`, `POST
  /api/v1/devices/{id}/override` (boost N min / pause until / clear), `GET
  /api/v1/proposals`, `POST /api/v1/proposals/{id}/confirm|dismiss`, decisions with filter
  by device and kind; live WebSocket carries control state changes.
- Operation mode endpoints now take effect; the UI label "active from a later version"
  goes away.

### WP10 – UI · `frontend-engineer`

- "Now" view: device cards (state, estimated power with uncertainty, mode switch per device
  with a confirmation for automatic, held/override badge, "Why?" with the latest reasons);
  global emergency stop button reachable from every view; assisted proposals with
  confirm/dismiss; decision log view (filter by device/kind, rendered `Reason`s).
- Settings → operating mode step: modes active, explanation of shadow/assisted/automatic.
- i18n en/de for every key (`i18n-sync`, Haiku); `check_reasons.py` covers `controller.*`
  and `safety.*`; bundle stays under 250 kB gzip.
- Vitest + Playwright E2E in demo mode (emergency stop, mode switch, proposal confirm,
  decision log), axe WCAG 2.1 AA; screenshots light/dark, phone/tablet via the
  `demo-screenshots` skill.

### WP11 – Docs, STATUS · `docs-writer`

- User docs en/de: how surplus control works, operating modes, overrides, emergency stop,
  power models (why counter-only devices react slower), "first device to automatic" guide.
- Architecture §4–§6 updated to the implementation; ADR only if a decision deviates from
  ADR-0011 (none planned). App `DOCS.md`, README status, STATUS, roadmap, `UNVERIFIED` table.

## 3. Pull requests and order

Three stacked PRs (same pattern as Phases 1/2). Each is reviewed by `reviewer` (Fable)
before opening; 3-A and 3-B additionally signed off by `control-safety` with the invariant
table in the PR description.

| PR | Content | Depends on |
|---|---|---|
| 3-A `feat(core,safety,storage)` | WP1, WP2, WP3, WP4 | – |
| 3-B `feat(devices,controller,sim)` | WP5, WP6, WP7, WP8, WP9 | 3-A |
| 3-C `feat(frontend),docs` | WP10, WP11, STATUS/CHANGELOG | 3-B |

```text
WP1 ─► WP2 ─► WP3 ─► WP4 ─┐
              └► WP5 ─► WP6 ┼─► WP7 ─► WP8 ─► WP9 ─► WP10 ─► WP11
```

Delegation is sequential, at most two subagents in parallel (WP4 ∥ WP5). I integrate, run
`./scripts/check.sh` and the frontend checks after every WP and commit per WP. CHANGELOG is
produced by release-please from the Conventional Commits (no hand edits).

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Invariants | hypothesis | each invariant over random intents/clocks/runtimes; the gate never writes in shadow/assisted, never under emergency stop, never with a violated invariant |
| Controller | pytest, time injected | surplus formula, allocation order, hysteresis state machine, switch counting, windows |
| Power models | pytest, hypothesis | counter deltas with quantisation/reset/backwards, RLS bounds, uncertainty monotone in age |
| Drivers | fake `ServiceCaller` | exact service payloads (from WP1), write-only-on-change, read-back, release |
| Simulator | `-m simulator` | the scenarios of WP8, 7-day oscillation run, fail-safe paths |
| Storage | pytest | migration 0003, retention, restart restores runtime |
| Boundary | AST test | only `safety.gate` calls `apply()`, drivers only imported by `safety`/`app`, safety reads no config/storage |
| API | httpx ASGI | control state, emergency stop, overrides, proposals |
| Frontend | Vitest, Playwright, axe | cards, modes, proposals, decision log, emergency stop |
| Coverage | pytest-cov | ≥ 80 % on core/safety/planner/controller (gate unchanged; aim ≥ 90 % for safety) |

No Docker daemon in the session: image and smoke test run in CI only. No hardware: the
maintainer's shadow-mode run is the acceptance test.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | A bug writes to real devices | comfort/hardware harm, trust lost | shadow default everywhere, automatic needs two explicit switches (global + device), invariants property-tested, `control-safety` sign-off, release on any doubt |
| R2 | Counter-only climate (hourly Wh) too slow to control | late or wrong switching | uncertainty-widened hysteresis, learned model, grid-balance validation; set-point shift (gentle) instead of on/off; scenario `climate_counter_only` |
| R3 | Override detection mis-fires (cloud push echoes, LG 2 s delay) | SunCore fights the user or holds forever | context-id check if WP1 verifies it, else value comparison after `settle_s`; conservative hold; test `manual_override` |
| R4 | Slow or polled sensors look stale (5-min power sensor) | needless safe state | per-entity cadence from template/power model, `last_reported` if verified |
| R5 | Inverter hang (frozen values) not detected | controller acts on old surplus | frozen-value watchdog in daylight, scenario `inverter_hang` |
| R6 | Decision log growth on a Pi SD card | wear, size | intents only on change, retention, batched inserts |
| R7 | Restoring the "baseline" surprises the user (e.g. AC turned off) | comfort | baseline = state at takeover, documented; question 2 |
| R8 | PR 3-B too large to review | slow review | split into 3-B1 (devices/power) and 3-B2 (controller/sim/runtime) if > ~3 000 lines |
| R9 | DB schema 3 blocks a downgrade to 0.3.x | user cannot roll back | documented in release notes; export before update |

## 6. Questions and answers

Answered by the maintainer on 2026-10-14; bold defaults applied where he did not answer.

1. PR structure: **three stacked PRs (3-A, 3-B, 3-C)** (default).
2. "Back to normal" / release: **restore the state at takeover** (default).
3. Water heater: the maintainer has none, **build it anyway** (select/number and 1–3 relays),
   simulator-tested only, `UNVERIFIED`.
4. Climate switch-on: **allowed, opt-in per device** (`allow_switch_on`).
5. Manual changes (remote control off, temperature changed) are respected: the unit stays as
   the user left it. SunCore takes over again **the next day** (default 04:00 local) or after
   a configurable number of hours, per device; "resume now" in the UI.
6. **No grid import caused by SunCore**; the battery may buffer short deficits, then
   SunCore backs off (WP7). Grid import only for smart battery charging in Phase 4.
7. Assisted mode: **proposals in the UI, valid 2 minutes** (default).
8. Import resets the global mode to shadow: **yes** (default).
9. Controller tick **5 s**, smoothing window **60 s** (default).

### Answers after implementation (2026-10-16)

The maintainer asked for the safe, simple choice everywhere ("sicher, nichts kaputt, nicht
verkompliziert"), so every open question keeps the behaviour as built:

- PR 3-B stays one PR.
- Without any goal the battery comes first; "climate before export" frees the battery's charging
  power for all controlled devices (as implemented).
- Hold (API `boost`) stays a pure hold; pause gives a controlled device back.
- Non-urgent hand-backs wait for min run/pause; emergency stop, shutdown and crash recovery give
  back at once.
- A configuration change resets a running set-point shift to the base (no short-cycling).
- A change found at restart is respected under the override policy (next day 04:00 by default).
- Failed switch attempts count towards max switches per hour.
- The version is left to release-please (no `min_app_version` on the relays template).
- No deterministic demo proposal; the proposal E2E keeps its marked mock.

## Deviations during implementation

Recorded after PR 3-C; none changes ADR-0011.

- **Model budget.** The Fable credits ran out during Phase 3. By the maintainer's decision the
  controller, safety and review work (including the `control-safety` and `reviewer` passes) was
  done with Opus instead of Fable.
- **PR 3-B was larger than risk R8 allowed.** It contains WP5 to WP9 (about 24 000 added lines
  over 3-A, tests included, against the ~3 000 line threshold) and was not split into 3-B1 and
  3-B2; the maintainer was asked about the size. The review fixes of `control-safety` went in as
  separate `fix(...)` commits.
- **Extra water heater template.** Next to `generic.water_heater_steps` there is
  `generic.water_heater_relays` (1 to 3 relays, the new additive `relays` control kind, schema v1
  changelog 0.4.0). Both are simulator-only and `UNVERIFIED`. A water heater's `max_c` is enforced
  through a tank probe (`core.control.heat_capped`, controller cap and a gate invariant); an unknown
  probe counts as full.
- **Operating mode step.** The wizard step only sets the **overall mode** (`PUT
  /api/v1/operation/mode`); the modes of single devices are changed on the Now view. A stored
  `operation` document may keep or lower modes but never raise them (409
  `api.error.mode_raise_rejected`), and raising a device above shadow needs its identity token.
- **Boost.** `boost` in the API is a timed hold: SunCore leaves the device alone and drives nothing
  (the UI calls it "Hold"). Whether it should drive the device is a question to the maintainer.
- **Documentation.** The guide `docs/user/<lang>/surplus-control.md` replaces the separate pages
  planned for modes, overrides, emergency stop and power models; power models are explained in the
  climate section.

## 7. Out of scope for Phase 3

Battery control and inverter writes (Phase 4, Kostal registers not verified); planner,
forecasts, tariff prices (Phase 4 – the controller uses the default plan); wallbox/EV,
heat pump/SG-Ready, shiftable programs, peak shaving with load shedding, export limit via
inverter, §14a, backup power (Phase 6); MQTT entities and HA notifications (Phase 6);
expert-mode parameter pages and charts (Phase 5).
