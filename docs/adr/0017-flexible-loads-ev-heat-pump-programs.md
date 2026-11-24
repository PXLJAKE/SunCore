# ADR-0017: Flexible loads – EV charging, heat pump, shiftable programs

- **Status:** proposed (Phase 6 proceeds on the recommendation by delegation of 2026-11-23; the
  maintainer may override)
- **Date:** 2026-11-24
- **Deciders:** maintainer, lead engineer (`architect`), `control-safety`, `optimizer-engineer`
- **Research:** [`competitors.md`](../research/competitors.md) (evcc MQTT API, re-read on
  2026-11-24 at `docs.evcc.io/de/integrations/mqtt-api`), ADR-0011 (and its 2026-10-16
  addendum), ADR-0012 (template policy), ADR-0013 (bounded import exception), BRIEF §4.5,
  §4.8, §9; plan [`phase-6.md`](../plans/phase-6.md) WP6–WP9

## Context

Phase 3 controls climate units, water heaters and switchable loads with five action kinds
(`switch`, `power_setpoint`, `setpoint`, `step`, `mode`) plus `release`; Phase 4 added the
battery. Phase 6 adds three device families whose behaviour does not fit the "surplus only,
switch or level" model:

- **EV charging** (BRIEF §4.5): modes Off | Now | Min+PV | PV only | Target (SoC by time,
  cheapest windows), 6 A minimum, 1/3-phase switching, hysteresis, vehicle SoC from HA,
  charging losses; drivers through HA integrations, or **evcc as actor** (evcc regulates the
  charge point, SunCore sets mode, target and limit).
- **Heat pump**: SG-Ready (two contacts, four states), set-point lift (hot water, flow,
  buffer), modes, a learnable thermal (RC) model, COP by outdoor temperature, §14a dimming.
- **Shiftable programs**: washing machine, dishwasher, dryer with run time, load profile and
  "finished by"; start through a smart plug, a remote start or a notification.

Three of them **cause grid import on purpose when the user asks** (EV "Now"/"Min+PV", a
target that PV cannot reach, a program whose deadline comes before the sun). The Phase 3
invariant "no SunCore-caused import" must stay for everything else; ADR-0013 showed how a
bounded exception is made (plan-permitted, live bounds, import limit always).

Facts (verified unless marked):

- evcc MQTT API: prefix `evcc/`, write by appending `/set`; loadpoint `mode` values `off`,
  `smart`, `now` (the old `pv`/`minpv` are gone); loadpoint topics `limitSoc`, `minCurrent`,
  `maxCurrent`, `phasesConfigured`, `planEnergy`, `planStrategy`; vehicle topics `limitSoc`,
  `minSoc`, `planSoc` with payload `{"value": 50, "time": "<UTC ISO>"}`; read topics
  `connected`, `charging`, `chargePower`, `vehicleSoc`, `phasesActive`. The page says its list
  is incomplete. `UNVERIFIED`: the allowed values of `phasesConfigured`, the read-back topic
  of each written value, the payload of `planStrategy`, whether loadpoint ids always start at 1.
- SG-Ready states: 1 utility block, 2 normal, 3 intensified (a recommendation to run more),
  4 forced on (a definitive command); the block is limited by the supplier rules
  (alpha-innotec knowledge base, read 2026-11-24). **Which contact combination selects which
  state is vendor-specific and `UNVERIFIED` here**; templates state it with a source.
- HA has no common EV-charger or phase-switch entity model; every integration differs
  (`UNVERIFIED` per integration until a template verifies it from source).
- Plug-started programs rely on the appliance resuming after power returns; this is
  appliance-specific and `UNVERIFIED`.

## Options considered

### 1. EV direct control

