# Phase 1 – App skeleton that shows live data

Status: **draft, awaiting maintainer approval**
Inputs: [`BRIEF.md`](../BRIEF.md) §2, §3, §4.1, §4.11, §5; ADR-0001, 0002,
0004, 0005, 0007, 0009, 0010, 0011; [`architecture.md`](../architecture.md);
[`roadmap.md`](../roadmap.md) Phase 1; [`STATUS.md`](../STATUS.md).

Goal: an installable beta app that connects to Home Assistant through the
Supervisor, shows live energy flows for hand-picked entities in the "Now"
view, and runs in shadow mode with nothing written anywhere. No planner, no
controller logic, no device drivers – only the plumbing they will plug into.

## 1. Acceptance criteria (from the roadmap, made testable)

1. `ghcr.io/pxljake/suncore-beta:<version>` exists for amd64 and aarch64,
   Cosign-signed, and the beta app installs from this repository on HA OS.
2. The app starts under s6, `/health` answers within 30 s and reports HA
   connection state, last entity update age and version; the Supervisor
   health check uses it.
3. The HA connector authenticates with the Supervisor token, subscribes to
   configured entities with `subscribe_entities`, tracks staleness per
   entity, reconnects with exponential back-off and survives an HA restart
   without a container restart.
4. A minimal settings page lets the maintainer pick grid, PV, battery power
   and SoC entities (with sign inversion toggles); the config is stored in
   SQLite with a schema version and can be exported/imported as JSON.
5. The "Now" view shows the energy flow (grid, PV, battery, house) from
   those entities, updates live over WebSocket, works under the ingress
   base path, in light and dark theme, on phone and tablet widths.
6. Safety module exists with `OperatingMode = shadow` and is the only
   module that can call a driver; there are no drivers yet, so the only
   path writes a "would have" decision to the decision log.
7. Simulator base: a fake `HAConnection` replays a scenario file so the
   whole stack runs in CI and in the demo mode (`SUNCORE_DEMO=1`) without
   HA; Playwright E2E loads the Now view against the demo mode.
8. i18n plumbing: ICU catalogues `en`/`de` in frontend and backend, lint
   rule against hardcoded UI strings, `i18n-sync` reports zero gaps.
9. Budgets measured in CI (amd64) and recorded in STATUS: RSS after 60 s
   idle, image size; aarch64 figures from the maintainer's device.
10. CI green; coverage gate for `core` and `safety` ≥ 80 % enabled.

## 2. Work packages

### WP1 – Application bootstrap and packaging (`ha-integration`, `backend-engineer`)

- `suncore/app/`: settings (`pydantic-settings`, env + app options from
  `/data/options.json`), structured logging (structlog, JSON when not a
  TTY, secrets filter), lifecycle (start/stop with ordered tasks), health
  model.
- `suncore/__main__.py`: `run` (default) and `--version`; exit codes.
- `addon/*/rootfs/etc/s6-overlay/s6-rc.d/suncore/` run script with bashio
  (`init: false` keeps s6 from the base image); `HEALTHCHECK` on `/health`.
- `uv.lock` grows: fastapi, uvicorn, aiohttp, aiosqlite, structlog,
  pydantic-settings, numpy (only if needed by WP5; otherwise Phase 3).
- Image smoke test in `build.yml`: start container with `SUNCORE_DEMO=1`,
  poll `/health`, record RSS and image size as job summary.
- First beta release: `addon/suncore_beta/config.yaml` version
  `0.2.0-beta.1`, images published from `main`.

Files: `suncore/app/*`, `suncore/__main__.py`, `addon/*/rootfs/**`,
`addon/*/Dockerfile`, `.github/workflows/build.yml`, `pyproject.toml`.

### WP2 – HA connector (`backend-engineer`)

- `suncore/ha/connection.py`: `HAConnection` with `SupervisorTransport`
  (URL `ws://supervisor/core/websocket`, token from `SUPERVISOR_TOKEN`) and
  `DirectTransport` (URL + long-lived token; used by the devcontainer).
- Auth flow, message ids, `supported_features: coalesce_messages`,
  ping/pong heartbeat, reconnect with jittered back-off, `subscribe_entities`
  with the compressed diff format (`a`/`r`/`c`, `+`/`-`), `get_states`,
  `get_config` (time zone, unit system, currency), `call_service` with
  `return_response` (used read-only in Phase 1 for `weather.get_forecasts`
  smoke), registry list commands – **verify names first** (`verify-ha-api`
  skill; STATUS lists them as `UNVERIFIED`).
