# Phase 2 – Setup assistant and device model

Status: **draft**, waiting for maintainer approval
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §4.1, §4.5 (common device
concepts), §4.10, §4.11, §7 (extensibility), §8, §9, §10;
[`docs/roadmap.md`](../roadmap.md) Phase 2; ADR-0004, 0005, 0006, 0007, 0011.
Branch: `phase-2/setup-wizard` (stacked PRs, see §3).

Phase 2 replaces the temporary settings page with a setup wizard that
describes the whole installation: region, grid connection, PV, battery,
tariff, controllable devices from templates, priorities, goals and operating
mode. It adds the device model and the template system (schema v1) that
Phase 3 (controller) and Phase 4 (planner, adapters) consume. **Nothing is
switched**: the safety gate stays structurally in shadow mode.

## 1. Goals and acceptance criteria

Phase 2 is done when (roadmap Phase 2 milestones):

1. **Wizard steps 1–6, 9, 10** (BRIEF §4.10): region/currency/time zone,
   energy-dashboard import, grid, PV, battery, tariff, operating mode and a
   summary with a system check (sensors fresh? units plausible? balance
   closes?). Every step has a short explanation, defaults, "Skip" where it
   makes sense and a live preview of mapped values. The live sign check
   asks "The battery is charging at 2.1 kW right now – correct?" and
   inverts with one click.
2. **Wizard steps 7–8:** add devices from a searchable template gallery,
   map entities, choose the power model and comfort limits; order
   priorities with drag & drop (keyboard accessible); pick goals
   ("battery full in the evening", "climate before export").
3. **Entity auto-detection:** candidates per role ranked by device class,
   unit, integration (platform), area and name, with a visible reason
   ("matches: power · W · kostal_plenticore"). Covers power, energy,
   battery SoC, price, climate, switch and number entities.
4. **Device model + template schema v1:** Pydantic models for the device
   instance config and for the template kinds `device`, `tariff`,
   `forecast`, `region`; JSON Schemas generated into `templates/schema/`;
   CI fails on drift and on an invalid template.
5. **First templates:** LG ThinQ climate, generic switch, generic climate,
   generic number load, Tibber tariff, Solcast forecast, plus region `de`.
   Each with `source`, `tested_with` and a simulator conformance scenario.
6. **Config versioning:** `site` v1 → v2 document migration (adds PV
   arrays, grid limits, battery parameters), new sections, migration test
   from v1 fixtures, export/import round-trip of all sections.
7. Phase 1 follow-ups closed: 422 issues rendered via catalogue keys,
   `/api/v1/entities` served from the cache, theme bridge from the ingress
   parent (best effort, see R6), automatic beta app version bump.
8. CI green (same gates as Phase 1 plus template validation and schema
   drift); Playwright E2E walks the full wizard in demo mode; axe WCAG 2.1
   AA on every step.

Exit (roadmap): the maintainer describes his whole system (BRIEF §9) with
the wizard, still in shadow mode.

## 2. Work packages

Owner = subagent that implements it (CLAUDE.md budget rule: Fable for the
schema review, safety sign-off and the final review; Sonnet for the rest;
Haiku for catalogue sync).

### WP1 – Verify HA interfaces · `ha-integration` (skill `verify-ha-api`)

Read from `home-assistant/core` on GitHub (the doc sites are unreachable
from the session) and record in `docs/research/`:

- Registry list commands and payloads: entity (`platform`, `device_id`,
  `area_id`, `entity_category`, `disabled_by`, `hidden_by`), device
  (`area_id`, `manufacturer`, `model`) and area registry. Clears the
  `UNVERIFIED` row in STATUS.
- `energy/get_prefs` response shape incl. `power_config` (STATUS item).
- LG ThinQ (`homeassistant/components/lg_thinq`): climate entity features
  and HVAC modes, energy sensors (today/yesterday, unit Wh) and their
  translation keys / unique ids, so the template can match them without
  guessing entity ids.
