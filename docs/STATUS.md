# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-13 · **Phase:** 2 implemented, waiting for maintainer acceptance
(PR 2-D, docs, stacked on 2-C) · **Branch:** `phase-2/*` (stacked on `main`)

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

- **Phase 2 – PR 2-A** (WP1–WP4, branch `phase-2/a-foundation`):
  - Research (WP1): HA interfaces verified against `home-assistant/core` dev
    `14f093e` (registry list commands and shapes, registry update events,
    `energy/get_prefs`, entity id regex), LG ThinQ, Tibber, Solcast; notes in
    `research/ha-websocket-api.md` and `research/device-integrations.md`.
  - ADR-0012 (accepted): template schema v1 reviewed before freeze
    (compatibility policy and "Schema v1 changelog" in `templates/README.md`).
  - Device model (`suncore/core/devices.py`) and template schema v1
    (`suncore/templates/`: models, loader, matcher; JSON Schemas generated
    into `templates/schema/`). Templates are plain YAML, anchors/aliases are
    rejected. `DeviceConfig.mode` removed: the `operation` section is the
    only store of the mode of operation.
  - 8 templates: `lg.thinq_climate`, `generic.switch`, `generic.climate`,
    `generic.number_load`, `tibber.price`, `solcast.pv`, regions `de` and
    `generic`; each with a conformance scenario in `tests/scenarios/`.
  - `sim`: registry (entity, device, area, Energy-dashboard data) and demo
    devices (LG-like AC with hourly Wh counter, pool plug, Tibber-like price,
    Solcast-like forecast) served by `FakeHAConnection`.
  - Storage (WP4): site v2 (`PvArray`, v1 → v2 migration), 7 config sections
    (`site`, `region`, `tariff`, `devices`, `goals`, `operation`, `wizard`),
    `config_history` (migration 0002) → DB schema version 2: 0.2.0 refuses a
    newer DB; `ConfigStore.get_section`/`put_section`; `TariffConfig.import_`
    is stored as `import` (old key still loads).
  - Review fixes: PV rows keep their array in the settings form, YAML alias
    rejection, template sources re-fetched from GitHub mirrors, one shared
    region id pattern.
  - `UNVERIFIED`: all three integration templates come from source reading
    only (no live HA or hardware); see the table below.

- **Phase 2 WP5/WP6 (PR 2-B, branch `phase-2/b-api`)**: `HAConnection` registry list commands,
  `energy/get_prefs`, registry update events and the read-only `call_action_response`
  (allowlist `READ_ONLY_ACTIONS`); `EntityCatalogue`, role detection (`ha/detect.py`), Energy
  dashboard import, config section/history/template/wizard endpoints, `operation` stored only
  (boundary tests keep the gate away from it). `UNVERIFIED`: `unknown_command` when the
  energy integration is not loaded; the older grid prefs format on a real older core.

- **Phase 2 – PR 2-C (WP7–WP9, branch `phase-2/c-wizard`)**, stacked on 2-B:
  - Frontend: setup wizard at `#/setup[/<step>]` (10 steps: region, Energy dashboard import, grid,
    solar, battery, tariff, devices, priorities and goals, operating mode, summary with system
    check), resumable via `wizard.completed_steps`; first start redirects once into it (Phase 1
    users with a site but no wizard section land in the pre-filled wizard). `RolePicker`
    (ranked candidates with the criteria that matched, live value, optional "also hidden and
    diagnostic entities") and `SignCheck` (live plausibility question, one-click sign
    inversion). Device gallery/editor from templates, drag-and-drop and keyboard priorities.
    Settings overview with per-step edit links, config history/restore and export/import;
    "Run setup again" restarts the wizard. Unsaved edits are guarded (leave confirmation,
    Settings never reloads over them, `beforeunload`).
  - Review fixes (this commit series): site role `price` (dynamic tariff with only a price
    sensor); template protection defaults (`settle_s`, hysteresis, `max_switches_per_hour`)
    carried into new devices; new device priority = highest + 1; "Finish" disabled while the
    check reports an error; German wording; catalogue/ICU split into own chunks.
  - Decisions: (1) the demo starts fully set up (region `de`, dynamic Tibber-style tariff,
    forecast, three devices instantiated from `lg.thinq_climate`/`generic.switch`/
    `generic.number_load`, goals, shadow, wizard completed); the wizard stays reachable.
    (2) Phase 1 users are redirected once into the pre-filled wizard. (3) The forecast source
    is its own optional section `forecast` (tariff is schema v2; tariff v1 forecast fields are
    moved on start-up and on import); the system check only notes a missing forecast.
    (4) `RolePicker` can include hidden/diagnostic entities.
  - Bundle: 107.5 kB gzip in total (budget 250 kB); the first-paint chunks are `index`
    11 kB, `icu` 10 kB, `catalogues` 22 kB (gzip), the wizard steps load on demand.
  - `UNVERIFIED`: the theme bridge (copying HA theme variables from the ingress parent document)
    is unit-tested with fake parent windows only, not on a real HA (table below).

- **Phase 2 – PR 2-D (WP10, branch `phase-2/d-docs`)**, stacked on 2-C:
  - Docs: quick start en/de rewritten around the setup wizard (one section and
    screenshot per step, shadow mode, settings/history/export-import,
    troubleshooting, honest "cannot do yet"); "Contribute a device template"
    guide en/de (`docs/user/<lang>/device-templates.md`); app `DOCS.md` (stable
    and beta), README status and `templates/README.md` updated.
  - Generated compatibility list `docs/user/compatibility.md` from the template
    loader (`scripts/gen_compat.py`, `--check` in `scripts/check.sh` and the CI
    python job, tested in `tests/test_gen_compat.py`).
  - Beta version derivation in `build.yml` and the E2E/screenshot work were done
    in PR 2-D, see Open – Phase 1.

