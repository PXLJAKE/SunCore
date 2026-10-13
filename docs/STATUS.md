# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-13 · **Phase:** 1 done, Phase 2 next ·
**Branch:** `main`

## Done

- **Phase 1 accepted** (2026-10-13): the maintainer installed the beta on
  HA OS, mapped grid, PV, battery (SunSpec Modbus) and house sensors and
  sees live flows. Finding from the test fixed in #9: sensors without a
  `device_class` (e.g. SunSpec state of charge, unit `%`) were missing in
  the picker; the picker is now a searchable combobox filtered by unit.
  Beta app version `0.2.0-beta.1`.

- **Phase 1 merged** (#3, #4, #5, #6, 2026-10-13). The beta image
  `ghcr.io/pxljake/suncore-beta:0.1.0-beta.0` is published for amd64 and
  aarch64; the smoke test on `main` passed on both (`/health` 200 after
  2–3 s, 49.9 MiB container memory after 60 s idle, clean SIGTERM stop).
  `hassio_api` removed until MQTT needs it (Phase 6).

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
- **PR 1-D** (WP9, WP10), stacked on 1-C:
  - Image: Node stage builds the SPA (`node:22-alpine` pinned by digest),
    Python stage installs the locked set from PyPI and serves
    `frontend/dist`; s6 service (`/etc/services.d/suncore`) execs
    `python3 -m suncore`; `HEALTHCHECK` polls `/health` over loopback.
  - Smoke test (`scripts/smoke_image.sh`): demo mode, `/health` within
    90 s, idle container memory ≤ 300 MiB after 60 s, clean stop (exit 0,
    `app.stopped`); PRs on amd64, `main` per architecture after the push.
  - App option `demo_mode` (en/de). Verified against Supervisor source:
    `panel_admin` and `ingress_port` defaults, ingress gateway
    `172.30.32.2`, ingress path always has a trailing slash.
  - Docs: app `DOCS.md`, quick start en/de with screenshots,
    `CONTRIBUTING.md` (frontend, local stack), README status.
- R1 cleared: every planned Python dependency has cp313 musllinux wheels
  for aarch64 and x86_64 or is pure Python.

- **Phase 2 WP1** (HA interfaces verified against `home-assistant/core` dev
  `14f093e`, 2026-10-13): registry list commands and shapes, registry
  update events (allowlisted, non-admin may subscribe), `energy/get_prefs`
  incl. `power_config`/`stat_rate`, HA entity id regex (`ENTITY_ID_PATTERN` in
  `suncore/core/site.py` is not stricter than HA, see
  `research/device-integrations.md`), LG ThinQ, Tibber, Solcast. Notes:
  `research/ha-websocket-api.md`, `research/device-integrations.md`.

- **Phase 2 WP2/WP3** (branch `phase-2/setup-wizard`): template schema v1 reworked per
  ADR-0012 (all 12 items, compatibility policy and "Schema v1 changelog" in
  `templates/README.md`); package moved to `suncore/templates/` (core-only dependency rule in
  the boundary test); `DeviceConfig.mode` removed (the `operation` section is the single
  store); `TariffConfig.import_` is stored as `import` (old key still loads). First
  templates: `lg.thinq_climate`, `generic.switch`, `generic.climate`, `generic.number_load`,
  `tibber.price`, `solcast.pv`, regions `de` and `generic`, each with a conformance scenario
  in `tests/scenarios/` (`-m simulator`). `FakeHAConnection` serves registry, device, area and
  Energy-dashboard data and demo entities (LG-like AC with hourly Wh counter, pool plug,
  Tibber-like price, Solcast-like forecast); WP5 wires the accessors to the WS commands.
  `UNVERIFIED`: all three integration templates come from source reading only (no live HA
  or hardware); section 51 EEG 2023 could not be fetched (rule per maintainer brief) and the
  region `de` feed-in tiers are empty until the official BNetzA table is cited.

- **Phase 2 WP5/WP6 (PR 2-B, branch `phase-2/b-api`)**: `HAConnection` registry list commands,
  `energy/get_prefs`, registry update events and the read-only `call_action_response`
  (allowlist `READ_ONLY_ACTIONS`); `EntityCatalogue`, role detection (`ha/detect.py`), Energy
  dashboard import, config section/history/template/wizard endpoints, `operation` stored only
  (boundary tests keep the gate away from it). `UNVERIFIED`: `unknown_command` when the
  energy integration is not loaded; the older grid prefs format on a real older core.

## Open – Phase 1

- Still to report by the maintainer: memory on the Pi, menu names in the
  HA UI, whether the panel follows the HA theme. The GHCR packages of the
  stable app (`suncore`, `aarch64-suncore`, `amd64-suncore`) must be made
  public after the first stable build.
- The beta app version is bumped by hand for now (HA only offers an update
  when `config.yaml` changes); automate it in the build workflow (Phase 2).
- Phase 2 wizard: theme bridge from the ingress parent document (see
  `frontend/README.md`). Done in PR 2-B: 422 issues are rendered as catalogue
  keys (`api.validation.*`), and `/api/v1/entities` is served from the entity
  catalogue (registry snapshot plus a state copy refreshed at most every 30 s,
  never a `get_states` per request).
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
- Carried over from Phase 0: deleting old tags `v0.1.0`–`v0.3.0`. The
  repository is public now, CodeQL runs; `RELEASE_PLEASE_TOKEN` is set.

## Next steps

1. Release `0.2.0` (release-please PR #7) for the stable app.
2. Phase 2 plan (`docs/plans/phase-2.md`): setup wizard, device model,
   template schema.
3. Phase 2 plan (`docs/plans/phase-2.md`).

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Open-Meteo tilted-irradiance parameters; Energy-Charts and aWATTar APIs and terms | `research/integrations.md` | Phase 4, session with network access |
| Forecast.Solar / Open-Meteo Solar Forecast attribute names; Octopus attributes | `research/integrations.md` | Phase 4, read integration source |
| Theme variable inheritance inside ingress iframe (spike: not verifiable without a running HA; fallback `prefers-color-scheme`) | `frontend/README.md` | Phase 2, on the maintainer's HA |
| Energy colour variable names (`--energy-*`) | `frontend/src/app.css` | read HA frontend source |
| HA UI menu paths in the user docs (Settings → Apps → App store, Repositories, Show in sidebar; German labels) | `addon/*/DOCS.md`, `README.md`, `docs/user/*/quick-start.md` | maintainer checks on HA OS during the beta install |
| Supervisor Core proxy passes `supported_features` through | `suncore/ha/websocket.py` | first run on HA OS |
| Registry and energy-prefs shapes on cores older than dev (single `config_entry_id` on devices, unified grid source); `unknown_command` code without the `energy` integration; Supervisor proxy passes registry commands | `research/ha-websocket-api.md` | first run on HA OS; parsers tolerate both shapes |
| Live entity ids and LG `unique_id` property part, LG model-specific HVAC/fan modes, Tibber tomorrow-price publication time, Solcast default day-sensor count | `research/device-integrations.md` | maintainer's HA with LG ThinQ, Tibber, Solcast (Phase 2 wizard test) |
| evcc REST API paths | `research/competitors.md` | Phase 6, MQTT API is verified and sufficient |
| Solve times on Raspberry Pi 4 | ADR-0003 | Phase 4 benchmark |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
