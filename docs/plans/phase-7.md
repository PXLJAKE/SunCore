# Phase 7 – Design pass, documentation, translations and release 1.0

Status: **draft, waiting for the maintainer's approval** (2026-11-25, revised on the 6-O tip).
Every decision in §0 and every question in §10 has a recommended default; the defaults apply
until the maintainer decides.
Source of truth: [`docs/BRIEF.md`](../BRIEF.md) §4.9 (UI), §4.10 (wizard), §4.13 (HA surface),
§5 (security), §7 (open source, docs, i18n, community), §8 (definition of done), §9 (reference
system), §10 Phase 7; [`docs/roadmap.md`](../roadmap.md) Phase 7; [`docs/STATUS.md`](../STATUS.md)
"Open - Phase 7" and the `UNVERIFIED` table; [`docs/plans/phase-6.md`](phase-6.md) §8 (6-M, 6-O),
§9 and §10; ADR-0002, ADR-0005, ADR-0007, ADR-0008, ADR-0009, ADR-0010, ADR-0011, ADR-0015,
ADR-0016, ADR-0018 (with its addendum of 2026-11-25); and the new
[ADR-0019](../adr/0019-ui-design-system.md), [ADR-0020](../adr/0020-docs-site-and-translation.md)
and [ADR-0021](../adr/0021-ha-dashboard-surface-for-1-0.md) (PR 7-A).
Branches: stacked `phase-7/<letter>-<topic>` (§3), based on `phase-6/o-gaps` (§0.1).

Phase 7 turns a feature-complete beta into a public 1.0. Its first and largest part is a **UI
design pass**: the maintainer finds the panel "ok aber nicht hübsch und etwas unübersichtlich" and
wants to review it when it is finished. The rest closes the Phase 6 gaps that block a clean 1.0,
publishes the documentation site, opens the translation platform, decides the (small) Lovelace
scope, and makes the release verifiable: upgrades from every released schema, Pi 4 budgets measured
on aarch64, a security review, the maintainer's hardware acceptance and a final review.

**Nothing in Phase 7 weakens the safety layer.** The only new write path is the inverter limit
verification procedure (7-J); it writes through the gate in assisted mode with the user confirming
each step, and every invariant that protects against import or a looser limit stays in force
(ADR-0018 addendum). Shadow mode stays the default for every device and asset.

## 0. Context and decisions

### 0.1 Starting point (verified in the tree of `phase-6/o-gaps` on 2026-11-25)

- **Two lines of history must meet first.** The Phase 6 stack (`phase-6/a-plan` … `n-docs`,
  `o-gaps`) contains Phase 5 up to 5-H (analytics core 5-B, storage and `day_summary` /
  `accuracy_day` 5-C, history API 5-D, plan chart v2 5-F, history view 5-G, decision log 5-H) but
  **not** the tail: `phase-5/e-params` (parameter registry, `scripts/gen_params.py`),
  `i-expert-ui`, `j-backtest`, `k-whatif` and `l-docs` are not ancestors of `phase-6/o-gaps` (no
  `suncore/params`, no `scripts/gen_params.py`, no what-if route in the frontend). ADR-0014 and
  ADR-0015 are in the tree (written in 5-A); their 5-E/5-J implementations are not. The parameter
  reference, expert mode, backtesting and what-if must be integrated before the design pass
  touches the views (PR 7-B, skipped when `main` already has both stacks).
- **What 6-O closed (not planned again):** island and §14a notifications from the grid monitor
  (start at once, end after 120 s without the signal) and "EV target at risk" from the newest
  plan's `planner.ev.target_shortfall`; the `notify.quiet_hint` text (en, de) that matches the
  rate limiter; read-only MQTT entities for the grid (month peak, grid state, §14a, island), the
  battery (action, lease), the EV (mode, target SoC/energy/time, plug, phases, vehicle SoC), the
  heat pump (SG state, lift) and programs (job state, planned start), with golden discovery
  payloads.
- **What 6-O left open (planned here or deferred):** the site sensors `plan_cost_today`,
  `savings_today`, `autarky_today`, `pv_forecast_error`, `next_action` (in the catalogue with
  names, not in `SUPPORTED`); the MQTT event types `island_*`, `para14a_*`, `program_*`,
  `cheap_window_*`, `ev_target_at_risk` (only "device switched", "proposal" and "error" fire);
  HA **commands** for EV mode and target, program arming, comfort band and battery settings
  (deferred past 1.0, decision 9); a push for a target that the plan meets and the controller
  later misses (tied to the late-car root cause, WP9). The data for the five sensors already
  exists on this tip (`day_summary`, `DaySummaryJob.preview`, `accuracy_day`, the plan), so the
  HA work (7-H) does **not** wait for 7-B.
- **Frontend** (`frontend/`, Svelte 5.57, Vite 8.3, ECharts 5.6, `intl-messageformat` 12): views
  in `src/lib/views/` `Now`, `Plan`, `History`, `Decisions`, `Settings`, `Setup` (wizard, 10 step
  ids in `lib/wizard/steps.ts` that are stored in `wizard.completed_steps` and never change), and
  on the Phase 5 tail `WhatIf`. Navigation is five pill links in a wrapping header with the
  emergency stop, the mode badge and the connection state; on phones the header wraps into three
  rows.
- **Styling:** `src/app.css` defines HA-named colour variables (light, dark, `data-theme`), two
  layout tokens (`--sc-radius`, `--sc-gap`) and shared classes (`.card`, `.btn`, `.input`,
  `.notice`, `.chip-tag`); every component adds its own scoped sizes, paddings and colours. The
  theme bridge (`lib/theme/bridge.ts`) copies twelve HA theme variables from the parent frame
  (copying on a running HA is `UNVERIFIED`, STATUS). The variable names themselves, including
  the five `--energy-*` names SunCore uses, are **verified** against the HA frontend source
  (`src/resources/theme/color/color.globals.ts`, branch `dev`, read 2026-11-25); the STATUS row
  "Energy colour variable names" can be closed. There is no card component, no type or spacing
  scale, no elevation, and no shared chart theme: the plan chart takes a `ChartColors` object
  (`lib/plan/chartOption.ts`), the history charts build their own (`lib/history/chartOptions.ts`).
- **Bundle** (`frontend/scripts/bundle-size.mjs`, one language counted): Phase 6 tip first paint
  123.5 of 125 kB, total without ECharts 228.6 of 250 kB (6-M). The Phase 5 tail measured 106.3 kB
  first paint on its own line; the merged figure is unknown until 7-B.
- **Screenshots:** `frontend/e2e/screenshots.spec.ts` (skill `demo-screenshots`) captures phone
  375×667 and tablet 768×1024, light and dark, en and de. No desktop size; the committed set is
  light only and has no Phase 6 views. The demo house has no EV, heat pump or program device.
- **Demo and MQTT:** the HA entity service is not built in demo mode
  (`app/ha_entities.py`: `None` in demo mode), so neither the screenshots nor a demo-based smoke
  test exercise MQTT today (WP13 adds a CI-only switch).
- **Docs:** user guides en/de under `docs/user/<lang>/` (identical structure), the compatibility
  list `docs/user/compatibility.md` generated by `scripts/gen_compat.py` (one file, no language
  folder), screenshots under `docs/assets/screenshots/`. The guides link out of `docs/user` to
  ADRs, `templates/README.md`, `CONTRIBUTING.md`, `addon/suncore/DOCS.md` and the screenshots.
  The parameter reference (`scripts/gen_params.py` into `docs/reference/parameters*.md`) exists
  only on the Phase 5 tail. There is **no `mkdocs.yml`**. ADR-0008 chose MkDocs Material +
  `mkdocs-static-i18n` with `docs/` as the site root.
- **Docs toolchain risk (new since ADR-0008, verified 2026-11-25):** the Material for MkDocs blog
  post "MkDocs 2.0" (published 2026-02-18, updated 2026-10-02) says MkDocs 2.0 (pre-release
  `2.0.dev2` since 2026-08-30) has no plugin system and is incompatible with Material; Material
  9.7.5 limits MkDocs to `<2`; critical maintenance is extended to 2027-05-05; the team
  recommends its successor Zensical, which is "not fully there yet" on plugin support. Material's
  newest release is 9.8.1 (2026-11-07; 9.8.0 requires Python ≥ 3.11); its `requirements.txt` on
  `master` pins `mkdocs>=1.6,<2`. `mkdocs-static-i18n` is 1.3.1 on `main` (MIT, `mkdocs>=1.5.2`).
  This needs a superseding ADR (ADR-0020).
- **Translations:** frontend catalogues `frontend/src/lib/i18n/locales/{en,de}.json` (2214 keys);
  the backend catalogue `suncore/i18n/catalogue/<lang>.json` (683 keys) is **generated** from them
  by prefix (`scripts/gen_backend_catalogue.py`, ADR-0016), so a Weblate "backend" component
  (ADR-0007) would be a second source of truth; app option translations
  `addon/*/translations/*.yaml`.
- **Release:** release-please manifest mode, stable app `addon/suncore` at `0.2.0` with
  `stage: experimental`, beta app (`0.3.0-beta.74` on this tip) derived as
  `<stable minor + 1>-beta.<n>` by `scripts/beta_version.py` (ADR-0009 addendum), images for
  aarch64 and amd64 through `home-assistant/builder`, Cosign, smoke test `scripts/smoke_image.sh`
  (demo mode, idle memory after 60 s ≤ 300 MiB, per architecture on `main`). Release PR #17
  (stable) is still open.
- **Storage schemas:** `suncore.db` 6 (Phase 6), `timeseries.db` 3; every config section carries
  its own `schema_version`. Earlier versions refuse newer databases ("export before updating").
- **Community files:** `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, issue templates,
  PR template, `NOTICE`, Dependabot and CodeQL exist. The bug template asks for "Diagnostics →
  Export", which **does not exist** (BRIEF §5 "anonymisierter Diagnose-Export").

### 0.2 Decisions (default, maintainer may override)

1. **Design pass before everything else on the frontend**, in reviewable steps: tokens, base
   components, shell and navigation first (7-D), then the views (7-E, 7-F, 7-G). Two review stops
   for the maintainer with screenshots (after 7-D and after 7-G). ADR-0019 records the design
   system.
2. **Tokens layer over the HA theme, not instead of it** (ADR-0019): HA theme variables stay the
   input (the bridge is unchanged); semantic `--sc-*` tokens derive from them with our defaults as
   fallback. The panel keeps following the user's HA theme (BRIEF §4.9 "HA-Look").
3. **Navigation: four destinations** – *Now*, *Plan*, *Analysis* (tabs History, Decisions,
   What-if), *Settings*. Top bar on tablet and desktop, bottom bar on phones; the emergency stop is
   in the top bar on every size. **Hash routes do not change** (`#/decisions`, `#/whatif`,
   `#/?device=` keep working; notifications and the decision log link to them).
4. **No new runtime dependency for the design** (no UI kit, no icon font, no CSS framework).
   Icons are inline SVG paths, licence checked and attributed in `NOTICE`. Budgets unchanged.
5. **Docs site: MkDocs 1.6 + Material 9.8 + `mkdocs-static-i18n` 1.3, pinned in a lockfile**,
   published from `docs/user` (en, de) with the generated reference and the compatibility list
   under each language folder; engineering docs stay on GitHub and are linked, not published.
   Re-evaluate Zensical before 2027-05-05 (ADR-0020, supersedes ADR-0008).
6. **Weblate for the UI and the app options only**; the backend catalogue stays generated; user
   docs are translated by pull request for 1.0 (ADR-0020, supersedes the component list of
   ADR-0007).
7. **Lovelace for 1.0 = a documented dashboard recipe** with built-in cards on the MQTT
   entities; a custom card and the companion integration (ADR-0005) come after 1.0 (ADR-0021).
8. **Phase 6 gaps closed in Phase 7:** inverter verification procedure with a dialog (7-J), panel
   fields for `para14a`, `shed_rank`, the island keep list, evcc `loadpoint_id` and
   `topic_prefix` and a manual MQTT broker (7-G), COP at the current weather and model quality on
   the heat pump card (7-I), the five site sensors and the missing MQTT event types (7-H), the
   anonymised diagnostics export (7-I), the late-car target gap (7-K, root cause first).