- Tibber (core): price sensor attributes and the price action, read-only.
- Solcast (HACS `BJReplay/ha-solcast-solar`): forecast sensor names and
  the `detailedForecast` attribute shape (P10/P50/P90).
- Anything still not verifiable stays `UNVERIFIED` in code and PR.

### WP2 – Device model and template schema v1 · `device-adapters`, review `architect` (Fable)

- `suncore/core/devices.py` (pure, `mypy --strict`, no sibling imports):
  `DeviceConfig` instance model – id, template id, name, `kind`
  (climate | switch | number | …), entity mapping, `PowerModel`
  (measured | counter_derived | learned | nominal, BRIEF §4.5),
  `ControlKind` (on_off | steps | continuous | setpoint | mode), priority,
  min run/pause, max switches per hour, comfort band, time windows,
  per-device `OperatingMode` (default shadow).
- `suncore/devices/templates/` – Pydantic models per template kind
  (`DeviceTemplate`, `TariffTemplate`, `ForecastTemplate`, `RegionTemplate`)
  with common header (`id`, `kind`, `schema_version: 1`, `name` key,
  `source`, `tested_with`, `capabilities`, `unverified` markers);
  role-based entity matchers (domain, device class, unit, platform,
  translation key) instead of hardcoded entity ids; loader for
  `templates/` with user overrides from `/data/templates/` by id
  (ADR-0006).
- `scripts/gen_schemas.py` → `templates/schema/*.json` (`--check` in
  `check.sh` and CI); `scripts/validate_templates.py` (schema + lint:
  unique ids, `source` present, name keys exist in catalogues).
- `architect` reviews the schema against Phase 3/4 needs (power models,
  battery and tariff adapters) before the first template is written.
  `schema_version: 1` is frozen at the end of Phase 2, not earlier.

### WP3 – First templates and conformance scenarios · `device-adapters`, `qa-engineer`

- `templates/device/lg.thinq_climate.yaml` (climate + Wh counter, no
  power sensor → `counter_derived`), `generic.switch.yaml`,
  `generic.climate.yaml`, `generic.number_load.yaml` (W, A or %),
  `templates/tariff/tibber.yaml`, `templates/forecast/solcast.yaml`,
  `templates/region/de.yaml` (currency EUR, negative-price rule for new
  PV, EEG tiers as data with source links; no legal advice, BRIEF §6).
- Simulator: entity metadata fakes for these integrations
  (`FakeHAConnection` and `tests/ha/fakes.py` learn the registry and
  energy-prefs commands from WP1); one conformance scenario per template
  (`tests/scenarios/<kind>/<id>.yaml`) checking that matching, units and
  signs resolve. Demo mode gets a climate unit, a plug and Tibber/Solcast
  sensors so the wizard and screenshots have real content.

### WP4 – Config sections and versioning · `backend-engineer`

- `SiteConfig` v2 (migration v1 → v2 in `ConfigStore`): PV arrays
  (`kwp`, azimuth, tilt, inverter, power entities), grid limits (import
  limit W, export limit W or % kWp, §14a yes/no + optional signal entity),
  battery capacity, min/max SoC, charge/discharge limits, control
  "observe only" (adapters come in Phase 4), house sensor optional.
- New sections: `region`, `tariff`, `devices` (list of `DeviceConfig`),
  `goals` (priorities + goals), `operation` (global and per-device mode),
  `wizard` (progress, completed steps).
- SQL `0002_config_history.sql`: previous section bodies kept on every
  `put` (last N per section) so a wizard save can be undone.
- Tests: migration from v1 fixture documents and a v1 export file,
  hypothesis round-trip export/import of all sections, newer-version
  refusal, history pruning.

### WP5 – HA metadata, detection and API · `backend-engineer`

- `ha/websocket.py` + `HAConnection`: registry list and `get_prefs`
  commands from WP1; registry snapshot cached and refreshed on
  `*_registry_updated` events (only if the event names are verified).
