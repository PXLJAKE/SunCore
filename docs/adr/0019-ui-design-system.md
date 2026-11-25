# ADR-0019: UI design system – tokens over the HA theme, components, navigation

- **Status:** proposed (Phase 7 proceeds on the recommendation; the maintainer may override at
  review stop 1)
- **Date:** 2026-11-25
- **Deciders:** maintainer, lead engineer (`architect`), `frontend-engineer`
- **Research:** HA frontend source `src/resources/theme/color/color.globals.ts` (branch `dev`,
  read 2026-11-25: the theme and `--energy-*` variable names); ADR-0002; BRIEF §4.9, §4.10, §8;
  plan [`phase-7.md`](../plans/phase-7.md) §0.1, WP4, WP5

## Context

The maintainer finds the panel "ok aber nicht hübsch und etwas unübersichtlich". The tree on the
Phase 6 tip has HA-named colour variables in `app.css` (light, dark, `data-theme`), two layout
tokens (`--sc-radius`, `--sc-gap`), a handful of global classes, and every component sets its own
sizes, paddings and colours. The theme bridge (`lib/theme/bridge.ts`) copies twelve HA theme
variables from the parent frame so the panel follows the user's HA theme (BRIEF §4.9 "HA-Look").
Charts build their colours in two places (`lib/plan/chartOption.ts`,
`lib/history/chartOptions.ts`). Navigation is five pill links in a header that wraps into three
rows on phones.

Constraints: first paint 123.5 of 125 kB gzip (6-M); no new runtime dependency (ADR-0002 keeps
Svelte 5 + ECharts); WCAG 2.1 AA (BRIEF §8); hash routes are linked from notifications and the
decision log and must keep working; the emergency stop must be reachable on every screen.

Verified: the HA frontend defines `--primary-color`, `--accent-color`, `--primary-text-color`,
`--secondary-text-color`, `--text-primary-color`, `--card-background-color`,
`--primary-background-color`, `--secondary-background-color`, `--divider-color`,
`--error-color`, `--warning-color`, `--success-color`, `--info-color` and the energy colours
`--energy-solar-color`, `--energy-grid-consumption-color`, `--energy-grid-return-color`,
`--energy-battery-in-color`, `--energy-battery-out-color` (plus gas, water, non-fossil). SunCore
uses exactly these names. That the parent frame's computed values reach the ingress iframe on a
running HA stays `UNVERIFIED` (STATUS).

## Options considered

### 1. Where colours come from

- **A – own palette, HA theme ignored.** Pros: full control, contrast tested once. Cons: the
  panel looks foreign in HA, dark mode and user themes break the "HA-Look" requirement.
- **B – HA variables used directly in components** (today). Pros: no layer. Cons: no semantic
  names, no tints, every component picks its own mix; nothing to test.
- **C – two layers: HA variables as input, semantic `--sc-*` tokens derived from them** with our
  defaults as fallback. Pros: follows the user's theme, one place to test contrast and to change
  the look. Cons: one more indirection; user themes with poor contrast stay poor.

### 2. Component layer

- **A – a UI kit** (e.g. a Svelte component library). Cons: a runtime dependency and bundle cost,
  a second look next to HA's.
- **B – HA's own web components** inside the iframe. Cons: not a public API, versions drift with
  HA, Lit in a Svelte app.
- **C – a small own set** in `frontend/src/lib/ui/` on the tokens.

### 3. Navigation

- **A – keep five top-level links.** Cons: the clutter the maintainer names.
- **B – four destinations** (*Now*, *Plan*, *Analysis* with tabs History, Decisions, What-if,
  *Settings*), top bar on tablet and desktop, bottom bar on phones, emergency stop always in the
  top bar.
- **C – a side drawer.** Cons: hides the destinations on phones; HA already has a drawer.

### 4. Chart theme

- **A – ECharts `registerTheme`.** Cons: pulls theme code into the ECharts chunks.
- **B – a plain option-defaults object** built from computed tokens and merged by the existing
  builders.

## Decision

**1C, 2C, 3B, 4B.**

1. **Tokens** live in `frontend/src/lib/theme/tokens.css` with a read-only mirror in `tokens.ts`
   for charts. Inputs are the HA variables above (names unchanged, bridge unchanged). Semantic
   groups: surfaces, text, accent and status (each with a tint and a foreground), energy palette,
   type scale (12–28 px in rem, tabular numbers for values), spacing (4–64 px), radius,
   elevation (shadow in light, surface step plus outline in dark), motion (zero under
   `prefers-reduced-motion`), layout (content max 1200 px, breakpoints 600 and 1024 px). The
   values are those of plan WP4. Tints use `color-mix()` with a static fallback declaration in
   front of every use. `--sc-radius` and `--sc-gap` stay as aliases until the last view moved.
2. **Components** in `frontend/src/lib/ui/`: `Card`, `Stat`, `Chip`, `Button`,
   `SegmentedControl`, `SectionHeader`, `EmptyState`, `Disclosure`, `Sheet`, `Banner`. Plain
   Svelte, no dependency. Global classes (`.card`, `.btn`, …) are removed with their last user.
3. **Navigation** as option 3B. Hash routes do not change: *Analysis* is a container that maps
   to `#/history`, `#/decisions`, `#/whatif`; `#/?device=<id>` still focuses the device card.
   Banners (emergency stop, demo, stale data, island, §14a) stack in one fixed severity order.
4. **Chart theme** `frontend/src/lib/charts/theme.ts` returns option defaults from computed
   tokens; plan and history builders take it instead of their own colour objects; a theme change
   re-applies it.
5. **Guards:** `check-tokens.mjs` in `npm run verify` rejects hex, `rgb()` and `hsl()` literals
   outside `tokens.css` and `bridge.ts`; a Vitest test checks ≥ 4.5:1 for text and ≥ 3:1 for UI
   and chart-line tokens against their surfaces, light and dark, for our defaults.
6. **Icons** are inline SVG paths with a compatible licence, attributed in `NOTICE`; no icon
   font.
7. **Budgets unchanged:** first paint ≤ 125 kB (target ≤ 115 kB after the lazy wizard steps and
   settings sub-pages), total without ECharts ≤ 250 kB.

**Not decided here:** the visual details inside each view (plan WP5, reviewed at the two review
stops); a design for HA dashboards beyond the recipe (ADR-0021); overriding a user's HA theme
when its contrast is poor (not done; documented).

## Consequences

- Positive: one place to change the look and to test contrast; the panel keeps following the
  user's HA theme; charts and cards share one energy palette; navigation fits a phone.
- Negative / risks:
  - The design pass can turn into a rewrite (plan R1): presentation only, stores, API calls and
    test ids unchanged, existing Playwright suites must pass in every view PR.
  - User themes with low contrast stay low (R3): contrast is guaranteed for our defaults only.
  - `color-mix()` in old webviews (R5): static fallback first.
- Contract changes: none (frontend only; no change to `Plan`, `Intent`, `Decision`, `Reason`).
- Follow-ups: 7-D (tokens, components, shell, chart theme; review stop 1), 7-E … 7-G (views;
  review stop 2), `frontend-engineer`; architecture §9 updated after 7-G (`architect`); the STATUS
  row "Energy colour variable names" is closed by the verification above (`docs-writer`).

## Recommendation to the maintainer

Accept; the look itself is decided by you at the two review stops, this ADR only fixes the
structure (tokens over your HA theme, own small component set, four destinations).
