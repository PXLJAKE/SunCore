# Phase 1 – App skeleton that shows live data

Status: **draft, awaiting maintainer approval**
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §2, §3, §4.9 ("Now"), §4.11
(shadow mode), §4.14, §5, §7, §8, §10; [`docs/roadmap.md`](../roadmap.md)
Phase 1; ADR-0001, 0002, 0004, 0007, 0009, 0010, 0011.
Branch: `phase-1/app-skeleton`.

Phase 1 turns the empty scaffold into an installable app: it connects to
Home Assistant, shows the "Now" view with live energy flows in the ingress
panel and runs in shadow mode. It contains no control logic, no planner,
no wizard – only the plumbing every later phase builds on.

## 1. Goals and acceptance criteria

Phase 1 is done when (roadmap Phase 1 milestones):

1. **Image:** multi-arch images (amd64, aarch64) are built by
   `build.yml`, pushed to GHCR on `main` (beta) and on release (stable),
   Cosign-signed; the PR build runs a smoke test that starts the container
   and polls `/health`.
2. **HA connector:** authenticates via the Supervisor token
   (`SupervisorTransport`) or URL + long-lived token (`DirectTransport`),
   subscribes with `subscribe_entities`, tracks staleness per entity,
   reconnects with exponential back-off and jitter; `/health` reflects the
   connection state. The token never appears in logs.
3. **Storage:** `/data/suncore.db` config store with versioned documents,
   ordered migrations, export/import as one JSON file.
4. **Simulator base:** a virtual house (PV, base load, battery, grid
   balance) provides a fake `HAConnection`; the full stack runs in CI and
   in demo mode (`demo_mode` option / `SUNCORE_DEMO=1`) without HA.
5. **Frontend:** Svelte 5 + Vite + TS app served by FastAPI under the
   ingress base path, light/dark theme, "Now" view with an animated energy
   flow (PV, grid, battery, house) from the configured entities, live via
   WebSocket, plus a minimal settings page to pick grid/PV/battery entities.
6. **Shadow-mode skeleton:** `core` contracts (`Intent`, `Decision`,
   `Reason`, `OperatingMode`) and a `safety` gate exist; the actuator
   protocol is only reachable through the gate; in Phase 1 the gate is
   hard-wired to shadow mode and writes nothing.
7. **i18n:** ICU catalogues `en`/`de` in the frontend, `intl-messageformat`,
   lint rule against hardcoded UI strings, app-option translations complete.
8. **Budget:** idle RSS < 300 MB measured by the smoke test (amd64 in PRs,
   aarch64 on `main`, see risk R4).
9. CI green (ruff, mypy, pytest with coverage ≥ 80 % on `core` and
   `safety`, vitest, eslint/prettier, Playwright E2E against demo mode,
   app linter, docs lint, image build).

Exit (roadmap): the maintainer installs the beta app, picks grid/PV/battery
entities by hand in the settings page and sees live flows.

## 2. Work packages

Owner = subagent that implements it (CLAUDE.md budget rule: Fable only for
controller/safety and the final review; everything else Sonnet/Haiku).

### WP1 – Core types and signals · `backend-engineer`

- `suncore/core/units.py` – unit enum (W, kW, Wh, kWh, %), conversion to SI
  (W, Wh), sign convention helpers (grid + = import, battery + = charging,
  PV + = generation).
- `suncore/core/signals.py` – `SignalId`, `Sample` (value, unit, ts,
  quality ∈ ok/stale/unavailable/invalid), `SignalSnapshot`.
- `suncore/core/site.py` – minimal `SiteConfig` for Phase 1: grid power
  (one signed sensor or import/export pair), PV power (list), battery power
  and SoC, optional house load, per-entity `invert_sign` flag. Pydantic v2,
  `mypy --strict`.
- `suncore/core/balance.py` – derives house load from the balance when no
  house sensor is configured; derived values carry the worst input quality.
- Tests: unit conversion, sign handling, balance (hypothesis: balance
  closes for random inputs), quality propagation.

### WP2 – Shadow-mode skeleton · `control-safety` (Fable)