- `ha/candidates.py` → `ha/detect.py`: candidates for all roles with a
  score and `Reason` keys (device class, unit, platform, area, name
  tokens); excludes disabled/hidden/diagnostic entities; energy units
  included for counter roles.
- Energy-dashboard import: maps prefs (energy statistics) to power
  sensors of the same device where possible; everything else becomes a
  suggestion, never an automatic choice.
- `/api/v1/entities?role=…` served from the entity cache + registry
  snapshot (no `get_states` per request).
- Endpoints: `GET/PUT /api/v1/config/{section}`, `GET /api/v1/templates`
  (+ `/{id}`), `GET /api/v1/wizard/energy-prefs`, `POST
  /api/v1/wizard/check` (system check with `Reason` results),
  `GET /api/v1/preview` (live values of an unsaved mapping).
- 422 handler: Pydantic errors → `{field, key, args}` with catalogue
  keys (`api.validation.*`), checked by `check_reasons.py`.

### WP6 – Operating mode in config · `control-safety` (Fable sign-off)

- `operation` section stores global and per-device mode
  (shadow | assisted | automatic). In Phase 2 the `SafetyGate` keeps
  ignoring it (stays structurally shadow); the boundary test asserts the
  gate does not read the `operation` section yet.
- New devices always start in shadow (BRIEF §4.11), also on import.
- Sign-off documented in the PR.

### WP7 – Wizard steps 1–6, 9, 10 · `frontend-engineer`

- Routes `#/setup/<step>`; first start (no `wizard.completed`) opens the
  wizard, afterwards the settings page becomes an overview that jumps into
  single steps (BRIEF §4.10 "add, change, remove later").
- Components: `WizardShell` (progress, back/next/skip, saved state),
  `RolePicker` (replaces `EntityField`, shows ranking reasons), `SignCheck`
  (live plausibility question + invert), `UnitBadge`, `SystemCheck`.
- Step 10 shows the system check and the current flow; the "first plan
  preview" from BRIEF §4.10 arrives with the planner in Phase 4 (placeholder
  text, no fake plan).
- Theme bridge: read HA CSS variables from `window.parent` when same
  origin, otherwise keep `prefers-color-scheme` (R6).
- i18n keys en/de for every text; `i18n-sync` (Haiku) checks catalogues.

### WP8 – Wizard steps 7–8 · `frontend-engineer`

- Template gallery with search and filter by kind; device form generated
  from the template (entity roles, power model, comfort limits, min
  run/pause defaults from the template).
- Priority list with drag & drop via pointer events plus keyboard
  (up/down buttons, ARIA live announcements); no extra library (bundle
  budget).
- Goals: battery target SoC at a time, "climate before export",
  "night grid charging only" – stored as data, used from Phase 3/4.

### WP9 – Tests, E2E, screenshots · `qa-engineer`

- Vitest for form↔config mapping, ranking display, drag & drop reducer.
- Playwright: full wizard in demo mode (desktop, phone, tablet), re-entry
  from settings, import/export round trip, axe on every step.
- Screenshots of the wizard steps (light/dark, en/de) via the
  `demo-screenshots` skill into `docs/assets/screenshots/`.

### WP10 – Build, docs, STATUS · `ha-integration`, `docs-writer`

- `build.yml`: beta app version derived automatically (STATUS follow-up)
  so HA offers the update without a manual `config.yaml` bump.
- Docs: quick start en/de rewritten around the wizard, "Contribute a
  device template" guide, generated template compatibility list,
  `templates/README.md`, app `DOCS.md`.
- STATUS, roadmap, `UNVERIFIED` table updated.

## 3. Pull requests and order

Four stacked PRs (same pattern as Phase 1), each reviewed by `reviewer`
(Fable) before opening; 2-B additionally by `control-safety`:

| PR | Content | Depends on |
|---|---|---|
| 2-A `feat(devices,templates)` | WP1, WP2, WP3 | – |
| 2-B `feat(storage,ha,api)` | WP4, WP5, WP6 | 2-A |
| 2-C `feat(frontend): setup wizard` | WP7, WP8 | 2-B |
| 2-D `test,build,docs` | WP9 (remaining), WP10, beta release | 2-C |