- `suncore/ha/cache.py`: entity cache with `last_updated`, staleness per
  entity (configurable max age), unit normalisation (W/kW, Wh/kWh),
  `unavailable`/`unknown` handling, sign inversion.
- `suncore/ha/supervisor.py`: `/addons/self/info` (ingress entry), services
  API for MQTT (read only, no publishing yet).
- Tests with a fake WebSocket server (aiohttp test utilities): auth,
  subscribe, diff application, reconnect, staleness.

Files: `suncore/ha/*`, `tests/ha/*`.

### WP3 – Storage (`backend-engineer`)

- `suncore/storage/db.py`: aiosqlite, WAL, two databases (`suncore.db`,
  `timeseries.db`), migration runner with ordered SQL files, `schema_version`
  table.
- Config repository: sections as JSON documents (`site`, `ui`), export and
  import with version header and validation.
- Decision log repository (append, query by time/asset/kind).
- Time series: `SAMPLE` table only (1-minute and 15-minute aggregates come
  with Phase 3), retention job skeleton.
- Tests: migrations from empty and from v1 fixture, export/import round
  trip, concurrent read while writing.

Files: `suncore/storage/*`, `tests/storage/*`.

### WP4 – Core model and safety skeleton (`control-safety`, `architect` review)

- `suncore/core/`: `units.py` (W, Wh, sign conventions, conversions),
  `signals.py` (`Signal` with value, unit, quality, timestamp), `assets.py`
  (`Site`, grid/pv/battery/house assets, entity mapping), `contracts.py`
  (`Plan`, `Intent`, `Decision`, `Reason` – typed, serialisable; full
  semantics land in Phase 3/4 but the shapes are frozen here with the
  architect).
- `suncore/safety/`: `OperatingMode` (shadow/assisted/automatic, global and
  per asset), `SafetyGate.apply(intent)` that logs a shadow `Decision`;
  `Driver` protocol; a lint test asserting no module outside `safety/`
  references `Driver.apply`.
- Property-based tests for unit conversions and sign handling;
  `mypy --strict` on both packages; coverage gate ≥ 80 % for both.

Files: `suncore/core/*`, `suncore/safety/*`, `tests/core/*`, `tests/safety/*`.

### WP5 – Simulator base and demo mode (`qa-engineer`)

- `suncore/sim/`: scenario file format (YAML: clock start, step, series for
  grid/PV/battery/house, optional events like `unavailable` windows),
  `FakeHAConnection` that emits compressed entity events from a scenario,
  deterministic virtual clock.
- Scenarios: `summer-day`, `sensor-dropout`, `cloud-flicker` (data only;
  controller tests come in Phase 3).
- Demo mode: `SUNCORE_DEMO=1` selects the fake connection and a demo site
  config; `SUNCORE_DEMO_CLOCK` pins the clock for screenshots.

Files: `suncore/sim/*`, `tests/scenarios/*`, `tests/sim/*`.

### WP6 – API (`backend-engineer`)

- FastAPI app under the ingress base path (all routes relative; reads
  `X-Ingress-Path` only to build absolute links in responses if ever
  needed), ingress source-IP check (`172.30.32.2`) in Supervisor mode,
  `X-Remote-User-*` passed into request context.
- Routes: `GET /health`, `GET /api/site`, `PUT /api/site` (entity mapping),
  `GET /api/entities?domain=sensor` (candidates with unit/device class for
  the picker), `GET/POST /api/config/export|import`, `GET /api/decisions`,
  `WS /api/live` (signals + health + latest decisions).
- OpenAPI exported to `docs/user/en/reference/openapi.json` by a script;
  CI checks it is current.
- Optional API port with token: **not** in Phase 1 (ingress only).

Files: `suncore/api/*`, `tests/api/*`, `scripts/export_openapi.py`.

### WP7 – Frontend scaffold and Now view (`frontend-engineer`)

- `frontend/`: Svelte 5 + Vite + TypeScript strict, ESLint + Prettier,
  Vitest, Playwright; `base: './'` and runtime base-path detection for
  ingress; build output copied into the image at `suncore/api/static/`.
- Theme tokens mapped to HA CSS variable names; light/dark via
  `prefers-color-scheme`; HA theme bridge spike (ingress iframe) –
  result recorded in STATUS (resolves one `UNVERIFIED`).
- Views: `/` Now (energy flow with four nodes and animated flows, health
  banner, shadow-mode badge), `/settings` (entity picker with live value
  preview and sign toggles, export/import), `/diagnostics` (health, entity
  staleness table, version, anonymised export button).