9. **Not in 1.0** (default): HA commands for EV mode/target, program arming, comfort band and
   battery settings (6-O, own PR with `control-safety` after 1.0), grid pre-heating execution,
   `generic.heat_pump_select`, `button.press` remote start, evcc plan clear and mode reset, the
   live `flex` feed (polling stays unless the Now redesign needs it), the standalone container,
   built-in PV/price fallbacks, calibration charging, per-phase limits, V2H, several vehicles per
   charger, EEBus and the certified §14a path.
10. **Versioning:** the stable app goes from `0.2.0` straight to `1.0.0`; release candidates are
    betas `1.0.0-beta.<n>` (the base derivation is changed in a PR, as ADR-0009's addendum
    prescribes). `stage: experimental` is removed from the stable app at 1.0 (verified against the
    Supervisor source in 7-N). An upgrade from 0.2.0 to 1.0.0 is a release gate.
11. **1.0 needs the maintainer's hardware acceptance** stages H0–H6 (§6); stage H7 (first day with
    real surplus) is a gate too unless the maintainer decides otherwise (question 10). Device
    families without hardware (wallbox, evcc, heat pump, programs, inverter limit) ship as
    "simulator-verified", stay shadow by default and capped at assisted until confirmed.

## 1. Goals and acceptance criteria

Phase 7 is done when:

1. **Design pass:** every view uses the token system and the shared components; a scripted check
   finds no raw colour literals outside the token files; the navigation follows decision 3; the
   maintainer has reviewed before/after screenshots (phone 390×844 and desktop 1280×800, light and
   dark, en) of Now, Plan, Analysis, Settings and the wizard and accepted them (review stop 2).
2. **Accessibility:** axe (WCAG 2.1 AA) passes on every view and wizard step at phone and desktop
   size in light and dark; a unit test proves ≥ 4.5:1 for text tokens and ≥ 3:1 for UI and chart
   line tokens against their surfaces in both schemes; keyboard-only walk-through of the main
   flows recorded in Playwright; `prefers-reduced-motion` stops the flow animation.
3. **Budgets:** first paint ≤ 115 kB gzip (budget stays 125), total without ECharts ≤ 250 kB,
   ECharts chunks within the Phase 5 budgets; no new runtime dependency.
4. **Phase 6 gaps:** the items of decision 8 are merged with tests; the inverter verification
   procedure has a `control-safety` sign-off table; `limit_verified` can only be set by a passed
   procedure (or an import that keeps the identity).
5. **Docs site:** `mkdocs build --strict` passes in CI; the site is deployed to GitHub Pages on a
   release with `latest` and `1.0`; English and German user docs have the same pages; the
   parameter reference and the compatibility list are generated and checked; screenshots of every
   view come from the demo (light and dark, en and de).
6. **Weblate:** the project is live with the components of decision 6, pushes to a `weblate`
   branch, CI accepts its pull requests (generators re-run, ICU checks pass), README badge.
7. **HA dashboard:** the dashboard recipe works on the maintainer's HA with the MQTT entities
   (stage H4) and is in the docs en/de.
8. **Release readiness:** migration tests from every released `suncore.db` (1–6) and
   `timeseries.db` (1–3) schema and every config section version; an upgrade test of the 0.2.0
   stable data directory to 1.0; the loaded smoke test (MQTT broker, full-house demo, plans
   running) stays below 300 MiB on aarch64 and amd64; the planner benchmark "full house" ≤ 10 s on
   the aarch64 CI runner (and on a Pi 4 or HA Green if available, question 14); stable and beta
   app configs pass the app linter; the security review has no open high or medium finding.
9. **Acceptance:** stages H0–H6 of §6 are done on the maintainer's system without an open blocking
   deviation; H7 per question 10. A final `reviewer` (Fable) pass on the whole Phase 7 diff.
10. **Release:** `1.0.0` published through release-please with the `prepare-release` skill; the
    stable image manifest lists aarch64 and amd64 with a Cosign signature; release notes say what
    is simulator-verified only; Discussions announcement.

Exit (maintainer): installs `1.0.0-beta.<n>`, reviews the design gallery, runs §6, reads the docs
site in German, sees the Weblate project, approves the release PR.

## 2. Work packages

Owner = subagent that implements it. Budget (CLAUDE.md, BRIEF §10 Phase 7 "Sonnet/Haiku,
Abschluss-Review mit Fable"): **Fable** for the design system spec (ADR-0019 and the token values,
with `frontend-engineer`), the inverter verification procedure (`control-safety`), the late-car
root cause if it lands in the controller, the security review and the final review; **Sonnet** for
frontend views, backend gaps, docs site, release engineering; **Haiku** for catalogue sync and
mechanical screenshot runs. At most two subagents in parallel; every WP ends with a commit and a
push; every frontend PR attaches before/after screenshots (BRIEF §8).

### WP1 – Plan and ADRs · `architect`, reviewed by `reviewer`

This plan; **ADR-0019** UI design system (token layers over HA theme variables, component set,
navigation, chart theme, budgets); **ADR-0020** documentation site and translation workflow
(supersedes ADR-0008 and the component list of ADR-0007: MkDocs pinned with an exit plan, site
root `docs/user`, Weblate components, docs translated by PR; one ADR because the docs translation
path is the point where both meet); **ADR-0021** HA dashboard surface for 1.0 (dashboard recipe,
custom card and companion integration after 1.0; supersedes the Phase 7 timing of the companion
integration in ADR-0005); an **ADR-0018 addendum** (ADR-0018 is still proposed) for the inverter
verification procedure. Boundary rules: unchanged. The MkDocs layout spike needs a build and
moves to the first commit of 7-L (§8, 7-A).

### WP2 – Integration of the Phase 5 tail · `backend-engineer`, `frontend-engineer`

Only if `main` does not contain both stacks when Phase 7 starts. Merge `phase-5/l-docs` (which
carries 5-E, 5-I, 5-J, 5-K) into the Phase 6 tip including 6-O; resolve the catalogues as unions,
the router and `vite.config.ts` chunks, the STATUS and the boundary tests; regenerate OpenAPI,
`schema.d.ts`, `gen_params`, `gen_backend_catalogue`, `gen_compat`; record the merged bundle
figures. `control-safety` reads only the conflict resolutions in `safety/` and in the parameter
registry's safety bounds (ADR-0015 decision 4). No new behaviour.

### WP3 – Demo devices for the flexible loads · `backend-engineer` (Sonnet)

The demo house gains, behind `SUNCORE_DEMO_FLEX=1` (on in Playwright and the screenshots, off by
default like `SUNCORE_DEMO_BATTERY_CONTROL`): one EV on a generic wallbox with the "arrives late"
profile, one washing machine on a plug (`generic.program_*`), one heat pump with SG-Ready
(`generic.heat_pump_*`), all from the existing simulator devices (`SimWallbox`, `SimProgram`,
`SimHeatPump`) and templates, all in shadow. The demo never touches `/data` (unchanged). This
makes the Phase 6 views reachable for the design pass, the screenshots and acceptance step H6.

### WP4 – Design system and shell · `frontend-engineer` (Sonnet), spec with Fable

**Tokens** (`frontend/src/lib/theme/tokens.css`, mirrored read-only in `tokens.ts` for charts):

| Group | Tokens | Values (light / dark default; HA theme overrides the colour inputs) |
|---|---|---|
| Colour inputs | the twelve HA variables of `THEME_MAP` plus `--energy-*` | unchanged names (verified, §0.1); bridge unchanged |
| Surfaces | `--sc-surface`, `--sc-surface-raised`, `--sc-surface-sunken`, `--sc-outline`, `--sc-outline-strong` | derived from `--card-background-color`, `--primary-background-color`, `--divider-color`; dark uses lighter raised surfaces instead of shadows |
| Text | `--sc-text`, `--sc-text-muted`, `--sc-text-on-accent` | from `--primary-text-color`, `--secondary-text-color`, `--text-primary-color` |
| Accent and status | `--sc-accent`, `--sc-ok`, `--sc-warn`, `--sc-error`, `--sc-info`, each with `-bg` (tint) and `-fg` | tints via `color-mix()` with a static fallback value first (older webviews) |
| Energy | `--sc-energy-pv`, `-grid-in`, `-grid-out`, `-bat-in`, `-bat-out`, `-house`, `-device` | from the `--energy-*` inputs; one palette for flow, cards and charts |
| Type scale | `--sc-font-xs` 12, `-sm` 14, `-md` 16, `-lg` 18, `-xl` 22, `-2xl` 28 px; weights 400/600; `--sc-leading-tight` 1.2, `-normal` 1.45; `font-variant-numeric: tabular-nums` for values | rem based, honours browser zoom |
| Spacing | `--sc-space-1` … `-8` = 4, 8, 12, 16, 24, 32, 48, 64 px | replaces `--sc-gap` (kept as alias until all views moved) |
| Radius | `--sc-radius-sm` 6, `-md` 12, `-lg` 16, `-pill` 999 px | `--sc-radius` alias kept |
| Elevation | `--sc-elev-0` none, `-1` card, `-2` dialog and bottom bar | light: soft shadow; dark: surface step + outline |
| Motion | `--sc-motion-fast` 120 ms, `-normal` 200 ms | zero under `prefers-reduced-motion` |
| Layout | `--sc-content-max` 1200 px, breakpoints 600 / 1024 px (CSS only) | phone < 600, tablet 600–1023, desktop ≥ 1024 |

**Components** (`frontend/src/lib/ui/`): `Card` (header with title, optional icon, status chip
and actions; body; optional footer; variants `default`, `attention`, `quiet`; density `regular` or
`compact`; collapsible body with a disclosure button), `Stat` (value, unit, label, quality state
from the existing `UnitBadge` rules), `Chip`, `Button` (primary, secondary, ghost, danger; 44 px
touch target), `SegmentedControl` (24/48 h, periods, tabs), `SectionHeader`, `EmptyState`,
`Disclosure` (for "Why?" and advanced fields), `Sheet` (dialog on desktop, bottom sheet on phones;
`ConfirmDialog` moves onto it), `Banner`. Existing global classes stay until their last user is
migrated, then they are removed in the last view PR.

**Shell and navigation:** app bar with the product name, the four destinations (decision 3),
a status cluster (mode chip, connection dot with text), and the emergency stop; on phones a fixed
bottom bar with the four destinations (icon + label) and the emergency stop and status in a
compact top bar. Banners (emergency stop, demo, stale data, island, §14a) share one `Banner`
component and stack in a fixed order of severity. The skip link and the focus move after navigation
stay. *Analysis* is a container route with tabs that maps to the existing `#/history`,
`#/decisions`, `#/whatif` hashes.

**Chart theme** (`frontend/src/lib/charts/theme.ts`): one function builds the ECharts option
defaults (text and muted colours, grid lines at low contrast, axis label size `--sc-font-xs`,
tooltip surface, radius and shadow, the energy palette, P10–P90 band opacity, legend at the bottom,
`hideOverlap` labels) from computed tokens; the plan and history builders take it instead of their
own colour objects; a theme change (bridge or `prefers-color-scheme`) re-applies it. Plain object,
no ECharts `registerTheme` import, so the chunks do not grow.

**Guards:** `frontend/scripts/check-tokens.mjs` (in `npm run verify`) fails on hex, `rgb()` or
`hsl()` literals outside `tokens.css` and `bridge.ts`; a Vitest contrast test parses
`tokens.css` and checks the pairs of criterion 2 with `luminance()` from `bridge.ts`.

**Screenshot harness:** `screenshots.spec.ts` gains the sizes phone 390×844 and desktop 1280×800
(tablet stays for the docs that use it), the dark scheme everywhere, the Phase 6 views
(EV, heat pump and program cards, grid badges, HA entities and notification settings) and the
Phase 5 tail views (expert mode, what-if). A `SCREENSHOT_SET=before|after` switch writes to a
scratch folder for PR comparisons; only the docs set is committed (question 18). The "before" set
is taken on the 7-D base commit and attached to 7-D.

### WP5 – Views · `frontend-engineer` (Sonnet)