- `suncore/core/decisions.py` – `Reason(key, args)`, `OperatingMode`
  (shadow/assisted/automatic), `Intent`, `Decision` (accepted/rejected/
  shadow + reasons). Contracts only; the `Plan` contract stays in Phase 3/4
  (ADR-0011).
- `suncore/safety/gate.py` – `SafetyGate.submit(intent) -> Decision`: the
  only object holding `Actuator` references. Phase 1: global mode is
  shadow and cannot be changed (no API, no option); every intent becomes a
  `shadow` decision with reason `safety.shadow_mode`; global emergency stop
  flag exists and is respected.
- `suncore/safety/actuator.py` – `Actuator` protocol (write, read_back);
  no implementations in Phase 1.
- Architecture test: an import-linter style test fails if any module
  outside `safety/` imports an `Actuator` implementation or calls
  `HAConnection.call_service` (the connector exposes `call_service` only
  through a capability object handed to `safety`).
- Tests: property test that no input sequence leads to an actuator call
  while the mode is shadow; emergency stop; decisions are serialisable.
- `control-safety` reviews WP3's `call_service` surface as part of this WP.

### WP3 – HA connector · `backend-engineer`

Verified against `docs/research/ha-websocket-api.md` and
`docs/research/ha-apps.md`; anything new goes through the `verify-ha-api`
skill first.

- `suncore/ha/connection.py` – `HAConnection` protocol: `connect()`,
  `subscribe_entities(ids)`, `get_states()`, `get_config()`, `call_service`
  (behind the safety capability, unused in Phase 1), state stream,
  connection state.
- `suncore/ha/transport.py` – `SupervisorTransport`
  (`ws://supervisor/core/websocket`, `SUPERVISOR_TOKEN`) and
  `DirectTransport` (URL + token, env `SUNCORE_HA_URL`/`SUNCORE_HA_TOKEN`,
  devcontainer and tests).
- `suncore/ha/websocket.py` – aiohttp client: auth handshake, message ids,
  `supported_features` with `coalesce_messages`, ping/pong heartbeat,
  result/error handling, compressed `subscribe_entities` format
  (`a`/`c`/`r`, `+`/`-` diffs), resubscribe after reconnect, back-off
  1 s → 60 s with jitter.
- `suncore/ha/cache.py` – entity cache with per-entity staleness
  (configurable age, `unavailable`/`unknown` handling), conversion to
  `Sample` via the unit of measurement attribute.
- `suncore/ha/redact.py` – log filter that removes tokens.
- Entity list for the settings picker: `get_states` filtered by
  `device_class: power`/`battery` and units W/kW/% (registry commands
  stay for Phase 2, they are still `UNVERIFIED`).
- Tests: against a fake HA WebSocket server (aiohttp test server)
  implementing auth ok/invalid, subscribe, compressed diffs, disconnects,
  slow pongs; back-off timing with a fake clock; token never in captured
  logs.

### WP4 – Storage · `backend-engineer`

- `suncore/storage/db.py` – `aiosqlite`, WAL, `/data/suncore.db`
  (path configurable for tests and demo).
- `suncore/storage/migrations/` – plain SQL/Python migrations, `0001`
  creates `config_doc`, `decision`, `learned` (ADR-0004 / architecture §7);
  runner applies them in order, records the schema version, refuses to
  start on a newer DB than the code knows.
- `suncore/storage/config_store.py` – get/put per section with
  `schema_version` and per-section document migrations.
- Export/import: one JSON file with header (`suncore_export`, app version,
  schema versions), import validates before replacing, atomic.
- `decision` repository: append + query latest N (shadow decisions from
  WP2 land here).
- `timeseries.db` is **not** created in Phase 1 (ADR-0004: Phase 3).
- Tests: fresh DB, migration from fixture DB v0, newer-DB refusal,
  export → import round trip, corrupt import rejected without data loss.

### WP5 – API and app composition · `backend-engineer`

- `suncore/app/settings.py` – `pydantic-settings`: reads
  `/data/options.json` (Supervisor app options), env overrides,
  `demo_mode`, `log_level`, ports.
- `suncore/app/main.py` + `suncore/__main__.py` – composition root, one
  asyncio process, Uvicorn in-process on port 8099, structlog JSON logs,
  graceful shutdown (SIGTERM: stop tasks, close DB, close WS).
