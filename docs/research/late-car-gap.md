# Late-car gap: root cause and fix (Phase 7, WP9)

Status: root cause found in step 1, fixed in step 2 (7-K, planner only; see "Fix" at the end).
Reproducer and regression: `tests/sim/test_late_car_gap.py`.

## Symptom

The scenario `ev_arrives_late` (30 % to 80 % of 60 kWh by 07:00, cheap hours 01:00-05:00) ends at
77.35 %. No plan reports a shortfall, and the controller never rejects or fails a write. The
missing 2.65 percentage points are 1766 Wh drawn from the grid (1590 Wh stored at 90 %).

## Cause

**Every plan books the whole first quarter-hour slot, although most of it has passed when the
plan is made.** The planner builds its grid from the quarter that contains `now` and gives that
slot its full length (`dt = 0.25 h`). It does not shorten the slot to the time that is left, and
it does not mark the slot unavailable once it has begun. Confirmed in step 2: the grid comes
from `core.series.planning_grid` (floor of `now`, called by `planner.inputs.build_inputs`), and
every energy term used the uniform `PlannerInputs.dt_h`; `flex.py` only sets powers per slot.

In the harness the plan is made 14.5 minutes into its first slot (870 s; 30 steps of 30 s,
phase 30 s before each quarter boundary). The slot offers `11040 W * 0.25 h = 2760 Wh` on paper
and about 92 Wh in reality. Each plan therefore books 2668 Wh that cannot be delivered, and the
later slots receive 2760 Wh less than the plan needs. The controller follows the plan faithfully
(`controller.ev.target_grid`, then `controller.deficit_budget` on a smaller band), so the car
receives what the future slots held. At the end the remaining 1766 Wh sit entirely in the first
slot of every later plan (04:29 until 06:44), each a slot that is already over. The plan counts
the need as covered, so there is no shortfall and no "EV target at risk" notification.

The effect is cumulative only in the last hour: while enough cheap slots lie ahead, each new plan
re-derives the need from the live SoC and the error is absorbed; once the cheap window runs out
(04:15) the phantom slot holds the whole remainder.

## Evidence

Per plan (harness cadence), SoC rebuilt from the charger trace, need = `(80 % - SoC) * 60 kWh /
0.9`. `slot 0 booked` is the grid band of the first interval times 0.25 h; `possible` is what
the charger can still deliver in it.

| Plan made | SoC % | Need Wh | Slot 0 booked Wh | Slot 0 possible Wh | Later slots Wh |
|-----------|------:|--------:|-----------------:|-------------------:|---------------:|
| 02:14:30  | 50.42 | 19717   | 2760             | 92                 | 16865          |
| 03:14:30  | 65.69 |  9540   | 2760             | 92                 |  6688          |
| 03:44:30  | 73.97 |  4020   | 2760             | 92                 |  1168          |
| 03:59:30  | 75.70 |  2870   | 2760             | 92                 |  1035          |
| 04:14:30  | 77.25 |  1835   | 1800             | 60                 |     0          |
| 04:29:30  | 77.35 |  1766   | 1766             | 59                 |     0          |
| 06:44:30  | 77.35 |  1766   | 1766             | 59                 |     0          |

Booked slot 0 plus later slots add up to the need (for example 2760 + 6688 = 9448 against 9540
at 03:14, the difference is the P10 surplus), so the planner's own arithmetic is consistent. It
is only the first slot that is fictitious.

Candidates checked:

| Candidate | Result | Numbers |
|-----------|--------|---------|
| Charging losses, planner vs sim | ruled out | both 0.9; stored = drawn * 0.9 to 1e-6 |
| Capacity, origin of target | ruled out | 60 kWh in sim and spec; target 80 %, start 30 % |
| 6 A floor | not causal | floor 4140 W used at 03:45-04:15 only as the plan's band |
| Phase pauses | not causal | one phase configuration, no switch decisions |
| Band hysteresis | not causal | writes at 01:00, 02:15, 02:30, 03:45, 04:15 follow the band |
| Plan refresh timing | **cause** | plan 870 s late; fresh plans (below) reach 81.13 % |

Control run with the planner ticked every step, so that the service's own scheduler fires at
quarter plus 20 s (`PlanningSettings.offset_s`, first slot 98 % intact): final SoC 81.13 %,
34086 Wh drawn, no rejected writes. The same controller, gate, battery lock and prices meet the
target, so the controller is not the origin.

## Which module

- Primary: **planner** (`suncore/planner`, probably the grid start in `inputs.py` and the EV
  availability in `flex.py`; to be confirmed in step 2). It books energy for time that has passed.
- Secondary: **sim harness** (`tests/sim/test_ev_scenarios.py`): `PLAN_EVERY_STEPS = 30` ticks
  the planner at the wrong phase, 870 s into every slot. The production scheduler does not do
  that, so the shipped behaviour is much closer to the control run. The planner defect still
  matters whenever a plan is late: a solver run of a few minutes on a small device, a busy
  event loop, a retry after a failed forecast fetch. The error then is `lateness / 15 min` of a
  full slot of charger power, here 2760 Wh at 11 kW.
- Not the controller. No change in `suncore/controller` or `suncore/safety` is needed.

## Fix proposal

