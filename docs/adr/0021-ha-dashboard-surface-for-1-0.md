# ADR-0021: Home Assistant dashboard surface for 1.0 – a recipe, not a card

- **Status:** proposed (Phase 7 proceeds on the recommendation; the maintainer may override).
  Supersedes the Phase 7 timing of the companion integration in ADR-0005 (the rest of ADR-0005
  stands).
- **Date:** 2026-11-25
- **Deciders:** maintainer, lead engineer (`architect`), `ha-integration`
- **Research:** HA frontend source `src/panels/lovelace/create-element/create-card-element.ts`
  and `src/panels/lovelace/views/get-view-type.ts` (branch `dev`, read 2026-11-25); ADR-0005,
  ADR-0016; BRIEF §4.13; plan [`phase-7.md`](../plans/phase-7.md) WP6, WP11

## Context

BRIEF §4.13 and the roadmap name "Lovelace cards" for Phase 7; ADR-0005 put the companion
integration (native actions, Diagnostics, Repairs) in Phase 7 as a separate repository. Since
Phase 6 SunCore announces its entities through MQTT discovery: status, mode, surplus, health,
shadow, emergency stop, events, per-device mode, power and last decision, read-only grid,
battery, EV, heat pump and program views (6-O), and in 7-H the five site sensors (savings,
autarky, planned cost, forecast error, next action). The panel itself is the full UI and is
reachable from the HA sidebar.

Verified: HA ships, among others, the card types `tile`, `entities`, `glance`, `button`,
`heading`, `gauge`, `history-graph` and `statistics-graph`; a dashboard view without a type
defaults to the sections layout.

Constraints: 1.0 is gated on hardware acceptance (plan §6), the design pass already takes most
of the frontend effort, and every extra package needs its own release cycle and review.

## Options considered

### Option A – dashboard recipe with built-in cards

A documented YAML dashboard (sections view) from cards that ship with HA, on the MQTT entities.
Pros: no code, no package, nothing to install; works with every HA theme; the user can edit it.
Cons: no SunCore-specific visuals (no energy flow animation, no plan chart on the dashboard).

### Option B – custom Lovelace card (HACS) for 1.0

A web component in its own repository showing flow, plan and decisions. Pros: a rich wall-tablet
view. Cons: a second frontend codebase and release cycle, HACS listing, compatibility with HA
frontend changes, it duplicates the panel; the card would need the API or extra MQTT topics.

### Option C – companion integration for 1.0 (ADR-0005 as planned)

Pros: native actions with parameters, Diagnostics, Repairs. Cons: a second package with config
flow and migration from the MQTT entities; HA commands beyond modes are deferred anyway (6-O,
plan decision 9).

## Decision

**Option A for 1.0.** The docs get a "Dashboard" page (en, de) with a ready sections dashboard
from built-in cards over the SunCore entities and a compact variant for small wall tablets. It
tells the user how to find the entity ids (no guessed ids) and every card option is checked
against the HA frontend source (`verify-ha-api`). It is tested on the maintainer's HA (plan §6,
H4).

A custom card (option B) and the companion integration (option C) come **after 1.0**, each with
its own ADR when the maintainer asks for them. ADR-0005's statement that Phase 7 starts the
companion repository is superseded by this timing; its staged design (MQTT first, integration
later, the API as the single source of truth) stands.

**Not decided here:** the content and repository of a later card; the companion integration
design; HA commands for EV, programs, comfort and battery settings (6-O, after 1.0).

## Consequences

- Positive: no new package before 1.0; the HA surface stays one thin client of the API (MQTT);
  the design effort goes into the panel.
- Negative: users who want SunCore visuals on an HA dashboard use the panel in the sidebar or an
  iframe until a card exists; the recipe can break when HA renames card options (mitigated by the
  source check and the HA version in the page).
- Contract changes: none.
- Follow-ups: 7-H (site sensors the recipe shows, `ha-integration`), 7-L (recipe page,
  `docs-writer`, reviewed by `ha-integration`); after 1.0 an ADR for card and companion
  integration if wanted. ADR-0005 status notes the superseded timing.

## Recommendation to the maintainer

Accept; tell us if you want a custom card for a wall tablet after 1.0.
