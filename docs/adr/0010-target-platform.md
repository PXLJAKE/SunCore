# ADR-0010: Target platform and architectures

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`ha-apps.md`](../research/ha-apps.md)

## Context

Apps exist only on Home Assistant OS (and the deprecated Supervised
install). HA dropped `i386`, `armhf` and `armv7` in 2025; the Supervisor
schema allows only `aarch64` and `amd64`. HA Container users cannot run
apps; BRIEF §2 asks for an architecture that allows a standalone container
later.

## Options considered

### Option A – HA OS app first, HA connection behind an interface, standalone container later

### Option B – Standalone container first, app as a thin wrapper

- Pros: reaches Container users at once.
- Cons: loses Supervisor token, ingress, services API and backups for the
  primary audience; setup friction contradicts "productive in 15 minutes".

### Option C – App only, no abstraction

- Cons: closes the door for Container users and complicates local
  development/testing without a Supervisor.

## Decision

Option A.

- Architectures: `aarch64`, `amd64`. Minimum hardware: Raspberry Pi 4 /
  HA Green, 4 GB RAM; budgets: < 300 MB RSS idle, planner < 10 s, UI
  smooth on tablets.
- `ha/` exposes `HAConnection` with two transports: `SupervisorTransport`
  (token from `SUPERVISOR_TOKEN`, URLs via `supervisor` host, MQTT
  credentials via services API, ingress headers) and `DirectTransport`
  (URL + long-lived access token, manual MQTT settings, own auth for the UI).
  Nothing outside `ha/` and `api/auth` knows which transport runs.
- The same image runs as the app and as the standalone container; the
  standalone mode is enabled by environment variables. Standalone support
  is **not** promised before 1.0 (Phase 7, optional).
- Development without HA: the simulator (`sim/`) provides a fake
  `HAConnection`, so the full stack runs in CI and in the demo mode.

## Consequences

- Positive: one image, one codebase, testable without HA.
- Negative: the standalone mode needs its own authentication for the UI –
  deferred, designed in Phase 7.
- Follow-up: Phase 1 `backend-engineer` implements both transports' skeleton
  (Direct transport used by the devcontainer against a local HA).

## Recommendation to the maintainer

Accept.
