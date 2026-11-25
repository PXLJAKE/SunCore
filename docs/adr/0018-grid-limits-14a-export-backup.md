# ADR-0018: Grid limits – peak shaving, export limit, §14a EnWG, backup power

- **Status:** proposed (Phase 6 proceeds on the recommendation by delegation of 2026-11-23; the
  maintainer may override); addendum 2026-11-25: inverter limit verification procedure
- **Date:** 2026-11-24
- **Deciders:** maintainer, lead engineer (`architect`), `control-safety`
- **Research:** [`kostal-plenticore-modbus.md`](../research/kostal-plenticore-modbus.md) (§3.3
  register 533), [`integrations.md`](../research/integrations.md)
  (`Inverter:ActivePowerLimitation`), Bundesnetzagentur "Integration von steuerbaren
  Verbrauchseinrichtungen" (read 2026-11-24); ADR-0011, ADR-0013, ADR-0017; BRIEF §4.6, §4.7,
  §5, §9; plan [`phase-6.md`](../plans/phase-6.md) WP5, WP10, WP11

## Context

BRIEF §4.6 asks for an import limit with a load-shedding order and battery support, monthly
peak tracking (capacity tariffs such as Belgium's), an export limit (static in W or % kWp,
dynamic by price, zero export) applied as "loads and battery first, then the inverter", §14a
EnWG dimming of controllable consumers on the grid operator's signal, and optional phase
imbalance limits. BRIEF §4.7 asks for grid-outage detection with load shedding, reserve
protection, no grid charging and a notification.

What exists: `GridConfig.import_limit_w`, `export_limit_w`, `export_limit_pct_kwp`, `para14a`,
`para14a_signal` (config only); `PlanInterval.grid_import_limit_w`; the device invariant
`check_grid` (import limit, no SunCore-caused import); battery B5 (import limit for every
battery action); the planner's `curtail_w` (informational, "until the export-limit chain").

Facts (verified unless marked):

- §14a (Bundesnetzagentur): the grid operator may temporarily reduce the draw to **4.2 kW per
  controllable device** with direct control; with an energy management system the minimum is
  computed for all devices together with a simultaneity factor, with a scaling factor of
  currently 0.4 for large systems (> 11 kW climate/heat pumps); affected: private wallboxes,
  heat pumps incl. auxiliary heaters, room cooling, storage "with regard to drawing from the
  grid", devices > 4.2 kW; preventive control up to two hours a day; since 1 January 2024.
  **`UNVERIFIED`: the exact EMS formula and factor table** (the Festlegung itself was not
  read), how the signal reaches the house (control box relay, EEBus) on a given grid.
- Kostal §3.3 register 533 "active power set-point 1..100 %", volatile (lost on reset);
  whether it limits AC output including battery discharge is `UNVERIFIED`. Kostal REST
  `Inverter:ActivePowerLimitation` (0–10000 W) semantics `UNVERIFIED`, disabled by default.
- The maintainer's export limit (13 500 W) is set in the inverter itself; he has no control
  box (§14a inactive); the Kostal BackUp Switch G3 provides backup power – which HA entity
  shows an outage is `UNVERIFIED`.

## Options considered

### 1. Where limits are computed

- **A – each layer computes its own** limit. Cons: planner, controller and gate drift (the
  reserve lesson of ADR-0013).
- **B – one resolver in `core`** (`core/grid.py`) used by planner, controller and gate.

### 2. Export limiting

