# ADR-0013: Battery as a controlled asset

- **Status:** proposed
- **Date:** 2026-10-16
- **Deciders:** maintainer, lead engineer (`architect`), `control-safety`
- **Research:** [`integrations.md`](../research/integrations.md) (Kostal Plenticore, summary
  table), [`kostal-plenticore-modbus.md`](../research/kostal-plenticore-modbus.md) (§3.4, §3.5,
  "Phase 4 verification"), [`ha-websocket-api.md`](../research/ha-websocket-api.md) ("Modbus
  write action"); plan [`phase-4.md`](../plans/phase-4.md) WP2, WP8, WP10, §6 questions 3, 6, 7

## Context

Phase 4 makes the battery the first asset SunCore writes to that can **cause grid import on
purpose** (planned grid charging) and that **guards energy the user relies on** (the backup
reserve). PR 4-A published `BatteryAction` in `core/decisions.py` (fields `max_charge_w`,
`max_discharge_w`, `min_soc_pct`, `grid_charge_w`, magnitudes, at least one set) and the
`battery` config section in `core/battery.py` (`BatteryModel` with `reserve_pct` +
`bms_drift_margin_pct`, `GridChargePolicy`, strategies). The gate rejects every
`BatteryAction` with `safety.battery_not_supported` (ADR-0011 addendum 2026-10-16). Before WP10
removes that rejection, the semantics below must be fixed, because they decide what the
invariants are.

Facts that constrain the design (verified unless marked):

- **Kostal Modbus §3.5** (G3 ≥ SW 03.05): limits only – 1280/1282 max charge/discharge (W
  float), 1284/1286 fallback limits, 1288 time until fallback 30–10800 s. Once 1280/1282 are
  written they must be re-written cyclically, otherwise the fallback limits take over: a
  documented hardware watchdog. §3.4 (DC set-point 1034, negative = charge) has no documented
  timeout and is out of scope for Phase 4 (plan §7).
- **Writing Modbus through HA:** `modbus.write_register` writes raw unsigned 16-bit words
  (floats must be encoded into two words, word order from register 5), defaults `slave` to 1,
  has no read action and **only logs a failed write** – read-back through modbus sensor
  entities is mandatory. Creating a modbus hub from the HA UI is `UNVERIFIED` (YAML may be
  required). External battery management via Modbus (register 1080 = `0x02`) is **not enabled**
  on the maintainer's inverter; how it is enabled is `UNVERIFIED`.
- **`kostal_plenticore` (REST):** `number` `Battery:MinSoc` (5–100 %, step 5; live `min`/`max`
  from the inverter), `Battery:MinHomeComsumption`, `Inverter:ActivePowerLimitation`; **all
  disabled by default**; a failed write is **silent** (`async_write_data` swallows
  `ApiException`, `number.set_value` succeeds) – read-back after the forced refresh is
  mandatory. No charge/discharge power set-point over REST. Sign of `Battery Power`/`Grid
  Power` and the meaning of `ActivePowerLimitation` are `UNVERIFIED`.
- **Sign conventions:** SunCore core: battery + = charging (ADR-0011). Kostal REST battery power
  is believed to be + = discharge (`UNVERIFIED`); §3.4 1034 and MDC 1549 negative = charge.
- **Reserve today exists twice:** `site.battery.min_soc_pct` (Phase 1/3, read by the controller
  and the gate through `GateInputs.reserve_soc_pct`) and `BatteryModel.reserve_pct +
  bms_drift_margin_pct` (Phase 4 planning floor). Two values for one concept will drift.
- **Phase 3 invariant "no SunCore-caused import"** holds for devices. Grid charging violates it
  by design, and so does a discharge lock (`max_discharge_w = 0` makes the house import what
  the battery would have covered).
- **Identity:** the device identity rule (`core/identity.py`: id + template + entities) and the
  mode store (`operation` section) cover `DeviceConfig`s only. The battery is configured in the
  `battery` section and has no identity token, so re-mapping the battery would not reset its
  mode to shadow today.
- Budgets: heartbeat and read-back are a few service calls per minute; no effect on the 300 MB
  RSS or the planner's 10 s budget.

## Options considered

### 1. Action shape

- **A – limits** (the 4-A `BatteryAction`): caps on the inverter's own self-consumption loop,
  a discharge floor, and an explicit forced grid-charge power. Pros: maps 1:1 onto §3.5
  (1280/1282), `Battery:MinSoc` and generic number entities; when SunCore stops, the inverter's
  own control is the natural fallback; PV charging needs no write at all. Cons: cannot
  express "discharge exactly 2 kW for export"; forced grid charging needs an adapter capability
  that §3.5 and REST do not offer.
- **B – power set-point** (§3.4 style, battery W per interval). Pros: full control, what the LP
  computes. Cons: SunCore becomes the battery's real-time controller (must track house load
  every second or cause import/export); no documented timeout on Kostal; failure mode is a
  battery stuck at a set-point.