- `suncore/api/` – FastAPI:
  - `GET /health` (200/503 with HA connection state, last update age,
    DB ok) – used by the Docker `HEALTHCHECK`;
  - `GET /api/v1/info`, `GET/PUT /api/v1/config/site`,
    `GET /api/v1/entities` (picker candidates),
    `GET /api/v1/config/export`, `POST /api/v1/config/import`,
    `GET /api/v1/decisions?limit=`;
  - `WS /api/v1/live` – snapshot on connect, then throttled updates
    (≤ 2/s) of the site signals, health and latest decisions;
  - static SPA from `frontend/dist`, `index.html` served for unknown
    non-API paths.
- Ingress security: bind `0.0.0.0:8099` inside the container, reject
  requests not coming from `172.30.32.2` when running under Supervisor
  (research note `ha-apps.md`); no open port by default. Direct mode
  requires an explicit `SUNCORE_ALLOW_ANY_PEER=1` (dev only).
- Settings page writes use `PUT /config/site` only for admin users
  (`panel_admin: true` in `config.yaml` restricts the whole panel to
  admins – verify with `verify-ha-api`).
- Tests: httpx/ASGI tests for every endpoint, peer check, WebSocket
  snapshot + update, demo-mode wiring end-to-end without HA.

### WP6 – Simulator base and demo mode · `qa-engineer`

- `suncore/sim/house.py` – virtual house with a fixed time step: PV from a
  clear-sky profile scaled by kWp with optional cloud factor series, base
  load profile, battery (capacity, power limits, efficiency, SoC bounds,
  battery covers deficit / absorbs surplus), grid as balance; signs per
  `core` conventions.
- `suncore/sim/fake_ha.py` – `FakeHAConnection` implementing the
  `HAConnection` protocol, exposing the house as HA-like entities
  (`sensor.sim_grid_power` etc. – clearly simulator-only ids), with fault
  injection (entity unavailable, stale, disconnect).
- `suncore/sim/scenarios/*.yaml` + loader: `summer_day`, `winter_fog`,
  `sensor_dropout` (more in Phase 3). Time can run accelerated in demo mode.
- Demo mode: `demo_mode: true` option or `SUNCORE_DEMO=1` wires the fake
  connection, a temp DB pre-filled with a site config mapping the
  simulator entities, and a "Demo" banner in the UI.
- Tests: energy balance closes at every step (hypothesis), SoC stays in
  bounds, scenarios load, `-m simulator` marker; full-stack test: app in
  demo mode → `/api/v1/live` delivers plausible flows.

### WP7 – Frontend scaffold and "Now" view · `frontend-engineer`

- `frontend/`: Svelte 5 (runes), Vite, TypeScript strict, ESLint +
  Prettier, Vitest, Playwright; `vite.config.ts` with `base: './'`; hash
  routing (`#/`, `#/settings`) so the ingress base path never matters.
- Theme: CSS custom properties named after HA theme variables
  (`--primary-color`, `--card-background-color`, …) with our own light/dark
  defaults via `prefers-color-scheme`. **Spike:** check whether the ingress
  iframe exposes HA's theme (resolves the `UNVERIFIED` item in STATUS); if
  not, we stay with `prefers-color-scheme` and document it.
- "Now" view: SVG energy-flow diagram (PV, grid, battery with SoC, house),
  flow direction and animated speed by power, values formatted with
  `Intl.NumberFormat` in W/kW, stale/unavailable states visible, shadow
  mode badge, connection indicator; responsive (phone, tablet), keyboard
  and ARIA (flows described as text for screen readers). No ECharts yet
  (Plan view is Phase 5) – keeps the bundle small.
- Settings page (minimal, admin): entity pickers for grid (signed or
  import/export pair), PV (one or more), battery power + SoC, optional
  house load; "invert sign" toggle with live value preview; save via API.
  This is a temporary page that the Phase 2 wizard replaces.
- WebSocket client with reconnect and back-off, typed messages generated
  from the FastAPI OpenAPI schema (`openapi-typescript`) so front- and
  backend cannot drift.