**Now (7-E).** Order from top: (1) banners only when something is wrong; (2) *Energy now*: the
flow diagram with four `Stat` values (PV, house, grid, battery SoC and power) – on phones the
compact flow above a 2×2 value grid; the surplus figures (available, allocated, deficit) join this
card instead of a separate one; (3) *Needs you*: assisted proposals and confirm results, only when
there are any; (4) *Next up*: the next three planned actions with "Why?" and a link to the plan;
(5) *Devices*: a grid of uniform device cards (collapsed: name, state, power, mode chip and one
primary action; expanded: estimate, held/paused details, override form, last decision, "Why?");
the battery, EV, heat pump and program cards use the same `Card` with a kind-specific body;
(6) *Recent decisions*: the last three with a link to *Analysis → Decisions*. Device cards keep
`id="device-card-<id>"` and the focus behaviour for `#/?device=`.

**Plan and Analysis (7-F).** Plan: one header row (24/48 h `SegmentedControl`, replan button,
planner status chip), the three chart panels on the chart theme, the Gantt with the token palette,
the text alternative in a `Disclosure`; the forecast panel and the ML load model panel become
collapsible cards below. Analysis: tabs History, Decisions, What-if; history tiles become `Stat`
cards; the decision table and its phone cards, the what-if form and results restyled; filters in a
`Sheet` on phones.

**Settings and wizard (7-G).** Settings becomes an index with grouped rows and one sub-page per
group (two-pane on desktop): *Your system* (site and grid, PV and forecasts, battery, tariff – each
opens its wizard step in edit mode), *Devices* (list, add from the gallery, per-device fields),
*Goals and operating mode*, *Home Assistant* (entities and MQTT incl. the manual broker,
notifications), *Advanced* (expert mode, configuration history, backup and import, diagnostics
from 7-I, about/versions). The wizard keeps its ten step ids but shows four stages (Basics: region,
energy import; Your system: grid, PV, battery, tariff; Devices and goals: devices, priorities;
Finish: mode, summary) with a stage progress indicator, one sticky footer (Back, Skip where
allowed, Next), advanced fields behind `Disclosure`, the live preview in the same place on every
step. **Every step and every settings sub-page becomes a lazy chunk** (first-paint relief, 6-M
question 4). The Phase 6 panel fields land here: per-device `para14a` flag and `shed_rank`
(device editor, *Grid* section), the island keep list (`GridConfig` backup `keep_devices`, grid
step), evcc `loadpoint_id` and `topic_prefix` (evcc device editor), the manual broker
(`MqttConfig.broker = manual` with host, port, TLS, user, masked password). All fields exist in
`core` today; the PR changes no validation.

### WP6 – HA surface completion · `ha-integration` (Sonnet)

6-O already announced the read-only grid, battery, EV, heat pump and program entities and wired
the island, §14a and "EV target at risk" notifications; none of that is redone. What is left,
read-only and without a command path:

- **Five site sensors** that are in the catalogue (`ha/mqtt/catalogue.py`, names en/de in the
  backend catalogue) but not in `SUPPORTED`: `savings_today` and `autarky_today` from today's
  `DaySummaryJob.preview` (5-C/5-D, savings against the baseline the history view shows by
  default), `plan_cost_today` from the current plan's intervals of the local day,
  `pv_forecast_error` from the newest `accuracy_day` row of the PV source (MAE, unit per the
  catalogue entry), `next_action` from the plan's next planned action, rendered by the backend
  ICU renderer in the configured language. A missing source gives no value (HA shows
  "unknown"), as in 6-O. Cadence: the existing state publisher's deadbands; the day values at
  most every 5 min.
- **Event types** of the MQTT event entity: `island_start`/`_end`, `para14a_start`/`_end`,
  `program_*`, `cheap_window_*` and `ev_target_at_risk`, fired from the same observations that
  drive the notifications since 6-O (one source of truth; the event fires regardless of quiet
  hours and the push cap, which only govern pushes). The exact type names come from the
  existing catalogue entries; nothing new is invented.
- Golden discovery payloads, `SUPPORTED` updated, en/de names. The device classes and units that
  6-O left `UNVERIFIED` (`plug`, `battery`, `timestamp`, unit `K` without a device class) and
  those of the five sensors (`monetary`, `%`) are checked against the HA core source with the
  `verify-ha-api` skill first.

Depends on 7-A only: the data sources exist on the 6-O tip. If 7-B lands first, nothing changes.

### WP7 – Diagnostics, heat pump live values · `backend-engineer`, `frontend-engineer` (Sonnet)

- **Diagnostics export** (BRIEF §5, bug template): `GET /api/v1/diagnostics` returns a JSON
  document: versions (app, schemas, HA core from `get_config`), architecture, configuration with
  secrets masked and entity ids, device names and HA area names replaced by stable pseudonyms
  (salted hash per export), the last 200 decisions with the same pseudonyms, the connection and
  watchdog state, planner status and solver statistics, `timeseries.db` `size_report`, the
  too-new-`timeseries.db` state (closes the 4-B/4-H follow-up), the last 500 log lines of level
  warning and above with the same redaction. Never coordinates, tokens, passwords or MQTT
  credentials. Built from an allowlist of fields, not a denylist. Tests assert the absence of
  every secret and real id of the demo configuration. Panel: *Settings → Advanced → Diagnostics*
  with a download button and a preview.
- **Heat pump card:** the control state gains (additive) the COP at the current outdoor
  temperature from the device's COP curve (labelled "assumed" when it is the generic curve) and the
  thermal model quality (fit error and whether the planner uses it). OpenAPI and types
  regenerated; the card shows both.
- **Live `flex` feed:** not built by default (decision 9). Built only if the Now redesign shows
  visible lag with the 10 s polling (then: a `flex` block in the existing live snapshot, no new
  message type).

### WP8 – Inverter limit verification procedure · `control-safety` (Fable), `frontend-engineer`

Closes 6-G/6-M: without it `limit_verified` stays false and no limit is ever written. Design in
the ADR-0018 addendum (7-A); summary:

- A procedure object in `app/` (no new writer): it **proposes** `InverterLimitAction` intents with
  `Origin.USER` through the services layer; the gate judges them like any assisted proposal and
  the user confirms each write in the dialog. Precondition: `operation.inverter_mode` is
  assisted (the procedure never raises a mode; the dialog asks the user to set it).
- **Invariants:** X1, X2, X4, X5 and X6 hold unchanged. X2 (an unverified export limit is read as
  an AC limit and must leave house load + battery charging + 500 W) is never waived. Only X3
  ("below the current export only with an exhausted chain") is waived, and only while the gate
  holds an active **verification window**: a new additive `GateInputs` field set by the
  procedure (id, expiry ≤ 10 min, the lowest limit the window allows). Outside the window, or
  for any other origin, X3 applies as before. No change to `Plan`, `Intent`, `Decision` or
  `Reason` (ADR-0011).
- Steps: (1) preconditions – adapter built, limit entity readable, inputs fresh and `OK`, no
  emergency stop, no island, battery not held by SunCore, export ≥ 1 kW **and** ≥ house load +
  battery charging + 1.5 kW, stable for 2 min; (2) read and record the user's current limit
  (the baseline); (3) propose the test limit `max(house + battery charging + 700 W, export −
  1 kW)` (as a percentage of `nominal_ac_w` for `ac_limit`); export must follow within the
  adapter's settle time plus 60 s and the read-back must equal the written value; the procedure
  records whether export fell to about the limit (export semantics) or to about the limit minus
  house and battery (AC semantics) and fails when this contradicts the configured capability;
  (4) release – the baseline is restored and read back, export recovers; (5) result. A failed
  check, a timeout, a rising house load (X2 lease end), an emergency stop, a lost connection or
  closing the dialog releases at once through the gate's release path; a crash is covered by the
  existing restart release (6-G).
- Pass sets `inverter.limit_verified = true` together with the adapter identity token and a
  timestamp (additive fields in `InverterConfig`, config history author `user`); a changed
  identity resets it (existing rule). Fail stores the reason key for the panel; nothing else
  changes.
- Endpoints: `POST /api/v1/inverter/verification` (start, 409 when preconditions fail, with
  reason keys), `GET .../verification` (state machine view), `POST .../verification/abort`.
  Wizard step 5 replaces the disabled block of 6-M with the dialog; the same dialog is reachable
  from *Settings → Your system → Grid*.
- Simulator scenarios: pass (export and AC semantics), semantics mismatch, export does not
  follow, read-back mismatch, house load rises mid-step, connection lost mid-step, emergency stop
  mid-step, crash mid-step (restart releases), window expired, REST and modbus adapters.
  `control-safety` sign-off table in the PR.

### WP9 – Late-car target gap · `qa-engineer` then `optimizer-engineer` / `control-safety`

The scenario `ev_arrives_late` ends at about 77 % of an 80 % target while the plan reports no
shortfall (6-O), so the gap is in execution or in the plan's energy accounting. Step 1 (QA,
Sonnet): instrument the scenario, compare planned and delivered energy per interval, name the
cause (charging losses, the 6 A floor, phase pauses, band hysteresis, plan refresh timing). Step 2:
fix where the cause is – planner (`optimizer-engineer`, Fable) or controller (`control-safety`,
Fable, **CS**). Acceptance: the late car meets a feasible target by its deadline in the simulator
within 1 %-point, without violating G1 or E1–E9; if the target is infeasible the "EV target at
risk" notification (6-O) fires. Whether a second rule is needed for a target the plan meets and
the controller still misses (6-O left it out on purpose) is decided from the root cause and
recorded in §8. Built only if the maintainer confirms question 12 (default yes).

### WP10 – Documentation site · `docs-writer` (Sonnet), `backend-engineer` for CI

- **Layout spike first** (moved from 7-A): a throw-away `mkdocs build --strict` of the current
  `docs/user` with the pinned versions, to confirm the `mkdocs-static-i18n` folder structure
  (`en/`, `de/`), shared assets outside the language folders and the language switcher. Until
  it passes, the plugin behaviour with this layout is `UNVERIFIED`; if it fails, the ADR-0020
  fallback (suffix structure) applies.
- `mkdocs.yml` per ADR-0020: `docs_dir: docs/user`, `mkdocs-static-i18n` with the folder
  structure, Material theme with light/dark palettes from the ADR-0019 accent values, search,
  Mermaid, `navigation.instant` off (static hosting). Pinned versions in a `docs` dependency group
  of `pyproject.toml` (lockfile).
- **Links and assets:** screenshots move from `docs/assets/screenshots/` to
  `docs/user/assets/screenshots/` (one copy, both languages); links from the guides to files
  outside `docs/user` (ADRs, `templates/README.md`, `CONTRIBUTING.md`, `addon/suncore/DOCS.md`)
  become absolute links to the repository on GitHub, so `--strict` passes and the pages still
  read on GitHub.
- `scripts/gen_params.py` writes `docs/user/en/reference/parameters.md` and
  `docs/user/de/reference/parameters.md` (the old `docs/reference/` paths stay as redirect stubs
  for one release); `gen_compat.py` writes `docs/user/<lang>/compatibility.md` (the single
  `docs/user/compatibility.md` becomes a stub).
- New pages en/de: Concepts (planner, controller, safety, shadow/assisted/automatic), FAQ,
  Troubleshooting (incl. diagnostics export), Translate (Weblate), Dashboard recipe (WP11),
  Release notes and upgrade ("export before updating", supported upgrade paths), Security
  (link to `SECURITY.md`), the Phase 5 guides from the tail. Existing guides keep their file
  names; links fixed by `--strict`.
- CI: `docs-lint.yml` gains `mkdocs build --strict` on pull requests; a `docs-deploy` job in the
  release workflow publishes with `mike` (`latest`, `x.y`) to `gh-pages`. GitHub Pages must be
  enabled by the maintainer (question 17). The deployed site cannot be opened from the session
  (`*.github.io` is blocked for builds and checks there): the maintainer checks it.
- Screenshots: the final set (all views, phone and desktop, light and dark, en and de; long pages
  desktop only) is generated in 7-P after the design pass; each PNG under 300 kB.

