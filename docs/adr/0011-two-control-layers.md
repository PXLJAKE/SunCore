# ADR-0011: Two control layers and the safety boundary

- **Status:** accepted (maintainer delegated the decision on 2026-10-12)
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

## Addendum 2026-10-16: new action kinds are rejected until their invariants exist

Context: the `Action` union in `core/decisions.py` is part of the stable
contract spine and grows additively. A new variant can appear in the
contract (so that planner, controller and storage can be built against it)
before the safety layer knows how to judge it. Without a rule, a new kind
could fall through the gate's dispatch and reach `apply()` without any
invariant having been checked.

Decision:

- Every new action kind is **rejected by the safety gate** with its own
  reason key `safety.<kind>_not_supported` (catalogued en/de like every
  `safety.*` key) until its invariants and their tests exist. The rejection
  happens before mode handling, so it also applies in shadow and assisted
  mode; nothing is written and no proposal is created.
- The invariant functions in `safety/invariants.py` handle the new kind
  explicitly (no catch-all that silently passes it).
- Removing the rejection requires: the kind's invariants in
  `safety/invariants.py` with property tests, simulator scenarios for its
  fail-safe paths, and a documented **`control-safety` sign-off** in the PR
  (invariant table). Any ADR that defines the kind's semantics must be
  accepted first.
- First case: `BatteryAction` (Phase 4, PR 4-A), rejected with
  `safety.battery_not_supported`. Its semantics are the subject of
  ADR-0013 ("Battery as a controlled asset"), which must be accepted before
  Phase 4 WP10 lifts the rejection.

Consequence: contracts can be published ahead of their write path without
weakening the single-writer guarantee; a boundary/unit test asserts that
every `ActionKind` is either handled by the invariants or rejected with its
`not_supported` reason.
