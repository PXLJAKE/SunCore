# Phase 6 – HA integration surface and remaining devices

Status: **draft, proceeds on the defaults by delegation** (2026-11-24). The maintainer asked on
2026-11-23 to continue with the remaining phases while he is away; every decision in §0 and every
question in §6 has a default marked "default, maintainer may override", and work proceeds on it.
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §4.4 (discharge into the EV), §4.5 (wallbox/EV,
heat pump, shiftable programs), §4.6 (import limit, export limit, §14a), §4.7 (backup power),
§4.8 (planner constraints: deadlines, thermal models, EV targets), §4.11 (notification with an
action), §4.13 (HA entities, actions, events, notifications), §4.14 (scenarios "Netzausfall",
"Auto kommt spät"), §5, §9; [`docs/roadmap.md`](../roadmap.md) Phase 6; ADR-0005, ADR-0006,
ADR-0011 (and its addendum), ADR-0012, ADR-0013, ADR-0015, and the new ADR-0016, ADR-0017,
ADR-0018 (PR 6-A); [`docs/architecture.md`](../architecture.md) §2, §4, §6;
[`docs/plans/phase-5.md`](phase-5.md) (format and §8 deviations).
Branches: stacked `phase-6/<letter>-<topic>` (§3), based on the tip of the Phase 5 stack.

Phase 6 completes the feature set: SunCore appears in Home Assistant as entities, events and
notifications, and it controls the remaining device families – the wallbox/EV (directly or through
evcc), the heat pump (SG-Ready, set-point lift, thermal model, COP) and shiftable programs – and it
honours the grid connection: peak shaving with a shedding order, the export-limit chain, §14a EnWG
dimming and backup-power (island) awareness. **Every new write to a device passes the safety gate**
(ADR-0011): new action kinds are rejected with `safety.<kind>_not_supported` until their invariants,
scenarios and `control-safety` sign-off exist. Shadow mode stays the default for every new device
and asset. MQTT and notifications never write to devices; HA commands reach SunCore through the same
services layer and gate as the panel.

## 0. Context and decisions

What exists at the tip of Phase 5 (verified in the tree on 2026-11-24):

- `core/devices.py`: `DeviceKind` already has `heat_pump`, `wallbox`, `program`; `ControlSpec`
  variants `on_off`, `steps`, `relays`, `continuous` (W/A/%, `phases` 1|3), `setpoint`, `mode`.
  No template, driver or controller logic exists for the three kinds.
- `core/decisions.py`: action kinds `switch`, `power_setpoint`, `setpoint`, `step`, `mode`,
  `release`, `battery`; `Origin` planner/controller/user/safety.
- `core/control.py`: `GateInputs.import_limit_w`, `PlanInterval.grid_import_limit_w`,
  `DeviceBand(allowed, preference, max_power_w)`, `IntervalFlows.curtail_w` (informational).
- `core/site.py` `GridConfig`: `import_limit_w`, `export_limit_w`, `export_limit_pct_kwp`,
  `para14a`, `para14a_signal` (config only, nothing reads §14a).
- `safety/invariants.py` (`check_grid`: import limit, no SunCore-caused import; comfort; min run
  and pause; switch rate; write rate; change; reserve; climate switch-on; heat limit) and
  `safety/battery.py` (B1–B9).
- `planner/inputs.py` `PlannerDevice`: energy-only, `[0, max_w]`, no binaries, no thermal model.
- `addon/suncore/config.yaml`: `services: [mqtt:want]`, `hassio_api` removed (Phase 1).
- No MQTT client, no notification code, no Python ICU renderer (ADR-0007 follow-up deferred to
  Phase 6, STATUS).
- `api/services.py`: emergency stop, overrides (boost/pause/resume), proposal confirm, modes,
  replan – the services the HA surface must reuse.
- Simulator: `sim/devices.py`, `sim/house.py`, `sim/inverter.py`, 19 scenario files.

Decisions. Each is **default, maintainer may override**; the architectural ones are ADRs in 6-A.

1. **HA surface = MQTT discovery + HA notification actions** (ADR-0016). `aiomqtt` on
   `paho-mqtt` 2.x; broker from the Supervisor services API (`hassio_api: true` re-enabled for
   `GET /services/mqtt` only) or manual settings. Device-based discovery, random install id,
   configs not retained and published on connect and on the HA birth message, states retained,
   availability with last will. **Without a broker the feature is `unavailable` and the panel
   shows a hint how to install the Mosquitto broker app**; nothing else depends on MQTT.
2. **HA commands call the services layer**, never config or devices directly (ADR-0016). Raising a
   mode or clearing the emergency stop from HA needs `mqtt.allow_mode_raise` (**default off**);
   lowering a mode, the emergency stop, pause, replan always work.
3. **Backend texts are rendered by a minimal ICU renderer** (`suncore/i18n/`, stdlib only) from a
   catalogue generated out of the frontend catalogues by prefix, checked against
   `intl-messageformat` by a golden test (ADR-0016 option 4C; closes the ADR-0007 follow-up).
4. **Notifications** (ADR-0016): persistent notification on by default for errors and safety
   events; push targets (`notify.send_message` entities or legacy `notify.<service>`) off until the
   user picks them; quiet hours 22:00–07:00 for non-critical categories; per-key dedupe; at most
   20 push messages a day. Actionable buttons only when the `mobile_app_notification_action`
   subscription works (Supervisor token admin rights `UNVERIFIED`), else plain text with a pointer
   to the panel.
5. **EV direct control** = new action kind `charge` (current, phases; full desired state) and
   control variant `charger` (ADR-0017). Modes `off`, `now`, `min_pv`, `pv` (**default `pv`**),
   `target` (SoC or kWh by a time). One vehicle per charger.
6. **1/3-phase switching** (default, maintainer may override): only when a `phases` role is mapped
   and `phase_switching: true`; 3 → 1 when the smoothed surplus stays below
   `3 × min_current_a × voltage` − hysteresis for 5 min, 1 → 3 when it stays above that value plus
   500 W for 5 min; the charger is paused (`current_a = 0`) for `phase_switch_pause_s` (default
   120 s) around the switch; at most 4 phase switches an hour; never during `now`.
7. **evcc as actor** = new action kind `loadpoint` (mode `off|smart|now`, limit SoC, vehicle
   plan, max current) over the evcc MQTT API (verified 2026-11-24); evcc regulates current and
   phases itself; SunCore never writes `evcc/site/*` (ADR-0017).
8. **Import caused by flexible loads** is a bounded exception (ADR-0017 decision 4): the
   user-demanded floor (EV `now`/`min_pv`, an armed program whose deadline binds) or plan-permitted
   grid power (`DeviceBand.grid_power_max_w`, EV `target` always, heat-pump pre-heating only with
   `grid_preheat`, **default off**). The import, peak and §14a limits hold in every case. The
   home battery is not discharged into the EV unless `battery.allow_discharge_to_ev` (**default
   off**, BRIEF §4.4).
9. **Heat pump** reuses `step` and `setpoint` (ADR-0017): control variant `sg_ready` (two
   contacts, steps = SG states 2/3/4), **state 1 (utility block) is never written**, state 4
   (forced) only with `allow_forced` (**default off**), minimum dwell per state 10 min, baseline
   and release = state 2. Set-point lift through `SetpointControl` within the lift band.
10. **Thermal model** (ADR-0017): first-order RC per thermal device, learned from at least 14
    days of 15-minute history of the temperature probe, electric power and outdoor temperature;
    used by the planner only when the normalised RMSE of a 24 h replay is ≤ 0.2 (default), else
    energy-only as in Phase 4. **COP curve**: from the template (with `source`), else the generic
    air/water assumption `(-7 °C, 2.5) (2 °C, 3.0) (7 °C, 3.6) (15 °C, 4.3)`, labelled as an
    assumption in the UI.
11. **Shiftable programs** = runtime jobs (ADR-0017): armed with a deadline (no automatic
    default deadline; the UI proposes the next 18:00 that leaves the run time, else now + 12 h),
    the planner picks the start, the controller starts once, the gate never interrupts a running
    program, **the plug's baseline is on**. Start methods `plug`, `remote`, `notify`; `plug`
    needs the user's confirmation that the appliance resumes after power returns
    (`UNVERIFIED` per appliance).
