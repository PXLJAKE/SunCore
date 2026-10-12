# ADR-0003: Optimiser and solver

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`solver-on-aarch64.md`](../research/solver-on-aarch64.md), [`competitors.md`](../research/competitors.md) (EMHASS model)

## Context

The planner minimises cost over 24–48 h in 15-minute steps with a battery,
grid limits, export limits, deferrable and thermal loads, target states and
switching costs (BRIEF §4.8). Some constraints are integer (minimum run
time, semi-continuous power, 1/3-phase switching). The planner must finish
in < 10 s on a Raspberry Pi 4 and fall back gracefully.

## Options considered

### Option A – HiGHS via `highspy`

- Pros: LP and MILP in one MIT-licensed library; wheels for aarch64 and
  musl; warm start, time limit, MIP gap; small dependency.
- Cons: modelling is done by hand (building sparse matrices) or through a
  thin modelling layer we write.

### Option B – SciPy `milp`/`linprog(method="highs")`

- Pros: same solver, SciPy may be needed later anyway.
- Cons: SciPy is ~40 MB, no warm start or incremental model API, no
  musllinux wheels verified.

### Option C – PuLP or Pyomo + solver binary

- Pros: readable algebraic modelling.
- Cons: extra layer, PuLP's bundled CBC is not available for aarch64/musl;
  Pyomo is heavy.

### Option D – OR-Tools

- Pros: strong MIP (CP-SAT).
- Cons: large wheels, heavier API, Apache-2.0 fine but overkill.

## Decision

Option A. A small internal modelling layer (`planner/model.py`) builds the
problem as sparse matrices from typed Pydantic inputs; the solver interface
is a Protocol so SciPy or another backend can be swapped in tests.

Modelling rules:

- Continuous LP by default; binaries only where a device needs them
  (minimum run/pause time, semi-continuous power, phase switching). The
  planner reports which binaries were active so the UI can explain long
  solves.
- Time budget: configurable, default 8 s, MIP gap 1 %; on timeout the best
  incumbent is used if feasible, else the heuristic fallback runs.
- Heuristic fallback: rule-based greedy allocation over price-sorted
  intervals (battery arbitrage threshold, deferrables into cheapest valid
  windows, surplus to priority order). Always available, also used in the
  demo mode when the solver is not installed.
- Warm start from the previous plan shifted by one interval.
- Degradation cost, comfort penalties and switching costs are linear terms
  with weights exposed in expert mode (BRIEF §4.12).
- Uncertainty: conservative planning uses P10 PV in the constraints that
  protect targets (e.g. "battery full by evening") and P50 in the objective;
  no stochastic programming in v1.

Not decided here: the exact device sub-models (Phase 4 design notes per
device type).

## Consequences

- Positive: lean dependency, fast LP, MILP available when needed.
- Negative: hand-built matrices are error-prone – mitigated by
  property-based tests (energy balance holds, SoC bounds hold, objective
  never worse than the heuristic) and a model dump in debug views.
- Follow-up: Phase 4 `optimizer-engineer` implements and benchmarks on
  aarch64 with the maintainer's scenario; results recorded in STATUS.

## Recommendation to the maintainer

Accept.
