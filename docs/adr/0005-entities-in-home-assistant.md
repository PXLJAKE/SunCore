# ADR-0005: Exposing SunCore entities and actions in Home Assistant

- **Status:** accepted (maintainer delegated the decision on 2026-10-12)
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`mqtt-discovery.md`](../research/mqtt-discovery.md), [`ha-apps.md`](../research/ha-apps.md) (services API, discovery)

## Context

SunCore should appear in HA as entities (status, next action, savings,
forecast quality, switches for auto mode, selects for strategies, numbers
for SoC limits), actions (replan, boost, pause, force-charge), events and
notifications (BRIEF §4.13). An app cannot create entities directly; the two
routes are MQTT discovery and a custom integration (HACS, later possibly
Core).

## Options considered

### Option A – MQTT discovery only

- Pros: no second codebase, works the day the app is installed if the
  Mosquitto app is present; device-based discovery gives one device with all
  entities; `button`/`switch`/`select`/`number` give write paths; `event`
  platform gives events.
- Cons: requires a broker; no actions with parameters (boost N minutes needs
  a `number` + `button` workaround); no Repairs, Diagnostics, config flow or
  translations managed by HA.

### Option B – Companion integration only

- Pros: native actions with schemas, events, Diagnostics, Repairs,
  Supervisor discovery (`POST /discovery`), proper translations.
- Cons: second package with its own release cycle and HACS listing; users
  must install two things before anything appears in HA; Core-quality
  requirements if ever upstreamed.

### Option C – Both, staged

MQTT discovery in v1, companion integration later for what MQTT cannot do.

## Decision

Option C.

- **v1 (Phase 6):** MQTT discovery, device-based payload, origin `SunCore`,
  availability topic with last-will, discovery published on HA birth
  message. Entities: sensors (mode, next action, plan cost, savings today,
  forecast error), binary sensors (healthy, shadow mode), switches (global
  auto, per-device auto), selects (battery strategy, wallbox mode), numbers
  (min SoC, target SoC, comfort bounds), buttons (replan, emergency normal
  mode), events (cheap window starts, device switched). Credentials from the
  Supervisor services API; manual settings for the standalone container.
- **The UI never depends on MQTT.** Without a broker SunCore works fully
  through its own panel and shows a hint how to enable HA entities.
- **Companion integration (Phase 7, separate repository `suncore-ha`):**
  config flow that discovers the app via Supervisor discovery or URL +
  token, native actions with parameters, events, Diagnostics, Repairs.
  When installed, it supersedes the MQTT entities (same unique ids are not
  possible across integrations, so the app offers "migrate" by removing the
  MQTT discovery topics).

## Consequences

- Positive: fast time to value, no second package in v1; a clean path to
  native actions later.
- Negative: two write paths to maintain eventually – mitigated by the API
  being the single source of truth; MQTT and the integration are both thin
  clients of it.
- Follow-up: Phase 6 `ha-integration` implements MQTT discovery; Phase 7
  starts the companion repository.

## Recommendation to the maintainer

Accept.