12. **Grid limits come from one resolver** `core/grid.py` (ADR-0018). Peak shaving **off by
    default**; when on, limit = max(month's highest 15-minute mean import, `peak_floor_w`); load
    shedding in reverse priority (optional `shed_rank`), continuous loads first.
13. **Export-limit chain** (ADR-0018): devices, then battery, then a new `inverter_limit` action
    (`max_export_w` or `max_ac_pct`) through an inverter adapter; **inverter writes capped at
    assisted until `inverter.limit_verified`**; dynamic export limit 0 W at negative export prices
    only with a verified adapter; zero export **off by default**. The maintainer's 13 500 W
    hardware limit stays in the inverter; SunCore only plans with it.
14. **§14a** (ADR-0018): SunCore **respects** the signal and is documented as not the certified
    control path; while active the flagged devices together stay ≤ `para14a_limit_w`
    (**default 4 200 W**, the user enters the grid operator's value for several devices; the EMS
    formula is `UNVERIFIED`), grid charging of the battery is blocked, everything is logged as
    grid events and notified. In shadow mode SunCore logs "would dim".
15. **Backup power** (ADR-0018): island detection from a mapped entity only (heuristic off);
    island → no load increase, no grid charging, shedding to a keep list, planner paused, critical
    notification; cool-down after the grid returns.
16. **Runtime state** for EV modes and targets and program jobs lives in a new `flex_state` table
    in `suncore.db` (schema 5 → 6), not in the config history (it changes many times a day).
    Grid events and monthly peaks live in `timeseries.db` (`grid_event`, `grid_peak`, schema
    2 → 3). Both migrations get the Phase 5 release note "export before updating".
17. **New device kinds start in shadow, unverified templates are capped at assisted** until the
    user confirms the vendor behaviour (`DeviceConfig.behaviour_confirmed`, part of the identity;
    ADR-0017 consequences). Phase 3 devices are not affected.
18. **Out of Phase 6** (default): per-phase imbalance limits, V2H/V2G, EEBus, the companion
    integration, built-in PV/price fallbacks and calibration-charge planning (both deferred from
    Phase 4/5, moved to after 1.0 unless the maintainer wants them in Phase 7, question 12).

## 1. Goals and acceptance criteria

Phase 6 is done when (roadmap Phase 6 table):

1. **MQTT discovery:** with the Mosquitto app installed, SunCore appears in HA as one site device,
   one device per configured device and the battery, with the entities of WP3 (sensors, binary
   sensors, switches, selects, numbers, buttons, text, one event entity); entities go
   `unavailable` when SunCore stops (last will) and come back after an HA restart (birth
   message); commands from HA act exactly like the panel (same validation, config history author
   `mqtt`, same gate decisions); raising a mode from HA is refused unless enabled. **Without a
   broker** SunCore works fully and Settings → "Home Assistant entities" and the system check say
   why there are no entities and how to enable them.
2. **Notifications:** errors (watchdog trips, failed writes, adapter refusals, planner failing for
   1 h), safety events (emergency stop, island, §14a), assisted proposals (with confirm/reject
   buttons where possible), cheap windows (opt-in), "start the washing machine now", EV target at
   risk – in the user's language, rate-limited, with quiet hours, configurable per category and
   target, each with an MQTT event as well.
3. **Wallbox/EV:** generic charger template plus at least one integration template verified from
   source; modes Off | Now | Min+PV | PV | Target; 6 A floor, hysteresis, 1/3-phase switching
   (decision 6); vehicle SoC from HA or energy-based targets; charging losses; "car arrives late"
   meets its target in the simulator at the planned cheapest windows; **evcc as actor** sets
   mode, limit, plan and max current over MQTT and is released when evcc goes silent.
4. **Heat pump:** SG-Ready (states 2/3, 4 opt-in, 1 never), set-point lift of hot water within the
   lift band, learned RC model and COP curve feeding the planner (pre-heating with surplus, with
   cheap grid only on opt-in), §14a dimming respected.
5. **Shiftable programs:** arm with "finished by" from the panel, HA (button/text) or a
   notification; the planner picks the start (surplus or cheapest); start via plug, remote start or
   a "start now" notification; never interrupted; a crash while waiting starts the program.
6. **Grid:** peak shaving with monthly peak tracking and shedding order; export-limit chain with
   inverter limit (capped at assisted until verified); §14a dimming; island awareness. **Every
   simulator scenario of WP12 passes** in CI (`-m simulator`).
7. **Cross-cutting:** all new action kinds with invariants, property tests, scenarios and a
   `control-safety` sign-off table in their PR; new reason keys en/de; Pi 4 budgets hold (RSS
   < 300 MB with MQTT and notifications running; planner ≤ 10 s with the reference house plus one
   EV, one heat pump and two programs on the CI arm runner, aarch64 Pi `UNVERIFIED` until
   measured); `reviewer` sign-off per PR and a final review.

Exit (maintainer): installs Mosquitto (or keeps it), sees the SunCore entities and one
notification on his phone; runs a simulated EV "arrives late" and a washing-machine job in the demo;
reads the grid section (his static export limit shown, §14a "not active", backup entity mapped or
skipped). Real wallbox and heat pump tests wait for hardware (BRIEF §9: wallbox in 1–2 years).

## 2. Work packages

Owner = subagent that implements it. Budget (CLAUDE.md, BRIEF §10 Phase 6 "Sonnet, review with
Fable"): **Sonnet** for routine backend, HA integration, drivers, frontend and docs; **Haiku** for
catalogue sync and mechanical work; the **stronger model** for contracts and invariants
(`control-safety`), the planner extensions (`optimizer-engineer`) and the final `reviewer` pass. At
most two subagents in parallel; every WP ends with a commit and a push.

### WP1 – Plan and ADRs · `architect`, reviewed by `reviewer`

This plan; ADR-0016 (HA surface: MQTT, events, notifications), ADR-0017 (flexible loads),
ADR-0018 (grid limits, §14a, export, backup); boundary test rules for the new packages:
`suncore/i18n` imports stdlib only; `suncore/ha/mqtt` imports `core` (and the client library);
`suncore/notify` imports `core` and `i18n`; `core/grid.py`, `core/thermal.py`, `core/flex.py`
import nothing from siblings; `HAConnection.notify` and the MQTT publish/command methods are used
only from `ha`, `notify` and `app`; no module outside `safety` calls `apply()`/`release()`.

### WP2 – Contracts and configuration · `control-safety`

Stronger model; plan fields with `optimizer-engineer`. All additive, each with validators and unit
tests, no behaviour yet:

- `core/decisions.py`: `ChargeAction`, `LoadpointAction`, `InverterLimitAction` in the `Action`
  union; `safety/gate.py` rejects them with `safety.charge_not_supported`,
  `safety.loadpoint_not_supported`, `safety.inverter_limit_not_supported` before mode handling
  (ADR-0011 addendum); the "every `ActionKind` handled or rejected" test extended.
- `core/devices.py`: `ChargerControl`, `SgReadyControl`, `LoadpointControl` (evcc loadpoint id,
  vehicle name, topic prefix default `evcc`) in `ControlSpec`; `EvSpec`, `ProgramSpec`,
  `ThermalSpec` (COP curve, optional learned model, lift band) on `DeviceConfig` with kind checks;
  `shed_rank`, `para14a: bool`, `behaviour_confirmed: bool` (identity, decision 17); a step of a
  `sg_ready` device is rejected with `safety.sg_ready_not_supported` until 6-K.
- `core/thermal.py` (`ThermalModel`, `CopCurve`), `core/flex.py` (EV modes, targets, program job
  states, pure transition rules), `core/grid.py` (`GridLimits`, `BackupConfig`, the resolvers of
  ADR-0018 decision 1, `GridState`).
- `core/control.py`: `DeviceBand.grid_power_max_w`, `DeviceBand.start`; `GateInputs`
  `export_limit_w`, `para14a_budget_w`, `grid_state`; plan stays schema v2 (additive, as in
  Phase 4).
- `core/site.py` `GridConfig` additions: `peak_shaving` (enabled, `peak_floor_w`, capacity price
  per kW and month), `export_dynamic` (negative-price limit on/off), `zero_export`,
  `para14a_limit_w`, `para14a_signal_kind` (`binary` | `power_w`), `backup` (`BackupConfig`).
- New config sections `mqtt` (enabled, broker source `supervisor` | `manual`, manual host/port/
  TLS/user/password as a secret, discovery prefix default `homeassistant`, `allow_mode_raise`) and
  `notifications` (categories, targets, quiet hours, daily cap); `inverter` (adapter, capability,
  `limit_verified`). Config schema bump with migrations and v-1 fixtures.
- Template schema v1 additions (ADR-0012 policy, changelog in `templates/README.md`): the three
  control variants, `ev`, `program`, `thermal` defaults, capabilities `ev_charging`,
  `phase_switching`, `sg_ready`, `remote_start`, `export_limit`, `ac_limit`, `backup_state`; new
  template kind `inverter` only if the battery kind cannot carry the inverter limit roles
  (decision in the PR, default: extend `battery` templates with optional inverter roles).
- Storage: `flex_state` (`suncore.db` 5 → 6), `grid_event` and `grid_peak` (`timeseries.db`
  2 → 3) with repositories and retention (events 400 days, peaks for ever).

### WP3 – MQTT base, discovery, read-only entities, backend ICU · `ha-integration`

Sonnet; the renderer by `backend-engineer` (Sonnet).

- `suncore/i18n/`: renderer (arguments, `number`, `plural` with `=n`/`one`/`other`, `select`;
  en/de plural rules, `other` fallback; decimal separator per locale), `catalogue/<lang>.json`
  generated by `scripts/gen_backend_catalogue.py` (prefixes `mqtt.`, `notify.`, `safety.`,
  `controller.`, `planner.`, `device.`, `grid.`) with `--check` in `check.sh` and CI; golden file
  produced by a Vitest helper with `intl-messageformat` for every generated key.
- `suncore/ha/mqtt/`: `client.py` (connect, back-off, last will, QoS, TLS), `broker.py`
  (Supervisor `GET /services/mqtt` → host, port, ssl, username, password, protocol; manual
  settings; status `disabled|no_broker|connecting|connected|error` with a reason key),
  `discovery.py` (device-based payloads, abbreviations of the research note, install id,
  birth-message handling with a random 1–5 s delay, removal), `states.py` (on-change publishing
  with deadbands, 5 s per entity, retained), `catalogue.py` (the entity catalogue below, pure).
- `addon/suncore{,_beta}/config.yaml`: `hassio_api: true` (only for the services lookup, test
  asserts no other Supervisor path is called).
- API `GET /api/v1/ha-entities/status` (state, broker source, entity count, last error key) and
  `POST /api/v1/ha-entities/republish`; system check item "Home Assistant entities".
- `UNVERIFIED` keys verified from `home-assistant/core` `components/mqtt` (`via_device`, `text`
  `pattern`, `translation_key` support, `monetary` state class) before use; whatever stays
  unverified is left out, not guessed.

**Entity catalogue v1** (object ids are stable; names rendered from `mqtt.entity.<id>.name`):

| HA device | Platform | Object ids |
|---|---|---|
| site | sensor | `status` (enum), `mode` (enum), `next_action` (text), `plan_cost_today`, `savings_today`, `savings_month` (monetary), `autarky_today` (%), `pv_forecast_error` (%), `month_peak` (W), `available_surplus` (W), `grid_state` (enum) |
| site | binary_sensor | `healthy`, `shadow_mode`, `emergency_stop`, `para14a_active`, `island` |
| site | switch | `auto` (global automatic; raise needs the opt-in) |
| site | button | `replan`, `emergency_stop`, `emergency_stop_clear` (opt-in) |
| site | select | `battery_strategy` |
| site | number | `battery_reserve` (%), `battery_evening_target` (%) |
| site | event | `events` with types `cheap_window_start`, `cheap_window_end`, `device_switched`, `proposal`, `proposal_expired`, `island_start`, `island_end`, `para14a_start`, `para14a_end`, `program_start_now`, `program_done`, `ev_target_at_risk`, `error` |
| each device | sensor | `<dev>_mode` (enum), `<dev>_power` (W, measurement), `<dev>_last_decision` (enum) |
| each device | switch | `<dev>_auto` (raise needs the opt-in) |
| each device | button, number | `<dev>_boost`, `<dev>_pause`, `<dev>_resume`; `<dev>_boost_minutes` |
| wallbox | select, number, text | `<dev>_ev_mode`; `<dev>_target_soc` (%) or `<dev>_target_energy` (kWh); `<dev>_target_time` (`HH:MM`, `text` if verified) |
| heat pump, climate, water heater | number | `<dev>_comfort_min`, `<dev>_comfort_max` (°C) |
| program | button, text, sensor | `<dev>_arm`, `<dev>_cancel`; `<dev>_finish_by`; `<dev>_job_state` (enum), `<dev>_planned_start` (timestamp) |
| battery | sensor | `battery_action` (enum), `battery_lease` (enum) |

No energy-class sensors mirroring the site meters (no double counting, BRIEF §4.13).

### WP4 – MQTT commands · `ha-integration` (Sonnet), `control-safety` review

`suncore/ha/mqtt/commands.py`: subscribe to `suncore/<iid>/+/set`, parse per platform, rate
limit (1/s per entity, burst 3), dispatch to an injected `CommandPort` implemented in `app` by the
services layer (`api/services.py` methods: modes, emergency stop, overrides, replan, config edits
of the numbers and selects through the same validators and `ParamSpec` ranges, EV mode/target,
program arm/cancel). Config edits carry the author `mqtt`. The mode-raise rule of decision 2 with
`safety.mqtt_mode_raise_disabled` and a persistent notification. Refused or invalid commands
republish the true state. Tests: every command maps to exactly one service call; a raise is
refused by default; a command never calls `apply()` (boundary); flood test (no tick delay).

### WP5 – Notifications · `backend-engineer`

Sonnet, with `ha-integration`; `control-safety` reviews the confirm path.

- `HAConnection.notify(domain, service, data, target)` with the allowlist of ADR-0016;
  `suncore/notify/`: `rules.py` (category → trigger from decisions, watchdog, planner status,
  grid events, flex jobs, cheap windows from the plan's prices: the cheapest contiguous block
  below the 25th percentile of the next 24 h, at least 1 h, announced once), `ratelimit.py`
  (dedupe key, min interval per category, quiet hours, daily cap), `render.py` (ICU renderer),
  `sink.py` (persistent notification, `notify.send_message`, legacy `notify.<service>` with
  `data.actions` for the companion app).
- Actionable: action id `SUNCORE_<verb>_<token>` (random, single-use, expires with the proposal);
  `subscribe_events` `mobile_app_notification_action` attempted once at start; on refusal the
  feature degrades to plain notifications and the settings page says why. Confirm → services
  `confirm_proposal` (gate, `Origin.USER`); "reject" lets the proposal expire.
- Settings model (WP2 `notifications` section), API `GET/PUT /api/v1/notifications/settings`,
  `POST /api/v1/notifications/test`, list of `notify` services/entities from the entity cache and
  `get_services` (`UNVERIFIED` over the Supervisor proxy; manual entry as fallback).
- Defaults per category:

| Category | Default | Sinks | Min interval | Quiet hours apply |
|---|---|---|---|---|
| error (trip, failed write ×3, adapter refuses, planner failed 1 h, MQTT lost 15 min) | on | persistent + push if set | 30 min per key | no |
| safety (emergency stop, island, §14a start/end) | on | persistent + push | per event | no |
| proposal (assisted) | on | push with actions | per proposal | yes |
| program start now / done | on for `notify` start method | push | per job | no for start, yes for done |
| EV target at risk | on | push | 1 per target | yes |
| cheap window | off | push | 1 per window | yes |
| daily summary | off | push | daily 20:00 | – |

### WP6 – Grid import side: peak shaving, shedding, §14a, island · `control-safety` (stronger model)

- `controller/grid.py`: effective limits from `core/grid.py` per tick; shedding plan (reverse
  priority, `shed_rank`, continuous first); battery support (lift SunCore's own discharge lock near
  the limit); §14a dimming of flagged devices; island shedding to the keep list.
- `safety/grid.py` invariants (property-tested, reasons `safety.grid_*`, `safety.para14a_*`,
  `safety.island_*`):

| # | Invariant |
|---|---|
| G1 | every load increase keeps import ≤ the effective import limit (fuse and peak), devices, EV, heat pump, programs and battery alike |
| G2 | reductions are always accepted, except min run of thermal kinds; above the fuse limit min run is waived for non-thermal kinds |
| G3 | while §14a is active the summed power of flagged devices after the action ≤ `para14a_budget_w`; battery grid charging rejected |
| G4 | in `island`: every load increase and grid charging rejected; reductions accepted |
| G5 | unknown limit inputs (stale grid power, unknown §14a signal) → no increase of flagged devices |
| G6 | after island or §14a ends, increases wait for `safe_state_cooldown_s` |

- `app`: `grid_peak` tracking from `measurement_15m`, `grid_event` recording, island detection from
  the backup entity (unavailable = connected with a warning), planner pause in island.
- Scenarios: `peak_shaving_month`, `import_limit_fuse_shed` (extends `grid_limit`),
  `para14a_dimming`, `grid_outage_island` (BRIEF "Netzausfall").

### WP7 – Export-limit chain and inverter limit · `control-safety`

Stronger model; the adapters by `device-adapters` (Sonnet).

- Controller chain of ADR-0018 decision 3 (`controller/export.py`); `InverterLimitAction` path in
  the gate (`safety/inverter.py`, lease and heartbeat like the battery where the register is
  volatile; release = captured baseline).
- Adapters (capability `export_limit` W or `ac_limit` %): generic (number entity W or %), Kostal
  REST `Inverter:ActivePowerLimitation` (semantics `UNVERIFIED`), Kostal Modbus register 533
  (%, volatile, `UNVERIFIED` whether battery discharge is included). All capped at assisted until
  `inverter.limit_verified`; the verification test is documented (WP13).
- Invariants:

| # | Invariant |
|---|---|
| X1 | the written limit is never above the configured static export limit nor the baseline |
| X2 | `ac_limit`: `max_ac_pct × nominal ≥ house load + battery charge + margin (500 W)`; else reject (must not cause import) |
| X3 | a limit below the current export only when devices and battery cannot absorb more (chain order) |
| X4 | write on change, rate limit, mandatory read-back; heartbeat only while the lease holds |
| X5 | stale PV/grid/house inputs, a hung inverter, shadow, emergency stop → release to the baseline |
| X6 | effective mode ≤ assisted until `inverter.limit_verified` |

- Planner: export limit becomes a hard constraint where an adapter enforces it (verified),
  informational `curtail_w` otherwise (with `optimizer-engineer` in WP8).
- Scenarios: `export_limit_zero_export`, `negative_price_curtail`, `inverter_limit_heartbeat_lost`.

### WP8 – Planner extensions for flexible loads and grid · `optimizer-engineer` (stronger model)

`planner/` (core only, `mypy --strict`), each feature behind its input being present:

- `PlannerEv`: energy need = (target − SoC) × capacity / efficiency (or kWh), deadline, availability
  window (connected), max power by phases and current, 6 A floor as semi-continuous with binaries
  in the first 24 h and the LP relaxation beyond; grid power only where allowed by mode; emits
  `DeviceBand.grid_power_max_w` and `max_power_w`; reason keys `planner.ev_*`.
- `PlannerJob`: one start slot per armed program (binary per candidate slot, profile convolution),
  finishing by the deadline; emits `DeviceBand.start` in the start interval and `allowed = False`
  before it; infeasible deadline → start as soon as possible with a reason.
- `PlannerThermal`: RC storage with COP per slot from the outdoor-temperature forecast, comfort
  (lift) band as soft bounds, `grid_preheat` gate.
- Grid: peak variable with the capacity price, effective import limit, export limit hard/soft.
- Heuristic fallback for all four (greedy cheapest/surplus slots, earliest feasible start, band
  keeping); warm start; `scripts/bench_planner.py` extended with the "full house" scenario; budget
  as in §1.7.

### WP9 – Wallbox/EV direct control · `device-adapters`, `control-safety`

`device-adapters` (Sonnet) for templates and the driver, `control-safety` (stronger model) for the
controller and the invariants.

- Templates: `generic.wallbox` (roles `current` number A, `enable` switch, `phases` select/switch
  optional, `charger_power` sensor, `vehicle_soc` sensor %, `connected` binary sensor) and at least
  one integration template verified from `home-assistant/core` source (candidates `wallbox`,
  `keba`; whichever can be verified – the others become `UNVERIFIED` community templates later).
- Driver `devices/drivers/charger.py` (writes current, phases, enable; read-back; baseline).
- Controller `controller/ev.py`: mode state machine, surplus to current (hysteresis, smoothing,
  6 A floor, pause), phase switching per decision 6, target via plan bands, charging losses in the
  energy accounting.
- Invariants `safety/flex.py` (EV part):

| # | Invariant |
|---|---|
| E1 | `current_a` is 0 or within `[min_current_a, max_current_a]` and the live entity range |
| E2 | phase change only when paused for `phase_switch_pause_s`, ≤ `max_phase_switches_per_hour`, never in `now` |
| E3 | demand above free surplus only per ADR-0017 decision 4 (`now`, `min_pv` floor, plan `grid_power_max_w`) |
| E4 | G1/G3/G4 hold (import, peak, §14a, island) |
| E5 | no current increase while `connected` is known false |
| E6 | never discharge the home battery into the EV unless allowed (controller does not count battery discharge as surplus; B4 interplay) |
| E7 | write on change, write rate, read-back within `settle_s`; three failures trip the device |
| E8 | effective mode ≤ assisted while the template is unverified and not confirmed (decision 17) |
| E9 | release restores the captured current, phases and enable |

- Scenarios: `ev_pv_only_flicker` (no SunCore-caused import), `ev_min_pv`, `ev_phase_switching`
  (bounded switch count), `ev_arrives_late` (BRIEF "Auto kommt spät": arrives 23:00, 80 % by
  07:00, cheapest night slots), `ev_para14a`.

### WP10 – evcc as actor · `device-adapters` (Sonnet), `control-safety` review

- `devices/evcc.py`: MQTT driver (topics of ADR-0017, payload `{"value", "time"}` for plans,
  `nil` to clear), read-back from the read topics, watchdog (no evcc message for 120 s), baseline =
  the values read at takeover.
- Template `evcc.loadpoint` (`source`: evcc MQTT API page; `unverified`: `phasesConfigured`
  values, read-back topics, `planStrategy`).
- Invariants L1–L5: values in range; `now` only in EV mode `now` or a planned grid interval;
  `max_current_a` ≤ what G1/G3 allow (§14a, peak); write on change and rate; release on evcc
  silence, shadow, emergency stop.
- Scenario `evcc_actor` with a fake evcc on the in-process test broker.

### WP11 – Heat pump · `optimizer-engineer`, `device-adapters`

`optimizer-engineer` (stronger model) for thermal learning, `device-adapters` (Sonnet) for
templates and drivers; `control-safety` reviews.

- Templates: `generic.heat_pump_sg_ready` (two switches; contact mapping as template data with the
  vendor `source`; generic template ships the common mapping as `UNVERIFIED`),
  `generic.heat_pump_dhw_setpoint` (`water_heater` set-point lift), `generic.heat_pump_select`
  (SG-Ready as a select, `ModeControl`).
- `suncore/forecast/thermal.py`: RC fit (least squares on 15-min data, outlier filter), fit quality
  (decision 10), persisted in `learned_param`; COP from the curve; shown in the device card.
- Controller: SG state 3 on surplus (with dwell and hysteresis), state 4 only if allowed, lift
  within the band on surplus or a planned interval; §14a → state 2, no lift.
- Invariants H1–H5: never state 1; state 4 only with `allow_forced`; dwell ≥ 10 min and the
  device's switch rate; lift within the band and the heat limit; release to state 2 and the
  user's set-point.
- Scenarios: `heat_pump_sg_ready_surplus`, `heat_pump_dhw_lift`, `heat_pump_thermal_plan` (winter
  day, COP by temperature, pre-heat before a price peak with `grid_preheat` on and off).

### WP12 – Shiftable programs · `control-safety`, `device-adapters`

`control-safety` (stronger model) for the job logic and invariants, `device-adapters` (Sonnet) for
the templates.

- Templates `generic.program_plug` (switch + power sensor), `generic.program_notify` (power sensor
  only), `generic.program_remote` (a `button` or `switch` role, `UNVERIFIED` per integration).
- `controller/programs.py`: job state machine (armed → scheduled → running → done), start from the
  `start` band (or at the latest feasible start), detection of a manual start by power.
- Invariants P1–P6:

| # | Invariant |
|---|---|
| P1 | switch-on only for an armed job of that device |
| P2 | never switch off a running program (until run time + 15 min and power < `idle_w` for 5 min) |
| P3 | the plug's takeover baseline is on; release and crash recovery switch it on |
| P4 | arming switches the plug off only while the appliance draws < `idle_w` (nothing running) |
| P5 | G1/G4 hold for the start; a deadline-bound start may import (ADR-0017 decision 4) |
| P6 | effective mode ≤ assisted while the template is unverified and not confirmed |

- Scenarios: `program_deadline_plug`, `program_notify_start`, `program_crash_recovery`.

### WP13 – Frontend · `frontend-engineer` (Sonnet)

Wizard step 3 (peak shaving, export chain, §14a signal and limit, backup entity with live
preview), step 5 (inverter limit adapter and its verification test), step 7 gallery (wallbox,
evcc, heat pump, program with the "behaviour confirmed" checkbox); device cards (EV mode and
target, phase state, vehicle SoC; heat pump SG state, lift, COP and model quality; program arm,
"finished by", planned start, job state); Now view badges (island, §14a, peak); Settings → "Home
Assistant entities" (status, hint without a broker, republish, mode-raise opt-in) and
"Notifications" (categories, targets, quiet hours, test); plan Gantt for EV and programs; history
grid events and month peak. Vitest, Playwright, axe, screenshots.

### WP14 – Docs, translations, STATUS · `docs-writer` (Sonnet), `i18n-sync` (Haiku)

User guides `docs/user/<lang>/home-assistant-entities.md`, `notifications.md`,
`ev-charging.md` (incl. evcc), `heat-pump.md`, `programs.md`, `grid-limits.md` (peak, export
chain, inverter verification test, §14a "not certified", backup); quick start, app `DOCS.md`
(the `hassio_api` note), architecture §2/§4/§6/§7, generated parameter reference and
compatibility list, `templates/README.md` schema changelog, all catalogue keys en/de, STATUS,
roadmap, `UNVERIFIED` table.

### Simulator (in every feature PR) · owner of the PR, reviewed by `qa-engineer`

`sim/devices.py` gains `EvSim` (vehicle SoC, arrival/departure, charger with phases, 6 A floor,
phase-switch delay), `HeatPumpSim` (RC tank/house, COP(outdoor), SG states, set-point), `ProgramSim`
(profile, resumes after power returns or not), `sim/house.py` grid outage and §14a signal, monthly
peak, `sim/inverter.py` export/AC limit with volatility, `sim/fake_evcc.py` on an in-process MQTT
test broker (`amqtt` or a stub broker as a dev dependency, decided in 6-C). Every new template has
a conformance scenario (CLAUDE.md).

## 3. Pull requests and order

Small stacked PRs, one topic each, `phase-6/<letter>-<topic>`, each based on the previous one.
Every PR gets the `reviewer` agent before it is opened (findings as `fix(...)` commits); PRs marked
**CS** additionally need a documented `control-safety` sign-off (invariant table in the PR). Open
questions go into each PR under "Offene Fragen an den Maintainer".

| PR | Content | Depends on | Agent(s) | CS |
|---|---|---|---|---|
| 6-A `docs(adr)` plan and ADRs | WP1: this plan, ADR-0016/0017/0018, boundary rules | Phase 5 tip | `architect` | – |
| 6-B `feat(core,safety,storage)` contracts | WP2: actions rejected as not supported, control variants, specs, grid resolver, config sections, migrations | 6-A | `control-safety`, `optimizer-engineer` (plan fields) | **CS** |
| 6-C `feat(ha,i18n)` MQTT base and backend ICU | WP3: client, broker lookup, discovery, sensors, events, hint, renderer | 6-B | `ha-integration`, `backend-engineer` | – |
| 6-D `feat(ha,api)` MQTT commands | WP4 | 6-C | `ha-integration` | **CS** |
| 6-E `feat(notify)` notifications | WP5 | 6-C | `backend-engineer`, `ha-integration` | **CS** (confirm path) |
| 6-F `feat(controller,safety)` import side, §14a, island | WP6, scenarios | 6-B | `control-safety` | **CS** |
| 6-G `feat(controller,safety,devices)` export chain, inverter limit | WP7, scenarios | 6-F | `control-safety`, `device-adapters` | **CS** |
| 6-H `feat(planner)` flexible loads and grid | WP8, benchmark | 6-B | `optimizer-engineer` | – (planner only writes bands) |
| 6-I `feat(devices,controller,safety)` wallbox/EV | WP9, scenarios | 6-F, 6-H | `device-adapters`, `control-safety` | **CS** |
| 6-J `feat(devices)` evcc as actor | WP10, scenario | 6-I, 6-C | `device-adapters` | **CS** |
| 6-K `feat(devices,forecast,controller)` heat pump | WP11, scenarios | 6-F, 6-H | `optimizer-engineer`, `device-adapters` | **CS** |
| 6-L `feat(devices,controller,safety)` shiftable programs | WP12, scenarios | 6-H, 6-E | `control-safety`, `device-adapters` | **CS** |
| 6-M `feat(frontend)` wizard, cards, settings | WP13 | 6-D, 6-E, 6-G, 6-I…6-L | `frontend-engineer` | – |
| 6-N `docs` guides, catalogues, STATUS, final review | WP14 | all | `docs-writer`, `i18n-sync`, `reviewer` | final CS read of the invariant tables |

```text
6-A ─► 6-B ─┬► 6-C ─┬► 6-D
            │       └► 6-E
            ├► 6-F ─► 6-G
            └► 6-H
6-F + 6-H ─► 6-I ─► 6-J (also needs 6-C)
6-F + 6-H ─► 6-K
6-H + 6-E ─► 6-L
6-D, 6-E, 6-G, 6-I … 6-L ─► 6-M ─► 6-N
```

Branch order A, B, C, D, E, F, G, H, I, J, K, L, M, N; the arrows say what a PR needs. 6-C…6-E
(HA surface) and 6-F…6-H (grid and planner) may be built in parallel (at most two subagents).
After every WP: `./scripts/check.sh`, frontend `npm run lint|test|build` where touched,
`pytest -m simulator` for the new scenarios, commit and push.

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Contracts | pytest, hypothesis | new actions/control variants/specs validate and round-trip; every `ActionKind` handled or rejected with its `not_supported` key; config migrations from v-1 fixtures; template schema `--check` |
| Grid resolver | pytest, hypothesis | effective import limit = min(fuse, peak) and never above the fuse; §14a budget; export limit ≤ static; DST month boundaries for peaks |
| Invariants | hypothesis | G1–G6, X1–X6, E1–E9, L1–L5, H1–H5, P1–P6: random intents and site states never violate them; reductions always pass (G2) |
| Planner | pytest, hypothesis, benchmark | EV meets a feasible target with ≤ planned import; job finishes by its deadline or reports it; thermal band kept when feasible; peak variable ≥ every import; heuristic fallback satisfies the same hard constraints; "full house" ≤ 10 s (CI arm runner), aarch64 Pi `UNVERIFIED` until run |
| MQTT | pytest with an in-process broker | discovery payloads (golden JSON per platform), birth republish, last will, removal, deadbands, commands → services mapping, mode-raise refusal, flood, no broker → `no_broker` status and hint |
| i18n | pytest + Vitest golden | Python renderer equals `intl-messageformat` for every generated key and sample args in en/de; `gen_backend_catalogue.py --check` |
| Notifications | pytest | rules per category, dedupe, quiet hours, daily cap, action token single-use and expiry, degraded mode without the event subscription, allowlist of `notify` calls |
| Simulator | `-m simulator` | every scenario of WP6–WP12 (19 new ones) with assertions on import, limits, switch counts, targets, deadlines, releases |
| Boundary | AST test | package rules of WP1; `notify`/MQTT never call `apply()`/`release()`; `HAConnection.notify` only from `notify`; `hassio_api` used only for `/services/mqtt` |
| API | httpx ASGI | new endpoints, OpenAPI and `schema.d.ts` regenerated |
| Frontend | Vitest, Playwright, axe | wizard steps, device cards, settings pages (desktop, phone, tablet; light/dark) |
| Budgets | smoke test, benchmark | RSS < 300 MB with MQTT, notifications and a full-house plan; controller tick unaffected by a command flood |

No wallbox, heat pump or §14a hardware in the session: the simulator is the acceptance test for the
logic; the maintainer's system is the acceptance test for MQTT, notifications, the grid section and
(later) the wallbox.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | HA commands bypass safety or arm devices | uncontrolled writes | services layer only, gate unchanged, mode-raise opt-in off, boundary test, `control-safety` review of 6-D |
| R2 | Retained/ghost entities, unique-id collisions with the later companion integration | clutter, migration pain | configs not retained, install id, removal on disable, "republish" and "remove all" buttons, ADR-0005 migration path |
| R3 | `hassio_api` widens privileges | security rating, review | used for one call, test, documented |
| R4 | Notification spam | users disable everything | dedupe, quiet hours, daily cap, cheap windows off by default |
| R5 | Actionable notifications not possible (token not admin) | assisted mode less convenient | detected at start, degraded plain mode, panel pointer, question 3 |
| R6 | EV, SG-Ready, plug-resume, evcc behaviour differs per vendor | wrong writes, wear | templates with sources, `UNVERIFIED` lists, assisted cap until confirmed, phase-switch limits, read-back |
| R7 | Import caused by flexible loads beyond the user's intent | cost | bounded exception (ADR-0017), plan-permitted only, limits always, reasons logged |
| R8 | Inverter export limit causes import or sticks after a crash | cost, lost PV | X2, volatile-register heartbeat, release, assisted cap, verification test |
| R9 | §14a misunderstood as certified control | compliance | documented "respects only", operator path kept, shadow says "would dim", question 5 |
| R10 | False island detection | needless shedding | entity only, live preview, unknown = connected |
| R11 | MILP binaries exceed the Pi budget | late plans | 24 h binary window, warm start, heuristic fallback, benchmark gate |
| R12 | Thermal model fits badly | poor pre-heating | quality threshold, energy-only fallback, model quality in the UI |
| R13 | Two migrations block a downgrade | no rollback | release note "export before updating", additive tables |
| R14 | Fourteen stacked PRs drift; Phase 3–5 acceptance findings change contracts | rebase work | merge in order, contracts first (6-B), small PRs |
| R15 | evcc and SunCore fight over the home battery | oscillation | SunCore never writes `evcc/site/*`; docs tell the user to neutralise evcc's battery rules; question 7 |

## 6. Questions for the maintainer (Fragen an den Maintainer)

Der Maintainer ist abwesend; **bis zur Antwort gilt jeweils der empfohlene Standard**.

1. **MQTT-Broker:** SunCore-Entities in HA brauchen den Mosquitto-Broker. **Empfehlung:** Broker
   automatisch über den Supervisor finden (dafür wird `hassio_api` wieder aktiviert, nur für
   diesen einen Aufruf); ohne Broker läuft alles weiter, die Oberfläche zeigt einen Hinweis.
   Ist Mosquitto bei dir installiert? *(Stand 6-C: `hassio_api` ist dafür gar nicht nötig und
   bleibt aus, siehe Abschnitt 8.)*
2. **Modus aus HA anheben:** Soll ein Schalter in HA ein Gerät auf „Automatisch“ stellen dürfen?
   **Empfehlung:** nein (Standard aus, in den Einstellungen einschaltbar); Absenken, Not-Aus und
   Pause gehen immer.
3. **Benachrichtigungen:** Über welche Ziele (z. B. `notify.mobile_app_<handy>`) und welche
   Kategorien? **Empfehlung:** Fehler und Sicherheitsereignisse als persistente Meldung an,
   Push erst nach Auswahl eines Ziels; günstige Fenster aus; Ruhezeit 22–7 Uhr; höchstens 20
   Push-Meldungen pro Tag. Aktions-Buttons (Bestätigen/Ablehnen) nur, wenn SunCore das
   Ereignis `mobile_app_notification_action` abonnieren darf – ob das über den Supervisor geht,
   ist ungeprüft.
4. **E-Auto:** **Empfehlung:** Standardmodus „Nur PV“, Mindeststrom 6 A, Phasenumschaltung nur
   wenn die Wallbox sie anbietet (höchstens 4 Wechsel pro Stunde, 2 Minuten Pause), Zielladung
   darf in den geplanten günstigsten Stunden aus dem Netz laden, der Hausakku wird nicht ins
   Auto entladen. Welche Wallbox planst du (Hersteller/Integration, evcc ja/nein)?
5. **§14a EnWG:** **Empfehlung:** SunCore *respektiert* das Steuersignal (begrenzt die markierten
   Geräte auf 4,2 kW bzw. den Wert des Netzbetreibers, protokolliert, benachrichtigt), ist aber
   ausdrücklich **nicht** der zertifizierte Steuerweg; im Schattenmodus wird nur „hätte gedimmt“
   protokolliert. Du hast keine Steuerbox – passt es, §14a bei dir auf „nicht aktiv“ zu lassen?
6. **Einspeisebegrenzung:** Deine 13,5 kW sind im Wechselrichter eingestellt. **Empfehlung:**
   SunCore plant damit, schreibt aber selbst keine Wechselrichter-Begrenzung, solange der
   Adapter nicht mit einem dokumentierten Test geprüft ist (bis dahin höchstens „Assistiert“);
   Null-Einspeisung aus; Abregelung bei negativen Preisen nur mit geprüftem Adapter.
7. **evcc:** **Empfehlung:** SunCore setzt nur Ladepunkt-Modus, Ziel, Limit und Maximalstrom,
   nie die evcc-Batterie-Einstellungen; evcc-Batterieregeln sollen neutral sein. Einverstanden?
8. **Wärmepumpe:** **Empfehlung:** SG-Ready nur Zustand 2/3, Zustand 4 (Zwangsein) nur nach
   Freigabe, Zustand 1 (Sperre) nie; Vorheizen nur mit PV-Überschuss, mit günstigem Netzstrom erst
   nach Freigabe (`grid_preheat`). Hast du eine Wärmepumpe oder ist das nur für andere Nutzer?
9. **Verschiebbare Programme:** **Empfehlung:** Start per Steckdose nur, wenn du bestätigst, dass
   das Gerät nach Stromrückkehr weiterläuft; ein laufendes Programm wird nie unterbrochen; nach
   einem Absturz wird die Steckdose eingeschaltet (das Programm startet lieber zu früh als nie).
10. **Notstrom:** Welche Entity zeigt bei dir den Inselbetrieb des Kostal BackUp Switch G3?
    **Empfehlung:** Erkennung nur über eine von dir zugeordnete Entity; ohne Zuordnung bleibt die
    Funktion aus.
11. **Spitzenlast (Leistungspreis):** **Empfehlung:** aus (in Deutschland meist nicht relevant);
    die Hausanschluss-/Sicherungsgrenze bleibt immer aktiv.
12. **Aufgeschobene Punkte** (eingebaute PV-/Preis-Fallbacks, Kalibrierladung im Planer,
    Schieflastgrenzen je Phase): **Empfehlung:** nicht in Phase 6; nach 1.0, außer du willst sie
    vorher.
13. **Datenbank-Migrationen** (`suncore.db` 5 → 6, `timeseries.db` 2 → 3): **Empfehlung:** wie in
    Phase 5 mit Hinweis „vor dem Update exportieren“.
14. **Modellwahl:** **Empfehlung:** Sonnet für Routine, Haiku für Übersetzungen, das stärkere
    Modell für Verträge/Invarianten (6-B, 6-F, 6-G, 6-I, 6-L), Planer (6-H, 6-K) und die
    Abschlussprüfung.
15. **Anzeigetexte der HA-Entities (neu, 6-C):** Aufzählungs-Sensoren (Modus, Status, letzte
    Entscheidung) zeigen den *übersetzten* Text („Automatisch“) als Zustand, so steht es in
    ADR-0016. Das heißt: Automationen, die auf den Zustand prüfen, hängen an der Sprache.
    **Empfehlung:** so lassen; alternativ feste englische Zustände plus übersetzte Namen. Welche
    Variante willst du?
16. **Sprache der Entity-Namen (neu, 6-C):** Bei „Sprache: automatisch“ nimmt SunCore die
    Sprache von Home Assistant (Schlüssel `language` aus `get_config`, ungeprüft), sonst
    Englisch. **Empfehlung:** so lassen; die Region-Einstellung hat Vorrang.

## 7. Out of scope for Phase 6

The companion integration with native actions, Repairs and Diagnostics (Phase 7, ADR-0005);
Lovelace cards and the docs site (Phase 7); V2H/V2G; several vehicles per charger; OCPP or Modbus
chargers outside HA integrations; EEBus/LPC and the certified §14a path; per-phase imbalance limits;
multi-inverter plants and MDC; battery set-points (§3.4); built-in PV/price fallbacks and
calibration-charge planning (question 12); push channels outside Home Assistant; heuristic outage
detection; CO₂ weighting.

## 8. Deviations during implementation

### 6-B (contracts and configuration)

- **No config schema bump** for `site`/`devices`: the new fields are additive with defaults, a
  bump would make the shipped frontend's PUT fail, and a downgrade is blocked by `suncore.db`
  schema 6 anyway.
- **Behaviour cap is conservative:** every device with a Phase 6 control (`charger`, `sg_ready`,
  `loadpoint`) or a program spec runs at most assisted until `behaviour_confirmed`, whatever
  its template's verification, until 6-I/6-J/6-K/6-L read the template's unverified list.
- **Wider gate rejection:** besides the three new action kinds, every action but a release for
  a device with a Phase 6 control or a program spec is rejected
  (`safety.sg_ready_not_supported`, `safety.program_not_supported`, ...). These devices also
  get a placeholder driver that never writes (second line of defence).
- **No `inverter` template kind** and no battery-template inverter roles yet: only the
  capabilities `export_limit`, `ac_limit`, `backup_state` (rejected on device templates); roles
  follow in 6-G. Changing the inverter adapter does not reset `limit_verified` yet (6-G); an
  import does.
- **Notify-only programs** still need a control spec; done in 6-L (control kind `notify`).
- **§14a numeric signal:** 0 = not dimmed, a value = the allowed power in W (`UNVERIFIED`).
- **MQTT password:** stored in clear text in `suncore.db`, masked in API, export and history; an
  import of a masked value onto a new system drops the secret (enter it again).
- **Peak-floor validator** (`peak_floor_w` below the fuse) applies only while peak shaving is
  enabled, so stored sites stay valid.
- **Frontend:** the site form keeps the grid fields it does not edit; the device form keeps
  `behaviour_confirmed`/`para14a`.
- **Review conditions (control-safety, PR #52):**
  - A1: the placeholder driver's release raises when there is a baseline, so the gate reports
    `failed` and keeps the runtime instead of marking a device released that may still be in
    the state SunCore wrote; without a baseline it stays a no-op.
  - A2: `build_levels` returns no levels for program devices and the Phase 6 controls until
    6-I..6-L, so the controller does not repeat intents the gate rejects.
  - H1: `GridState.NOT_CONFIGURED` (no backup entity) is distinct from `unknown` (entity
    configured but unavailable). 6-F: only `island` applies island rules; `unknown` warns and is
    treated as connected; `not_configured` is connected without a warning. `GateInputs`
    defaults to `not_configured`.
  - H3: a `PUT` whose MQTT password equals the mask while none is stored is rejected with 422
    (`api.validation.mqtt_password_is_mask`) instead of dropping the password.

### 6-C (MQTT base and backend ICU)

- **`hassio_api` stays off** (plan WP3 and decision 1 said "re-enable"): the Supervisor lets apps
  call `/services*` without it (`security.py` `api_bypass`, verified 2026-11-24), the `mqtt:want`
  services role is what grants the lookup. A boundary test asserts that `GET /services/mqtt` is
  the only Supervisor REST URL in the code and that no app folder sets `hassio_api`.
- **Stub broker, not `amqtt`**: `amqtt`'s latest release is a pre-release that does not resolve with
  the project's dependencies; `tests/ha/mqtt/stub_broker.py` is a ~200-line MQTT 3.1.1 broker
  (credentials, will, retained, wildcards) and the tests run the real `aiomqtt` against it.
  6-J can reuse it for the evcc fake.
- **Read-only subset announced**: only the entities whose values SunCore has today are
  announced (`app/ha_entities.py` `SUPPORTED`): `status`, `mode`, `available_surplus`, `healthy`,
  `shadow_mode`, `emergency_stop`, `events`, and per device `<dev>_mode`, `<dev>_power`,
  `<dev>_last_decision`. Not announced yet (catalogue entries exist, values follow): `next_action`,
  `plan_cost_today`, `savings_today`, `savings_month`, `autarky_today`, `pv_forecast_error`
  (need a summary reader, to be added with 6-D or 6-N), `month_peak`, `grid_state`,
  `para14a_active`, `island` (6-F), `battery_action`, `battery_lease` (battery state mapping),
  EV, program and comfort entities (6-I, 6-K, 6-L). All command entities (switch, button, select,
  number, text) are in the catalogue but never announced before 6-D.
- **Object id** of the emergency stop button is `emergency_stop_button` (the binary sensor keeps
  `emergency_stop`): object ids are unique per site because they form the topics.
- **Enum sensors publish the rendered label** (state and `options` in the backend language), as
  ADR-0016 decision 6 says; a language change republishes configs and states. Automations on
  these states therefore depend on the language; see question 15.
- **`available_surplus`** is the controller's smoothed `available_w` (before the reserve).
  `healthy` = Home Assistant connected and the control loop active.
- **Events** produced now: `device_switched` (accepted decision), `proposal` (proposed),
  `error` (failed), derived from each device's newest gate decision; the other types of the
  `events` entity belong to later PRs. Events are not retained (HA ignores retained replays).
- **Install record** (`iid`, announced device ids) lives in `learned_param` (namespace
  `ha_entities`), not in a new table: no schema bump.
- **Discovery** uses one device payload per HA device with `via_device` pointing at the site
  device; no `cu` (configuration_url) and no `child_devices`.
- **Backend catalogue prefixes** as planned (`mqtt.`, `notify.`, `safety.`, `controller.`,
  `planner.`, `device.`, `grid.`); the golden file covers up to 8 sample argument sets per key.
  Languages other than `en` and `de` have plural rule `other` only.
- Image smoke test not run locally (no Docker daemon in the session); the new dependencies
  `aiomqtt` 2.5.1 and `paho-mqtt` 2.2.0 are pure-Python wheels with hashes in `uv.lock`, so the
  image's `--only-binary --require-hashes` install is unaffected. CI's smoke job covers it.
- `UNVERIFIED`: `get_config` language key, custom HA birth topic, monetary unit rule, TLS to the
  Mosquitto app with verification (see `docs/research/mqtt-discovery.md`).

### 6-D (MQTT commands)

- **Scope: the commands that have a service today.** Announced: `auto`, `replan`,
  `emergency_stop_button`, `emergency_stop_clear`, and per device `<dev>_auto`, `<dev>_boost`,
  `<dev>_pause`, `<dev>_resume`, `<dev>_boost_minutes`. Not announced (catalogue entries stay):
  `battery_strategy`, `battery_reserve`, `battery_evening_target` (a config edit of the `battery`
  and `goals` sections through `put_section`; it rebuilds the control stack and needs the author
  `mqtt` in the config history, which the history table does not have, so it follows with a
  schema-free solution decided by the maintainer, see question 17), EV, comfort and program
  entities (6-I, 6-K, 6-L, no service yet). `select` and `text` parsing is therefore not written.
- **`auto` switch semantics:** state = the *stored* mode is `automatic` (global, or the device's own
  stored mode; the effective mode is `min(global, device)` and is shown by the mode sensor).
  `ON` raises to `automatic`, `OFF` lowers to `shadow` (not to `assisted`: the least autonomous
  state is the safe reading of "off"). A device raise sends the device's *current* identity token
  (`DeviceControlState.identity`), which is what the opt-in means; a re-mapped device is not
  protected by the token here, only by the opt-in.
- **"Back to normal"** is read as: `resume` (gives the device back to SunCore), `OFF` (shadow) and
  the emergency stop; they always work. Clearing the emergency stop is a raise (ADR-0016
  decision 5, plan decision 2) and needs the opt-in; its button is announced only then.
- **Rate limits:** per entity 1 token/s, burst 3; over all entities 20 commands per 10 s; the
  emergency stop is exempt from both. Commands are handled in the MQTT task, never in the control
  tick; every service call is bounded by those limits.
- **No persistent notification yet** (6-E owns notifications): a refusal is logged with its
  `Reason` (`safety.mqtt_mode_raise_disabled`, `mqtt.command.*`) and the true state is
  republished; the notification hook follows in 6-E.
- **No config author `mqtt`** (no config command exists yet); mode changes are stored through the
  same `operation` path as the panel's.
- **No API change** (the plan title says `feat(ha,api)`): the status endpoint is unchanged; the
  decision log is the structured log (`mqtt.command`, with entity, accepted, reason, args).
- **`MqttClient.on_message`** now receives the broker's retain flag as third argument.
- **Boost duration** (`<dev>_boost_minutes`) is held in memory (default 60, 1..1440) and sent
  with the boost press; it is not stored across restarts.
- **`UNVERIFIED`:** the default payloads and number keys listed in the research note (HA source was
  not reachable); that HA sends commands with retain off.

**Review conditions (control-safety, PR #56):**

- A1: the opt-in is withdrawn at once: `reconfigure()` calls `CommandHandler.revoke_raise()`, so the
  old session cannot raise while it shuts down.
- A2: commands no longer run in the client's read loop. The emergency stop gets its own task;
  everything else goes through one worker queue (64 deep) and each service call has a 5 s timeout
  (`mqtt.command.failed`, error `timeout`). The emergency stop has no timeout.
- N1: `pl_prs`, `pl_on`, `pl_off`, `stat_on`, `stat_off` are announced explicitly (abbreviations
  `UNVERIFIED`, same source gap as above).
- N2: an emergency-stop press while the stop is set and no device waits for its release is
  acknowledged as `unchanged` without a service call.
- N3: numbers must match `-?[0-9]+(\.[0-9]+)?` before `float()` (no `1e3`, `0x10`, `+5`, `inf`).
- H1 (residual risk): a device raise sends the device's *current* identity token, so the token's
  protection against a re-mapped device does not apply to HA commands; only the opt-in does.
- H2 (open question 18): `resume` works without the opt-in and hands a device that was paused in
  the panel back to SunCore. It stays within the device's mode (a shadow device is still not
  written), but should HA be allowed to undo a panel pause? **Empfehlung:** ja, ohne Opt-in.

**Safety self-review (6-D), for the `control-safety` review**

| # | Requirement | Where | Evidence |
|---|---|---|---|
| S1 | A command reaches the system only through the services layer, never a device | `commands.py` talks to `CommandPort`; `RuntimeCommandPort` calls `Runtime` methods | `test_commands_reach_the_system_only_through_the_command_port`, `test_the_app_command_port_calls_only_service_methods`, boundary list extended (`submit`, `hold`, ...) |
| S2 | No `apply()`, no direct gate access | same | `test_mqtt_package_never_calls_a_write_path`; port test bans `gate`, `stack`, `apply`, `put_section` |
| S3 | Raising autonomy refused by default | `_mode`, `emergency_stop_clear` | `test_raising_a_mode_is_refused_by_default`, `test_clearing_the_emergency_stop_is_refused_by_default`, runtime e2e |
| S4 | Emergency stop and lowering always work, even flooded | exempt from limits, no opt-in check | `test_emergency_stop_is_never_rate_limited`, `test_emergency_stop_and_resume_work_with_the_opt_in_off`, `test_lowering_always_works_and_goes_to_shadow` |
| S5 | Payloads validated | `_parse`: exact `ON`/`OFF`/`PRESS`, finite numbers, range, integer, 64 bytes, UTF-8 | parametrised invalid/oversize/binary tests |
| S6 | Rate limited, no tick delay | token bucket plus global window, handled in the MQTT task | flood tests (unit, broker, 200 messages and the state sync keeps running) |
| S7 | Retained commands ignored | `retain` flag, removal from the broker | `test_a_retained_command_is_ignored_and_removed` |
| S8 | Unknown or unannounced entities ignored | target map built from the announced entities only | `test_unknown_object_ids_are_refused`, `test_garbage_is_ignored` |
| S9 | A decision/Reason per command | `mqtt.command` log with `Reason(key, args)`; keys in en/de catalogue | all result tests; catalogue check |
| S10 | The true state is republished after a command | `StatePublisher.forget` + sync | `test_raise_is_refused_and_the_true_state_is_republished` |
| S11 | A failing service never kills the session | `except Exception` in `_handle` | `test_a_failing_service_call_is_a_refusal_not_an_exception` |

Residual risks: anyone with broker credentials can still stop SunCore, pause devices and lower
modes (by design; stopping is always safe); with the opt-in on, a broker client can arm devices
(R1, documented, default off); the opt-in is checked against the config loaded at session start
(a change reconnects the session).

**New question for the maintainer (17):** Batterie-Strategie, -Reserve und Abend-Ziel als
HA-Entities brauchen eine Konfigurationsänderung durch HA. Soll ich dafür den Verlauf der
Konfiguration um einen „Urheber“ (`mqtt`) erweitern (eine kleine Migration) oder reichen die
Betriebsmodi und der Not-Aus aus HA vorerst? **Empfehlung:** vorerst nur Modi und Not-Aus; die
Batterie-Einstellungen bleiben im Panel.

### 6-E (notifications)

- **Scope.** Built: `HAConnection.notify` (allowlist, own limiter), `subscribe_notification_actions`,
  `suncore/notify/` (rules, rate limits, tokens, sink, render, service), the app composition
  (`app/notifications.py`), the API (`GET/PUT /api/v1/notifications/settings`, `GET .../status`,
  `POST .../test`), the persistent notification for a refused mode raise from HA (6-D hook
  `HaEntityService(on_refusal=...)`), 46 new catalogue keys per language and the regenerated backend
  catalogue and golden file. Demo mode runs no notification service.
- **`notify` imports the types of `ha.connection`** (errors, `NotificationAction`) besides `core`
  and `i18n`: the plan said `core` and `i18n` only; the boundary test bans every other `suncore`
  package and `ha.websocket`/`ha.mqtt`.
- **Entity targets get plain text.** `notify.send_message` has no documented button field, so
  buttons go only to legacy `notify.<service>` targets (companion app); entity targets receive the
  text with a pointer to the panel. The status reports `notify.reason.actions_entity_only`.
- **Targets are entered by name.** The list of legacy `notify.*` services (`get_services`) is not
  read (`UNVERIFIED` over the proxy, plan WP5); the status lists the `notify` entities from the
  entity cache, services are typed in. Service names that could switch something
  (`send_message`, `persistent_notification`, anything with a dot or capital) are rejected by the
  config model; the connection's allowlist only sends configured names.
- **Event subscription tried when HA is first connected**, not at process start (nothing can be
  subscribed before). A refusal (`CommandError`) is final until a restart (`actions: refused`);
  an offline error is retried at the next connect. After a reconnect the connection renews the
  subscription itself; a failure there is only logged, the service keeps `active` (a tap then
  simply never arrives, which is safe).
- **Quiet hours drop, they do not queue.** A proposal or cheap-window message in the quiet hours
  is not sent later (a proposal expires within minutes anyway). Persistent notifications ignore
  quiet hours and the cap.
- **Sources not yet there.** Island and section 14a (6-F) enter through `Observation.island` and
  `.para14a` (both `None` today: no event); program start/done and EV target at risk (6-L, 6-I)
  through `NotificationService.post`; "trip" is read from a watchdog pause
  (`safety.watchdog_*`), "adapter refuses" has no source yet. The daily summary only carries the
  mode and the number of switchings seen today (no cost or savings reader yet, see 6-C).
- **Cheap window** = cheapest contiguous block of at least 1 h with prices strictly below the
  25th percentile of the next 24 h, from the tariff report (refreshed every 5 min), announced once
  per window start.
- **Replies.** A tap is answered with a short message (confirmed, rejected, no longer valid, not
  switched). A tap on an expired, used or forged token is logged and not answered (no amplification).
- **Tests.** pytest, the sim fake, the WebSocket fake server (`FakeHA`) and a stub connection; no
  network. The stub MQTT broker is not needed.
- `UNVERIFIED`: see `docs/research/ha-websocket-api.md`, section "Notifications and notification
  actions" (the `home-assistant/core` source was not reachable).

**Safety self-review (6-E), for the `control-safety` review of the confirm path**

| # | Requirement | Where | Evidence |
|---|---|---|---|
| S1 | A confirm from a notification takes the panel's service path | `NotificationService._handle` -> `ProposalPort.confirm_proposal` -> `Runtime.confirm_proposal` -> `gate.confirm` (`Origin.USER`) | `test_a_tap_confirms_through_the_same_gate_path_as_the_panel`, boundary tests (`confirm_proposal` only at `self._proposals`, port has two methods) |
| S2 | Proposal still current: id, expiry, mode | token bound to the intent id and `expires_at`; `open_proposals()` must list the id, `now < expires_at`, device `assisted`; the gate re-checks expiry | `test_confirm_after_expiry...`, `test_confirm_needs_the_proposal_to_be_current_and_assisted`, `test_the_port_expiry_is_checked_by_the_service_too`, runtime tests (expired, panel first, mode lowered) |
| S3 | Idempotent | the token is consumed synchronously by the first tap (its sibling dies with it); the gate pops the proposal once | `test_confirm_goes_through_the_services_layer_once_and_is_idempotent`, `test_a_second_tap_does_not_confirm_again`, `tests/notify/test_actions.py` |
| S4 | Never raises a mode or clears the emergency stop | the port has no such method; `RuntimeProposalPort` calls `proposals`/`confirm_proposal` only; during an emergency stop the gate rejects | `test_a_tap_never_raises_the_mode_or_clears_the_emergency_stop`, `test_the_app_port_calls_only_the_two_service_methods`, `test_notify_package_never_calls_a_write_path` |
| S5 | Rejected when action events cannot be authenticated | taps are honoured only in `ActionsState.ACTIVE` (subscription succeeded) and only for a 128-bit token SunCore issued (the event carries no sender proof, `UNVERIFIED`); a refused subscription issues no buttons | `test_a_refused_subscription_means_plain_notifications_and_no_taps`, `test_before_the_subscription_succeeds_no_tap_is_honoured`, `test_forged_and_foreign_actions_do_nothing` |
| S6 | Token hygiene | random (`secrets`), memory only (a restart voids all), SHA-256 keyed, never logged, verb must match, 32 open proposals at most | `tests/notify/test_actions.py` |
| S7 | Reject does not act | no service call; the proposal lapses | `test_reject_calls_nothing_and_kills_the_confirm_button` |
| S8 | No device write from the notification layer | AST bans `apply`, `release`, `call_service`, gate and stack names; imports of `safety`, `controller`, `devices`, `api`, `app` forbidden; `HAConnection.notify` allowlist | `tests/notify/test_boundary.py`, `tests/ha/test_websocket_notify.py` (switch.turn_on etc. refused before the wire) |
| S9 | A flood cannot hurt | forged ids cost one hash; at most 8 tasks in flight; notification rate 1/s on its own limiter; push cap per day | `test_a_tap_flood_is_bounded` |
| S10 | Shadow stays default | the service reads modes, never writes them; no default target, push off until a target is chosen | defaults of `NotificationsConfig`, API default test |

Residual risks: anyone who can fire events on the HA bus *and* reads the notification's token (the
phone, the HA event log while the event is public) can confirm that one proposal within its
lifetime; the effect is limited to what the panel's confirm does (one assisted proposal, all
invariants apply). The token travels in the notification payload through the HA notify service and
the push provider (as every actionable notification does). A refusal of the subscription after a
reconnect is not detected (the service stays `active`, taps just never arrive).

**Review conditions (control-safety, PR #57):**

- 1 (required): `SAFETY` events bypass the daily push cap (a proposal flood cannot starve an
  emergency-stop push; quiet hours never applied to safety).
- 2: `gate.confirm` rejects when the device's *current* effective mode is not `assisted`
  (`safety.proposal_mode_changed`, panel and tap alike); test assisted to automatic to confirm.
- 3: persistent `SAFETY` notices are created even when the safety category (push) is off.
- 4: the in-flight limit is checked before a token is spent.
- 5: a failed renewal of the event subscription after a reconnect calls `on_lost`; the service
  drops its buttons (`pending`) and subscribes again at the next connect.
- 6: braces in device names are replaced before rendering (templates in legacy services,
  `UNVERIFIED`).

**New questions for the maintainer (19-21):**

- **Frage 19: Push-Ziele (6-E):** Der Name des Ziels (z. B. `mobile_app_<handy>`) wird in den
    Einstellungen eingetragen; die Liste der `notify`-Dienste liest SunCore nicht. Reicht das,
    oder soll die Liste über Home Assistant geholt werden (ungeprüft über den Supervisor)?
    **Empfehlung:** vorerst eintragen; `notify`-Entities werden als Auswahl angeboten.
- **Frage 20: Aktions-Buttons (6-E):** Buttons gibt es nur für Companion-App-Dienste
    (`notify.mobile_app_...`), nicht für `notify`-Entities. Ist das für dich in Ordnung?
    **Empfehlung:** ja; ohne Button steht der Hinweis auf das Panel in der Meldung.
- **Frage 21: Ruhezeit (6-E):** Vorschläge in der Ruhezeit (22-7 Uhr) werden verworfen statt am Morgen
    nachgereicht (sie verfallen nach Minuten). **Empfehlung:** so lassen.

### 6-F (import side, §14a, island)

- **Scope:** WP6 and its four scenarios; no export chain or inverter writes (6-G). No new write
  path: shedding, dimming and island shedding are ordinary controller intents through the gate;
  the battery is released through the existing release path.
- **G2 also waives the switch limit** above the fuse limit (not only min run): a switch-off
  that the hourly switch count blocks would keep the connection overloaded. Thermal kinds keep
  both; min pause (a switch-on) is never waived. With only `import_limit_w` configured (no peak
  shaving) that limit **is** the fuse limit, so the Phase 3 import-limit back-off now reports
  `controller.grid.fuse_shed` and waives min run for non-thermal kinds (two tests updated).
- **Unknown after island** keeps "no increase" (cool-down not started) until a definite
  connected state is seen; it never applies island shedding (unknown = connected, H1). Unknown
  without a preceding island is connected with the warning `controller.grid.backup_unknown`.
- **§14a enabled without a mapped signal** counts as inactive with the warning
  `controller.grid.para14a_no_signal` (SunCore cannot observe it; the operator's own relay path
  acts on the device). A mapped but unavailable signal is `unknown`: flagged devices frozen
  (G5), battery grid charging blocked.
- **§14a numeric signal (`power_w`), conservative treatment:** 0 W = not dimmed, as in 6-B.
  Argument (not a proof): the Bundesnetzagentur guarantees at least 4.2 kW per controllable
  device, so 0 W can never be a legal allowed power; a positive value is used as
  `min(value, para14a_limit_w)`; unparsable or negative values are `unknown`. Besides, every
  SunCore increase still needs real export surplus (no grid draw) until the ADR-0017 grid
  exception arrives in 6-I/6-K, where G3 applies. Stays `UNVERIFIED` (STATUS).
- **§14a sum in the gate** uses the upper bound of the other flagged devices' estimates plus
  every increase already let through against the same snapshot (conservative); an unknown
  estimate of one of them rejects every increase of a flagged device while dimmed.
- **Controller §14a band:** a flagged device keeps its level while its power fits the remaining
  budget and rises only with `hysteresis_w` to spare (no tolerance above the legal budget);
  the budget is shared in allocation priority order (lower priority dimmed first).
- **Peak tracking** is computed from the tick values the recorder aggregates into
  `measurement_15m`, not by reading the table back in the tick; a quarter needs half its time
  measured; gaps count as no import; quarters are aligned to UTC epoch (equal to local quarters
  for whole-hour offsets). The peak limit reacts to the instantaneous import (conservative).
- **Planner pause in island** is implemented in the control stack (the controller uses
  `Plan.default()` while the grid state is `island`); the planning service keeps computing
  plans that are not used. Pausing the planning loop itself is left to 6-H.
- **Cool-down memory** lives in the gate (and a mirror in the controller), survives a
  configuration change (`predecessor`) and, since review H1, a restart: the control stack
  rebuilds it from the `grid_event` rows of the last two days at start and merges it
  conservatively (a seed never shortens a running hold; an `island_start` without
  `island_end` holds increases until a connected snapshot plus the cool-down).
- **Notifications and MQTT events** for island/§14a are recorded as `grid_event` rows only;
  sending them is 6-C/6-E.

#### Safety self-review 6-F (control-safety)

| Invariant | Result | Evidence |
|---|---|---|
| G1 import <= effective limit (fuse and peak), devices and battery | pass | `GateInputs.import_limit_w` = `core.grid.effective_import_limit_w` from `controller/grid.py`; `check_grid` and B5 unchanged; `test_effective_limit_never_above_the_fuse`; scenarios `import_limit_fuse_shed`, `peak_shaving_month` (no rise while importing above the limit) |
| G2 reductions accepted; min run kept for thermal kinds; waived above the fuse for non-thermal | pass | `fuse_shed_waiver`; `test_min_run_is_waived_only_above_the_fuse_for_non_thermal_kinds`, `test_the_waiver_needs_an_ok_reading_and_a_fuse_limit`, `test_a_release_and_a_switch_off_pass_every_grid_situation`; scenario: pump shed after < 30 min only above the fuse, not under peak shaving |
| G3 §14a sum <= budget; no battery grid charging | pass | `check_para14a`, `check_battery_grid`; `test_para14a_sum_never_exceeds_the_budget`, `test_gate_para14a_budget_counts_the_other_flagged_devices`, `test_para14a_caps_keep_the_sum_within_the_budget`; scenario `para14a_dimming` (sum <= 2500 W throughout the window, under flicker) |
| G4 island: no increase, no import-causing battery state | pass | `check_island`, `check_battery_grid`, lease ends via `STATE_CHECKS`; `test_gate_island_then_cooldown_then_increase`, `test_battery_grid_rules`; scenario `grid_outage_island` |
| G5 unknown inputs: no increase | pass | `check_grid_inputs` (stale snapshot, grid reading not OK), `check_para14a` (unknown signal/estimate); `test_gate_stale_snapshot_rejects_increases`, `test_unknown_para14a_freezes_flagged_devices`; scenario window with an unavailable §14a signal |
| G6 cool-down after island and §14a | pass | `core.grid.GridMemory`; `test_no_increase_without_a_full_cooldown_after_island`, `test_gate_para14a_end_starts_the_cooldown`; scenarios: no rise within 120 s of each end |
| Reserve never undercut by SunCore | pass | unchanged B1/B6; island releases SunCore's battery limits (the inverter's own floor applies) |
| Min run/pause, switches per hour | pass (with the G2 waiver) | `test_switching_stays_within_the_limits_and_nothing_is_rejected` for all four scenarios |
| No oscillation | pass | `test_para14a_cap_does_not_flap_on_noise`; `para14a_dimming` cloud flicker: <= 1 charger switch in the window |
| Rate limit, write on change, read-back | pass | unchanged gate path; no new `apply()` caller (`tests/safety/test_boundary.py`) |
| Stale/missing inputs -> safe state | pass | G5; `resolve_grid_inputs` failure falls back to the fuse limit; unknown backup = connected with warning |
| Shadow / assisted / automatic | pass | shadow evaluates G3-G6 ("would dim": `test_gate_shadow_logs_would_dim`, `test_para14a_shadow_says_would_dim`); defaults unchanged |
| Emergency "back to normal" from any state | pass | `ReleaseAction` is never judged by G1-G6, also in island |
| Only `safety/` calls `apply()` | pass | boundary test green; `controller/grid.py`, `app/grid.py` write nothing |

#### Offene Fragen an den Maintainer (6-F)

1. **Lastabwurf über der Anschlussgrenze:** Über der Sicherungs-/Anschlussgrenze schaltet
   SunCore nicht-thermische Geräte auch vor Ablauf der Mindestlaufzeit und trotz Schaltlimit ab
   (Wärmepumpen/Klimageräte nie). **Empfehlung:** so lassen. Einverstanden?
2. **§14a ohne Signal-Entity:** zählt als „nicht aktiv“ mit Hinweis. **Empfehlung:** so lassen
   (du hast keine Steuerbox).
3. **§14a-Zahlensignal:** 0 W = nicht gedimmt (eine Dimmung unter 4,2 kW ist rechtlich nicht
   vorgesehen). Kennst du eine Steuerbox, die das anders meldet?
4. **Notstrom-Entity:** Welche Entity und welcher Zustand zeigen beim Kostal BackUp Switch G3
   den Inselbetrieb (Frage 10 aus §6)?

#### Review conditions 6-F (control-safety, PR #54)

- **A1 (done):** the G2 waiver needs a fresh snapshot (`GridSafety.inputs_fresh`); an old
  `OK` value above the fuse in a stale snapshot keeps min run and the switch limit
  (`test_a_stale_snapshot_never_waives_min_run`).
- **A2 (done):** the §14a sum of the other flagged devices raises each estimate to the expected
  power of a write in flight: sent and not yet read back (the gate's settle wait), a pending
  unconfirmed read-back, or a confirmed write while the snapshot is older than write + settle
  time + one tick (`test_gate_para14a_counts_a_write_during_its_settle_wait`,
  `test_gate_para14a_counts_a_write_until_a_settled_snapshot`).
- **H1 (done):** see "Cool-down memory" above (`test_gate_seed_restores_a_running_cooldown`,
  `test_memory_from_events_and_merge`, `test_flush_persists_and_load_seeds`).
- **H2 (done, pre-existing):** G1 (`check_grid`) rejects a load increase of unknown size (no
  nominal power, no estimate) with `safety.power_unknown`; before, such a delta passed and was
  not claimed (`test_an_increase_of_unknown_size_is_rejected`, updated property
  `test_grid_passes_only_with_proven_surplus`).
- **H3 (for 6-L):** the G2 waiver must not interrupt a running program (ADR-0018 decision 11):
  6-L exempts devices with a running program from `fuse_shed_waiver` (min run and switch limit
  stay).
- **H4 (known behaviour):** an automatic release in island mode (manual override, watchdog,
  stop) restores the device's baseline; when the user's own state at takeover was "on", the
  release switches the device on although island forbids SunCore increases. This is
  intended: "back to normal" restores the user's state (BRIEF §5) and is never judged by
  G1-G6; the inverter's backup limits stay the hard bound.

### 6-H (planner for flexible loads and grid)

- **No battery into the EV:** `battery.allow_discharge_to_ev` does not exist in the contracts,
  so the EV is fed only by surplus and grid. Programs and heating may use the battery.
  EV grid slots do not set the battery's discharge lock (open for 6-I).
- **Conservative EV targets:** the target constraint counts surplus only up to the P10 surplus
  (P10 PV minus P50 load); the objective uses P50.
- **Energy target semantics:** `EvTarget.energy_wh` is energy delivered to the car; the wallbox
  draws it divided by the charging efficiency.
- **Arrival and departure** of the EV come from a planner-side `EvLive` (SoC, connected,
  arrival); the 6-B contracts carry no such fields. Unknown need = `planner.ev.need_unknown`.
- **§14a:** the budget holds for a fixed hold time (default 15 min from now); unknown status
  is treated as budget 0 for the flagged loads (`planner.grid.para14a_unknown`).
- **Import limit:** a demanded load (EV floor, running program, heating to `min_c`) above the
  import limit uses a slack penalised like the reserve, bounded by the demanded power, so the
  plan stays solvable; the export limit stays a hard bound (curtailment is informational).
- **Thermal model:** one outdoor series for losses and COP (a domestic hot-water tank uses it
  too); heating only, no cooling.
- **Programs** are never energy-only and plan one job per device; further jobs get
  `planner.job.ignored`.
- **Runtime wiring:** `FlexRequest`/`GridRequest` are not built by the app yet; the planner
  service passes them from 6-I..6-L. Plans carry the bands; nothing writes.

### 6-I (wallbox/EV)

- **No integration template:** only the home-assistant.io page of the `wallbox` integration
  was readable (2026-11-24); the core source and its translation keys were not, so only
  `generic.wallbox` ships. An integration template (wallbox, keba) follows when the source can
  be read (plan WP9 asked for one).
- **E6 by lock, not by accounting:** grid power for the car (`now`, `min_pv` floor, plan
  `grid_power_max_w`) is accepted only without a home battery or while SunCore holds a
  confirmed discharge lock (`max_discharge_w` 0 or `min_soc` at the SoC). The battery
  controller requests the lock while a charger has an allowance (`controller.battery.ev_lock`),
  B4 accepts it also without a fresh plan. Consequence: with a battery in shadow or
  observe-only, the car charges from surplus only, also in `now` (safe default, question 1).
- **E2 from the gate's own observation (review M2):** the gate keeps, per charger, since
  when it has observed it not charging (every `poll` and `observe`; an unknown or charging
  observation restarts it) and requires the phase-switch pause from that time. The
  controller's `idle_since` only mirrors it.
- **Floors and the deficit back-off:** the back-off does not lower a charger below its floor
  unless the import limit is exceeded (G1 always wins); §14a, fuse and phase caps win over the
  floor and give their own reason.
- **Fail-safe:** untrusted site inputs cap the charger at the minimum current (no increase);
  a site watchdog trip releases to the baseline. The mode lookup failing counts as `off`.
- **Runtime:** EV states come from `flex_state` (reloaded each planning round); the planner
  gets `FlexRequest` with the live SoC and plug state. `GridRequest` is still not wired, and
  there is no API to set the EV mode/target yet (6-D/6-M).
- **Simulator:** `ControlParams.site_battery: false` removes the demo site's battery for EV
  scenarios without one. In `ev_arrives_late` the 6-H planner stops permitting grid power at
  about 77 % (target 80 %); the controller follows the plan (finding for 6-H).
- **Behaviour cap:** unconfirmed chargers stay capped at assisted (E8, 6-B rule unchanged);
  scenarios set `behaviour_confirmed: true`.

#### Safety self-review 6-I (control-safety)

| Invariant | Result | Evidence |
|---|---|---|
| E1 current 0 (pause possible) or in control and entity range | pass | `check_current_range`; `test_e1_an_accepted_current_is_a_possible_pause_or_inside_both_ranges`, `test_e1_current_outside_the_range_is_rejected`; driver snaps down to the step (`test_current_is_clamped_to_the_control_range`) |
| E2 phase change only paused, <= max/h, never in `now`, resume after the pause | pass | `check_phases` with the gate-observed idle time; `test_e2_a_phase_change_is_accepted_only_paused_rarely_and_never_in_now`, `test_m2_e2_counts_the_pause_from_the_gates_own_observation`, `test_m2_a_charging_observation_restarts_the_pause`, `test_flicker_never_exceeds_the_phase_switch_limit`; scenario `ev_phase_switching`; also for a release (A1) |
| E3 import for the car <= the mode's allowance (ADR-0017 d. 4) | pass | `check_charge_grid`, `ev_allowance_w`; `test_e3_e6_g1_an_accepted_charge_imports_at_most_the_allowance`, `test_surplus_only_modes_get_no_grid_power`, `test_target_uses_the_fresh_plan_band_only`; scenarios `ev_pv_only_flicker` (caused import <= 2 % of moved energy), `ev_min_pv` (no import beyond the floor) |
| E4 / G1-G6 hold for a charge | pass | `expected_power_w` knows the charge power; `test_g1_the_import_limit_holds_for_a_user_demanded_floor`; scenario `ev_para14a` (<= 4200 W while dimmed, no rise in the cool-down or with an unknown signal) |
| E5 no increase unplugged or in `off` | pass | `check_ev_mode`; `test_e5_no_increase_unplugged_or_off`, `test_e5_unplugged_or_off_never_raises_but_pauses`, `test_an_unreadable_mode_counts_as_off`; `ev_min_pv` pauses on departure |
| E6 home battery never discharges into the car | pass | `battery_locked` in the gate, `controller.battery.ev_lock`, B4 extension, one lock tolerance (`core.battery.discharge_lock_held`, M1); a lost lock sheds the car with min run waived (A2); `test_e6_grid_power_needs_a_battery_lock`, `test_m1_the_written_lock_floor_is_always_a_lock_for_the_gate`, `test_a_lock_lost_while_charging_sheds_the_car_within_a_tick_and_settle`; scenario `ev_arrives_late` (`battery_w >= -50 W` whenever the car draws) |
| E7 write on change, rate, read-back | pass | unchanged gate path; `charge_matches`; `test_write_only_on_change`, `test_read_back_and_device_state` |
| E8 assisted until the behaviour is confirmed | pass | `test_e8_unconfirmed_behaviour_is_capped_at_assisted`, `test_unconfirmed_phase6_devices_are_capped_at_assisted` |
| E9 release restores current, phases, switch | pass | full `ChargeAction` baseline with `enabled`; staged when phases differ or §14a dims (A1, M3); `test_release_restores_current_phases_and_switch`, `test_e9_back_to_normal_restores_the_baseline`, `test_a1_release_while_charging_pauses_first_and_restores_phases_after_the_pause`, `test_m3_release_while_para14a_dims_only_pauses_until_it_ends` |
| Min run/pause, switches per hour | pass | template defaults 300 s / 6 per hour; `test_nothing_is_rejected_and_switching_stays_within_the_limits` (four scenarios) |
| No oscillation (cloud flicker) | pass | `test_pv_only_flicker_does_not_oscillate` (<= 2 transitions per flicker episode), `test_flicker_never_exceeds_the_phase_switch_limit` (hypothesis) |
| Stale/missing inputs -> safe state | pass | fail-safe cap at the minimum (`test_floor_and_cap_by_mode`), surplus unknown rejected (`check_charge_grid`), stale plan band permits nothing; a pause is accepted with unknown inputs (`test_a_pause_is_accepted_even_with_unknown_inputs`) |
| Shadow / assisted / automatic | pass | `test_shadow_is_the_default_and_writes_nothing`, `test_shadow_writes_nothing` (all four scenarios) |
| Emergency "back to normal" from any state | pass | `ReleaseAction` never judged by E1-E6; `test_a_release_of_a_phase6_device_writes_nothing[wallbox]`, E9 tests |
| Only `safety/` calls `apply()` | pass | boundary test green; `controller/ev.py` and the runtime wiring write nothing |
| Reserve never undercut by SunCore | pass | the EV lock only stops discharging; B1/B6 unchanged |

#### Offene Fragen an den Maintainer (6-I)

1. **Hausbatterie im Schattenmodus:** Solange SunCore die Batterie nicht steuern darf, kann es
   keine Entladesperre setzen – dann lädt das Auto auch in „Sofort“ und „Min+PV“ nur aus
   Überschuss (sonst könnte die Batterie ins Auto entladen). **Empfehlung:** so lassen und im
   UI erklären. Einverstanden?
2. **Entladesperre kostet Hausbezug:** Während das Auto aus dem Netz lädt, deckt auch die
   Hauslast nicht die Batterie, sondern das Netz. **Empfehlung:** akzeptieren (einfach und
   sicher); eine genauere Aufteilung erst mit `allow_discharge_to_ev`.
3. **Integrations-Vorlage:** Welche Wallbox planst du? Ich konnte nur die Doku der
   `wallbox`-Integration lesen, nicht ihren Quellcode; eine Vorlage dafür folgt, sobald der
   Quellcode gelesen werden kann.
4. **Phasenumschaltung ohne vorherige SunCore-Pause:** Erledigt (Review M2): Das Gate
   merkt sich selbst, seit wann es die Wallbox „lädt nicht“ sieht, und verlangt die Pause ab
   diesem Zeitpunkt – auch wenn SunCore sie nie pausiert hat. Keine Frage mehr offen.
5. **Ziel knapp verfehlt:** Im Szenario „Auto kommt spät“ hört der Planer bei ca. 77 % statt
   80 % auf, Netzleistung freizugeben. Soll ein Ziel hart bis zur Abfahrtszeit gelten (dann
   auch zu teureren Preisen)? **Empfehlung:** ja, Prüfung in 6-H/QA.
6. **Freigabe während §14a:** Gibt SunCore eine markierte Wallbox frei, während §14a dimmt
   oder die Abklingzeit läuft, pausiert sie nur und bekommt ihren alten Zustand (z. B. 11 kW)
   erst danach zurück (Prüfung jede Minute). **Empfehlung:** so lassen; alternativ den
   Mindeststrom statt der Pause. Einverstanden?
7. **Gestufte Freigabe beim Beenden:** Beendet sich SunCore (Update, Neustart), während eine
   Freigabe noch auf die Phasenpause oder das Ende von §14a wartet, bleibt die Wallbox
   pausiert, bis SunCore wieder läuft (die Wiederherstellung holt `recover()` nach).
   **Empfehlung:** so lassen – Pause ist der sichere Zustand. Einverstanden?

#### Review conditions 6-I (control-safety, PR #59)

- **A1 (done):** a charger's release whose baseline has other phases is staged: while the car
  charges, the gate pauses it first (0 A / enable off) and restores the baseline only after
  `phase_switch_pause_s` (`release_due_ts`, executed by `poll`); an idle charger waits the rest
  of the gate-observed pause. Same phases release at once.
- **A2 (done):** a running charger whose floor waits for the battery lock
  (`controller.ev.battery_wait`) while the battery discharges more than 50 W
  (`EV_BATTERY_DISCHARGE_W`) is paused at once, min run and the switch limit waived in the
  controller and in the gate (`ev_unlocked_shed_waiver`, like G2). Scenario: the battery
  goes to shadow at 02:00 in `ev_arrives_late`; the car is paused within settle + tick and
  `battery_w >= -50 W` afterwards (the test fails without the fix: -5000 W).
- **M1 (done):** `SOC_LOCK_TOLERANCE_PCT` and `discharge_lock_held` live in `core.battery`;
  the gate, the EV logic and the battery controller's floor rewrite use them (hypothesis test
  over the SoC window).
- **M2 (done):** see "E2 from the gate's own observation" above; `never_charged` is gone.
- **M3 (done):** a release of a `para14a` charger while dimming or in the cool-down pauses it
  and restores the baseline only afterwards (question 6).
- **H1 (done):** the gate wants the lock only for a charger that is connected or drawing;
  the controller's E6 flag ignores a lock whose release is pending
  (`BatteryControlInputs.release_pending`).
- **H2 (done):** the driver refuses to write when the current entity's
  `unit_of_measurement` is set and not `A`.
- **H3 (done):** a battery counts as present also when a non-zero battery flow was measured
  (sticky for the session; without a configured battery `core.balance` reports a synthetic
  0 W) or a battery adapter is wired (`tests/app/test_ev_battery_present.py`).
- **CI (done):** the e2e gallery count includes `generic.wallbox` (7 templates).

### 6-L (shiftable programs)

- **Arming:** one open job per device (`ProgramJobBook.arm`, `api.error.program_job_open`).
  There is no API, MQTT command or panel to arm or cancel a job yet; the book is ready
  (`arm`, `cancel`, error keys in the catalogue) and the services follow with 6-D/6-M, like
  the EV mode in 6-I. Until then jobs come only from the simulator.
- **Plug method, P4 interpretation:** the user selects the programme, presses start and arms
  the job; SunCore switches the plug off right after arming (within
  `PROGRAM_ARM_WINDOW_S` = 600 s, while the appliance draws less than `idle_w`). A plug that is
  on later (the user, a release, the crash recovery) stays on for this job. An appliance that
  draws `idle_w` or more right after its start button is seen as a manual start and runs at
  once (no delay); the template says so in its `unverified` list.
- **Start without a plan:** without a fresh plan `start` band the controller starts at the
  latest start that still meets the deadline (deadline - run time). A stale plan permits
  nothing beyond that. The deadline binding (plan band or latest start) lets the start import
  up to the program's peak power (P5, `program_allowance_w`); the import limit holds, and the
  battery reserve and B-invariants are unchanged, so a deadline start can still be rejected
  (then the job is missed: cancelled with a `notify.program.missed` notification).
- **Fail-safe = "on":** a release, the emergency "back to normal", the crash recovery, a
  watchdog trip on a stale power sensor, a missed or cancelled job all restore the plug's
  baseline, which is **on** (P3): the appliance then runs earlier than planned rather than
  never. Consequence (scenario `program_deadline_plug` with a stale sensor): the watchdog
  releases the plug and the waiting machine starts on its own (question 1).
- **Release never interrupts:** a stored *off* baseline (only possible from a store written
  before 6-L) is not applied while the program may run (`_release_baseline`, the write is
  skipped); remote and notify programs write nothing on release.
- **Notify-only programs:** new control kind `notify` (closes the 6-B item "notify-only
  programs still need a control spec"). The gate rejects every action but a release
  (`safety.program_notify_only`), the driver has no entity. The "start now" notification is
  sent in every mode, also in shadow, because it writes nothing (question 2).
- **Remote start:** a `switch` role only; a `button` (`button.press`) is not supported yet
  (WP12 asked for `button` or `switch`; `UNVERIFIED` per integration). A remote start is never
  switched off (`safety.program_remote_off`).
- **Running detection:** power >= `idle_w` for 30 s (`MANUAL_START_S`) or an accepted start;
  done after the run time and 5 min below `idle_w` (`PROGRAM_DONE_IDLE_S`); an unknown power
  never ends a run. Programs need a measured power role; without one the controller starts
  nothing (`controller.program.no_power_sensor`).
- **Runtime:** the job book loads the open jobs from `flex_state` at start, the planner gets
  them (`FlexRequest.jobs`), the control stack persists the controller's job updates and posts
  the program events through `NotificationService.post` (category `program`, quiet hours for
  `done`/`missed`, not for `start_now`).
- **6-I M1 boundary (found by hypothesis here):** `BatteryIntents._lock_floor` kept a held
  `min_soc` lock only for `soc < held + 0.5`, `discharge_lock_held` accepts `soc <= held +
  0.5`; at exactly the tolerance the controller rewrote a lock the gate still counted as
  held (`held=20, soc=20.5`). Now both use `<=` (one character in `controller/battery.py`).
- **Templates:** `generic.program_plug`, `generic.program_notify`, `generic.program_remote`;
  `program.resume_confirmed` moved from `ProgramSpec` to the device check, so the plug template
  can default to `start_method: plug` while the user confirms on the device. Run time, idle
  and peak power are placeholders the wizard asks for (6-M).

#### Safety self-review 6-L (control-safety)

| Invariant | Result | Evidence |
|---|---|---|
| P1 switch-on only for an armed job of that device, with a fresh measured power | pass | `check_program`; `test_p1_a_start_needs_an_armed_job_and_a_known_power` (hypothesis), `test_p1_no_start_without_an_armed_job`, `test_p1_no_start_while_the_power_is_unknown_or_stale`, `test_an_unreadable_job_lookup_counts_as_no_job` |
| P2 never switch off a running program | pass | `program_running` (job running, power >= `idle_w`, unknown power, run time + 15 min after SunCore's switch-on); `test_p2_a_switch_off_never_passes_while_the_program_may_run` (hypothesis), `test_p2_drawing_idle_power_or_more_counts_as_running`, `test_p2_a_running_program_is_not_switched_off_even_above_the_fuse_limit`, `test_p2_a_release_never_switches_a_running_program_off`; scenarios: `interruptions == 0` in all program scenarios |
| P3 plug baseline on; release and crash recovery switch it on | pass | gate takeover `PROGRAM_BASELINE_ON`; `test_p1_an_armed_start_on_surplus_is_written_and_the_baseline_is_on`, `test_p3_a_release_restores_the_on_baseline`, `test_plug_program_is_released_to_on_after_it_finished`, `test_the_crash_recovery_switches_the_plug_on_and_the_program_runs` |
| P4 off at arming only while idle | pass | `check_program` (armed job, <= 600 s after arming, power < `idle_w`); `test_p4_a_switch_off_needs_an_armed_job_within_the_arm_window` (hypothesis), `test_p4_a_plug_that_is_on_later_stays_on`, `test_p4_no_switch_off_after_the_arm_window`, controller `test_no_power_down_after_the_arm_window_or_when_the_machine_draws_power` |
| P5 G1/G4 hold; deadline-bound start may import its peak | pass | `check_program_grid`, `program_allowance_w`; `test_p5_an_accepted_start_stays_within_allowance_and_import_limit` (hypothesis), `test_p5_without_surplus_a_start_waits_until_the_deadline_binds`, `test_p5_the_import_limit_holds_for_a_bound_start`, `test_the_planned_start_imports_nothing_beyond_the_import_limit` |
| P6 assisted until the behaviour is confirmed | pass | existing cap; `test_p6_an_unconfirmed_program_is_capped_at_assisted` |
| G2 fuse waiver never interrupts a running program (6-F H3) | pass | `_fuse_shed` returns False for a running or unknown program; `test_p2_a_running_program_is_not_switched_off_even_above_the_fuse_limit` |
| Deficit back-off never touches a program | pass | programs have no surplus levels (`controller/levels.py`); the program controller never emits a switch-off after arming (`test_a_manual_start_is_detected_by_power_and_never_switched_off`) |
| Deadline guaranteed or reported | pass | start at the latest start without a plan (`test_without_a_plan_the_start_is_at_the_latest_start`, `test_plug_program_is_powered_down_at_arming_and_started_once_at_the_latest_start`: finished <= 18:00); a missed deadline is cancelled and notified (`test_a_job_that_did_not_start_by_its_deadline_is_missed`) |
| Write on change, rate limits, read-back | pass | unchanged gate path; driver `test_plug_switches_on_and_off_and_writes_only_on_change` |
| No oscillation (cloud flicker) | pass | `test_cloud_flicker_does_not_switch_the_program` (exactly 2 switches, 4 physical transitions); `program_notify_start` and `program_crash_recovery` carry flicker too |
| Stale/missing inputs: no start, safe state | pass | `test_unknown_inputs_or_island_never_start` (hypothesis), `test_p5_stale_site_inputs_never_start_a_program`, `test_a_stale_power_sensor_never_starts_the_program_but_its_safe_state_is_on`, `test_unknown_power_never_ends_a_run` |
| Shadow / assisted / automatic | pass | `test_shadow_writes_nothing`, `test_shadow_writes_nothing_and_the_machine_runs_as_the_user_left_it`, `test_notify_shadow_still_asks_and_writes_nothing` |
| Emergency "back to normal" from any state | pass | `ReleaseAction` never judged by P1-P5; `test_p3_a_release_restores_the_on_baseline`, `test_an_off_baseline_is_restored_when_the_program_is_idle` |
| Fail-safe on shutdown, crash, connection loss, update | pass | release to on (P3) via the existing paths; crash and restart: `program_crash_recovery` |
| Notify-only programs never written | pass | `test_a_notify_program_is_never_written` (hypothesis and gate), `test_notify_never_writes`, `test_notify_program_is_never_written_and_asks_once` |
| Only `safety/` calls `apply()` | pass | boundary test green; `controller/programs.py`, `app/programs.py` (`update_jobs`) and the runtime wiring write nothing |
| Reserve never undercut by SunCore | pass | B-invariants unchanged; a program start is a load, not a battery action |
| Deterministic, time injected | pass | `test_the_controller_is_deterministic`; no sleeps or wall-clock reads in `controller/programs.py` |

#### Offene Fragen an den Maintainer (6-L)

1. **Sicherer Zustand „an“:** Fällt der Leistungssensor aus, gibt der Watchdog die Steckdose
   frei, und die Freigabe schaltet sie ein – die wartende Maschine läuft dann sofort los
   (früher als geplant, aber sicher fertig). Dasselbe gilt für Absturz, Not-Aus-„Zurück zu
   normal“ und einen verpassten Auftrag. **Empfehlung:** so lassen („lieber zu früh als nie“).
   Einverstanden? Läuft SunCore gar nicht, während die Steckdose aus ist (Review M2), schaltet
   sie niemand ein: Die Nutzerdoku (`docs/user/*/surplus-control.md`, Abschnitt „Verschiebbare
   Programme“) und die Kompatibilitätsliste empfehlen deshalb eine HA-Automation „Steckdose
   länger als X h aus → ein“. Soll SunCore diese Automation später selbst anlegen?
2. **„Jetzt starten“ auch im Schattenmodus:** Bei Programmen mit Benachrichtigung schickt
   SunCore die Bitte „jetzt starten“ auch im Schattenmodus, weil nichts geschaltet wird.
   **Empfehlung:** so lassen. Oder soll der Schattenmodus auch Benachrichtigungen unterdrücken?
3. **Fernstart per Taste (`button.press`):** Nur ein Schalter wird unterstützt. Welche
   Geräte planst du (Hersteller/Integration)? Dann prüfe ich die Taste anhand des Quellcodes.
4. **Vormerken bedienen:** Es gibt noch keine API, kein MQTT-Kommando und keine Karte zum
   Vormerken („fertig bis“). **Empfehlung:** mit 6-D/6-M nachziehen (wie der EV-Modus aus
   6-I). Einverstanden?
5. **Startfenster 600 s:** SunCore schaltet die Steckdose nur in den ersten 10 Minuten nach
   dem Vormerken aus. Reicht das, oder soll das Fenster einstellbar sein?

#### Review conditions 6-L (control-safety, PR #62)

Approved with conditions; all done in the follow-up commit:

- **H1 (done):** the release after a job is an intent in flight (`kind="release"`); it is
  settled only by `accepted`, `held` or `rejected` with `safety.not_under_control`, otherwise
  sent again after `RETRY_S` (`test_h1_a_failed_release_is_sent_again_until_it_is_settled`,
  `test_h1_a_held_or_not_under_control_release_is_settled`). The optional gate-side
  `release_due_ts` on `failed` was not added: the controller's retry covers it.
- **M1 (done):** the in-flight timeout is checked at the start of every device tick, and
  `ControllerLoop._submit_one` resets a `program:` intent whose submission raised
  (`ProgramController.reset_pending`; `test_m1_a_submit_error_does_not_block_the_release`,
  `test_m1_an_intent_without_a_decision_expires_and_the_release_follows`,
  `test_m1_the_loop_resets_a_program_intent_whose_submission_raised`).
- **M2 (done):** user docs (en/de `surplus-control.md`) and the compatibility list recommend
  an HA automation that switches the plug on after many hours off; question 1 extended.
- **L1 (done):** `NotifyEvent.cap_exempt`; `start_now` and `missed` (also `unknown`) are never
  held back by the daily push cap (`test_a_cap_exempt_event_is_sent_beyond_the_daily_cap`).
- **L2 (done):** `ProgramJob.notified_at` (additive, persisted in `flex_state`); a restart
  does not ask again (`test_l2_a_notify_job_asked_before_a_restart_is_not_asked_again`).
- **L3 (done):** `ProgramSpec.idle_w` must be above 0 (`test_program_spec_rules`).
- **L4 (done):** a missed job whose appliance power was unknown at some tick is announced as
  `notify.program.unknown` ("Status unbekannt") instead of `missed`
  (`test_l4_a_missed_job_with_a_power_gap_has_an_unknown_outcome`).
- **N1 (re-check of 693903b, done):** a release answered with `shadow` or rejected with
  `safety.emergency_stop` is settled too (the gate released every device when it entered
  shadow or the emergency stop); before, it was retried every 60 s, about 60 decision-log
  entries per hour (`test_n1_shadow_or_an_emergency_stop_settles_the_release_at_once`: one
  submission per hour in both cases, a real failure still retried).
- **CI (done):** `e2e/wizard.spec.ts` searches the gallery for "switch or smart plug" (the
  new `generic.program_plug` also matched "plug").

#### Notes for 6-M (from the 6-L review)

- The UI must warn before cancelling a job and before "back to normal" on a program device
  that the plug is switched **on**, so the machine starts immediately.
### 6-J (evcc as actor)

- **evcc facts read from source (`evcc-io/evcc`, branch `master`, 2026-11-24):** `server/mqtt.go`,
  `server/mqtt_setter.go`, `core/keys/loadpoint.go`, `core/loadpoint_api.go`,
  `plugin/mqtt/client.go`. Loadpoint ids start at 1 (index + 1); a value is written by
  publishing to `<prefix>/loadpoints/<id>/<key>/set` (not retained: evcc ignores empty payloads
  and resets the topic itself); the read topics are published retained under the same names;
  modes are `off`, `now`, `smart` (`pv`/`minpv` are deprecated aliases); `maxCurrent` is a
  float and must be at least `minCurrent`; `limitSoc` is an integer; `<prefix>/updated` is the
  Unix time of the latest publication (the heartbeat). This resolves three items the plan
  listed as `UNVERIFIED` (ids from 1, read-back topics, `phasesConfigured` values 0/1/3).
- **Only mode, `maxCurrent` and `limitSoc` are written.** Never `<prefix>/site/*` (evcc's
  battery rules stay the user's, question 7), never `minCurrent`, `phasesConfigured` or a plan.
  `test_only_mode_max_current_and_limit_of_the_loadpoint_are_ever_sent` proves it on the fake
  evcc; the fake refuses and records anything else.
- **No vehicle plan (deviation from WP10).** The plan asked for the payload `{"value","time"}`
  and `nil` to clear. The setter parses JSON only, so clearing a plan over MQTT is
  `UNVERIFIED`; a plan that cannot be cleared could keep charging after SunCore is gone.
  `LoadpointAction` keeps the plan fields, but L1 rejects them
  (`safety.evcc_plan_unverified`) and the driver refuses them. SunCore's own plan (6-H) drives
  `now` in planned grid intervals instead.
- **`min_pv` maps to `smart` (no floor).** evcc has no minimum-power mode any more (`minpv` is
  `smart` plus `alwaysCharge`); SunCore cannot enforce the 6 A floor, so `min_pv` behaves like
  `pv` for an evcc loadpoint (`controller.evcc.min_pv`). The user-demanded floor of ADR-0017
  decision 4 exists for `now` only (allowance = the configured maximum current).
- **`now` only with an allowance and a battery lock.** The controller asks for `now` only in
  the user's EV mode `now` or in a fresh plan band of `target` with `grid_power_max_w`, with a
  `maxCurrent` that fits the allowance, the import limit and the §14a budget, and only while
  there is no home battery or SunCore holds a confirmed discharge lock (E6, the same
  `LoadpointView.lock_wanted` request as for a charger). Otherwise it waits in `smart`
  (`controller.ev.battery_wait`).
- **Controller:** `controller/evcc.py` `EvccIntents` is separate from the level allocator
  (evcc regulates current and phases itself). Intents on change; a refused or shadowed one
  is repeated after 60 s; `maxCurrent` is raised only by at least 2 A, lowered at once.
  **Fail-safe:** unknown or untrusted site inputs, island, a running cool-down, §14a unknown,
  an unplugged vehicle, a stale or unreadable evcc state -> the wanted state is cut down to
  what evcc shows now (`controller.evcc.no_raise`); a silent evcc yields no intent.
- **Link and driver:** `app/evcc_link.py` `EvccLink` is a second MQTT connection (own client
  id, no last will) to the same broker as the HA entity surface; it needs the `mqtt` section
  enabled (broker from the Supervisor or manual). evcc counts as alive only while the link is
  up **and** `<prefix>/updated` is younger than 120 s by evcc's clock and was received within
  120 s, so a stale retained value after a reconnect never makes a dead evcc look alive. The
  driver (`devices/evcc.py`) is dumb: it publishes only what differs, `off` first, and a
  release restores the takeover baseline (mode, `maxCurrent`, `limitSoc`) and waits for evcc's
  echo; it is refused while evcc is silent.
- **Core change:** `DeviceState` gained `max_current_a` and `limit_soc_pct`;
  `LoadpointAction.matches` (the 0.05 A tolerance) lives in `core/decisions.py`
  (`safety/` must not import `devices/`); `NOT_YET_SUPPORTED_KINDS` is now only
  `inverter_limit`. `safety.loadpoint_not_loadpoint` / `safety.loadpoint_needs_loadpoint`
  keep the two control families apart like the charger rules of 6-I.
- **Template without roles:** `evcc.loadpoint` writes no Home Assistant entity, so the device
  template validator allows `roles: {}` for a `loadpoint` control (conformance scenario
  `tests/scenarios/device/evcc.loadpoint.yaml`). The wizard has no field for `loadpoint_id`
  and `topic_prefix` yet: the defaults are loadpoint 1 and prefix `evcc` (frontend work for
  6-M, question 1). The e2e gallery count is 8.
- **Simulator:** `SimLoadpoint` (no HA entities) behind `FakeEvccBus` (`sim/fake_evcc.py`, an
  in-memory evcc, no broker). Its regulation of `smart` (surplus of the previous step, 6 A
  minimum, 30 s start and stop delays) is a model, not evcc's code. `ScenarioRunner.evcc` is
  the bus; `ControlStack(evcc=...)` is the same class as in production. The real link is
  tested against the in-process stub broker (`tests/app/test_evcc_link.py`).
- **Release retries (review B1/C1):** a release that fails on a device trip or in `poll`
  is rescheduled with the battery's back-off (30 s doubling to 10 min, `release_due_ts`,
  writing blocked meanwhile) instead of one `failed` decision per 5 s tick; the first retry
  after evcc is back restores the baseline. Since the re-review (R2) the emergency stop,
  recovery, reconnect and a site trip reschedule a failed first attempt the same way (the
  first attempt stays immediate); hand-over and shutdown keep the plain behaviour because
  their successor retries at once.
- **Behaviour cap:** an unconfirmed loadpoint stays capped at assisted (E8); the scenario sets
  `behaviour_confirmed: true`, `test_an_unconfirmed_loadpoint_stays_at_assisted` covers the
  cap.
- **`UNVERIFIED` (also in the template):** clearing a plan over MQTT; how fast evcc reacts to
  `maxCurrent` while charging; evcc resetting the mode to its default when a vehicle
  disconnects (the gate reads it as the user's override until 04:00 next day); the exact
  behaviour of `smart` with `alwaysCharge`; nothing was run against a real evcc.

#### Safety self-review 6-J (control-safety)

| Invariant | Result | Evidence |
|---|---|---|
| L1 values in range: `maxCurrent` in `[max(6 A, evcc minCurrent), configured max]`, `limitSoc` 1-100, no plan | pass | `check_values`; `test_l1_a_value_in_range_passes_and_one_outside_never_does` (hypothesis), `test_l1_max_current_outside_the_range_is_refused`, `test_l1_evccs_own_minimum_raises_the_floor`, `test_l1_a_vehicle_plan_is_refused`; the driver refuses a plan and a value below evcc's minimum (`test_a_plan_is_refused`, `test_max_current_below_evccs_minimum_is_refused`) |
| L2 `now` only in EV mode `now` or a planned grid interval of `target`; no raise in `off`, unplugged or with unknown evcc state; E6 battery lock | pass | `check_raise`, `loadpoint_allowance_w`, `check_charge_grid`; `test_now_without_an_allowance_is_refused`, `test_now_in_a_planned_grid_interval_of_target_is_accepted_within_the_band`, `test_e6_now_needs_a_battery_lock`, `test_a_mode_off_user_gets_no_raise_but_a_lowering`, `test_an_unplugged_vehicle_gets_no_raise`, `test_an_unknown_evcc_state_blocks_every_raise`, `test_l2_now_never_passes_without_an_allowance`, `test_l2_an_unknown_state_blocks_every_raise` |
| L3 the potential power (`maxCurrent` x V x phases) keeps G1/G3/G4/G5/G6; a raise to `smart` keeps G1 and the peak limit with the worst case `minCurrent` x phases x V (review A2) | pass | `potential_power_w` in `expected_power_w`, `_check_smart_import`; `test_g1_the_import_limit_holds_for_now`, `test_a2_a_raise_to_smart_is_judged_against_the_import_limit`, `test_a2_the_worst_case_uses_evccs_own_minimum`, `test_a2_lowering_and_off_are_never_blocked_by_the_import_limit`, `test_potential_power_is_zero_for_off_and_never_above_the_wiring` (hypothesis); reducing path `EvccIntents._import_cut` (`test_a2_*` in `test_evcc_intents.py`); scenarios `evcc_actor` (caused import <= 3 %) and `evcc_plan` (evcc imports by its own plan, cut within 20 s) |
| L4 write on change, rate, read-back, three failures trip | pass | unchanged gate path; the driver writes only what differs (`test_apply_writes_only_what_differs_to_the_set_topics`, `test_nothing_is_written_when_evcc_already_shows_the_values`); `matches` with the 0.05 A tolerance; the controller repeats a wanted state at most every 60 s (`test_intents_are_sent_on_change_only_and_repeated_after_the_retry_time`) |
| L5 evcc silent, shadow, emergency stop, site trip -> release to the baseline | pass | the release is refused while evcc is silent and retried with back-off until it succeeds, also after evcc is back (`test_a_silent_evcc_is_never_written_and_the_release_is_retried_with_backoff`, `test_release_fails_while_evcc_is_silent`), proven by the echo on mode and `maxCurrent` (`test_release_restores_the_baseline_and_waits_for_the_echo`, `test_release_fails_when_evcc_never_shows_the_baseline`); best effort per field, review A1 (`test_a1_release_works_without_a_limit_soc_topic`, `test_a1_release_clamps_the_current_up_to_evccs_minimum`, `test_a1_the_emergency_stop_restores_the_mode_without_a_limit_soc_topic`); `test_the_emergency_stop_restores_evccs_own_values`, `test_a_release_restores_the_takeover_baseline` |
| Never touch evcc's battery settings (`site/*`) | pass | the fake evcc refuses and records anything outside mode/`maxCurrent`/`limitSoc`; `test_only_mode_max_current_and_limit_of_the_loadpoint_are_ever_sent`, `test_never_a_site_or_a_foreign_topic`; boundary test `test_evcc_set_publisher_is_called_only_by_the_evcc_driver` |
| Fail-safe on stale or unknown evcc state | pass | driver `device_state` unavailable -> watchdog; `test_a_silent_or_missing_evcc_is_unavailable`, `test_a_stale_retained_heartbeat_never_makes_a_dead_evcc_look_alive`, `test_evcc_goes_silent_when_the_heartbeat_ages_out`; controller no-raise (`test_no_raise_when_anything_is_unknown_or_protective`, `test_property_a_wanted_state_never_exceeds_the_wiring_and_unsafe_inputs_never_raise`) |
| §14a, import limit, island | pass | `maxCurrent` capped by the budget, below 6 A `off` (`test_para14a_budget_caps_the_current_and_below_the_floor_it_is_off`); the gate's G1-G6 judge the potential power |
| Shadow / assisted / automatic | pass | `test_shadow_is_the_default_and_writes_nothing`, `test_shadow_writes_nothing` (scenario), `test_an_unconfirmed_loadpoint_stays_at_assisted`, `test_unconfirmed_phase6_devices_are_capped_at_assisted` |
| Respect the user (manual change in evcc) | pass | `test_a_mode_change_in_evccs_own_app_is_respected` (override held until 04:00, no fighting) |
| Emergency "back to normal" from any state | pass | `ReleaseAction` never judged by L1-L3; `test_a_release_of_a_phase6_device_writes_nothing[evcc_lp]` |
| Loadpoint and charger families kept apart | pass | `test_a_loadpoint_rejects_every_action_but_loadpoint_and_release`, `test_a_loadpoint_action_on_another_device_is_rejected` |
| Only `safety/` calls `apply()`/`release()`; only the driver calls `publish_set` | pass | boundary tests (`test_drivers_are_imported_only_by_safety_and_app` now covers `suncore.devices.evcc`, `test_evcc_set_publisher_is_called_only_by_the_evcc_driver`) |
| Reserve never undercut | pass | the car's lock only stops discharging; B1/B6 unchanged |

#### Offene Fragen an den Maintainer (6-J)

1. **Ladepunkt-ID und Topic-Präfix im Assistenten:** Die Vorlage nimmt Ladepunkt 1 und das
   Präfix `evcc`. Ein Feld dafür fehlt im Frontend (6-M). **Empfehlung:** Felder im
   Geräte-Editor ergänzen; bis dahin per Konfiguration. Einverstanden? Welche evcc-Version
   und welchen Broker (Mosquitto-App?) nutzt du, und zeigt evcc auf denselben Broker wie
   SunCore?
2. **MQTT-Abschnitt gemeinsam:** Die evcc-Anbindung nutzt den Broker des `mqtt`-Abschnitts
   (Supervisor oder manuell). Ist der Abschnitt aus, gibt es keine evcc-Anbindung (Gerät
   „nicht verfügbar“, nichts wird geschrieben). **Empfehlung:** so lassen; alternativ ein
   eigener `evcc`-Abschnitt. Einverstanden?
3. **Min+PV ohne Mindestleistung:** evcc kennt kein „Min+PV“ mehr; SunCore setzt `smart`
   (nur Überschuss) und erzwingt keine 6 A. **Empfehlung:** so lassen und im UI erklären.
   Alternative: der veraltete Alias `minpv` (evcc normalisiert ihn auf `smart` mit „immer
   laden“, ungeprüft). Einverstanden?
4. **Kein Fahrzeug-Plan in evcc:** SunCore schreibt keinen Plan (Löschen über MQTT ist
   ungeprüft); Ladeziel und Abfahrtszeit bleiben im SunCore-Planer, der `now`-Intervalle
   freigibt. **Empfehlung:** so lassen; nutzt du evcc-Pläne selbst? Dann kollidiert nichts,
   SunCore liest sie nicht.
5. **Moduswechsel beim Abstecken:** evcc setzt den Modus beim Trennen des Fahrzeugs auf seinen
   Standard zurück; SunCore liest das als deine Übernahme und pausiert bis 04:00 des
   Folgetags. **Empfehlung:** erst mit einem echten evcc messen, dann ggf. das Abstecken
   erkennen und ignorieren. Einverstanden?
6. **evcc-Batterieregeln:** SunCore schreibt nie nach `<prefix>/site/*` (Frage 7 des Plans).
   Bitte die evcc-Batterieregeln neutral lassen, damit evcc nicht selbst aus der Batterie
   ins Auto entlädt (E6 gilt nur für das, was SunCore freigibt). Einverstanden?
7. **Rauschen im Protokoll:** Erledigt (Review B1): Fehlgeschlagene Freigaben werden mit
   wachsendem Abstand (30 s bis 10 min) wiederholt statt jeden Takt. Keine Frage mehr offen.

#### Review conditions 6-J (control-safety, PR #61)

The independent review rejected the PR until A1 and A2 were fixed; all items are done.

- **A1 (done):** release and emergency stop could write nothing (a missing `limitSoc` topic or a
  baseline `maxCurrent` below a raised `minCurrent` made the driver refuse; the echo compared
  `limitSoc`). The release is best effort per field: mode first, `maxCurrent` clamped up to
  `max(baseline, minCurrent)`, `limitSoc` only while evcc shows it, the echo only on mode and
  `maxCurrent`. Tests: driver (`test_a1_*`), scenario `evcc_actor` with the topic removed.
- **A2 (done):** G1 and the peak limit were not checked for `smart`, but evcc can import in
  `smart` (its own plan, a smart-cost limit, `alwaysCharge`). A raise to `smart` is judged
  against the effective import limit with the worst case `minCurrent x phases x V`
  (`safety.grid_import_limit`); lowering and `off` are never blocked. The controller has a
  reducing path: while the measured import is over the limit and the car draws power,
  `maxCurrent` is lowered to the headroom (`limit - import + draw`, below 6 A `off`) and held
  until the headroom allows the maximum again (`controller.evcc.import_cut`,
  `controller.evcc.import_off`). Scenario `evcc_plan` (the fake evcc models an own plan,
  `always_charge`): the import is over the limit for 20 s, then stays under it, no rejected
  re-tries.
- **B1 (done):** failed releases are retried with back-off (see "Release retries").
- **B2 (done):** `Runtime._evcc_bus` starts the link (`EvccLink.ensure_started`) when the
  runtime is already started, so a first loadpoint added later is connected
  (`tests/app/test_evcc_runtime.py`).
- **C1 (done):** the retry after evcc's return succeeds (scenario test), so the L5 statement in
  the table is accurate.
- **C2 (done):** template `unverified`, the driver docstring and the user guide say that
  liveness depends on evcc's publication interval (a quiet evcc above 120 s trips falsely) and
  on evcc's own clock (a clock running ahead makes an old retained timestamp look fresh).
- **C3 (done):** warning in the template, the user guide (en/de) and as a reason
  (`controller.evcc.battery_warning`, shown with `smart` next to a home battery without a
  lock): `smart` has no E6 lock, evcc's `bufferSoc` rules can discharge the battery into the car.

#### Re-review 6-J (control-safety, commit 4b1d51a)

A1, B1, B2 and C1-C3 are confirmed fixed; A2 is partly fixed. Two new items, both fixed.

- **Residual risk (A2):** the G1/peak check for `smart` is reactive. evcc may import by its own
  plan until the controller has seen the measured import and the cut is written and echoed:
  an overshoot of up to about one minute (scenario `evcc_plan`: over the limit for 20 s). The
  gate cannot prevent it, because it only sees what evcc *may* do (the worst case of a raise),
  not what evcc's plan does afterwards. Accepted; the installed fuse (G2) and the grid
  operator's limit stay with the user's own protection.
- **R1 (done, blocking):** the import cut flapped between `off` and `smart` (off and re-enable
  on the same threshold, the headroom adds the charge power back; the reviewer measured 47
  mode changes in 120 ticks). Now: off only after 60 s continuously over the limit (until then
  the minimum of 6 A is held), back on only after 300 s with at least 1 A above evcc's minimum
  (`OFF_AFTER_S`, `MIN_OFF_S`, `REENABLE_MARGIN_A` in `controller/evcc.py`). In the gate,
  `action_turns_on` now follows the loadpoint mode (off <-> on/smart/now), so every such
  change counts toward the switch counter, minimum run/pause and the switch rate apply to
  switching on; **off is counted but never blocked** (the safe direction). Tests:
  `test_r1_*` (controller, gate), scenario `evcc_flicker` (clouds and load flicker around the
  threshold: at most 2 off <-> on changes and 4 switches per hour).
- **R2 (done):** a failed emergency-stop release (echo timeout with evcc reachable) was never
  retried. `_release_all(..., reschedule=True)` (emergency stop, recovery, reconnect, site
  trip): the first attempt is immediate, a failure sets `release_due_ts` with back-off and
  `release_due_reason`, and `poll` retries. Hand-over and shutdown are unchanged, so
  `recover(after_handover=True)` and `tests/safety/test_release_paths.py` hold. Test:
  `test_r2_a_failed_emergency_stop_release_is_retried_by_poll_with_backoff`.
- **C4 (done, re-check of 5ae3da5):** the "over the limit" clock restarted on a single tick
  below the limit, so noise around a mean above the limit (6 kW limit, house 2000 +- 500 W,
  65 % of the ticks over) held the car at 6 A for an hour: a permanent G1 violation. The clock
  now restarts only after the import stayed under the limit for 30 s (`UNDER_RESET_S`), so the
  cut follows within about 60-90 s. Above the fuse limit (`GridInputs.fuse_limit_w`, G2) the car
  goes `off` at once, without the 60 s hold. Tests: `test_c4_*` (controller); one test per
  release path (`recover`, `connection_restored`, site trip) for the rescheduled release.
- **Residual risk (safety review, listed as such):** G1 for `smart` is reactive, about 1 min
  (evcc's own plan may import until the cut is written; up to 60-90 s with noisy import).

### 6-C (MQTT base and backend ICU)

- **`hassio_api` stays off** (plan WP3 and decision 1 said "re-enable"): the Supervisor lets apps
  call `/services*` without it (`security.py` `api_bypass`, verified 2026-11-24), the `mqtt:want`
  services role is what grants the lookup. A boundary test asserts that `GET /services/mqtt` is
  the only Supervisor REST URL in the code and that no app folder sets `hassio_api`.
- **Stub broker, not `amqtt`**: `amqtt`'s latest release is a pre-release that does not resolve with
  the project's dependencies; `tests/ha/mqtt/stub_broker.py` is a ~200-line MQTT 3.1.1 broker
  (credentials, will, retained, wildcards) and the tests run the real `aiomqtt` against it.
  6-J can reuse it for the evcc fake.
- **Read-only subset announced**: only the entities whose values SunCore has today are
  announced (`app/ha_entities.py` `SUPPORTED`): `status`, `mode`, `available_surplus`, `healthy`,
  `shadow_mode`, `emergency_stop`, `events`, and per device `<dev>_mode`, `<dev>_power`,
  `<dev>_last_decision`. Not announced yet (catalogue entries exist, values follow): `next_action`,
  `plan_cost_today`, `savings_today`, `savings_month`, `autarky_today`, `pv_forecast_error`
  (need a summary reader, to be added with 6-D or 6-N), `month_peak`, `grid_state`,
  `para14a_active`, `island` (6-F), `battery_action`, `battery_lease` (battery state mapping),
  EV, program and comfort entities (6-I, 6-K, 6-L). All command entities (switch, button, select,
  number, text) are in the catalogue but never announced before 6-D.
- **Object id** of the emergency stop button is `emergency_stop_button` (the binary sensor keeps
  `emergency_stop`): object ids are unique per site because they form the topics.
- **Enum sensors publish the rendered label** (state and `options` in the backend language), as
  ADR-0016 decision 6 says; a language change republishes configs and states. Automations on
  these states therefore depend on the language; see question 15.
- **`available_surplus`** is the controller's smoothed `available_w` (before the reserve).
  `healthy` = Home Assistant connected and the control loop active.
- **Events** produced now: `device_switched` (accepted decision), `proposal` (proposed),
  `error` (failed), derived from each device's newest gate decision; the other types of the
  `events` entity belong to later PRs. Events are not retained (HA ignores retained replays).
- **Install record** (`iid`, announced device ids) lives in `learned_param` (namespace
  `ha_entities`), not in a new table: no schema bump.
- **Discovery** uses one device payload per HA device with `via_device` pointing at the site
  device; no `cu` (configuration_url) and no `child_devices`.
- **Backend catalogue prefixes** as planned (`mqtt.`, `notify.`, `safety.`, `controller.`,
  `planner.`, `device.`, `grid.`); the golden file covers up to 8 sample argument sets per key.
  Languages other than `en` and `de` have plural rule `other` only.
- Image smoke test not run locally (no Docker daemon in the session); the new dependencies
  `aiomqtt` 2.5.1 and `paho-mqtt` 2.2.0 are pure-Python wheels with hashes in `uv.lock`, so the
  image's `--only-binary --require-hashes` install is unaffected. CI's smoke job covers it.
- `UNVERIFIED`: `get_config` language key, custom HA birth topic, monetary unit rule, TLS to the
  Mosquitto app with verification (see `docs/research/mqtt-discovery.md`).
