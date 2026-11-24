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
   Ist Mosquitto bei dir installiert?
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
- **Notify-only programs** still need a control spec; open for 6-L.
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
