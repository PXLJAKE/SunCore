# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-14 · **Phase:** 3 in progress (plan approved, WP1–WP3 on
`phase-3/a-safety`) · **Branch:** `phase-3/a-safety`

## Done

- **Phase 3 – WP2 contracts v2 and WP3 safety layer** (branch `phase-3/a-safety`, signed off
  by `control-safety`):
  - `core`: actions `SetpointAction` (absolute °C + base), `StepAction`, `ModeAction`,
    `ReleaseAction`; `Decision.schema_version = 2`, kinds `proposed`/`held`/`failed`,
    fields `expires_at`/`held_until`/`observed`; `Origin.SAFETY` for release intents;
    `decision_from_payload` loads Phase 1 v1 rows (`DecisionLog.latest` skips newer or
    corrupt rows). `core/control.py`: `PowerEstimate`, `DeviceState`, `ControlSettings`
    (tick 5 s, smoothing 60 s, deficit 30 s / 50 Wh, proposal TTL 120 s, release deadline
    10 s), minimal `Plan` with `Plan.default()` and per-interval `DeviceBand`/`BatteryBand`.
    `DeviceConfig` additive: `allow_switch_on`, `switch_on_hvac_mode`, `override_policy`
    (`next_day` 04:00 | `hours` 1–48). `core/identity.py`: device identity (id +
    `template_id` + entities) for WP4's shadow fallback. `effective_mode = min(global, device)`.
  - `safety`: `invariants.py` (pure, hypothesis-tested: no SunCore-caused import, import
    limit, comfort band, min run/pause, max switches per sliding hour, write rate, write
    only on change, battery reserve guard, climate set-point while off needs
    `allow_switch_on`); `gate.py` with an injected `ModeProvider` (default all shadow),
    `DeviceLookup`, `StateReader`, `InputsProvider`, `RuntimeStore`; shadow → `shadow` (with
    the invariants that would have fired), assisted → `proposed` + `confirm` as
    `Origin.USER`, automatic → invariants → try-acquire → takeover baseline → `apply()` →
    settle → read-back → `accepted`/`failed` (3 failures trip the device);
    `overrides.py` (value-based, context id only as positive evidence, hold until next day /
    N hours, boost/pause/resume); `watchdog.py` (stale with cadence allowance, unavailable,
    invalid unit, jumps, frozen PV in daylight, balance, connection loss; site faults pause
    all, device faults one; SafeState → cool-down → normal); release on emergency stop
    (persisted through `RuntimeStore`), shutdown (deadline), mode → shadow, site trip,
    reconnect and crash recovery (`recover()`); `Actuator` gained `release(baseline)` and
    `apply()` returns the HA context id. Boundary tests: only `safety/gate.py` calls
    `apply()`/`release()`, drivers importable by `safety`/`app` only. 43 `safety.*` reason
    keys in en/de. Coverage safety ≥ 96 %.
  - App: `Runtime.start()` calls `gate.recover()`, `Runtime.stop()` calls `gate.shutdown()`
    before closing the HA connection; the gate still has no actuators and the all-shadow
    provider until WP9. OpenAPI and frontend API types regenerated; the decision list knows
    the new kinds; the device editor carries the Phase 3 fields through (UI in WP10).
  - Decisions: the watchdog judges a slow sensor's staleness as
    `age > cadence × stale_cadence_factor` (2) because `subscribe_entities` has no
    `last_reported`; the app sets cadences per entity (WP9). `release()` bypasses min
    run/pause (restoring the user's state is the safe direction; noted as a risk).

- **Phase 3 – WP4 storage** (branch `phase-3/a-safety`): migration `0003_runtime.sql` (DB
  schema 3: `device_runtime`, `safety_state`, `power_model_state`; `decision(ts)` was already
  indexed in 0001). **0.3.x cannot open a schema-3 database (it refuses newer ones): export the
  configuration before updating.** `storage/runtime.py`: `SqliteRuntimeStore` (the gate's
  `RuntimeStore`; an unparseable payload is logged and counts as missing, an unreadable
  emergency-stop flag counts as stopped) and `PowerModelStateStore` (JSON per device, for WP5).
  `DecisionLog.prune(now, max_age_days=30, max_rows=200_000)`, run hourly by
  `Runtime` (time and sleep injected). Device identity rule replaces the id-only rule in
  `ConfigStore`: put/import/restore of `devices` drop the `operation` mode of devices whose id,
  template or entities changed; restore of `operation` drops modes of devices whose identity
  differs from the one recorded when the mode was set (`safety_state.mode_identities`; no record
  = shadow); an import always resets the global mode to shadow. The gate uses
  `SqliteRuntimeStore`; the demo uses it on its throw-away database, never `/data`.
  Known gap: a plain `operation` put is trusted (a stale UI document could re-add a mode for a
  device that was just re-mapped).

- **Phase 2 completion from the live LG check (PR #18, 2026-10-14)**: the maintainer runs the
  same AC through `lg_thinq` and HACS `smartthinq_sensors`. (1) Climate templates
  (`lg.thinq_climate`, `generic.climate`) have an optional `power` role without
  `same_device_as` (sensor of another device); mapping it
  preselects the `measured` power model (switchable), candidates of a device with the same
  name rank first (`name_affinity`, query parameter `device_name`, no schema change). The
  `energy_current` sensor polls every 5 minutes, so Phase 3 must treat it as slow (noted in
  the templates). (2) System check warning `wizard.check.duplicate_unit` (same control entity
  twice, or climate entities of different registry devices on different platforms with equal
  device names; a renamed device silences it) and a hint in the device editor; the check never
  blocks. The demo has a second, smartthinq-like
  device. `UNVERIFIED`: the `smartthinq_sensors` source was not read, only its entities on
  the maintainer's HA.

- **Phase 2 merged** (#13, #14, #15, #16, 2026-10-13). First automatic beta
  bump on `main` worked (`0.3.0-beta.50`, pushed with `RELEASE_PLEASE_TOKEN`).
  The maintainer installed it on HA OS 18.3 / core 2026.10.1 (amd64, KVM),
  completed the wizard and sees plausible live flows; the panel follows the HA
  theme accent colour. App memory 65 MiB, CPU < 0.1 % idle. LG ThinQ facts
  checked live (see `research/device-integrations.md`, "Live check").

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

- Still to report by the maintainer: menu names in the HA UI. Memory on
  amd64 HA OS: 65 MiB (2026-10-14); the panel follows the HA theme. The GHCR packages
  `suncore`, `aarch64-suncore`, `amd64-suncore` and `suncore-beta` are public
  (anonymous pull verified on 2026-10-13).
- ~~Beta app version bumped by hand~~ Done (Phase 2, WP10): `build.yml`
  derives `<next>-beta.<n>` and the `bump-beta` job commits it to
  `addon/suncore_beta/config.yaml` on `main` (ADR-0009 addendum). Needs
  push permission to `main`; the job uses the `RELEASE_PLEASE_TOKEN` secret
  (set by the maintainer) before `GITHUB_TOKEN`; verified by the first run
  on `main` (`0.3.0-beta.50`).
- Done in PR 2-B: 422 issues are rendered as catalogue keys (`api.validation.*`),
  and `/api/v1/entities` is served from the entity catalogue (registry snapshot
  plus a state copy refreshed at most every 30 s, never a `get_states` per
  request). The theme bridge follows the HA theme on the maintainer's HA
  (accent colour confirmed 2026-10-14).
- Python ICU library decision (ADR-0007 follow-up) deferred to Phase 6:
  in Phase 1 the backend only emits `Reason(key, args)`.
- Phase 3 follow-ups: ~~boundary rule 2 for drivers, removal of
  `test_phase_1_has_no_apply_call_anywhere`, versioned decision payload, per-device rate
  limits and max switches/hour in `safety/`, read-back from the cache after a settle
  delay, try-acquire rate limiter, persisted emergency stop~~ done in WP2/WP3. Still open:
  retention for the `decision` table and the `RuntimeStore` implementation (WP4); the
  1 s connection flood floor is lowered when WP6 wires the first driver; tables
  `plan_snapshot`/`template_override` and `timeseries.db` (Phase 4); an `AuthError` on
  reconnect needs a restart (document in the UI).
- Phase 3 follow-up (review F6, A2): `core/identity.py` provides the rule; WP4 applies it
  in put/import/restore of `devices` **and** put/restore of `operation`, and an import
  resets the global mode to shadow (plan §6.8).
- Phase 4 follow-up (A3): per-(domain, service) rate limiter with try-acquire/deadline for
  `call_action_response` before the first adapter calls it (boundary rule 7 then admits `app`
  together with the adapter wiring).
- Carried over from Phase 0: deleting old tags `v0.1.0`–`v0.3.0`. The
  repository is public now, CodeQL runs; `RELEASE_PLEASE_TOKEN` is set.

## Next steps

1. Maintainer: watch the beta in surplus, check the summary system check at
   noon, export a config backup; wizard walk-through with other people.
2. Release the next release-please PR for the stable app after acceptance.
3. Phase 3 starts in a new session: write and approve `docs/plans/phase-3.md`
   (real-time controller and safety, roadmap Phase 3) from this file, the
   roadmap and the Phase 3 follow-ups above. Nothing of Phase 3 exists yet.

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Open-Meteo tilted-irradiance parameters; Energy-Charts and aWATTar APIs and terms | `research/integrations.md` | Phase 4, session with network access |
| Forecast.Solar / Open-Meteo Solar Forecast attribute names; Octopus attributes | `research/integrations.md` | Phase 4, read integration source |
| Energy colour variable names (`--energy-*`) | `frontend/src/app.css` | read HA frontend source |
| HA UI menu paths in the user docs (Settings → Apps → App store, Repositories, Show in sidebar; German labels) | `addon/*/DOCS.md`, `README.md`, `docs/user/*/quick-start.md` | maintainer checks on HA OS during the beta install |
| Supervisor Core proxy passes `supported_features` through | `suncore/ha/websocket.py` | first run on HA OS |
| Registry and energy-prefs shapes on cores older than dev (single `config_entry_id` on devices, unified grid source); `unknown_command` code without the `energy` integration; Supervisor proxy passes registry commands | `research/ha-websocket-api.md` | first run on HA OS; parsers tolerate both shapes |
| Section 51 EEG 2023 text could not be fetched; region `de` feed-in tiers are empty until the official BNetzA table is cited | `templates/region/de.yaml` | maintainer provides the table or a session with access to gesetze-im-internet.de |
| Whether Tibber's `total` includes grid fees (country dependent) | `templates/tariff/tibber.price.yaml` | live check on the maintainer's Tibber account |
| LG translation keys (inferred from source and unique ids; LG entity ids, modes and units checked live on 2026-10-14), other LG models' modes, Tibber tomorrow-price publication time, Solcast default day-sensor count | `research/device-integrations.md` | maintainer's HA with LG ThinQ, Tibber, Solcast (Phase 2 wizard test) |
| Wording of the quick start steps and the HA menu names in the new docs (written from the UI catalogues, not clicked through on a real HA) | `docs/user/*/quick-start.md` | maintainer follows the quick start on HA OS |
| Which Home Assistant tool lets users place files in the app's `/data/templates` | `docs/user/*/device-templates.md` | maintainer checks on HA OS |
| evcc REST API paths | `research/competitors.md` | Phase 6, MQTT API is verified and sufficient |
| Solve times on Raspberry Pi 4 | ADR-0003 | Phase 4 benchmark |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