- Bundle size recorded in CI (budget: < 250 kB gzip for Phase 1).
- Tests: Vitest for the flow maths, formatting and the WS client;
  Playwright E2E against the demo-mode backend (Now view renders, values
  update, settings save round trip, dark mode, phone viewport).

### WP8 – i18n plumbing · `frontend-engineer`, then `i18n-sync` (Haiku)

- `frontend/src/lib/i18n/` with `intl-messageformat`, catalogues
  `locales/en.json` (source) and `locales/de.json`; locale from
  `navigator.language`, fallback English (open question 3).
- ESLint rule forbidding raw text in Svelte markup and string literals in
  `aria-*`/`title` outside `t()` (custom rule in `frontend/eslint/` if
  `eslint-plugin-svelte` has no suitable rule – checked during the WP).
- Backend: no rendering of messages in Phase 1. The API returns
  `Reason(key, args)` and the frontend renders it; the Python ICU library
  decision (ADR-0007 follow-up) is deferred to the phase that sends
  notifications (Phase 6) and recorded in STATUS.
- Python check (`scripts/check_reasons.py`, run in CI): reason keys used in
  `suncore/` exist in `frontend/.../en.json`.
- App options: `addon/*/translations/{en,de}.yaml` for every option.
- `i18n-sync` fills and checks `de.json` at the end of the phase.

### WP9 – App packaging, image, smoke test · `ha-integration`

- `addon/suncore*/config.yaml`: `ingress: true`, `ingress_port: 8099`,
  `panel_admin: true`, options `log_level`, `demo_mode`,
  `controller_interval_s` (kept), versions; app linter green.
- Dockerfile: multi-stage – Node stage (pinned `node:22-alpine` digest)
  builds `frontend/dist`, Python stage as today plus the dist copy;
  s6-overlay service `rootfs/etc/services.d/suncore/run` (+ `finish`)
  starting `python3 -m suncore`; `HEALTHCHECK` against
  `http://127.0.0.1:8099/health` with Python's `urllib` (no curl needed).
  The `init: false` / s6 interplay is verified against the base image
  before it is changed (`verify-ha-api`).
- `build.yml`: frontend build inside the image; smoke test runs the image
  in demo mode, waits for `/health` = 200, reads RSS
  (`docker stats --no-stream`) after 60 s idle and fails above 300 MB;
  aarch64 smoke on `main` (R4).
- Beta release at the end of the phase: release-please PR → `0.2.0-beta.0`
  in `addon/suncore_beta/config.yaml` → GHCR push → maintainer installs
  (prepare-release skill).
- Dependencies (ADR-0001), all checked for musllinux wheels on amd64 and
  aarch64 before they are added (the image installs with
  `--only-binary=:all:`): `fastapi`, `uvicorn` (without `uvloop`/
  `httptools` extras unless wheels exist), `aiohttp`, `aiosqlite`,
  `structlog`, `pydantic-settings`; dev: `pytest-asyncio`, `httpx`.

### WP10 – Docs, STATUS, CHANGELOG · `docs-writer`

- `addon/suncore*/DOCS.md`: installation, demo mode, settings page,
  shadow mode, what Phase 1 does not do yet.
- `docs/user/` quick start draft with screenshots from demo mode
  (`demo-screenshots` skill: light/dark, phone/tablet).
- `CONTRIBUTING.md`: frontend commands, running the stack in demo mode,
  `DirectTransport` against a local HA.
- `docs/STATUS.md`, `docs/roadmap.md` (Phase 1 🟨/✅), CHANGELOG via
  Conventional Commits (release-please generates it; no manual edits
  beyond what release-please expects).

## 3. Pull requests and order

CLAUDE.md asks for one topic per PR. Proposal – four PRs, each reviewed by
`reviewer` (Fable) before opening, safety-relevant ones additionally by
`control-safety`:

| PR | Content | Depends on |
|---|---|---|
| 1-A `feat(core,safety,storage)` | WP1, WP2, WP4 | – |
| 1-B `feat(ha,api,sim)` | WP3, WP5, WP6 | 1-A |
| 1-C `feat(frontend)` | WP7, WP8 | 1-B |
| 1-D `build(app)` + docs | WP9, WP10, beta release | 1-C |

Alternative (open question 1): one phase PR with these four groups as
separate commits, which is what the kickoff message describes.