### WP11 – HA dashboard recipe · `docs-writer` (Sonnet), `ha-integration` review

ADR-0021 option A: a page with a ready YAML dashboard (sections view) built only from cards that
ship with HA (`tile`, `entities`, `glance`, `button`, `heading`, `history-graph`; type names
checked in the HA frontend source, `create-card-element.ts`, branch `dev`, 2026-11-25) over the
SunCore MQTT entities: status and mode, energy numbers (the five site sensors of 7-H), next
action, emergency stop and "back to normal", per-device mode, the grid view (6-O), a compact
variant for small wall tablets. The page shows how to find the entity ids instead of guessing
them; card options are verified against the HA frontend source with the `verify-ha-api` skill.
Tested on the maintainer's HA (stage H4).

### WP12 – Weblate onboarding · `i18n-sync` (Haiku), maintainer

- Maintainer: create the Hosted Weblate project (libre plan; the free tier is still `UNVERIFIED`,
  STATUS), connect the GitHub repository, grant push to the `weblate` branch.
- Components (ADR-0020): `frontend` (monolingual JSON, `frontend/src/lib/i18n/locales/*.json`,
  source `en.json`) and `app-options` (HA YAML, `addon/suncore/translations/*.yaml`; the beta
  folder's copy is generated from it by a small sync step in `scripts/` when the option sets are
  equal, checked in CI; otherwise a second component). Checks: placeholders and the ICU
  MessageFormat check of Weblate (flag name `UNVERIFIED` against the Weblate source; verify
  before configuring). The YAML file format id for HA's translation layout is `UNVERIFIED` too.
- Repository: `.github/workflows/weblate-sync.yml` runs on pull requests from the `weblate`
  branch, re-runs `gen_backend_catalogue.py` and `check_reasons.py`, and pushes the regenerated
  files to the PR branch (so a translator cannot leave the backend catalogue stale); messages
  outside the backend renderer's ICU subset fail with a clear message. `CONTRIBUTING.md` and the
  docs page "Translate"; README badge. `.claude/skills/sync-translations` updated for the Weblate
  round trip.

### WP13 – Release readiness · `qa-engineer`, `ha-integration` (Sonnet)

- **Migration and upgrade tests:** frozen SQL dumps per released schema in
  `tests/fixtures/db/` (`suncore_v1.sql` … `suncore_v6.sql`, `timeseries_v1.sql` …
  `timeseries_v3.sql`) produced once from the tagged code with representative rows; tests migrate
  each to the current schema and read every table through the repositories; config documents of
  every section version (`v1` … current) load and migrate; an export from 0.2.0 imports into 1.0;
  newer-than-supported databases are refused with the documented message; a hypothesis test feeds
  random valid old documents through the section migrations.
- **0.2.0 → 1.0 upgrade test:** a CI job starts the `0.2.0` stable image (GHCR) with a seeded data
  directory, stops it, starts the candidate image on the same volume and checks `/health`, the
  configuration, the decision log and that the mode is still shadow. Seeding uses the API of
  0.2.0 only (no hand-made files).
- **Loaded smoke test:** `scripts/smoke_image.sh` gains a `LOADED=1` mode: a Mosquitto container
  (pinned digest) on the same network, the demo with `SUNCORE_DEMO_FLEX=1` and battery control,
  MQTT on (manual broker), a forced replan every minute for 10 min; the peak and the final memory
  must stay below 300 MiB; runs on both architectures on `main`. Because the HA entity service is
  not built in demo mode (§0.1), the demo gains a CI-only switch `SUNCORE_DEMO_MQTT=1` that
  builds it against the manual broker (off by default, logged at start; MQTT commands then act
  on the demo runtime only, which never touches `/data`).
- **aarch64 budgets:** a job on the arm runner the build matrix already uses runs
  `scripts/bench_planner.py` (reference house and full house) and `scripts/bench_load_ml.py`, and
  reports median, worst and the GIL test; fails above 10 s for the full house. A Pi 4 or HA Green
  run by the maintainer or a volunteer is recorded in STATUS (question 14).