- i18n: `intl-messageformat` wrapper `t()`, `locales/en.json`, `de.json`,
  ESLint rule against string literals in markup.
- Accessibility pass (keyboard, ARIA, contrast) and viewport tests at
  390×844 and 1024×768.

Files: `frontend/**`, `suncore/api/static/` (build artefact, git-ignored),
Dockerfile multi-stage node build.

### WP8 – i18n backend plumbing (`backend-engineer`, `i18n-sync`)

- `suncore/locales/en.json`, `de.json`; `Reason` rendering helper for API
  responses (ICU via a Python library chosen in this WP – spike `babel` +
  a small ICU parser vs. existing packages; decision recorded in STATUS).
- `scripts/i18n_extract.py`: scans frontend and backend for keys, compares
  catalogues, exits non-zero on gaps; wired into `scripts/check.sh` and CI.

### WP9 – Docs and release (`docs-writer`, `ha-integration`)

- `docs/user/en/quickstart.md` (install beta, pick entities, read the Now
  view), `addon/*/DOCS.md` updated, screenshots from the demo mode via the
  `demo-screenshots` skill.
- `docs/STATUS.md`, roadmap status, ADR status touches (none expected).
- First beta release through release-please; `prepare-release` skill run.

## 3. Pull requests (one topic each, in this order)

| PR | Content | Depends on |
|---|---|---|
| 1 | WP1 bootstrap + s6 + health + smoke test in build.yml | – |
| 2 | WP4 core model + safety skeleton | – |
| 3 | WP3 storage | 2 |
| 4 | WP2 HA connector + cache | 2 |
| 5 | WP5 simulator + demo mode | 2, 4 |
| 6 | WP6 API + live WebSocket | 1, 3, 4, 5 |
| 7 | WP7 frontend scaffold + Now/Settings/Diagnostics | 6 |
| 8 | WP8 i18n plumbing + lint | 7 |
| 9 | WP9 docs + first beta release | all |

PR 1 and PR 2 can run in parallel; everything else is sequential. Each PR
carries its own tests, the German description, and – for PR 2 – the
`control-safety` review note.

## 4. Model use

- Fable: WP4 (contracts and safety skeleton – they are the spine),
  architect review of the contract shapes, final `reviewer` pass on PR 6
  and PR 7.
- Sonnet: WP1, WP2, WP3, WP5, WP6, WP7, WP8, WP9.
- Haiku: `i18n-sync` runs, catalogue bookkeeping.
- No fan-out beyond two subagents at a time.

## 5. Verification before "done"

- `./scripts/check.sh` and `npm test` green locally and in CI; coverage
  gate active for `core` and `safety`.
- Image smoke test green; RSS and size numbers in STATUS.
- Maintainer installs the beta app on HA OS, picks the Kostal/KSEM entities
  from the HA integration, sees live flows, and confirms the sign checks
  match reality (grid import positive, battery charging positive).
- `UNVERIFIED` items resolved or re-listed: registry command names, ingress
  theme bridge, Python ICU library.

## 6. Risks

| Risk | Mitigation |
|---|---|
| `ubuntu-24.04-arm` runners unavailable for the private repo → no aarch64 image | fall back to QEMU on amd64 for the beta channel; documented in build.yml |
| Ingress iframe does not expose HA theme variables | `prefers-color-scheme` default plus a user theme toggle; bridge later |
| No Python ICU MessageFormat library with aarch64/musl wheels | fall back to a minimal in-repo ICU subset (plural/select/number) – explicitly scoped |
| Entity picker needs registry commands that are `UNVERIFIED` | verify first (Core `components/config/*.py`); fall back to `get_states` attributes |
| RSS budget missed on Pi 4 | measure early (PR 1), trim dependencies (no pandas/scipy) |

## 7. Open questions for the maintainer

1. Beta version for the first release: **`0.2.0-beta.1`** (0.1.0 is the
   Phase 0 skeleton)?
2. Can you run the beta app on your HA OS and report RSS (Supervisor shows
   it per app) and whether the ingress panel follows your HA theme?
3. Is `RELEASE_PLEASE_TOKEN` in place before PR 9, or should the first beta
   be built via `workflow_dispatch`?
4. Devcontainer with a local HA for `DirectTransport` development: wanted
   in Phase 1 (adds a day) or later?

## 8. Out of scope

Setup wizard beyond the minimal entity picker (Phase 2), device model and
templates (Phase 2), controller and real safety invariants (Phase 3),
forecasts/tariffs/planner (Phase 4), MQTT discovery (Phase 6), standalone
container auth (Phase 7).