```text
WP1 ─► WP2 ─► WP3 ─┐
        └──► WP4 ──┼─► WP5 ─► WP6 ─► WP7 ─► WP8 ─► WP9 ─► WP10
```

Delegation is sequential; at most two subagents in parallel (WP3 and WP4).
I integrate, run `./scripts/check.sh` and the frontend checks after every
WP and commit per WP.

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Python unit | pytest, hypothesis | device model validation, template loader/overrides, matcher ranking, migrations v1→v2, history |
| Templates | `validate_templates.py`, `gen_schemas.py --check`, `-m simulator` | schema, lint, conformance scenario per template |
| HA | fake WS server | registry and energy-prefs commands, cache-backed entity list |
| API | httpx ASGI client | config sections, templates, wizard check, 422 keys |
| Frontend unit | Vitest | wizard state, ranking reasons, priority reducer |
| E2E | Playwright against demo mode | full wizard, re-entry, export/import, axe |
| Safety | boundary test | gate still shadow-only, mode section not read |
| Coverage | pytest-cov | ≥ 80 % on core/safety (unchanged gate) |

No Docker daemon in the session: image and smoke test run in CI only.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Registry command names/payloads differ from the research note | detection fails on HA OS | WP1 verifies from HA source; detection degrades to the Phase 1 `get_states` rules when a command errors |
| R2 | Energy prefs only reference energy statistics, not power sensors | import maps nothing useful | treat import as suggestions, correlate via `device_id`; explain in the step |
| R3 | Schema v1 frozen before Phase 3/4 needs are known | breaking template changes later | `architect` review in WP2, schema frozen only at the end of Phase 2, versioned migrations for templates |
| R4 | LG ThinQ entity naming differs between firmware/integration versions | template does not match | match by translation key/device class/unit, not entity id; maintainer check (question 5) |
| R5 | Wizard scope (10 steps) grows large | PR 2-C hard to review | steps as independent components, shared shell; split 2-C into two PRs if > ~2 500 lines |
| R6 | Ingress iframe theme variables not reachable | UI ignores HA theme | best-effort bridge, fallback stays; maintainer checks on HA OS |
| R7 | Storing assisted/automatic mode is mistaken for active control | user expects switching | UI label "active from a later version"; gate ignores it (WP6 test) |
| R8 | `ENTITY_ID_PATTERN` in `core/site.py` forbids digits in the domain, HA may allow them | a valid entity is refused | compare with HA's `valid_entity_id` in WP1 and align the pattern |
| R9 | Bundle budget (250 kB) with gallery and drag & drop | CI size failure | no new UI library, lazy-load wizard route |

## 6. Open questions for the maintainer

Defaults in bold apply if unanswered.

1. **Four stacked PRs (2-A … 2-D)?** **Yes**, like Phase 1.
2. **Release 0.2.0 (release-please PR #7) before Phase 2 starts?**
   **Yes, merge it first**; Phase 2 does not depend on it.
3. **Regions:** **only `de` plus a neutral "other" profile** in Phase 2;
   AT/CH/BE later as community templates.
4. **Operating mode step:** **all three modes selectable and stored, but
   labelled "active from Phase 3"**; alternative: only shadow selectable
   until the controller exists.
5. **LG ThinQ verification:** could you send the attributes of one climate
   entity and its two energy sensors (Developer tools → States, or a
   diagnostics download)? **Default:** template from the integration source,
   marked `UNVERIFIED` until you confirm.
6. **Settings page:** **becomes an overview that opens single wizard
   steps**; the Phase 1 picker page is removed.

## 7. Out of scope for Phase 2

Any real device write and the controller (Phase 3); battery control
adapters incl. Kostal (Phase 4, needs verified registers); reading tariff
prices and forecasts, planner and plan preview (Phase 4); expert-mode
parameter pages (Phase 5); MQTT entities (Phase 6); Python plugins
(ADR-0006, later).