- **Versioning:** `scripts/beta_version.py` takes an explicit next base from
  `release-please-config.json` (`release-as`) when it is set, so betas become `1.0.0-beta.<n>`;
  tests extended. The stable `config.yaml` loses `stage: experimental` at the release commit (the
  key's allowed values and default verified against the Supervisor source first);
  `breaking_versions` considered for the schema jumps.
- **Community and repository hygiene:** issue templates match the real diagnostics export,
  labels (`good first issue`, `device-template`, `translation`), Discussions categories,
  `CONTRIBUTING.md` (docs site, Weblate, design tokens), README (status 1.0, badges, docs link),
  `NOTICE` (icons, MkDocs theme if bundled assets are shipped), `addon/suncore/DOCS.md` and
  `CHANGELOG.md` copy rules from the `prepare-release` skill.

### WP14 – Security review · `reviewer` (Fable), `control-safety` for command paths

Output `docs/security/review-1.0.md` (findings table: id, area, severity, status, fix PR) and
`fix(...)` PRs. Scope:

| Area | What is checked |
|---|---|
| Exposure | ingress only, no `ports`; the peer check (`172.30.32.2` / loopback), `SUNCORE_ALLOW_ANY_PEER` only for development and logged loudly; `panel_admin` default |
| API | every state-changing route behind the same peer rule; body limits (`BODY_LIMITS`, the 6-M optional items); 422/409 never echo secrets; WebSocket origin |
| HA commands | MQTT command topics: who can publish (anyone with broker access), mode raise opt-in, emergency stop semantics, flood limits; notification action tokens single-use and expiring |
| Verification window | the X3 waiver of 7-J: only with an active window, only `Origin.USER`, expiry enforced, cleared on abort, restart and emergency stop |
| Secrets | Supervisor token never logged; MQTT password stored in clear in `suncore.db` (`REVEAL_SECRETS`) – acceptable with HA's backup encryption or not (question 19); masking in API, export, history, diagnostics; log redaction tests |
| Supply chain | hash-locked `uv.lock` from PyPI only, `npm ci` with lockfile, base and node images pinned by digest, Cosign signatures, Dependabot, CodeQL, a one-off `pip-audit` and `npm audit` with triage |
| App hardening | `homeassistant_api` justification, no `hassio_api`, AppArmor profile option (verify the Supervisor's handling first), read-only `homeassistant_config` map, the s6 service user |
| Data | `/data` contents, backup exclusions, diagnostics pseudonymisation, no telemetry (BRIEF §6) |

### WP15 – Final docs, screenshots, STATUS, final review, release · `docs-writer`, `reviewer`

Final screenshot set (Haiku runs the skill, Sonnet reviews the images), guides updated to the new
navigation names, STATUS, roadmap, architecture §9 (frontend structure, tokens), the release notes
(what is simulator-verified only, schema jumps, "export before updating"), final `reviewer` (Fable)
pass on the whole Phase 7 diff and the `control-safety` read of 7-J and 7-K tables, then the
`prepare-release` skill with the maintainer.

## 3. Pull requests and order

Small stacked PRs, one topic each, `phase-7/<letter>-<topic>`. Every PR gets the `reviewer` agent
before it is opened (findings as `fix(...)` commits); PRs marked **CS** need a documented
`control-safety` sign-off. Open questions go into each PR under "Offene Fragen an den Maintainer".
Frontend PRs attach before/after screenshots.

| PR | Content | Depends on | Agent(s) and model | CS |
|---|---|---|---|---|
| 7-A `docs(plan,adr)` plan and ADRs | WP1: this plan, ADR-0019/0020/0021, ADR-0018 addendum | Phase 6 tip (`phase-6/o-gaps`) | `architect` (Fable) | – (the addendum is read by `control-safety` in 7-J) |
| 7-B `chore` integrate the Phase 5 tail | WP2 (skipped if `main` has both stacks) | 7-A | `backend-engineer`, `frontend-engineer` (Sonnet) | CS read of conflicts in `safety/` and the registry bounds |
| 7-C `feat(sim,app)` demo flex devices | WP3 | 7-B | `backend-engineer` (Sonnet) | – |
| 7-D `feat(frontend)` tokens, components, shell, chart theme | WP4; **review stop 1** | 7-C | `frontend-engineer` (Sonnet; token spec Fable) | – |
| 7-E `feat(frontend)` Now view and cards | WP5 Now | 7-D | `frontend-engineer` (Sonnet) | – |
| 7-F `feat(frontend)` Plan and Analysis | WP5 Plan, Analysis | 7-D | `frontend-engineer` (Sonnet) | – |
| 7-G `feat(frontend)` Settings, wizard, panel fields | WP5 Settings and wizard; **review stop 2** after it | 7-E, 7-F | `frontend-engineer` (Sonnet) | CS read (fields feeding G1–G6) |
| 7-H `feat(app,ha)` site sensors and event types | WP6 | 7-A | `ha-integration` (Sonnet) | – |
| 7-I `feat(api,app,frontend)` diagnostics, heat pump live values | WP7 | 7-E (cards), 7-G (settings page) | `backend-engineer`, `frontend-engineer` (Sonnet) | – |
| 7-J `feat(app,safety,frontend)` inverter verification | WP8 | 7-G | `control-safety` (Fable), `frontend-engineer` | **CS** |
| 7-K `fix(sim,planner/controller)` late-car gap | WP9 (if question 12 = yes) | 7-B | `qa-engineer`, then `optimizer-engineer` or `control-safety` (Fable) | **CS** if the controller changes |
| 7-L `docs(site)` MkDocs site and dashboard recipe | WP10 (spike first), WP11 | 7-A (skeleton), 7-B (reference), 7-H (recipe sensors) | `docs-writer` (Sonnet), `backend-engineer` (CI) | – |
| 7-M `chore(i18n)` Weblate | WP12 | 7-L, 7-G (catalogues settled) | `i18n-sync` (Haiku), maintainer | – |
| 7-N `test,ci` release readiness | WP13 | 7-C; final runs after 7-J | `qa-engineer`, `ha-integration` (Sonnet) | – |
| 7-O `docs(security)` security review and fixes | WP14 | 7-E … 7-N | `reviewer` (Fable), `control-safety` | **CS** for command-path fixes |
| 7-P `docs` screenshots, STATUS, final review, release | WP15 | all, acceptance §6 | `docs-writer`, `reviewer` (Fable), maintainer | final CS read of 7-J, 7-K |

```text
7-A ─► 7-B ─► 7-C ─► 7-D ─┬► 7-E ─┐
                          └► 7-F ─┴► 7-G ─┬► 7-I
                                          └► 7-J
7-A ─► 7-H ─► (7-L recipe page)
7-B ─► 7-K
7-A ─► 7-L ─► 7-M (also needs 7-G)
7-C ─► 7-N (final runs after 7-J)
7-E … 7-N ─► 7-O ─► 7-P ─► release 1.0 (after §6)
```

Branch order A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P; the arrows say what a PR needs. The
design line (7-C … 7-G) and the backend line (7-H, 7-K, 7-N) may run in parallel (at most two
subagents); 7-H and 7-L can start right after 7-A. 7-N needs 7-C for the flex demo of the loaded
smoke test. After every WP: `./scripts/check.sh`, frontend `npm run verify` where touched,
`pytest -m simulator` for new scenarios, markdownlint for docs, commit and push.

**Review stops.** Stop 1 (after 7-D): the shell, the tokens and a sample card in light and dark on
phone and desktop; the maintainer answers within a few days or the default applies. Stop 2 (after
7-G): the full before/after gallery of every view; changes requested here are made in a `fix`
commit on 7-G before 7-I and 7-J build on it.

## 4. Tests and verification

| Layer | Tool | What |
|---|---|---|
| Tokens | Vitest, `check-tokens.mjs` | contrast pairs in light and dark (criterion 2); no colour literals outside the token files; the chart theme reads tokens (snapshot of the option defaults) |
| Components | Vitest + Testing Library | `Card` collapse and ARIA (`aria-expanded`, heading levels), `Sheet` focus trap and Escape, `SegmentedControl` arrow keys, `Button` disabled and busy states |
| Views | Playwright against the demo (`SUNCORE_DEMO_FLEX=1`) | Now, Plan, Analysis tabs, Settings groups, wizard stages; deep links `#/decisions`, `#/whatif`, `#/?device=`; phone, tablet, desktop |
| Accessibility | axe in Playwright | every view and wizard step, light and dark, phone and desktop; keyboard walk-through of the emergency stop, a mode change, a proposal confirm, the wizard |
| Visual | screenshots skill | before/after sets per frontend PR; the final docs set |
| Bundle | `npm run size` | criterion 3 after every frontend PR |
| HA surface | pytest | golden discovery payloads for the five site sensors and the new event types; values from `day_summary` preview, `accuracy_day` and the plan; nothing announced without a producer; events fire once per observation edge |
| Diagnostics | pytest | no secret, token, coordinate, real entity id or name in the export of a configuration seeded with known values; pseudonyms stable within one export |
| Inverter verification | pytest, hypothesis, `-m simulator` | the procedure only proposes through the services layer (boundary test); X3 is waived only inside an active window for `Origin.USER`, X1/X2/X4–X6 never (property test); every abort path releases and reads back; `limit_verified` only after a pass; identity binding; scenarios of WP8 |
| Late car | `-m simulator` | the WP9 acceptance; no G1 or E-invariant violation |
| Migrations | pytest, hypothesis | WP13 fixtures and documents; refusal of newer schemas |
| Upgrade | CI job | 0.2.0 image → candidate image on one volume |
| Budgets | smoke test, benchmarks | loaded RSS on both architectures; planner on aarch64 |
| Docs | `mkdocs build --strict`, markdownlint, `gen_params.py --check`, `gen_compat.py --check`, Mermaid parse | every PR touching `docs/user` |
| i18n | `check_reasons.py`, `gen_backend_catalogue.py --check`, Vitest ICU parse | Weblate PRs and every PR with new keys |

No hardware in the session: the simulator stays the acceptance test for the control logic; the
maintainer's system is the acceptance test for everything in §6.

## 5. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | The design pass becomes a rewrite and breaks behaviour | regressions in safety-relevant UI (emergency stop, mode switch, confirmations) | presentation only; stores, API calls and test ids unchanged; the existing Playwright suites must pass unchanged in every view PR |
| R2 | Taste disagreements late | rework | review stop 1 on the shell and tokens before the views; stop 2 before anything builds on 7-G |
| R3 | HA theme variables give low contrast in a user theme | unreadable panel | contrast is tested for our defaults only; document; the bridge already falls back when the parent is unreachable; no automatic override of user themes |
| R4 | First-paint budget breaks after the Phase 5 merge | CI red | lazy wizard steps and settings sub-pages in 7-G; 7-B records the merged figure; tokens add about 2 kB |
| R5 | `color-mix()` unsupported in an old companion-app webview | wrong tints | a static fallback declaration before every `color-mix()` |
| R6 | Phase 5 tail merge conflicts hide a safety change | unsafe bounds | CS read of the conflict resolutions; boundary and safety tests unchanged |
| R7 | MkDocs ecosystem end of maintenance | docs build breaks later | pinned versions and lockfile, Markdown sources stay portable, Zensical evaluation before 2027-05-05 (ADR-0020) |
| R8 | Weblate PRs break generated files or the backend ICU subset | red CI, lost translations | `weblate-sync` workflow regenerates; clear failure text; ICU check in Weblate |
| R9 | The verification procedure leaves a limit set or causes import | lost PV or import | X2 never waived; X3 waiver bound to a short window; gate release on every abort path, read-back, crash recovery releases, CS sign-off, assisted only |
| R10 | Hardware acceptance needs surplus (winter) | 1.0 slips to spring | stages H0–H6 run now; H7 decision in question 10; release candidates as betas meanwhile |
| R11 | Diagnostics export leaks personal data | privacy | pseudonyms, allowlist of fields (not a denylist), tests with seeded secrets, security review |
| R12 | Upgrade from 0.2.0 fails on a real installation | data loss | upgrade CI job, migration fixtures, release notes "export before updating", refusal instead of silent loss |
| R13 | aarch64 CI runner is much faster than a Pi 4 | budget looks met but is not | a Pi 4 / HA Green run requested (question 14); the planner keeps its 8 s budget and heuristic fallback |
| R14 | Sixteen stacked PRs drift while the maintainer reviews Phases 5 and 6 | rebase work | 7-B first; small PRs; the design line and the backend line touch different files |
| R15 | The docs layout spike fails (static-i18n folder mode with shared assets) | 7-L slips | ADR-0020 fallback: suffix structure (`page.de.md`) in one folder; decided in the first commit of 7-L |

## 6. Maintainer hardware acceptance (1.0 gate)

Everything `UNVERIFIED (hardware)` in STATUS and in phase 6 §9, as steps on the maintainer's
system (HA OS, amd64 KVM, Kostal Plenticore G3 L, BYD HVM, KSEM G2, BackUp Switch G3, Tibber,
Solcast, LG ThinQ). Report each step as **ok** or **Abweichung** with a screenshot or log excerpt
in one GitHub issue "1.0 acceptance" (the lead opens it from this list). **Blocking** steps must be
ok or have an accepted workaround before 1.0; the others are recorded and do not block.

### H0 – Preparation (blocking)

1. In SunCore: *Settings → Advanced → Backup* → export the configuration; keep the file.
2. Create a full HA backup (HA's backup page).
3. Install the beta `1.0.0-beta.<n>` (or the newest beta) from the SunCore repository; note the
   version and the HA core and OS versions in the issue.

### H1 – Install, panel and basics (blocking)

1. Note the exact HA menu path and labels you used to add the repository, install the app and
   show it in the sidebar (en and de); they replace the `UNVERIFIED` menu names in the docs.
2. Open the panel: it loads, follows your HA theme (switch HA between light and dark; the panel
   follows), the emergency stop is visible, the mode chip says *Shadow*.
3. After 24 h of running: the app's memory as shown by HA's app page (and, if possible, `docker
   stats` from the SSH app) – must be below 300 MB. Note CPU at idle.
4. Open *Settings → Advanced → Diagnostics*, download the export and check that it contains no
   entity ids, names, coordinates or passwords of yours.

### H2 – Shadow run, live data (blocking; Phase 3 stage A)

1. Keep everything in shadow for 2 days.
2. No "paused" or "stale data" banner appears for the battery SoC (SunSpec) or the LG power
   sensor; the system check in the wizard summary is green.
3. Without surplus the decision log stays (nearly) empty; note any entry you do not understand.
4. Note how often your plugs and power sensors report an unchanged value (Developer tools →
   States, `last_reported`); this resolves the cadence assumptions.

### H3 – Forecasts, tariff, planner (blocking; Phase 4)

1. Wizard step 4: Solcast found, its forecast appears in the plan view; if you also map the
   Workday and weather entities, the load forecast shows workday/weekend profiles.
2. In the app log after the first start: the history rebuild says it read the recorder
   statistics (the Supervisor proxy passes `recorder/statistics_during_period`), and the energy
   preferences were read (`energy/get_prefs`, `energy/solar_forecast`).
3. Wizard step 6: Tibber prices appear for today; note the local time tomorrow's prices arrive.
   Compare Tibber's price with your bill: does `total` include grid fees?
4. Plan view for 2 days: are the reasons, the evening target and the morning buffer plausible?
   Note the planner's run time from the status chip.
5. Weather integration: does it offer hourly forecasts (the load model asks for them)?

### H4 – Home Assistant entities and notifications (blocking; Phase 6 stage A)

1. With the Mosquitto broker app installed: *Settings → Home Assistant* shows "connected"; in HA
   the SunCore device appears with its entities, in your language, including the grid view
   (month peak, grid state, §14a, island), the battery device (action, lease) and the five site
   sensors (savings, autarky, planned cost, forecast error, next action) with values.
2. Restart Home Assistant: the entities come back (birth message). Stop the SunCore app: the
   entities turn unavailable within a minute (last will). Start it again.
3. Press the SunCore *Replan* button, set a device's boost duration number, toggle a device's
   auto switch **down** (automatic → shadow is allowed): the change appears in the panel and the
   decision log; raising from HA is refused while the opt-in is off.
4. Restart the Mosquitto app: SunCore reconnects without a restart.
5. *Settings → Home Assistant → Notifications*: pick your phone's `notify.mobile_app_…`, send the
   test message; it arrives in your language. Note whether the confirm/reject buttons appear on a
   proposal later (Supervisor token may subscribe to `mobile_app_notification_action`).
6. Quiet hours: a test during quiet hours is held back for non-critical categories.
7. Build the dashboard from the recipe page; all cards show values.

### H5 – Grid section (blocking; Phase 6)

1. Wizard step 3: your 13 500 W export limit is shown as static (enforced by the inverter);
   §14a stays "not active"; peak shaving off.
2. Backup switch: find the entity that shows island operation of the BackUp Switch G3 (if any)
   and note its states during normal operation; map it or skip. Do **not** cause an outage for
   the test.
3. *Analysis → History*: the month peak and grid events section shows data.

### H6 – Demo flex scenarios (blocking; Phase 6 exit)

1. Start the demo (`demo_mode: true` in the app options, restart; switch back afterwards). The
   demo never touches your data.
2. EV card: set mode *Target* 80 % by a time; follow the plan's charging windows; note whether
   the target is reached.
3. Washing machine card: arm "finished by" a time; the plan shows the start; the job starts once.
4. Heat pump card: SG-Ready state, lift, COP (marked "assumed") and model quality are shown.

### H7 – First live writes with real surplus (blocking unless question 10 decides otherwise)

1. On the first day with real surplus: are the "would have" entries plausible at noon?
2. Switch **only the LG climate** to automatic (confirmation dialog with the identity token).
   The first writes show `accepted`, not `failed`; the unit reaches the set-point.
3. Press the emergency stop: the climate returns to your own setting; "Back to normal" restores
   SunCore's control only after confirmation.
4. Change the climate by hand while SunCore controls it: SunCore detects the override and keeps
   its hands off until the configured time.
5. Note whether `lg_thinq` raises an error on `set_temperature` (the `step is None` case) and the
   push latency after a command.

### H8 – Battery (not blocking; Phase 4)

1. Enable the disabled Kostal `Battery:MinSoc` number entity, map it in step 5 ("Kostal
   Plenticore, minimum SoC"), compare the battery and grid power signs with the inverter display.
2. Battery to *assisted*; confirm the first proposal; watch the read-back (35 s settle assumed)
   and whether out-of-range values are clamped or rejected.
3. Optional, with the documented procedure only: enable external battery management via Modbus,
   add the modbus hub, measure the §3.5 fallback timer (Watchdog switch of the app off,
   `docker kill`, abort criteria as in the guide), then set `battery.fallback_measured`. Note the
   U32 word order of register 1288 and the modbus `scan_interval`.

### H9 – Inverter limit verification (not blocking; needs 7-J)

1. Prefer the REST limit (`Inverter:ActivePowerLimitation`): map it in step 5, set the inverter
   to *assisted*, start the verification dialog at midday with clearly more export than house
   load, confirm each step; the export follows the lower limit and returns after the release.
   The dialog reports whether the value limits export or AC power; note whether it survives an
   inverter restart (check after the next restart).
2. Only if you want the modbus path: create a modbus sensor on register 533 and check whether it
   shows the written value, and whether the inverter offers a communication timeout.
3. Watch whether curtailed PV is held exactly constant (it would trip the frozen-PV check).

### H10 – Later hardware (not blocking for 1.0)

Wallbox (generic template, phase switching, cloud polling), evcc (MQTT on your broker, `smart`
behaviour, publication interval, mode reset at unplugging), heat pump (SG-Ready contact mapping
from its manual), program plug (does the appliance resume after power returns), §14a control box,
Kostal REST/Modbus details listed in STATUS. These stay "simulator-verified" in the 1.0 release
notes, shadow by default and capped at assisted until `behaviour_confirmed`.

### Budget measurements outside the maintainer's system

- aarch64 planner and load model benchmarks and the loaded smoke test run in CI (7-N).
- A Pi 4 or HA Green run of `scripts/bench_planner.py` inside the container (question 14).

## 7. Out of scope for Phase 7

The companion integration and a custom Lovelace card (after 1.0, ADR-0021); HA commands for EV
mode/target, program arming, comfort band and battery settings (6-O, after 1.0); the standalone
container (roadmap "optional"); grid pre-heating execution, `generic.heat_pump_select`,
`button.press` remote start, evcc plan clear and mode reset; built-in PV/price fallbacks,
calibration charging, per-phase imbalance limits, V2H/V2G, several vehicles per charger, EEBus and
the certified §14a path; a docs site in other languages than en and de; a design for HA's own
dashboard look beyond the recipe; any change of the contracts `Plan`, `Intent`, `Decision`,
`Reason` (ADR-0011).

## 8. Deviations during implementation

(Filled in per PR, as in Phases 5 and 6.)

### 7-A

- The plan draft was written before 6-O was on the branch; this revision rebases it on
  `phase-6/o-gaps`. 6-O closed the island, §14a and "EV target at risk" notifications, the
  read-only grid, battery, EV, heat pump and program entities and the `notify.quiet_hint` text;
  WP6 now covers only the five site sensors and the event types, and 7-H depends on 7-A instead
  of 7-B because `day_summary`, its preview and `accuracy_day` are already on the 6-O tip.
- The Phase 6 stack contains Phase 5 up to 5-H; only 5-E and 5-I … 5-L are missing (the draft
  named ADR-0015 as missing; the ADR is in the tree, its implementation is not).
- Docs versions re-checked: Material 9.8.1 is the newest release (not 9.7), with end of life on
  2027-05-05; `mkdocs-static-i18n` 1.3.1. The draft's statement that MkDocs 1.x is closed to
  outside contributors was not found in the cited post and is dropped.
- ADR-0020 stays one ADR for the docs site and the translation workflow (the docs translation
  path ties them together); no second ADR number is used.
- The ADR-0018 addendum keeps X2 and waives only X3 inside a verification window (the draft's
  test limit `max(export − 1 kW, 0.5 × export)` would have been rejected by X2 and X3 on most
  days); preconditions and the test limit changed accordingly (WP8).
- The MkDocs layout spike moved to the first commit of 7-L (it needs a build).
- New in WP13: `SUNCORE_DEMO_MQTT=1` (CI only), because the HA entity service is not built in
  demo mode.

### 7-B

- Merged `origin/phase-5/l-docs` (an ancestor check confirmed it contains 5-E, 5-I, 5-J and 5-K).
  Conflicts were unions: `docs/STATUS.md` (Phase 5 entries 5-L, 5-K, 5-J inserted before 5-F; the
  maintainer questions of #38-#50 kept as their own section), the two app `DOCS.md`, `scripts/check.sh`
  and CI (both generator checks), `check_reasons.py`, the config section tables
  (`core/config.py`, `storage/config_store.py`, `sim/demo_config.py`: `expert` next to `mqtt`,
  `notifications`, `inverter`), `app/runtime.py` (expert overrides, backtest jobs, flex state and
  grid store side by side), `api/app.py` (params, backtests and flex routes) and three tests.
  `openapi.json` and `schema.d.ts` were regenerated, not merged. The i18n catalogues merged
  without conflict (2415 keys each, no duplicates).
- `frontend/scripts/bundle-size.mjs`: kept the Phase 6 form (`catalogue-*` chunks, one language
  counts); the Phase 5 variant only matched `catalogue-(en|de)`.
- No conflict in `suncore/safety/` or `suncore/controller/`. The merge adds `SAFETY_LIMITS` and
  `inputs_max_age_ticks_within` to `suncore/core/control.py` and the registry in
  `suncore/core/params.py` without touching Phase 6 code. The registry holds 32 parameters of the
  controller, planner, tariff and forecast groups; none of the Phase 6 settings (EV, heat pump,
  programs, grid limits, notifications, inverter) is a registry parameter yet (they are device or
  site configuration), so WP2 added none ("no new behaviour").
- Merged bundle, first count (one language, parameter catalogue not counted): first paint 128.3 kB
  against 125 kB, total 254.1 kB against 250 kB (R4). Follow-up in the same PR: `Setup` and
  `Settings` became lazy routes (dynamic `import()` with the `app.loading` state), which brings the
  first paint to 113.2 kB. `bundle-size.mjs` now counts what one user downloads: the larger main
  catalogue (`catalogue-en|de`) plus `catalogue-params` in full (one chunk with both languages,
  loaded by the expert area, and English is the fallback so both are needed). The total is 262.3 kB
  against 250 kB: lazy loading does not shrink a total that sums all chunks, so the 12.3 kB are
  content (what-if 12.7, expert 6.4, parameter catalogue 5.4, about 180 keys).
- Budget split pending maintainer decision; option A implemented: `bundle-size.mjs` reports the
  standard user path (limit 250 kB, everything except ECharts and the expert extras) and the
  expert extras (new limit 30 kB: the `ExpertArea` and `WhatIf` entries, `catalogue-params` and
  the chunks only they import). The extras are found from the Vite manifest by source file
  (`scripts/bundle-classify.mjs`, unit-tested), not by hashes. Numbers: first paint 113.2 kB,
  standard path 236.9 kB, expert extras 25.4 kB; ECharts chunks unchanged. Budgets for first
  paint and ECharts are unchanged.

### 7-C

- Built as specified: `SUNCORE_DEMO_FLEX=1` (default off; on in `frontend/playwright.config.ts`)
  adds `wallbox` (`generic.wallbox`), `washer` (`generic.program_plug`) and `heat_pump`
  (`generic.heat_pump_sg_ready`) from `SimWallbox`, `SimProgram` and `SimHeatPump`
  (`suncore/sim/demo_flex.py`); the configs come from `build_device_config`, like the scenarios.
  All three run in shadow; nothing in `controller/` or `safety/` changed.
- "Arrives late" is the *request*, not the arrival time: the car (60 kWh, 30 %) is plugged in at
  08:00 scenario time, so it is connected at every demo start time, and the runtime EV state asks
  for 80 % by the next 07:00 (numbers of `ev_arrives_late`). The washer is loaded at 10:30 with a
  job armed at start (through `Runtime.arm_program`, in the throw-away database) with the
  deadline six hours ahead. A one-shot job was gone after about 13:00 scenario time, so the
  tablet run (the last project, hours of demo time later) found no job card: the sim user now
  loads the machine again after each programme and `demo_flex_user` arms a job per load.
  The heat pump's store starts at 48.5 degrees with an evening-only draw profile; with
  the default morning draw it heated for hours before 09:30 and drained the demo battery.
- Effect on the existing demo: the plan now gives the battery the surplus first (a plan band), so
  the heating rod no longer shows "would run" at 09:30 and the e2e test of the site summary checks
  "running 0" and the shadow badge instead. The demo's flex entities have registry devices
  (`registry.*(flex=True)`) so the picker's "same device" rule keeps working.
- E2E: device cards 3 to 6, wizard review keeps 6 devices, decisions specs pick a device with a
  card, the Phase 6 spec's seeded washer became `dryer` (the demo owns `washer`); new spec
  "the demo shows the flexible loads". Registry accessors and `RecordingConnection` take `flex`.

### 7-D

- Based on `phase-7/b-phase5-tail`, not on 7-C (7-C runs in parallel on a sibling branch); the
  after screenshots therefore lack the EV, washing machine and heat pump demo devices.
- **Tints:** ADR-0019 asks for "a static fallback declaration in front of every use". That does
  not work for custom properties (an unsupported `color-mix()` inside `var()` makes the use
  invalid at computed-value time, it does not fall back to the previous declaration). The static
  tints are declared per scheme instead and the `color-mix()` versions replace them inside
  `@supports` (selector `:root:root:root` so it outranks the scheme blocks). The contrast test
  checks both.
- **Status tokens:** `--sc-<status>` is the base colour (text and icons on surfaces, text on its
  own tint), `-bg` the tint, `-fg` the text on the solid base colour.
- **Components:** `Card`, `SectionHeader`, `Stat`, `Chip`, `Button`, `FormRow`, `EmptyState`,
  `Loading`, `Banner`, `SegmentedControl`, `Disclosure`, `Icon`. `Sheet` (and `ConfirmDialog` on
  it) moves to 7-G, where the settings filters and the wizard need it; `ConfirmDialog` is restyled
  on the tokens meanwhile. The shell uses `Button`, `Chip`, `Banner`, `Icon` and `Loading`; the
  views keep the global classes, which are restyled on the tokens (cards, buttons, inputs,
  notices, chips).
- **Shell:** the top bar is sticky on every size, so the emergency stop is always in view. Phone:
  mark, mode chip, connection dot (text only when not live), expert switch as an icon (the text
  stays its accessible name) and the emergency stop with the short label "Stop"/"Not-Aus" (the
  accessible name stays "Emergency stop", WCAG 2.5.3). Tablet: destinations in a second row.
  The expert switch stays in the top bar until 7-G moves expert mode into *Settings → Advanced*.
  Sticky bars inside views (wizard actions, parameter save bar) sit above the bottom bar through
  `--sc-sticky-bottom`.
- **Chart theme:** the plan and history/what-if charts read their colours from the tokens. The
  house line is now the energy palette's slate (was the text colour) and the plan's device line
  the device green (was the info blue). The legend stays at the top (the plan chart computes its
  grid from the legend rows); moving it to the bottom belongs to 7-F.
- **Icons:** own line drawings (Apache-2.0, `NOTICE`) instead of a third-party set, so no licence
  question arises.
- **Screenshots:** `e2e/review-shots.spec.ts` (`REVIEW_SHOTS_DIR`) takes the review set (Now,
  Plan, Analysis, Settings, PV step; 390×844 and 1280×800; light and dark; English) at device
  scale 2. Before: `docs/assets/screenshots/before/` (taken on the base commit with only this spec
  added), after: `docs/assets/screenshots/after-7d/`; the PNGs are palette-quantised to keep the
  repository small. The `SCREENSHOT_SET` switch of `screenshots.spec.ts` was not added; the
  separate spec does the job without touching the docs set.
- **Budgets:** first paint 113.2 → 118.9 kB (tokens and shell CSS +2.2 kB, `Button`, `Chip`,
  `Banner`, `Icon`, `Loading` in the entry chunk), standard path 236.9 → 243.7 kB, expert extras
  25.4 kB, ECharts chunks unchanged. Within the budgets (125 / 250 / 30 kB); the 115 kB target of
  criterion 3 depends on the lazy wizard steps and settings sub-pages of 7-G.

### 7-E

- Based on `phase-7/d-design-shell` with `phase-7/c-demo-flex` merged, so the screenshots show
  the wallbox, washing machine and heat pump (`SUNCORE_DEMO_FLEX=1`).
- **Energy now:** the flow is HTML, not an SVG with text: the nodes are the list items of
  "Current power values" (values once; the separate tiles and the screen-reader-only list are
  gone), lines and arrows are CSS decoration. Node test ids are `flow-<id>` (the `node-<id>` ids
  are gone); quality is `data-quality`. `EnergyFlow` and `SiteSummary` are removed; `FlowList`
  stays because the wizard summary uses it.
- **Surplus** sits in the energy card (WP5); the "would run" hint is in a `Disclosure`.
- **Battery card** stays a card of its own next to "Next up" (desktop) rather than in the device
  grid; SoC and power appear both in the flow and, larger, on the card, as the maintainer asked
  for the card (question in `review-7e.md`).
- **Mode controls:** `ToggleGroup` with `aria-pressed` buttons, not `SegmentedControl` (a radio
  group selects on arrow keys, which would change a device's mode or open the confirmation
  while moving). Short visible labels (`mode.short.*`), the full name as accessible name
  (WCAG 2.5.3: the visible label is contained).
- **"Why?" and "Take over manually"** are disclosure buttons in a `CardFooter`; panels stay in the
  DOM (`hidden`). The planned actions are a timeline whose whole row is the `<details>` summary
  with a visible "Why?"; this applies to the Plan view's list too (same component).
- **Collapsed device cards** (WP5: only name, state, power, mode chip collapsed) were not built:
  the mode switch stays visible on every card because it is the main control; the secondary
  parts are collapsed instead.
- **Decisions:** four entries (WP5 said three; the maintainer's brief allowed 3-5); reasons all
  entries share (same key and arguments) are shown once (`splitSharedReasons`).
- `Card` gained `article`, `id` (tabindex -1) and `data-*` pass-through and is a flex column whose
  footer sits at the end, so cards in a row have equal heights.
- `review-shots.spec.ts` gained `REVIEW_SHOTS_VIEWS` and a device-cards crop for the Now view.
- **E2E order dependence (from 7-C, not changed here):** in a full three-project run, the 7-C
  test "the demo shows the flexible loads" fails on phone and tablet because the washer's armed
  job is gone after the desktop `phase6.spec.ts` reset the demo. Fixed on the 7-C branch
  (b2ca0c9: the demo washer keeps a job with a rolling deadline), merged here; the full suite
  (desktop, phone, tablet in project order) then ran 426 passed, 0 failed, 228 skipped.
- **Polish after the first review:**
  - Reasons render through `control/reasonText.ts` (`tReason`): the planner puts the device *id*
    into `args.device` (it has no display names), so the panel substitutes the configured name
    and capitalises the first letter in the active locale. Used by `ReasonList`, the planned
    actions, the decision list, table and "Why?" and the plan status; the catalogue is
    unchanged.
  - Free surplus is shown as `max(0, free_w)`: `free_w` is available minus reserve minus the
    committed draw, and below zero it only means "no surplus" (the gate clamps the same way,
    `SiteSample.gate_estimate`); import caused by the controlled devices is `deficit_w`.
  - `stateView` maps an adjustable load at level 0 to `off` (was "Level 0 W").
  - The battery and "Next up" cards keep their own height (`align-items: start`); device-card
    footers stay pinned to the card end (checked at 768 px, two columns).
- **E2E fix:** `shell.spec.ts` asserted `#/history$` right after the History tab click, but the
  history view writes its state into the URL with `router.replace` (`#/history?period=week`).
  The assertion only passed while the view's chunk loaded after the first poll; it now asserts
  the settled canonical URL.
- **Budgets:** first paint 118.9 → 120.5 kB, standard path 243.7 → 245.8 kB, expert extras
  25.4 kB, ECharts unchanged.

### 7-F

- Based on `phase-7/e-now` (7-E with 7-C and 7-D), not on 7-D alone as the table in section 3
  says, so Plan and Analysis share the 7-E cards, `ToggleGroup` and the planned-actions timeline.
- **Range switches** (plan 24/48 h/today, history periods) are `ToggleGroup` (`aria-pressed`),
  not `SegmentedControl` (WP5): the plan range can have no pressed option (a past time or day),
  and the E2E suites assert `aria-pressed`; same reasoning as the mode switches in 7-E.
- **Planner status** is a quiet line inside the toolbar card instead of a status chip in the
  header row: the last and next run times are the information, a chip would hide them.
- **Forecast panel:** two collapsible cards ("Forecasts and accuracy", "Machine-learning load
  model"), closed by default, each with a status chip; the Open-Meteo credit stays visible
  below them. `data-testid="forecast-panel"` moved to the wrapper of both cards. The E2E tests
  open the cards with the new helper `openCard` (no assertion changed).
- **Plan chart legend stays at the top** (7-D left the move to 7-F): four stacked panels and the
  Gantt's own legend at the bottom; question 2 in `review-7f.md`.
- **History:** the ten tiles became three group cards of `Stat` (energy with the energy palette
  dots, self-sufficiency, costs with the savings attribution); chart cards end in one row (table
  disclosure, PNG, CSV) instead of three stacked controls; one global `.data-table` style for
  all data tables of Plan and Analysis (and `.table-scroll`).
- **Decision log:** filters in a collapsible card with an active-filter count (`filterCount`),
  closed on phones unless the URL sets filters; `Sheet` is still 7-G. The note all listed
  decisions share is shown once above the table (`splitSharedReasons`, new `reasons` prop of
  `DecisionTable`); outcomes are tinted tags. E2E: `openCard` before the filter tests on phones.
- **What-if:** header and spacing only, the form keeps a 56 rem reading width; result cards
  unchanged.
- `Card` collapsible: the chevron now leads the title (pointing right while closed), like
  `Disclosure`. New icons prev, next, refresh, download, filter. The `no-hardcoded-text` lint
  rule allows the `density` and `layout` props (enums of `Card` and `Loading`).
- **Bundle:** once the lazy views import `lib/ui`, Rolldown split the components into a dozen
  tiny chunks (first paint 125.0 kB, standard path 253.1 kB). A `manualChunks` rule puts
  `src/lib/ui/` into one `ui` chunk: first paint 120.5 → 122.6 kB, standard path 245.8 →
  249.6 kB, expert extras 25.4 → 25.5 kB, ECharts unchanged. The standard path has 0.4 kB left
  until 7-G's lazy wizard steps and settings pages.
- **Phone overflow found by the full E2E run:** `Card`'s body and `Disclosure`'s content were
  grids with an implicit `auto` column, so a wide table (the forecast accuracy table, 30 rem)
  widened the card and the page on a 375 px phone (Chrome then zoomed out and the clicks missed).
  Both now use one `minmax(0, 1fr)` column; the plan spec checks for no sideways scroll with
  the forecast cards open.
- **After the coordinator's review:** the demo's stored plans came from the planner's reference
  house (`ac_living`, `ac_bedroom`), devices the demo does not configure, so the Gantt lanes,
  the schedule table and the tooltip showed raw ids. `seed_demo_plans` now replaces those two by
  the demo's own air conditioner (`living_room_ac`); the plan panel resolves names like
  `tReason` (configured name, then the live feed's name, then the id of a device no longer
  configured). "Look back and compare" starts collapsed and opens only for a link to a past
  day, a time or a comparison (today, also via the range switch, keeps it closed).
- Merged `origin/phase-7/e-now` at `1b2eeb4` (deterministic demo washer test) before the final
  checks.
- `review-shots.spec.ts` gained the views `plan-live`, `decisions` and `whatif` (expert mode via
  local storage). Before: `docs/assets/screenshots/before-7f/` (7-E tip with only this spec
  change), after: `docs/assets/screenshots/after-7f/`; palette-quantised like 7-D and 7-E.

### 7-G

- Based on `phase-7/f-plan-analysis` (7-F with 7-C, 7-D and 7-E). Presentation and structure
  only: stores, API calls, validation and the wizard step ids are unchanged.
- **Wizard:** four stages (Basics: region, energy; Your system: grid, PV, battery, tariff;
  Devices and goals: devices, priorities; Finish: mode, summary) in `wizard/steps.ts`
  (`STAGES`, `stageOf`), shown as a four-part stage bar above the step and as group headings in
  the step list (side column on desktop, behind "Show all steps" below 1024 px). The step
  heading carries a kicker "Your system · step 3 of 10" (`wizard-count`); the "3." before the
  title is gone. One sticky footer on every step: "Leave setup" (ghost) at the start, Back,
  Skip (ghost, where allowed) and Next at the end, spanning the card.
- **No card inside a card:** a `fieldset.card` or `section.card` inside a card is flat (a
  divider and a heading; global rule in `app.css`). This calms every wizard step, the device
  editor and the notification settings at once.
- **Advanced fields behind `Disclosure`:** only the grid step so far ("Peak shaving, export
  limits, §14a and backup power"; "Energy counters and house consumption (optional)"). Each
  opens by itself when something in it is set or has a server issue. The other steps keep their
  fields open; candidates for a follow-up are the battery model and the PV forecast sources.
  E2E helper `openDisclosure`.
- **Live preview "in the same place on every step"** (WP5) was not built: only the grid
  (sign check) and the summary (plan preview) have a preview, and both sit where the user acts
  on them. Moving them to a fixed slot would separate the check from its field.
- **Settings:** an index with five groups (`#/settings/system|devices|goals|ha|advanced`,
  `router.page`, `settingsHash`); desktop (≥ 1024 px) shows the index and the page side by side
  and opens "Your system" for `#/settings`; phones and tablets show the index, then the page
  alone with "‹ Settings" as the way back (the view's h2). Opening a page focuses its heading.
  Your system: setup assistant status and "Run setup again", rows region … tariff. Devices: the
  devices row and the configured devices (read-only). Goals and operating mode: priorities,
  mode, summary. Home Assistant: entities (MQTT) and notifications. Advanced: expert mode,
  (slot for the 7-I diagnostics panel), earlier versions, backup, about.
- **Expert mode** moved from the top bar into *Settings → Advanced* (as 7-D planned); the
  what-if hint links there. The top bar is now mark, destinations, status and the emergency
  stop.
- **About:** version from `/info`, demo or live data, licence and the project link
  (`addon/suncore/config.yaml` `url`). No backend change.
- **Phase 6 panel fields** (`para14a` and `shed_rank` per device, the island keep list, the
  manual MQTT broker): not added in this PR. The evcc `loadpoint_id` and `topic_prefix` fields
  exist already (6-x). Adding the others changes what feeds G1–G6 and needs the `control-safety`
  read; they follow in their own PR on top of 7-G so the design review is not blocked on it
  (question in `review-7g.md`).
- **Bundle (the important deviation):** the standard path is the sum of all chunks, so splitting
  more lazy chunks makes it *larger*: settings sub-pages as five lazy chunks took it from
  249.6 to 258.8 kB (each chunk brings its own import list, CSS file and gzip overhead). Measured
  instead:
  - Settings pages ride in the one lazy `Settings` chunk (it was lazy already; first paint is
    unaffected either way).
  - Wizard steps: one lazy chunk per **stage** (`views/steps/stages.ts`) instead of one per step:
    −8 kB on the standard path. The wizard was never part of the first paint.
  - The build leaves the backend-only messages out of the browser catalogues
    (`scripts/backend-only.mjs`: `mqtt.entity.*`, `mqtt.state.*` and the notification texts;
    the backend catalogue is still generated from the full files). A test checks that no
    frontend source uses one. −2.1 kB standard path, −2.0 kB first paint.
  - Result: first paint 122.6 → 121.4 kB, standard path 249.6 → 244.1 kB, expert
    extras 25.5 → 25.6 kB, ECharts unchanged.
- New icons `plug` (an appliance), `target`, `bell` for the settings groups.
- **Focus after navigation** (`App.svelte`): the shell moved the focus to `main` on every hash
  change, also when only the query or the settings page changed (the effect read the whole
  location). It now reacts to a change of the route only, so a settings page can focus its own
  heading; found by the new index test.
- E2E: settings specs open the sub-page they test (`#/settings/system`, `/ha`, `/advanced`); the
  overview count of ten rows became 6 + 1 + 3 over the three pages; new tests for the index and
  the way back, and for "about"; axe runs on the index and on every sub-page; the responsive
  check covers the index, two sub-pages and the grid step.
- `review-shots.spec.ts` gained the settings sub-pages and the wizard steps region, grid,
  devices and summary. Before: `docs/assets/screenshots/before-7g/` (7-F tip with only this spec
  change; the sub-pages did not exist), after: `docs/assets/screenshots/after-7g/`.

### 7-J

- Based on `phase-7/g-settings`. Built as the ADR-0018 addendum says, with these deviations:
- **Four endpoints, not three:** `GET` and `POST /api/v1/inverter/verification` (view, start),
  `POST .../confirm` (the one write) and `POST .../abort`. None takes a body: the server alone
  computes the test limit, so the panel cannot choose a value. A refusal is 409
  `api.error.verification_refused` with reason keys.
- **The user confirms the write, not the release.** The addendum says "the user confirms each
  write"; the procedure writes once (the test limit, confirmed in the dialog within 2 min) and
  releases on its own (`gate.release_device` with `Origin.USER`). Asking to confirm the way
  back would keep a test limit in place while the user is away; the release only moves towards
  the user's own value. H9 step 1 ("confirm each step") reads accordingly.
- **Who fills the window field:** `GateInputs.verification_window` exists as the addendum
  says, but the gate overwrites it with the window it holds in memory (`open_verification_window`
  / `close_verification_window`); a value from the inputs provider is ignored. So no provider,
  test fixture or future code path can grant the waiver by putting a window into the inputs. The
  procedure calls the gate's window methods directly (it is in `app/`, next to the stack), not
  through `api/services.py`; the services layer only starts, confirms and aborts.
- **The waiver is narrower than the addendum:** besides an active window, `Origin.USER`, the
  inverter asset and `≥ lowest`, the intent id must be `verify:<window id>:...`, so a manual
  USER limit from elsewhere during the window is still judged by X3. The window opens only
  while the inverter is effectively assisted, without an emergency stop and with an actuator,
  and at most one at a time.
- **Expiry releases.** When the window closes or expires while a limit written under it is
  held, the gate's `poll` releases that limit (`safety.verification_window_closed`); the
  procedure's own deadline (settle + 60 s) is shorter, the window (≤ 600 s) is the backstop.
  The window is also cleared on connection loss, shutdown and handover (the addendum names
  pass, fail, abort, emergency stop, restart and expiry).
- **A fail with evidence resets the flag.** The addendum says "a fail stores a reason key;
  nothing else changes". Here a semantics mismatch, an export that does not follow, a read-back
  mismatch or a baseline that is not restored set `limit_verified = false` (an earlier pass is
  no longer trusted); interruptions (load rise, disconnect, emergency stop, abort, timeouts)
  leave it as it was. Aborts and runs that never wrote are not stored.
- **No author column:** the config history has no author field, so the pass is a normal
  `inverter` section version written by `ConfigStore.record_inverter_verification`. A `PUT
  /config/inverter` cannot raise `limit_verified` (`_guard_verification` keeps the stored
  value), an import resets it, and the effective `InverterConfig.verified` also needs
  `verified_identity` to equal the adapter's current identity token.
- **Fail records:** stored in `inverter.last_verification` (outcome, reason key, semantics,
  identity, time), shown in the card.
- **View model in `core/verification_view.py`** (pydantic, for the API and the OpenAPI types);
  `core/verification.py` imports only `core.decisions` and `core.inverter`, so `safety/` does
  not reach `core.config` through it.
- **Classification:** export semantics when the export settles at the limit; AC semantics when
  it settles at limit − house (DC-coupled battery) or limit − house − battery charging
  (AC-coupled); tolerance `max(150 W, 5 %)`, held 20 s; both near (small house load) =
  `verify.semantics_unclear` (failed without evidence, flag unchanged). `UNVERIFIED` on
  hardware (STATUS).
- **While the test runs** the export chain proposes nothing for the inverter
  (`verify.running`), so the controller cannot write over the test limit.
- **Panel:** no Grid page exists in Settings, so the card sits in setup step 5 (where the
  disabled Phase 6 block was) and under Settings, Your system. Without an adapter the card says
  so and the button is disabled. Closing the dialog or pressing Escape while the test runs
  aborts it.
- **Review fixes (CS, approved with conditions):** F1 the gate judges X2 as for an unverified
  limit while a window is open or a limit written under it is held (a re-test questions the
  stored semantics; write and lease); F2 `confirm` returns when the procedure was ended during
  the gate's await, and `_finish` is idempotent (one record, no spurious reset); F3 the export
  chain waits only from the write on (`holds_inverter`), not while checking or waiting for the
  confirmation; F4 a window cannot be re-opened, also not with the same id; F6 the behaviour
  change for stored flags without `verified_identity` is in STATUS for the release notes.
- **Simulator:** the limiter got physical semantics (an export limit caps export, an AC limit
  caps the AC output, so export = AC − house − AC-coupled charging) and an `inert` mode; the
  house model got timed load events. Two bundled scenarios, 14 scenario tests.

## 9. Follow-ups per agent

- `architect`: architecture §9 after 7-G; record the 7-L spike outcome in ADR-0020 (update
  section) if the fallback applies.
- `frontend-engineer`: 7-D … 7-G, the frontend parts of 7-I and 7-J; the screenshot harness.
- `backend-engineer`: 7-B (with the frontend engineer), 7-C, 7-I backend, the docs CI of 7-L,
  `SUNCORE_DEMO_MQTT` in 7-N.
- `ha-integration`: 7-H, `verify-ha-api` runs for device classes, card options and the
  Supervisor `stage` key; parts of 7-N.
- `control-safety`: 7-J (Fable) including the verification window in `GateInputs` and the X3
  waiver; the CS reads of 7-B, 7-G and 7-K, the command paths in 7-O.
- `optimizer-engineer`: 7-K if the cause is in the planner.
- `qa-engineer`: 7-K root cause, 7-N fixtures, upgrade job, loaded smoke test, aarch64 benches.
- `docs-writer`: 7-L (spike first), the recipe, 7-P guides and release notes; close the STATUS
  row "Energy colour variable names" (verified in 7-A).
- `i18n-sync`: catalogue keys of every PR, 7-M.
- `reviewer`: every PR, 7-O security review, the final review in 7-P.

## 10. Questions for the maintainer (Fragen an den Maintainer)

**Bis zu deiner Antwort gilt jeweils die Empfehlung.**

1. **Phase-5-Rest zuerst zusammenführen.** Der Phase-6-Stapel enthält Phase 5 nur bis 5-H; es
   fehlen 5-E, 5-I, 5-J, 5-K und 5-L (Parameter-Register, Expertenmodus, Backtesting, What-if,
   Phase-5-Doku). **Empfehlung:** Phase 5 (5-A bis 5-L) zuerst nach `main` mergen, dann Phase 6
   (6-A bis 6-O); wenn das vor Phase 7 nicht passiert, führt PR 7-B beide Linien zusammen.
2. **Prüfstopps für das Design.** **Empfehlung:** zwei Stopps mit Screenshots (Handy und Desktop,
   hell und dunkel): nach 7-D (Grundgerüst, Farben, Schrift, eine Beispielkarte) und nach 7-G
   (alle Ansichten vorher/nachher). Ohne Antwort innerhalb einiger Tage geht es mit dem Stand
   weiter; Änderungswünsche kommen als Korrektur-Commit.
3. **Navigation.** **Empfehlung:** vier Hauptpunkte – Jetzt, Plan, Analyse (Reiter Verlauf,
   Entscheidungen, What-if), Einstellungen; auf dem Handy eine Leiste unten, der Not-Aus immer
   oben sichtbar. Alle bisherigen Links bleiben gültig.
4. **Aussehen: HA-Theme oder eigene Farben?** **Empfehlung:** weiter dem HA-Theme folgen (Akzent,
   Hell/Dunkel); unsere eigenen Werte gelten nur, wenn das HA-Theme nicht lesbar ist.
5. **Doku-Werkzeug.** MkDocs 2.0 hat keine Plugins mehr und passt nicht zu Material; Material
   for MkDocs bekommt nur noch kritische Korrekturen bis 5. Mai 2027. **Empfehlung:** MkDocs 1.6,
   Material 9.8 und `mkdocs-static-i18n` 1.3 fest gepinnt für 1.0, Nachfolger Zensical vor Mai
   2027 prüfen (ADR-0020).
6. **Was kommt auf die Doku-Seite?** **Empfehlung:** nur die Nutzer-Doku (en, de); Architektur,
   ADRs und Pläne bleiben auf GitHub und werden verlinkt.
7. **Weblate.** Das Projekt hängt an deinem Konto. **Empfehlung:** Hosted Weblate (Libre-Plan, ob
   kostenlos, bitte auf weblate.org bestätigen), nur Oberfläche und App-Optionen; die
   Nutzer-Doku wird für 1.0 per Pull Request übersetzt. Legst du das Projekt an?
8. **Lovelace-Karten.** **Empfehlung:** für 1.0 nur eine dokumentierte Dashboard-Vorlage aus
   HA-Bordmitteln (Kacheln, Buttons, Verlauf) auf den MQTT-Entitäten; eine eigene Karte (eigenes
   Repository, HACS) nach 1.0, wenn du sie für ein Wandtablet willst.
9. **Begleit-Integration** (native HA-Aktionen, Diagnose, Reparaturen; ADR-0005).
   **Empfehlung:** nach 1.0.
10. **Wann 1.0?** Die Abnahme mit echtem Überschuss (Stufe H7) geht frühestens am ersten sonnigen
    Tag im Frühjahr. **Empfehlung:** H0–H6 jetzt, Release-Kandidaten als Beta
    `1.0.0-beta.<n>`, 1.0 erst nach H7 (erster echter Schreibzugriff im Automatikmodus). Willst du
    1.0 früher, dann mit dem Hinweis „Automatikmodus noch nicht live erprobt“.
11. **Gerätefamilien ohne Hardware in 1.0.** Wallbox, evcc, Wärmepumpe, Programme und
    Wechselrichter-Limit sind nur im Simulator geprüft. **Empfehlung:** in 1.0 enthalten, in den
    Release-Notes als „im Simulator geprüft“ gekennzeichnet, Standard Schatten, höchstens
    Assistiert bis zur Bestätigung.
12. **Spätes Auto (77 % statt 80 %).** **Empfehlung:** Ursache in 7-K suchen und beheben; ein
    erreichbares Ziel muss bis zur Abfahrt erreicht werden.
13. **HA-Befehle für E-Auto, Programme, Komfort und Batterie-Einstellungen.** **Empfehlung:**
    nach 1.0 (eigener PR mit Sicherheitsprüfung), wie in 6-O vorgeschlagen.
14. **Messung auf echter Pi-4-Hardware.** Die CI misst auf einem ARM-Server, der deutlich
    schneller ist als ein Pi 4. Hast du einen Pi 4 oder HA Green? **Empfehlung:** falls ja, einmal
    `scripts/bench_planner.py` im Container laufen lassen (Anleitung in 7-N); sonst ein Aufruf in
    den Discussions.
15. **Versionssprung.** **Empfehlung:** die stabile App springt von 0.2.0 direkt auf 1.0.0 (den
    offenen Release-PR #17 nicht veröffentlichen); vorher Betas `1.0.0-beta.<n>`; der Upgrade-Test
    0.2.0 → 1.0 ist Pflicht. `stage: experimental` entfällt mit 1.0.
16. **Live-Feed für E-Auto/Programme.** **Empfehlung:** beim Abfragen alle 10 s bleiben, außer das
    neue Jetzt-Layout wirkt dadurch träge.
17. **GitHub-Einstellungen.** Bitte GitHub Pages (Branch `gh-pages`) und Discussions aktivieren;
    `main` muss für den Beta-Bump und den Weblate-Abgleich weiter erreichbar bleiben.
    **Empfehlung:** so einrichten.
18. **Screenshots im Repository.** **Empfehlung:** nur der finale Doku-Satz wird eingecheckt;
    Vorher/Nachher-Bilder hängen als Anhang an den PRs, damit das Repository klein bleibt.
19. **MQTT-Passwort im Klartext in `suncore.db`.** Es landet in HA-Backups. **Empfehlung:** so
    lassen und dokumentieren (HA-Backups sind verschlüsselt); der Sicherheits-Review (7-O)
    bestätigt oder empfiehlt eine Änderung.
20. **Modellwahl.** **Empfehlung:** Fable für Design-Spezifikation, Wechselrichter-Test, die
    Ursache „spätes Auto“ (falls Regler), Sicherheits- und Abschlussprüfung; Sonnet für Ansichten,
    Backend, Doku und Release-Technik; Haiku für Übersetzungen und Screenshot-Läufe.
21. **Wechselrichter-Test: Ausnahme von X3.** Für den Test muss SunCore die Einspeisung kurz unter
    den aktuellen Wert begrenzen, obwohl Geräte oder Akku den Strom noch aufnehmen könnten (das
    verbietet X3 sonst). Die Regel gegen Netzbezug (X2) bleibt immer in Kraft. **Empfehlung:**
    Ausnahme nur während eines vom Test geöffneten Fensters (höchstens 10 min), nur für deine
    bestätigten Schritte im Modus Assistiert (ADR-0018, Nachtrag vom 2026-11-25).
22. **Doku-Sprachen per Pull Request.** Die deutsche Nutzer-Doku wird für 1.0 weiter von uns per
    PR gepflegt, nicht in Weblate. **Empfehlung:** so lassen; Weblate für die Doku nach 1.0 prüfen,
    wenn Übersetzer für weitere Sprachen kommen.
