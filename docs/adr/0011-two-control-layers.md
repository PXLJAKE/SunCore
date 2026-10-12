# ADR-0011: Two control layers and the safety boundary

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`competitors.md`](../research/competitors.md), BRIEF §3, §5

## Context

evcc is a real-time controller without a planner; EMHASS and Predbat are
planners without a real-time surplus controller. SunCore needs both, plus a
safety layer that no code path can bypass (BRIEF §3, §5).

## Options considered

### Option A – Planner → Controller → Safety → Actuators, with explicit contracts

### Option B – Single loop that re-solves the optimisation every few seconds

- Cons: solver latency and cost on a Pi; chattering; no clear explanation
  per decision.

### Option C – Planner writes set-points directly, controller only for surplus devices

- Cons: two writers to the same hardware; safety invariants split across
  modules.

## Decision

Option A.

- **Planner** (every 15 min, and on events: new prices, forecast update,
  deviation above threshold, config change, user override) produces a
  `Plan`: per interval and per asset a set-point or band (battery target
  power range and SoC corridor, device allowed/preferred state, grid
  limits) plus a `Reason` list (structured, translatable) and the plan's
  assumptions (forecasts used, prices, mode).
- **Controller** (every 5–10 s, configurable) reads live signals, computes
  available surplus, and issues `Intent`s per device within the plan's bands,
  applying hysteresis, minimum run/pause times, ramps and switch-rate
  limits. It may deviate from the plan when reality deviates (clouds, load
  spikes) and records why.
- **Safety** is the only module that may call an actuator. It validates
  every `Intent` against invariants (grid limit, battery reserve, comfort
  bounds, max switches/hour, rate limits, plausibility of inputs, operating
  mode: shadow/assisted/automatic, user overrides), rewrites or rejects it,
  and runs watchdogs (stale data → safe state, heartbeat to inverters,
  connection loss → release control). Rejections are logged with the
  invariant that fired.
- **Actuators** (device drivers) are dumb: write, read back, report.
- **Shadow mode** is implemented in Safety: intents are logged as
  "would have done" and never written. New devices start in shadow mode.
- Contracts are typed (`Plan`, `Intent`, `Decision`) and serialised to the
  decision log; the UI's "Why?" reads them.

## Consequences

- Positive: single writer, testable invariants (property-based tests on
  Safety), explanations fall out of the data model.
- Negative: three modules must agree on units and sign conventions –
  fixed in `core/` (W, Wh, positive = consumption/import, battery positive
  = charging) and enforced by types.
- Follow-up: Phase 3 `control-safety` implements Controller and Safety;
  Phase 4 `optimizer-engineer` implements the Planner against the `Plan`
  contract defined in Phase 3.

## Recommendation to the maintainer

Accept. Note the sign convention (battery positive = charging) – it is the
opposite of the Kostal HA integration, conversion happens in the adapter.