- **C – both, set-point as opt-in.** Pros: future-proof. Cons: two invariant sets now for a path
  nobody can test on hardware in Phase 4.

### 2. Missing fields in an action

- **A – delta** (4-A docstring: "a missing field leaves that limit as it is"). Cons: the full
  state SunCore holds is spread over several decisions; heartbeat, read-back and release cannot
  be judged from one decision.
- **B – full desired state:** `None` = "no SunCore restriction on this field", i.e. the
  adapter's baseline value. Pros: each accepted action is the complete state; the heartbeat
  re-sends it idempotently; read-back compares one record.

### 3. Reserve

- **A – each layer keeps its own value.** Cons: the planner and the gate can disagree; the gate
  could accept a floor the planner considers below reserve.
- **B – one function in `core`** used by planner, controller and gate.

### 4. Grid import caused by the battery

- **A – forbid** (Phase 3 rule unchanged). Cons: no grid charging and no discharge lock – both
  are approved Phase 4 features (questions 7, 3).
- **B – a bounded, explicit exception:** allowed only when the current plan interval asks for
  it and every live bound holds at execution time.
- **C – trust the planner.** Cons: the gate would no longer judge; a stale or wrong plan writes.

### 5. Heartbeat and release

- **A – the controller re-issues the intent every N s** through the gate. Cons: every heartbeat
  is a decision row and fights "write only on change" and the switch/write-rate invariants.
- **B – lease held by the gate, heartbeat in the actuator:** an accepted `BatteryAction` creates
  a lease; `gate.poll()` (every tick) renews it while the asset is still automatic (or the
  assisted proposal is confirmed), inputs are fresh, no trip/emergency stop/shutdown is active
  and the plan still covers the action; the actuator re-writes the accepted state at its
  heartbeat cadence while the lease is valid and stops otherwise. Hardware fallback (§3.5)
  catches a dead SunCore.
- **C – no heartbeat, rely on the hardware fallback only.** Cons: the limit drops out every
  1288 seconds; only works with §3.5.

### 6. Identity and mode

- **A – battery as a `DeviceConfig`.** Cons: battery fields do not fit the device model; the
  template kind `battery` already exists separately.
- **B – reserved asset id `battery`** with its own identity token from the `battery` section,
  stored and enforced through the existing `operation` / `mode_identities` machinery.

## Decision

Proposed: **1A, 2B, 3B, 4B, 5B, 6B.**

1. **Action shape – limits.** `BatteryAction` stays a set of limits. Powers are magnitudes
   ≥ 0; core sign convention (battery + = charging) applies to every *measured* value; each
   adapter converts its integration's signs (Kostal REST battery/grid power, §3.4) and the
   wizard's sign check confirms them live. §3.4 set-points are not decided here (later ADR).
   Every adapter declares **capabilities** (`charge_limit`, `discharge_limit`, `min_soc`,
   `grid_charge`, `hardware_fallback`); the gate rejects an action using a field the adapter
   cannot execute (`safety.battery_capability_missing`). Phase 4 adapters: `kostal_modbus`
   (§3.5: charge/discharge limit, hardware fallback; no `grid_charge`), `kostal_plenticore`
   (`min_soc` only; step 5, so the adapter rounds **up**), `generic` (per template roles).
2. **Full desired state.** A `BatteryAction` is the complete state SunCore wants; `None` =
   baseline value of that field (captured at takeover). Changes the 4-A docstring (contract
   change covered by this ADR).