1. Planner: scale the first slot to what is left. In the EV availability (`flex.py`, the
   `available` list and `max_w`/`grid_max_w`) cap slot 0 at `max_w * remaining / dt` and drop it
   when `remaining` is below the minimum run time or one controller tick plus settle time. The
   same cut applies to the battery and other flexible loads that use slot 0, so do it where the
   grid is built (`inputs.py`: `starts[0]` with `now`), with a per-slot `fraction` array that
   `flexmodel.py` multiplies into every `dt`. Add a hypothesis property: energy booked in slot 0
   never exceeds `max_w * (slot_end - now)`.
2. Harness: tick the planner every step (`PLAN_EVERY_STEPS = 1`); the service decides itself
   when a run is due. Keep one scenario with a deliberately late plan (14.5 min) as the
   regression test for item 1.
3. Raise the assertion in `test_the_late_car_reaches_its_target_in_the_cheap_hours` from
   `soc_pct >= 75.0` to `>= 79.0` (within 1 point of the target) and remove the `xfail` of
   `test_the_late_car_reaches_80_percent_at_the_harness_cadence`.
4. Fix the 6-I/6-O wording ("the 6-H planner stops permitting grid power at about 77 %"): the
   planner does not stop on purpose, it books a dead slot.

Acceptance after the fix: SoC within 1 point of 80 % at the harness cadence and at 14.5 minutes
lateness, no G1 or E1-E9 violation, and if the target is infeasible the existing "EV target at
risk" rule fires.

## Second notification rule

Not needed for this cause. The notification reads the plan's shortfall, and the shortfall was
hidden by the booking of the elapsed slot. With item 1 the residual moves to a real slot or
becomes a shortfall, and the existing rule (6-O, 600 s hold, once per target) fires. A rule for
"plan meets, controller misses" would need a different cause; this scenario provides none (the
controller delivered 100 % of every band it was given, and the control run has no gap). Revisit
only if a scenario shows a band that the controller does not deliver.

## Fix (step 2, 7-K)

Where: the planner only (`suncore/planner/`). Nothing in `suncore/controller/` or
`suncore/safety/` changed.

- `planner/slots.py`: `slot_hours(now, starts, step)` gives the usable hours per slot, the first
  slot `starts[0] + step - now`, every later slot the full step. `PlannerInputs.hours` carries
  them (derived from `now` in `__post_init__`, so `dataclasses.replace(inputs, now=...)`
  recomputes them).
- Every energy and cost term of a slot is `power * hours[t]`: the LP (import, export,
  curtailment, device value, degradation, battery SoC dynamics, the device budget, the EV
  target and total rows, the EV floor fill of modes `now`/`min_pv`, the import-limit slack, the
  thermal comfort penalty), the heuristic, the warm-start repair, the feasibility check, the
  "why" facts and the morning-buffer simulation. Powers (bands, limits, flows, the import and
  export limits per slot) stay powers.
- Thermal loads: `alpha`, `heat_k`, `drift` and the thermostat base power are computed per slot
  with the slot's usable length (`PlannerThermal.alpha` is now a tuple).
- Programs: a start in slot 0 means "start now". That start's profile is resampled onto the grid
  from `now` (`PlannerJob.now_profile_w`, `flex.profile_from`), and a running job's remainder is
  resampled from its real start, exact in energy (before, the remainder was cut at the quarter
  boundary).
- Negligible remainder: the slot stays in the plan (the controller needs an interval that
  covers `now`; dropping it would leave the controller without a band for that moment). A
  remainder below `FIRST_SLOT_MIN` (1 s) counts as 1 s so that no energy coefficient falls under
  the solver's matrix tolerance; at most one second of full power is booked too much.

Harness: `tests/sim/test_ev_scenarios.py` ticks the planner every step (`PLAN_EVERY_STEPS = 1`,
`PLAN_OFFSET_STEPS = 0`; the service fires at quarter + 20 s). The late cadence stays as the
regression `test_late_plans_still_bring_the_car_to_its_target` (`LATE_PLAN_EVERY_STEPS = 30`,
870 s into every first slot).

Results:

| Run | Final SoC | Drawn | Rejected / failed |
|-----|----------:|------:|------------------:|
| Service cadence (quarter + 20 s) | 81.15 % | 34098 Wh | 0 / 0 |
| Late plans (870 s into the slot), before the fix | 77.35 % | 31567 Wh | 0 / 0 |
| Late plans (870 s into the slot), after the fix | 79.94 % | 33293 Wh | 0 / 0 |

Tests:

- Property (`tests/planner/test_flex_properties.py`): for random situations planned 0 to 898 s
  into the quarter, the energy booked in slot 0 is at most `max_w * remaining hours` for the EV
  and the battery, in the LP and the heuristic; both stay feasible, the LP is never worse than
  the heuristic. A remainder of 1 microsecond solves and books almost nothing.
- Notification (`tests/sim/test_notify_scenarios.py`): a target that needs 5 kWh within the first
  two quarters is met by a plan made at quarter + 20 s, and reported as a shortfall and announced
  once ("EV target at risk") by a plan made 14.5 minutes into the quarter; with the old
  accounting the late plan reported the target as met.

Residual: at the late cadence the last 0.06 points stay open. The 6 A floor (4140 W) makes a
remainder of less than a quarter at the floor (1035 Wh) unplaceable in a whole slot when the
vehicle's total caps the energy, so the plan books it in the 30 s left of slot 0, which the
controller does not start for. At the service cadence the first slot has 14 min 40 s, and the
target is met (81.15 %). Bounded by `max_w * 30 s` (92 Wh at 11 kW) per late plan.