- **A – controller and planner only** (load up, battery up, then curtail in the plan's eyes).
  Pros: no new write path. Cons: a limit that the hardware does not enforce is not met.
- **B – plus an inverter limit action** through the battery/inverter adapter. Pros: complete
  chain. Cons: a new write that can cause import if the limit acts on AC output.

### 3. §14a

- **A – SunCore as the certified control path** (writes even in shadow mode). Cons: breaks
  shadow mode, SunCore is no certified EMS, liability.
- **B – SunCore respects the signal**: it never raises the flagged devices above the allowed
  power, dims what it controls in automatic mode, plans with the limit and logs every event;
  the grid operator's own path (relay to the device, control box) stays in place.

### 4. Outage detection

- **A – heuristic** from grid power and voltage. Cons: false positives switch the house into
  island rules.
- **B – entity only** (a backup switch or inverter state mapped by the user), heuristic off.

## Decision

**1B, 2B (capped), 3B, 4B.**

1. **One resolver.** `core/grid.py`: `GridLimits` (config: fuse import limit, peak shaving,
   export limit static/percent/dynamic/zero, §14a, backup) and pure functions
   `effective_import_limit_w(...)`, `effective_export_limit_w(...)`,
   `para14a_budget_w(...)`; planner, controller and gate call only these. `GateInputs` gains
   `export_limit_w`, `para14a_budget_w`, `grid_state` (`connected`, `island`, `unknown`).
2. **Import side.** The effective import limit = min(fuse limit, peak-shaving limit). Peak
   shaving (off by default): limit = max(this month's highest 15-minute mean import so far,
   `peak_floor_w`), tracked from `measurement_15m` (persisted in `grid_peak`, `timeseries.db`);
   the planner gets the capacity price per kW and a peak variable. **Load shedding order**:
   reverse priority, with an optional `shed_rank`; continuous loads (EV current) step down
   before switched loads. Shedding is a reduction and always allowed by the gate, except that
   `min_run_s` of thermal kinds (compressors) is kept; above the **fuse** limit min run is
   waived for non-thermal kinds. Battery support: when import nears the limit the controller
   lifts SunCore's own discharge lock (`ReleaseAction` or a `BatteryAction` without the lock).
3. **Export side – the chain.** (1) the controller allocates surplus to devices (existing);
   (2) the battery charges – SunCore lifts its own charge caps while export exceeds the limit;
   (3) only then a new **`InverterLimitAction`** (`kind: "inverter_limit"`, asset id
   `inverter`): `max_export_w: float | None` or `max_ac_pct: float | None` (exactly one),
   executed by an inverter adapter with capability `export_limit` (W) or `ac_limit` (%).
   Invariants X1–X6 (plan): never above the configured static limit or the baseline; with
   `ac_limit` never below house load + battery charging + margin (it must not cause import);
   write on change, rate limit, read-back, heartbeat for volatile registers; release = the
   baseline captured at takeover (100 % for register 533). **The asset is capped at assisted
   until `inverter.limit_verified`** (a documented test the user runs, like ADR-0013's
   fallback measurement). Until 6-G lands the gate rejects it with
   `safety.inverter_limit_not_supported`. The planner's export constraint becomes hard where
   the limit is enforceable and stays informational (`curtail_w`) otherwise.
4. **Dynamic and zero export.** Dynamic: limit 0 W while the export price is negative (Phase 4
   negative-price rule) – default on only when an inverter adapter is verified; zero export:
   off by default.
5. **§14a (3B).** Signal sources: a binary entity (relay contact, control box input) or a
   numeric entity with the allowed power (W). While active, the sum of the power of devices
   flagged `para14a: true` (wallbox, heat pump, climate, the battery's grid charging) is held at
   or below `para14a_limit_w` (default 4 200 W; the user enters the value from the grid
   operator for several devices). The gate rejects every increase above the budget
   (`safety.para14a_limit`), grid charging is blocked, the controller dims in automatic mode
   (EV current down, below the 6 A floor pause; heat pump back to SG state 2 and no lift) and
   logs. In shadow mode SunCore only logs "would dim". Start and end are stored as grid events
   and notified. **SunCore is documented as not being the certified §14a control path.**
6. **Backup power (4B).** `BackupConfig`: an entity and the state value(s) meaning "island"
   (`UNVERIFIED` per backup device; the template or the user names them). On island the site
   state is `island`: the gate rejects every load increase and grid charging
   (`safety.island_no_increase`), the controller sheds controlled devices by the shedding order
   down to a keep list, the battery floor stays at the effective reserve, the planner pauses
   (`Plan.default()` with island reasons), a critical notification is sent. On return the
   Phase 3 cool-down (`safe_state_cooldown_s`) applies before increases resume. Unknown state
   (entity unavailable) = not island, with a warning.
7. **Phase imbalance limits** are **not** part of Phase 6 (need per-phase signals and
   per-phase device mapping); recorded as a later ADR.

**Not decided here:** EEBus/LPC, the certified §14a path, per-phase limits, inverter set-points
for the battery (§3.4), multi-inverter plants.

## Consequences

- Positive: one definition of every grid limit for all layers; the import limit holds for
  devices, battery and inverter alike; island and §14a are visible, logged and notified.
- Negative / risks:
  - An export limit on AC output can cause import (if 533 limits battery discharge too);
    mitigated by X3, the assisted cap and the verification test.
  - §14a semantics on real grids are `UNVERIFIED`; mitigated by user-entered values, the
    "not certified" note and keeping the operator's own path.
  - A wrong backup entity mapping puts the house into island rules; mitigated by a live
    preview in the wizard and the unknown-is-connected rule.
- Contract changes (additive): `InverterLimitAction`, `GateInputs` fields, `GridConfig`
  additions (peak shaving, dynamic/zero export, §14a limit and device flags, backup),
  `grid_peak` and `grid_event` tables.
- Follow-ups: 6-B (contracts), 6-F (import side, §14a, island), 6-G (export chain and inverter
  adapters), 6-H (planner peak and export), all with `control-safety` sign-off.

## Recommendation to the maintainer

Accept; please confirm that SunCore only *respects* §14a and that writing an inverter export
limit stays capped at assisted until you have run the verification test.

## Addendum 2026-11-25: the inverter limit verification procedure (plan phase 7, WP8)

Decision 3 caps the inverter asset at assisted "until `inverter.limit_verified` (a documented
test the user runs)". Phase 6 built the cap but no test, so no limit is ever written. This
addendum fixes how the test works; ADR-0018 is still proposed, so it is amended here rather than
superseded. Implementation: PR 7-J, `control-safety` (Fable), **CS** sign-off.

**Problem with the plain approach.** A test must curtail export below its current value while
devices or the battery could still absorb power. Invariant X3 ("below the current export only
when devices and battery cannot absorb more", `safety/inverter.py` `check_chain`) rejects that
on almost every day. X2 (an unverified export limit is read as an AC limit and must leave house
load + battery charging + 500 W) rejects a deep test limit. X2 protects against grid import;
X3 protects against wasting PV in the wrong order of the chain.

**Decision.**

1. **No new writer.** A procedure object in `app/` proposes `InverterLimitAction` intents with
   `Origin.USER` through the services layer; the gate judges them; the user confirms each write
   as an assisted proposal. Precondition: `operation.inverter_mode` is assisted (the procedure
   never raises a mode).
2. **X1, X2, X4, X5, X6 are never waived.** X3 is waived only while the gate holds an active
   **verification window**: a new additive field of `GateInputs` (procedure id, expiry at most
   10 min after opening, the lowest limit it allows), opened by the procedure through the
   services layer and cleared on pass, fail, abort, emergency stop, restart and expiry. The
   waiver applies only to `Origin.USER` intents for the inverter asset at or above the window's
   lowest limit. No change to `Plan`, `Intent`, `Decision` or `Reason` (ADR-0011).
3. **Preconditions:** adapter built and its limit entity readable, inputs fresh and `OK` (X5),
   no emergency stop, no island, the battery not held by SunCore, export ≥ 1 kW and at least
   1.5 kW above house load plus battery charging, stable for 2 min.
4. **Steps:** read and record the baseline; propose the test limit
   `max(house + battery charging + 700 W, export − 1 kW)` (W for `export_limit`; the matching
   percentage of `nominal_ac_w` for `ac_limit`); require that export follows within the
   adapter's settle time plus 60 s and that the read-back equals the written value; classify
   the observed semantics (export fell to about the limit = export limit; to about the limit
   minus house and battery = AC limit) and fail when it contradicts the configured capability;
   release to the baseline and read it back.
   A rising house load ends the lease through X2 as today and fails the test.
5. **Result:** a pass sets `inverter.limit_verified = true` with the adapter identity token and a
   timestamp (additive `InverterConfig` fields; config history author `user`); a changed
   identity resets it (unchanged rule). A fail stores a reason key; nothing else changes.

**Consequences.** The only relaxation is X3 for a few user-confirmed minutes; the import
protection (X2) and the bounds (X1) hold throughout. Risks and tests: plan §4 ("Inverter
verification"), §5 R9; the security review (7-O) checks the window. Contract changes (additive):
the `GateInputs` window field, the `InverterConfig` verification fields, three API endpoints
(plan WP8).

**Recommendation.** Accept; question 21 of the Phase 7 plan asks the maintainer to confirm the
X3 exception.