3. **One reserve.** `core.battery.effective_reserve_pct(site_battery, model) =
   max(site.battery.min_soc_pct or 0, model.reserve_pct + model.bms_drift_margin_pct)`. The
   planner's SoC floor, `GateInputs.reserve_soc_pct` and the controller's back-off use only this
   function. Without a `BatteryModel` the battery is observe-only (gate rejects with
   `safety.battery_model_missing`).
4. **Bounded exception to "no SunCore-caused import"**, for the battery only. A battery action
   that can cause import – `grid_charge_w > 0`, or `max_discharge_w` below the current house
   deficit (discharge lock), or `min_soc_pct` at/above the live SoC – is accepted only if **all**
   hold at execution time:
   - the plan interval covering *now* explicitly allows it (new additive `BatteryBand` fields
     `grid_charge_max_w: float | None` and `discharge_lock: bool = False`; plan v2 contract
     change covered by this ADR) and the plan is fresh (created ≤ 2 intervals ago);
     `Plan.default()` allows neither;
   - for grid charging: `effective_grid_charge_policy(...).allows(now, live import price)` with
     the **live** price (not the planned one; an unknown price never satisfies a price level),
     and the live SoC is below `min(band.max_soc_pct, model.max_soc_pct)`;
   - `grid_charge_w ≤ min(model.max_charge_w, adapter/inverter limit, band.grid_charge_max_w,
     import headroom)` where import headroom = `grid_import_limit_w` − current import without
     the battery − `ControlSettings.reserve_w`; the site import limit holds **always**, planned
     or not.
   Devices keep the Phase 3 rule unchanged; a device may never use import created for the
   battery (the free surplus excludes it).
5. **Lease and heartbeat (5B).** The actuator owns the heartbeat (e.g. §3.5: 1288 = 60 s,
   re-write every 20 s, fallback registers 1284/1286 = the battery's own limits from 1076/1078,
   written and read back **before** the first limit write, otherwise refuse). Heartbeat writes
   are the accepted state, exempt from write-on-change but bounded by the heartbeat rate and the
   per-(domain, service) rate limiter. **Release** = stop the heartbeat and write the baseline
   (§3.5: own limits; `kostal_plenticore`: the `MinSoc` value captured at takeover), then
   read back. Release paths are the Phase 3 ones (emergency stop, shadow, shutdown, site/device
   trip, reconnect, crash recovery via `recover()`). An adapter without `hardware_fallback`
   (`kostal_plenticore`, most `generic`) relies on `recover()` and the next start: documented
   as a risk; such adapters may apply a discharge lock only for the planned interval, never open
   ended.
   **Read-back is mandatory** for every battery write (both Kostal paths fail silently); a
   mismatch is `failed`, three failures trip the battery (Phase 3 rule). For `kostal_modbus`
   the adapter also reads register 1080 through a modbus sensor and refuses to write unless it
   is `0x02`.
6. **Identity and mode.** The battery is the asset id `battery` (one battery per site in
   Phase 4). `core/identity.py` gains `battery_identity(settings, site)` = asset id + adapter
   template + mapped role entities + the site's SoC and battery power entities. The `operation`
   section stores its mode like a device's; every rule of the device identity applies (changed
   identity → shadow; import → shadow; raising the mode needs the identity token). The battery
   starts in shadow. The gate caps the effective mode at **assisted** for an adapter whose
   hardware fallback is not marked verified (`kostal_modbus` until the maintainer's timer
   measurement – plan §6 question 3).

**Invariants to add in `safety/invariants.py`** (pure, property-tested; reason keys
`safety.battery_*`):

| # | Invariant |
|---|---|
| B1 | `min_soc_pct` written (after rounding to the device step, rounded up) is never below `effective_reserve_pct` |
| B2 | `grid_charge_w ≤ min(model.max_charge_w, adapter limit, band.grid_charge_max_w, import headroom)`; 0 when any bound is unknown |
| B3 | `grid_charge_w > 0` only with a fresh plan interval allowing it, the live policy check, SoC below max |
| B4 | a discharge lock (`max_discharge_w` below the house deficit, or `min_soc_pct` ≥ live SoC) only when the plan interval has `discharge_lock`; otherwise `safety.battery_unplanned_import` |
| B5 | the site import limit holds for every battery action |
| B6 | every value inside the adapter's live range (e.g. `min`/`max` attributes of the Kostal numbers) |
| B7 | write rate, write-on-change; heartbeat only while the lease is valid |
| B8 | stale SoC/battery power while the grid meter moves, or a hung inverter → release and stop writing |
| B9 | capability check: no field the adapter cannot execute |

