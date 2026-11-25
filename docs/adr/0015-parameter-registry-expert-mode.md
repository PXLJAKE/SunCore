# ADR-0015: Parameter registry and expert mode

- **Status:** Accepted (2026-11-23, by delegation: the maintainer asked to proceed with the
  plan's defaults while away)
- **Date:** 2026-11-23
- **Deciders:** maintainer, lead engineer (`architect`), `control-safety`
- **Research:** plan [`phase-5.md`](../plans/phase-5.md) §0 decisions 4 and 5; related
  [ADR-0006](0006-template-and-plugin-system.md),
  [ADR-0007](0007-i18n-and-translation-platform.md),
  [ADR-0011](0011-two-control-layers.md)

## Context

Tunables are spread over Pydantic config fields, environment variables and constants
(controller tick, smoothing, hysteresis, solver budget and MIP gap, write rates). Users who
want to tune need one place with units, ranges and help text; the documentation must not drift
from the code. At the same time anything exposed must not let a user weaken the safety layer
(ADR-0011).

## Options considered

### Option A – hand-written expert page and hand-written docs

Fast, but validation, UI and documentation diverge over time.

### Option B – one `ParamSpec` registry generated from the models

A single source feeds UI, validation and docs; a test catches missing text or bad defaults.

## Decision

Option B.

1. **`ParamSpec`** has: key, config path, type, unit, default, range, tags
   (`expert|safety|restart|whatif`), help key `param.<key>.help` in the ICU catalogue and an
   optional `see` link.
2. **Generation.** The registry is built from the Pydantic models in `core` (field metadata in
   `json_schema_extra`, the mechanism of ADR-0006) plus a small explicit table for parameters
   that are not config fields today.
3. **Docs.** `scripts/gen_params.py` writes `docs/reference/parameters.md` and
   `parameters.de.md`; `--check` runs in `check.sh` and CI like `gen_compat.py`. A test fails
   when an entry has no catalogue text in both languages or a default outside its range.
4. **Expert mode never weakens the safety layer.** Parameters tagged `safety` (min run and
   pause, max switches per hour, write rates, import limit, reserve guards) are shown, but
   their range is the hard bound of the invariants in `safety/`; the registry reads the same
   constants, so there is no second copy. Changes need a confirmation dialog, are recorded in
   the config history, and take effect through the same document validation and
   `gate.handover` path as a normal device edit. Raising a device mode stays exclusively with
   `PUT /operation/...` (Phase 3). Expert mode writes configuration, never device set-points.
5. **Review.** `control-safety` reviews the PR that introduces expert mode (PR 5-E).

Not decided here: the expert UI layout and which individual parameters get the `expert` tag
(plan WPs).

## Consequences

- Positive: one source for UI, validation and docs; safety bounds cannot be tuned away;
  every change is auditable in the config history.
- Negative: the registry depends on discipline for non-config parameters (mitigated by the
  explicit table and the catalogue/range test); moving environment variables and constants
  into config is a small migration.
- Follow-ups: `backend-engineer` (registry, generator, CI check), `control-safety` (expose
  the invariant constants, review), `frontend-engineer` (expert UI), `i18n-sync` (help texts
  in both languages), `docs-writer` (reference pages).

## Recommendation to the maintainer

Accept as is.
