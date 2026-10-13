# Roadmap

Phases from [`BRIEF.md`](BRIEF.md) §10, refined with milestones and
acceptance criteria. Status legend: ⬜ not started · 🟨 in progress ·
✅ done · ⏸ waiting for maintainer. Current state per item is in
[`STATUS.md`](STATUS.md).

## Phase 0 – Blueprint ✅

Goal: an approved, verifiable plan. No feature code.

| Milestone | Acceptance |
|---|---|
| Research notes | `docs/research/` complete, every fact sourced or `UNVERIFIED` |
| ADRs 0001–0011 | each with recommendation; maintainer accepted or amended |
| Architecture, roadmap, STATUS | `docs/architecture.md` renders (8 Mermaid diagrams verified), roadmap and STATUS exist |
| Repo scaffold + CI (PR B) | empty packages, community files, workflows green, app linter green |
| Agent team, skills, hooks, CLAUDE.md (PR B) | agents load, hooks run, CLAUDE.md < 150 lines |

Exit: both PRs merged, open questions answered.

## Phase 1 – App skeleton that shows live data ✅

Goal: installable app that connects to HA and shows the "Now" view in shadow
mode.

| Milestone | Acceptance |
|---|---|
| Image build and publish | multi-arch images on GHCR, Cosign-signed, beta app installs from the repository on HA OS |
| HA connector | authenticates via Supervisor, `subscribe_entities` with staleness tracking, reconnect with back-off, `/health` reflects state |
| Storage | config store with migrations, export/import JSON |
| Simulator base | virtual house runs the stack in CI without HA; demo mode flag |
| Frontend scaffold | Svelte app under ingress base path, theme light/dark, Now view with energy flow from configured entities, WebSocket live feed |
| Shadow-mode skeleton | Safety module exists, every write path goes through it, nothing is written yet |
| i18n plumbing | ICU catalogues en/de, lint against hardcoded strings |
| Budgets | RSS < 300 MB idle measured in smoke test on aarch64 runner |

Exit: maintainer installs the beta app, picks grid/PV/battery entities by
hand in a minimal settings page, sees live flows.

## Phase 2 – Setup assistant and device model ⬜

| Milestone | Acceptance |
|---|---|
| Wizard steps 1–6, 9, 10 | region, energy-dashboard import, grid, PV, battery, tariff, mode, summary with system check |
| Entity auto-detection | candidates ranked by device class, unit, integration, area; sign check live |
| Device model + template schema v1 | Pydantic models, generated JSON Schema, CI validation |
| First templates | LG ThinQ climate, generic switch, generic climate, generic number load, Tibber tariff, Solcast forecast |
| Wizard steps 7–8 | add devices from gallery, priorities drag & drop, goals |
| Config versioning | migration test from v1 fixtures |

Exit: maintainer's system fully described through the wizard, still shadow
mode.

## Phase 3 – Real-time controller and safety ⬜

| Milestone | Acceptance |
|---|---|
| Controller | surplus computation, priority allocation, hysteresis, min run/pause, no oscillation (simulator test with cloud flicker) |
| Safety | invariants property-tested, modes shadow/assisted/automatic per device, override detection, watchdogs, fail-safe |
| Power models | measured, counter-derived (LG ThinQ Wh), learned, nominal – equal behaviour in simulator |
| Devices | climate (both power models), water heater (stepped), switchable loads |
| Decision log + Why | every decision logged with reasons, visible in UI |
| `control-safety` review | documented in each PR |

Exit: climate surplus control runs in shadow mode on the maintainer's
system with plausible "would have" entries; maintainer switches one device
to automatic.

## Phase 4 – Forecasts, tariffs, planner, battery ⬜

| Milestone | Acceptance |
|---|---|
| Forecast adapters | Solcast, Forecast.Solar, Open-Meteo Solar Forecast, generic attribute, built-in fallback; blending and bias calibration; accuracy stats |
| Load forecast | profiles from statistics (weekday/holiday/temperature), controlled loads excluded |
| Tariffs | Tibber, Nord Pool, ENTSO-E/EPEX via HACS, fixed/HT-NT, price formula, EEG tiered export, negative-price region rule |
| Planner | HiGHS model, heuristic fallback, warm start, time budget, benchmark on aarch64 < 10 s |
| Battery strategies | self-consumption, target SoC by time, grid charging on spread, discharge lock, reserve, calibration charge |
| Battery adapters | generic HA entities, Kostal REST (min SoC), Kostal Modbus (only with verified register map) |
| Explanations | per-interval reasons rendered in UI |

Exit: plan with battery strategy and "Why?" on the maintainer's system,
battery control in shadow mode, then assisted.

## Phase 5 – Visualisation, analysis, expert mode ⬜

| Milestone | Acceptance |
|---|---|
| Plan chart | price, P10–P90 PV, load, SoC, flows, device Gantt, zoom/pan, tooltips with reasons |
| History | day/week/month/year, autarky, self-consumption, cost vs baselines, Sankey, heatmap, cycles, forecast skill |
| Decision log UI | filters, "why did X switch at Y" |
| Expert mode | all parameters with help, defaults, ranges, reset; generated parameter reference |
| What-if + backtesting | run plan with other parameters/tariffs against history |
| Export | CSV, PNG |

## Phase 6 – HA integration surface and remaining devices ⬜

| Milestone | Acceptance |
|---|---|
| MQTT discovery | entities, switches, selects, numbers, buttons, events; works without broker (hint) |
| Notifications | errors, cheap windows, "start washing machine now" |
| Wallbox / EV | modes, 1/3-phase, target SoC by time, evcc as actor |
| Heat pump | SG-Ready, set-point lift, thermal model, COP curve |
| Shiftable programs | runtime, profile, deadline, start via plug/notification |
| Peak shaving, export limit, §14a, backup power awareness | simulator scenarios pass |

## Phase 7 – Release 1.0 ⬜

| Milestone | Acceptance |
|---|---|
| Docs site | MkDocs Material, en + de, screenshots from demo mode |
| Weblate | project live, badge in README |
| Community files complete | templates, discussions, compatibility list generated |
| Lovelace cards | energy flow, plan mini, device control, compact mode |
| Standalone container (optional) | Direct transport with token, own UI auth |
| 1.0 | stable app channel, release notes, security review |
