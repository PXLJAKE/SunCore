# ADR-0006: Template and plugin system

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`integrations.md`](../research/integrations.md), [`competitors.md`](../research/competitors.md) (evcc templates)

## Context

Device, tariff, forecast and battery adapters as well as region profiles
must be contributable without touching the core (BRIEF §1, §7). evcc shows
that declarative YAML templates scale to hundreds of devices. Some adapters
need code (Modbus register maps with scaling, API clients).

## Options considered

### Option A – YAML templates validated by JSON Schema, Python plugins later

Templates describe *what* (entities to map, capabilities, defaults,
constraints, register maps); a small set of generic drivers in the core
interpret them. Code plugins only when a template cannot express the
behaviour.

### Option B – Python plugins only (entry points)

- Pros: maximum flexibility.
- Cons: every contribution is code with security and review cost; no
  schema-driven UI for parameters.

### Option C – Templates as Python dataclasses in the repo

- Pros: typed.
- Cons: contributors must write Python; no runtime loading of user templates.

## Decision

Option A.

- Template kinds: `device` (climate, heat pump, water heater, wallbox,
  switch, program, generic), `battery` (control adapter), `tariff`
  (import/export sources and price formula), `forecast` (PV/load/price
  sources), `region` (regulatory and market defaults, with source links).
- Location: `templates/<kind>/<vendor>.<model>.yaml` in the repo; user
  templates under `/data/templates/` override by id. Each template has
  `id`, `kind`, `schema_version`, `name` (translatable), `source` (docs it
  is based on), `tested_with` (integration versions) and `capabilities`.
- JSON Schemas are generated from the Pydantic models of each kind
  (`scripts/gen_schemas.py`) into `templates/schema/*.json` and used by CI,
  by the editor (YAML language server header) and by the UI's template
  editor.
- Generic drivers in the core: HA-entity driver (`climate`, `switch`,
  `number`, `select`, actions), Modbus-register driver (via HA Modbus
  integration first, direct `pymodbus` only when an ADR approves it), MQTT
  driver (for evcc as actor).
- Every template is exercised in CI against the simulator with a
  per-kind conformance scenario (BRIEF §7).
- Python plugins (Phase 7 or when the first real need appears): entry point
  group `suncore.plugins`, versioned Protocol classes
  (`suncore.plugins.v1`), loaded only from the image or `/data/plugins`
  with an explicit allow switch; not in v1 scope.
- Device compatibility list in the docs is generated from the templates.

## Consequences

- Positive: contributors add a YAML file plus a test scenario; schema gives
  validation and UI for free.
- Negative: template expressiveness limits must be found in practice –
  mitigated by designing the first five templates (LG ThinQ climate, generic
  switch, Kostal battery, Tibber tariff, Solcast forecast) in Phase 2/4
  before freezing `schema_version: 1`.
- Follow-up: Phase 2 `device-adapters` defines the Pydantic models and the
  first templates; `architect` reviews the schema before it is frozen.

## Recommendation to the maintainer

Accept.