**Not decided here:** §3.4 set-points and MDC; export limiting through
`ActivePowerLimitation` / register 533 (Phase 6); several batteries; whether forced grid
charging is possible on the maintainer's Kostal without §3.4 (see consequences).

## Consequences

- Positive: the safety gate can judge every battery write from one decision plus live inputs;
  a crashed SunCore leaves a §3.5 inverter in its own control within 60 s; the reserve cannot
  diverge between planner and gate; re-mapping the battery drops it to shadow.
- Negative / risks:
  - **Grid charging is not executable on the maintainer's system in Phase 4:** §3.5 only
    limits and REST has no charge set-point; the planner may still plan it and shadow/assisted
    shows "would charge". Mitigation: a `generic` adapter with a force-charge role, or a later
    §3.4 ADR. Needs the maintainer's input.
  - `kostal_plenticore` has no hardware fallback; a discharge lock via `MinSoc` survives a
    SunCore crash until the next start. Mitigation: plan-bounded locks only, `recover()` on
    start, documented.
  - Required entities disabled by default (Kostal numbers) or not creatable from the UI
    (modbus hub, `UNVERIFIED`): the wizard must check and explain.
- Contract changes (additive, ADR-0011 spine): `BatteryAction` `None` semantics (docstring and
  tests), `BatteryBand.grid_charge_max_w`, `BatteryBand.discharge_lock`,
  `core.battery.effective_reserve_pct`, `core.identity.battery_identity`.
- Follow-ups:
  - Phase 4 WP2 fix (`optimizer-engineer`): the contract changes above, before PR 4-B.
  - WP8 (`optimizer-engineer`): planner floor = `effective_reserve_pct`; emits the new band
    fields only where the policy allows.
  - WP10 (`control-safety`): invariants B1–B9, lease/heartbeat in the gate, mode cap, identity;
    removal of `safety.battery_not_supported` with sign-off (ADR-0011 addendum).
  - WP10 (`device-adapters`): adapters with capabilities, sign conversion, read-back,
    fallback registers, 1080 check.
  - WP13 (`docs-writer`): enabling the Kostal numbers, modbus hub setup, the no-fallback risk
    of the REST adapter.

## Recommendation to the maintainer

Accept as proposed; your input is needed on how grid charging should be executed on your
Kostal in Phase 4 (not at all, a generic force-charge entity, or a later §3.4 ADR).

## Addendum 2026-10-26: implementation notes (Phase 4 WP10, PR 4-G review)

Additive, no decision above changes:

- The battery invariants live in `safety/battery.py` (not `safety/invariants.py`, which stays the
  device module); the gate's battery path is `SafetyGate._submit_battery`, the lease
  `SafetyGate._battery_lease`.
- B4/B5 are judged on the **resolved state that is written** (and re-sent by the heartbeat), not
  on the request: a cap or floor inherited from the baseline counts. Forced grid charging is no
  discharge lock (its import is bounded by B2/B5).
- A release is complete only when the read-back shows the original baseline (or the user set a
  third value). Until then the runtime keeps the original baseline, nothing else is written and
  the release is retried with back-off, also during an emergency stop.
- A field is the user's third value only if it differs from the baseline **and** from every
  target SunCore wrote or attempted while under control (`DeviceRuntime.attempted`); a late or
  partly applied SunCore write is restored, the user's fields are kept (restore = baseline with
  those fields), proven by read-back and held like any override (next 04:00).
- `kostal_modbus`: the baseline (`None` fields, release) is the battery's own limits (1076/1078);
  the fallback registers get the larger of the model's and the own limits and are confirmed
  against the values written; register 5 must match the configured word order.
- `battery.fallback_measured` is part of the battery identity (it lifts the assisted cap).
- Reserve (maintainer default): SunCore enforces the effective reserve in the plan and the gate
  only; the user's own lower inverter floor outside a SunCore lock stays as it is.
