# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-12 · **Phase:** 1 (app skeleton) ·
**Branches:** `phase-1/app-skeleton` (#3) → `phase-1/ha-api-sim` (#4) →
`phase-1/frontend`

## Done

- Phase 0 merged (PR #1): brief, research, ADRs 0001–0011 accepted,
  architecture, roadmap, scaffold, CI, agent team.
- Phase 1 plan `docs/plans/phase-1.md` approved (defaults of its §6: four
  stacked PRs 1-A…1-D, `sensor.sim_*` demo entities, browser language,
  repository stays private).
- **PR 1-A** (WP1, WP2, WP4):
  - `core`: units and HA unit parsing (unknown unit → `invalid`, never
    guessed), signals with quality, `SiteConfig`, energy balance
    (`house = pv + grid − battery`, worst input quality propagates).
  - `core.decisions` + `safety`: `Reason`/`Intent`/`Decision` contracts,
    `SafetyGate` structurally fixed to shadow mode, global emergency stop,
    `Actuator` protocol without implementations; AST boundary test
    (single writer, `call_service` only in `ha`/`safety`, dependency rule).
    Signed off by `control-safety`.
  - `storage`: `/data/suncore.db` with SQL migrations (`PRAGMA
    user_version`), refusal of newer DBs, `ConfigStore` with per-section
    document migrations, atomic export/import, decision log.
  - Coverage gate ≥ 80 % for core/safety/planner/controller in
    `check.sh` and CI.
- **PR 1-B** (WP3, WP5, WP6), stacked on 1-A:
  - `ha`: WebSocket connector (Supervisor and direct transport), auth,
    compressed `subscribe_entities`, heartbeat, reconnect with jittered
    back-off and resync, entity cache with staleness, token redaction,
    picker candidates, rate-limited `ServiceCaller` reserved for `safety`.
    Message shapes verified against HA core (research note updated).
  - `api` + `app`: FastAPI with `/health` (liveness: 503 only for DB
    failure, `auth_failed`, `closed`), site config, entities, export/import
    (1 MiB limit), decisions, live WebSocket (≤ 2 flow updates/s); peer
    check (Supervisor → `172.30.32.2` only, otherwise loopback unless
    `SUNCORE_ALLOW_ANY_PEER=1`); composition root with graceful SIGTERM.
  - `sim`: virtual house, scenarios `summer_day`, `winter_fog`,
    `sensor_dropout`, `FakeHAConnection` with fault injection; demo mode
    (`demo_mode` option / `SUNCORE_DEMO=1`) runs without HA and never
    touches `/data`.
  - Image installs the hash-checked set from `uv.lock` from PyPI only
    (the base image's HA wheels index lagged behind, see PR #3).
- **PR 1-C** (WP7, WP8), stacked on 1-B:
  - `frontend/`: Svelte 5 + Vite + TypeScript, hash routing and relative
    URLs (ingress base path), "Now" view with animated SVG energy flow,
    quality states, shadow-mode badge, connection indicator, demo banner;
    temporary settings page for grid/PV/battery/house entities with sign
    inversion and live preview; theme tokens named after HA variables with
    `prefers-color-scheme` defaults. Bundle 40 kB gzip (budget 250 kB).
  - i18n: `intl-messageformat`, `en.json`/`de.json` (same keys, ICU
    checked), ESLint rule against hardcoded UI text,
    `scripts/check_reasons.py` keeps backend reason/error keys and
    catalogues in sync.
  - Tests: Vitest, Playwright E2E against the real demo backend (desktop,
    phone, tablet; axe WCAG 2.1 AA), CI job `frontend`.
  - Demo starts at `SUNCORE_DEMO_START_TIME` (default 10:00); screenshots
    in `docs/assets/screenshots/` come from the real demo.
- R1 cleared: every planned Python dependency has cp313 musllinux wheels
  for aarch64 and x86_64 or is pure Python.

## Open – Phase 1

- PR 1-D (image, smoke test, docs, beta release).
- WP9: Dockerfile `HEALTHCHECK`/`CMD` still call `--version`; switch to
  `/health` and the s6 service. Smoke test must reach `/health` from inside
  the container (loopback) or set `SUNCORE_ALLOW_ANY_PEER=1`. Decide
  `panel_admin: true` with `verify-ha-api` (the skeleton test currently
  rejects it as a redundant default).
- WP9: redirect to a trailing slash when the ingress path arrives without
  one (relative asset URLs, UNVERIFIED whether Supervisor ever does that).
- Phase 2 wizard: render 422 validation issues through catalogue keys
  instead of Pydantic's English messages; serve `/api/v1/entities` from the
  entity cache instead of a full `get_states` per request; theme bridge from
  the ingress parent document (see `frontend/README.md`).
- Python ICU library decision (ADR-0007 follow-up) deferred to Phase 6:
  in Phase 1 the backend only emits `Reason(key, args)`.
- Phase 3 follow-ups: extend boundary rule 2 to `suncore.devices` drivers
  (only `safety` and `suncore.app` may import them) and remove
  `test_phase_1_has_no_apply_call_anywhere`; version the decision payload
  (or deserialise tolerantly) before changing `Decision`; retention for the
  `decision` table; tables `plan_snapshot`/`template_override` and
  `timeseries.db`. Emergency stop is not persisted yet (no hardware effect
  in Phase 1). Migrations are SQL files; Python migrations when needed.
  From the PR 1-B safety review: per-device rate limits and max
  switches/hour belong in `safety/` (the 1 s connection floor is only a
  flood guard, lower it to 0.2–0.5 s); read back from the entity cache with
  a settle delay instead of a full `get_states`; give the rate limiter a
  deadline/try-acquire API so stale intents are dropped; an `AuthError` on
  reconnect needs a restart (document in the UI).
- Carried over from Phase 0: `RELEASE_PLEASE_TOKEN` secret, CodeQL while
  the repository is private, deleting old tags `v0.1.0`–`v0.3.0`.

## Next steps

1. Maintainer reviews and merges PR 1-A, 1-B, 1-C in this order.
2. WP9 image/s6/health/smoke test, WP10 docs, first beta → PR 1-D.

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Open-Meteo tilted-irradiance parameters; Energy-Charts and aWATTar APIs and terms | `research/integrations.md` | Phase 4, session with network access |
| Forecast.Solar / Open-Meteo Solar Forecast attribute names; Octopus attributes | `research/integrations.md` | Phase 4, read integration source |
| Theme variable inheritance inside ingress iframe (spike: not verifiable without a running HA; fallback `prefers-color-scheme`) | `frontend/README.md` | Phase 2, on the maintainer's HA |
| Energy colour variable names (`--energy-*`) | `frontend/src/app.css` | read HA frontend source |
| Ingress gateway peer `172.30.32.2` (from research note, not from Supervisor source) | `suncore/app/settings.py` | WP9 `verify-ha-api` |
| Supervisor Core proxy passes `supported_features` through | `suncore/ha/websocket.py` | first run on HA OS |
| Registry list WebSocket command names; `power_config` shape in energy prefs | `research/ha-websocket-api.md` | Phase 1/2, read `components/config/*.py` |
| evcc REST API paths | `research/competitors.md` | Phase 6, MQTT API is verified and sufficient |
| Solve times on Raspberry Pi 4 | ADR-0003 | Phase 4 benchmark |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