- **A – reuse `power_setpoint`**; the driver picks current and phases. Cons: the gate cannot
  see a phase switch (contactor wear, the car's pause before switching), the 6 A floor or the
  "pause" state; invariants would live in a driver.
- **B – a new action kind `charge`** with the full desired charger state. Pros: the gate
  judges current, phases and pause; mirrors ADR-0013 (full state, `None` = baseline).

### 2. evcc as actor

- **A – evcc is just another charger driver** taking `charge` actions. Cons: evcc regulates
  current itself every few seconds; two regulators fight.
- **B – a new action kind `loadpoint`** carrying what evcc accepts (mode, limits, plan). Pros:
  honest about who regulates; SunCore stays the planner and the guard of limits.

### 3. Heat pump

- **A – new action kinds** (`sg_ready`, `lift`). Cons: contract growth without new semantics.
- **B – reuse `step` and `setpoint`** with a new control variant `sg_ready` (two contacts,
  steps = states 2/3/4) and the existing `setpoint` control for the lift. Pros: invariants for
  steps and set-points exist; only the SG-specific rules are added.

### 4. Programs

- **A – a program is a switch** with a long `min_run_s`. Cons: a crash would leave the plug
  off with the laundry inside; arming and deadlines have no home.
- **B – program jobs**: an armed job (deadline, profile) is runtime state; the planner picks a
  start; the controller starts it once; the gate forbids interrupting it; the baseline of the
  plug is **on**.

### 5. Import caused by user-chosen modes

- **A – forbid** (Phase 3 rule). Cons: "Now", "Min+PV" and deadlines become impossible.
- **B – a bounded exception per device**, mirroring ADR-0013 decision 4.

## Decision

**1B, 2B, 3B, 4B, 5B.** Contract changes are additive (ADR-0011 spine, ADR-0012 policy).

1. **`ChargeAction`** (`kind: "charge"`): `current_a: float` (0 = paused, otherwise within the
   charger's `[min_current_a, max_current_a]`), `phases: Literal[1, 3] | None` (`None` = leave
   as is). Full desired state; release = the baseline captured at takeover. New control variant
   `ChargerControl` (`kind: "charger"`): roles `current` (number, A), optional `enable`
   (switch), optional `phases` (select or switch, with the option value for 1 and 3),
   `min_current_a` (default 6), `max_current_a`, `phases_supported`, `voltage_v` (230),
   `phase_switch_pause_s` (default 120, vendor-dependent, `UNVERIFIED`),
   `max_phase_switches_per_hour` (default 4). Device options `ev: EvSpec` on
   `DeviceConfig` (kind `wallbox`): vehicle capacity in Wh, charge efficiency (default 0.9),
   optional roles `vehicle_soc`, `connected`, `charging`. One vehicle per charger in v1.
2. **`LoadpointAction`** (`kind: "loadpoint"`): `mode: Literal["off", "smart", "now"]`,
   `limit_soc_pct: float | None`, `plan_soc_pct` + `plan_time` (both or neither),
   `max_current_a: float | None`. Executed by an `evcc` driver over MQTT (`devices/evcc.py`,
   using the client of ADR-0016), loadpoint id and vehicle name from the device config. SunCore
   never writes `evcc/site/*` (evcc's own battery rules stay the user's; conflicts are
   documented). Read-back from the read topics; evcc offline (no message for 120 s) =
   device watchdog trip.
3. **EV modes** are runtime state (not configuration): `off`, `now`, `min_pv`, `pv` (default),
   `target` (SoC or energy by a time). The controller maps them per tick to `ChargeAction`s
   (direct) or, on change, to `LoadpointAction`s (evcc: off→`off`, now→`now`, pv/min_pv→
   `smart`, target→`smart` + vehicle plan; the min-PV mapping to evcc is `UNVERIFIED`).
4. **Bounded import exception for flexible loads** (5B). A device intent that adds demand
   which the free surplus does not cover is accepted only if one of these holds at execution
   time, and in every case the site import limit, the peak-shaving limit and the §14a limit
   (ADR-0018) hold:
   - **user-demanded floor**: EV mode `now` (up to `max_current_a`) or `min_pv` (up to
     `min_current_a` on the current phases), a program the user started or armed with a
     deadline that has become binding (`start` band, below);
   - **plan-permitted grid power**: the fresh plan interval's new `DeviceBand.grid_power_max_w`
     (W, `None` = none) allows it, set by the planner only for EV `target` and for heat-pump
     pre-heating when `grid_preheat` is enabled (default off).
   Everything else keeps the Phase 3 rule. Battery energy is never planned into the EV unless
   `battery.allow_discharge_to_ev` (default false, BRIEF §4.4).
5. **Heat pump.** New control variant `SgReadyControl` (`kind: "sg_ready"`): roles `contact_a`
   and `contact_b` (switches), a mapping of states 2, 3, 4 to contact pairs (from the
   template, with `source`), `allow_forced: bool = False`. Written with `StepAction`
   (0 = state 2, 1 = state 3, 2 = state 4); **state 1 (utility block) is never written by
   SunCore** and has no step. Baseline and release = state 2. Set-point lift uses the existing
   `SetpointControl` on a `water_heater`, `climate` or `number` role, inside the comfort band
   (the lift band), never below the user's base. A select-based SG-Ready interface uses
   `ModeControl`.
6. **Thermal model and COP** (`core/thermal.py`): `ThermalModel` (first-order RC:
   `capacity_wh_per_k`, `loss_w_per_k`, optional `t_internal_gain_w`) and `CopCurve`
   (2–8 points `(outdoor_c, cop)`, piecewise linear, clamped at the ends). Parameters are
   learned from history (`suncore/forecast/thermal.py`) and used by the planner only when the
   fit is good enough (plan defaults); otherwise the device is planned as energy-only as in
   Phase 4. A template may ship a COP curve with a source; the generic default is an
   assumption and labelled as such.
7. **Program jobs.** `ProgramSpec` on `DeviceConfig` (kind `program`): `runtime_min`,
   `profile_wh` per 15 min (or nominal × run time), `start_method: plug | remote | notify`,
   `idle_w` (finished threshold). A job (`armed`, `scheduled`, `running`, `done`, `cancelled`,
   deadline, armed by) is runtime state in a new `flex_state` table (`suncore.db`). The
   planner picks one start slot per job (MILP, at most one binary per candidate slot) and
   marks it with the new `DeviceBand.start: bool = False`; the controller issues one
   `SwitchAction(on)` (plug), `SwitchAction(on)` on a `button`/`switch` role (remote) or a
   notification (notify). Invariants: switch-on only for an armed job; **never switch a
   running program off** (until `runtime_min` plus a margin passed and the power stayed below
   `idle_w` for 5 min); the plug's baseline is **on**, so a crash or release while waiting
   starts the program instead of stranding it.
8. **Invariants** (`safety/flex.py`, property-tested, reason keys `safety.ev_*`,
   `safety.evcc_*`, `safety.sg_*`, `safety.program_*`): the full table is in the plan (E1–E9,
   L1–L5, H1–H5, P1–P6). Until each kind's invariants, scenarios and `control-safety` sign-off
   exist, the gate rejects `charge` and `loadpoint` with `safety.charge_not_supported` /
   `safety.loadpoint_not_supported` (ADR-0011 addendum); `sg_ready` steps are rejected with
   `safety.sg_ready_not_supported` until their PR.
9. **Planner** (`planner/`, `core` only): energy demands with a deadline (EV target), one-shot
   jobs (programs), thermal storage with COP per slot (heat pump), semi-continuous minimum power
   by binaries where needed (EV 6 A floor) – each with a heuristic fallback. Budget: the
   reference house plus one EV, one heat pump and two programs solves within the 10 s budget on
   aarch64 (`UNVERIFIED` until benchmarked; binaries only in the first 24 h, LP relaxation
   beyond).

**Not decided here:** V2H/V2G (data model only, BRIEF §4.4), several vehicles per charger,
evcc's own optimiser, per-phase load balancing, OCPP directly (only through HA integrations).

## Consequences

- Positive: the gate judges every EV current, phase switch, SG-Ready state and program start;
  the import exception is as narrow as the battery's; a crash never strands laundry or locks a
  heat pump.
- Negative / risks:
  - Two new action kinds and three control variants enlarge the contract; mitigated by the
    not-supported rejection until each PR's sign-off, and by reusing `step`/`setpoint`.
  - Vendor behaviour (phase-switch pause, plug resume, SG contact mapping, evcc values) is
    `UNVERIFIED`; templates must cite sources and the gate caps a device of the new control
    variants (`charger`, `loadpoint`, `sg_ready`, program jobs) at **assisted** while its
    template's `unverified` list is non-empty and the user has not confirmed the behaviour
    (`DeviceConfig.behaviour_confirmed`, part of the identity), as ADR-0013 did for the
    fallback timer. Existing Phase 3 devices are not affected.
  - MILP binaries can blow the Pi budget; mitigated by the 24 h binary window, warm start,
    heuristic fallback and a benchmark gate.
  - `flex_state` needs a `suncore.db` migration (export before update, as in Phase 5).
- Follow-ups: 6-B contracts (`control-safety`), 6-H planner (`optimizer-engineer`), 6-I EV
  direct, 6-J evcc, 6-K heat pump, 6-L programs (each with `control-safety` sign-off).

## Recommendation to the maintainer

Accept; your input is wanted on whether heat-pump pre-heating and EV target charging may use
cheap grid energy by default (recommended: EV target yes inside the plan, heat pump no).