```text
WP1 ─┬─► WP2 ─┐
     └─► WP4 ─┼─► WP3 ─► WP5 ─┬─► WP7 ─► WP8 ─► WP9 ─► WP10
              └─► WP6 ────────┘
```

Delegation is sequential (no parallel fan-out larger than two subagents):
WP2 (control-safety) and WP4 (backend) may run in parallel; everything
else runs one after another. I integrate, run `./scripts/check.sh` and the
frontend checks after every WP and commit per WP.

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Python unit | pytest, hypothesis, pytest-asyncio | core units/signs/balance, safety gate properties, storage migrations, WS protocol, back-off, redaction |
| Python integration | pytest + fake HA WS server; `-m simulator` | connector end-to-end, demo-mode stack, scenarios |
| API | httpx ASGI client | all endpoints, peer check, live WS |
| Frontend unit | Vitest | flow maths, number formatting, i18n, WS client |
| E2E | Playwright (pre-installed Chromium) against demo mode | Now view, settings round trip, dark mode, phone viewport |
| Lint/type | ruff, mypy (strict for core/safety), eslint + no-raw-text rule, prettier, svelte-check | CI |
| Image | build.yml smoke test | `/health`, RSS budget |
| Coverage | pytest-cov | ≥ 80 % on `suncore/core` and `suncore/safety`, enforced in `check.sh` |

The cloud session has no Docker daemon: the image and the smoke test are
validated by CI only; everything else runs locally before each push.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | A dependency has no musllinux aarch64 wheel | image build fails | check wheels per dependency before adding; drop optional extras (`uvloop`, `httptools`); Debian base image is ADR-0001's escape hatch |
| R2 | Ingress iframe does not expose HA theme variables | UI does not follow the user's HA theme | own light/dark defaults via `prefers-color-scheme`; spike result recorded in STATUS |
| R3 | `init: false` vs s6-overlay of the base image behaves differently than assumed | app does not start or ignores SIGTERM | verify against base image source before changing; smoke test catches start failures |
| R4 | GitHub arm64 runners not available for this private repository | no aarch64 smoke/RSS in PRs | amd64 RSS in PRs, aarch64 on `main` push; maintainer measures on the Pi as exit check |
| R5 | `subscribe_entities` compressed diff semantics differ in edge cases | wrong live values | tests built from the HA core source format (research note), plus fallback to `get_states` resync on inconsistency |
| R6 | Peer-IP check `172.30.32.2` breaks behind a changed Supervisor network | UI unreachable | value from research note; configurable; documented in DOCS troubleshooting |
| R7 | Settings page grows into a wizard | scope creep into Phase 2 | strictly the entity pickers listed in WP7 |
| R8 | Phase 1 delivers no device writes, but the safety boundary must already hold | later bypasses | WP2 architecture test makes the boundary a CI failure |

## 6. Open questions for the maintainer

Defaults in bold are used if unanswered.

1. **Four PRs (1-A … 1-D) or one phase PR?** Recommendation: **four PRs**
   (CLAUDE.md "one topic per PR", smaller reviews). Your kickoff text says
   "ein PR" – if you prefer one, I keep the four groups as separate commits.
2. **Demo-mode entities:** simulator entity ids are prefixed `sensor.sim_*`
   and exist only inside SunCore – **ok**?
3. **UI language:** **browser language (`navigator.language`)**, fallback
   English. Reading the HA user's language from inside the ingress iframe
   is `UNVERIFIED` and deferred.
4. **`RELEASE_PLEASE_TOKEN`** (ADR-0009) is still missing – needed for the
   first beta release at the end of the phase; otherwise I start the build
   with `workflow_dispatch`.
5. **Repository visibility:** while it is private, CodeQL is skipped and
   arm64 runners may be unavailable (R4). Planned to go public in Phase 1?
   **Stays private** until you decide.

## 7. Out of scope for Phase 1

Setup wizard and auto-detection (Phase 2), device model and templates
(Phase 2), controller logic and any real device write (Phase 3),
time-series DB and history (Phase 3/5), forecasts, tariffs and planner
(Phase 4), ECharts plan view (Phase 5), MQTT discovery (Phase 6), Weblate
project (Phase 7).