## Open – Phase 1

- Still to report by the maintainer: memory on the Pi, menu names in the
  HA UI, whether the panel follows the HA theme. The GHCR packages of the
  stable app (`suncore`, `aarch64-suncore`, `amd64-suncore`) must be made
  public after the first stable build.
- ~~Beta app version bumped by hand~~ Done (Phase 2, WP10): `build.yml`
  derives `<next>-beta.<n>` and the `bump-beta` job commits it to
  `addon/suncore_beta/config.yaml` on `main` (ADR-0009 addendum). Needs
  push permission to `main` for `github-actions[bot]` or
  `RELEASE_PLEASE_TOKEN`; UNVERIFIED until the first run on `main`.
- Done in PR 2-B: 422 issues are rendered as catalogue keys (`api.validation.*`),
  and `/api/v1/entities` is served from the entity catalogue (registry snapshot
  plus a state copy refreshed at most every 30 s, never a `get_states` per
  request). The theme bridge from the ingress parent document is implemented
  (PR 2-C) but `UNVERIFIED` on a real HA, see the table below.
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
- Phase 3 follow-up (review F6, A2): before the gate reads `operation`, base the "new
  device = shadow" rule on device identity (id + `template_id` + entities) in
  put/import/restore of `devices` **and** put/restore of `operation`, not on the id alone.
  Decide whether an import resets the global mode to shadow (recommendation: yes).
- Phase 4 follow-up (A3): per-(domain, service) rate limiter with try-acquire/deadline for
  `call_action_response` before the first adapter calls it (boundary rule 7 then admits `app`
  together with the adapter wiring).
- Carried over from Phase 0: deleting old tags `v0.1.0`–`v0.3.0`. The
  repository is public now, CodeQL runs; `RELEASE_PLEASE_TOKEN` is set.

## Next steps

1. Maintainer: test the beta on HA OS (describe your system through the
   wizard, with the Energy dashboard import, LG ThinQ, Tibber, Solcast; report
   the `UNVERIFIED` items below), then merge in order #13 → #14 → #15 → #16
   (2-A, 2-B, 2-C, 2-D).
2. Release `0.2.0`/the next release-please PR for the stable app after Phase 2.
3. Then the Phase 3 plan (`docs/plans/phase-3.md`: real-time controller and
   safety), including the Phase 3 follow-ups above.

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Open-Meteo tilted-irradiance parameters; Energy-Charts and aWATTar APIs and terms | `research/integrations.md` | Phase 4, session with network access |
| Forecast.Solar / Open-Meteo Solar Forecast attribute names; Octopus attributes | `research/integrations.md` | Phase 4, read integration source |
| Theme bridge: `window.parent.document` readable from the ingress iframe, HA theme variable names (`THEME_MAP`) and the `MutationObserver` on the parent `<html>` follow HA theme switches (implemented in PR 2-C, unit-tested with fake parent windows only; fallback `prefers-color-scheme` is unchanged) | `frontend/src/lib/theme/bridge.ts`, `frontend/README.md` | maintainer's HA OS: switch the HA theme light/dark and check the panel follows |
| Energy colour variable names (`--energy-*`) | `frontend/src/app.css` | read HA frontend source |
| HA UI menu paths in the user docs (Settings → Apps → App store, Repositories, Show in sidebar; German labels) | `addon/*/DOCS.md`, `README.md`, `docs/user/*/quick-start.md` | maintainer checks on HA OS during the beta install |
| Supervisor Core proxy passes `supported_features` through | `suncore/ha/websocket.py` | first run on HA OS |
| Registry and energy-prefs shapes on cores older than dev (single `config_entry_id` on devices, unified grid source); `unknown_command` code without the `energy` integration; Supervisor proxy passes registry commands | `research/ha-websocket-api.md` | first run on HA OS; parsers tolerate both shapes |
| Section 51 EEG 2023 text could not be fetched; region `de` feed-in tiers are empty until the official BNetzA table is cited | `templates/region/de.yaml` | maintainer provides the table or a session with access to gesetze-im-internet.de |
| Whether Tibber's `total` includes grid fees (country dependent) | `templates/tariff/tibber.price.yaml` | live check on the maintainer's Tibber account |
| Live entity ids and LG `unique_id` property part, LG model-specific HVAC/fan modes, Tibber tomorrow-price publication time, Solcast default day-sensor count (LG, Tibber and Solcast templates are verified from source only) | `research/device-integrations.md` | maintainer's HA with LG ThinQ, Tibber, Solcast (Phase 2 wizard test) |
| Wording of the quick start steps and the HA menu names in the new docs (written from the UI catalogues, not clicked through on a real HA) | `docs/user/*/quick-start.md` | maintainer follows the quick start on HA OS |
| Which Home Assistant tool lets users place files in the app's `/data/templates` | `docs/user/*/device-templates.md` | maintainer checks on HA OS |
| `bump-beta` job pushes the version commit to `main` (needs push permission for `github-actions[bot]` or `RELEASE_PLEASE_TOKEN` with bypass) | `.github/workflows/build.yml`, ADR-0009 addendum | maintainer: first run on `main` |
| evcc REST API paths | `research/competitors.md` | Phase 6, MQTT API is verified and sufficient |
| Solve times on Raspberry Pi 4 | ADR-0003 | Phase 4 benchmark |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
