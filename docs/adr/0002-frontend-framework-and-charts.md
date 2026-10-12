# ADR-0002: Frontend framework and chart library

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** npm registry (Svelte 5.57, ECharts 6.1 Apache-2.0), [`ha-apps.md`](../research/ha-apps.md) (ingress)

## Context

The UI is an ingress panel inside an iframe, served from the app under a
dynamic base path (`X-Ingress-Path`), used on wall tablets, iPads and
phones, with live WebSocket updates, an animated energy-flow view, a dense
plan chart (price bands, P10–P90 PV band, SoC, stacked flows, device Gantt)
and history views (Sankey, heatmap). It must follow HA's light/dark theme,
be accessible, and run smoothly on tablet-class hardware (BRIEF §4.9).
Lovelace cards are separate packages (Lit, as HA's frontend) and are out of
scope here.

## Options considered

### Option A – Svelte 5 + Vite + TypeScript, Apache ECharts

- Pros: smallest runtime and fastest updates for frequently changing values
  (runes compile to direct DOM updates); simple component model lowers the
  bar for community contributors; ECharts (Apache-2.0, canvas) covers every
  chart in BRIEF §4.9 including Sankey, heatmap, custom series for Gantt,
  dataZoom for 24/48 h pan and zoom, and large datasets.
- Cons: smaller ecosystem than React; ICU i18n integration needs a thin
  wrapper (FormatJS `intl-messageformat` is framework-agnostic); fewer
  ready-made accessible component libraries – we build a small set of our
  own, which we would do anyway to match HA's look.

### Option B – React + Vite + TypeScript, Apache ECharts

- Pros: largest ecosystem, FormatJS has first-class React bindings.
- Cons: larger bundle and more re-render work for a dashboard that updates
  every few seconds; more boilerplate; no advantage for charts.

### Option C – Lit (same as HA frontend)

- Pros: same stack as Lovelace cards, HA theme CSS variables feel native.
- Cons: weaker for app-sized state management and routing; developer
  experience for a full SPA is poorer; still an iframe, so no shared theme
  runtime with HA anyway.

### Charts: ECharts vs Plotly vs D3 vs Chart.js

ECharts wins on Sankey/heatmap/Gantt coverage, canvas performance and
licence. Plotly is heavier; Chart.js lacks Sankey/Gantt; raw D3 costs too
much development time.

## Decision

Option A: Svelte 5 (runes), Vite, TypeScript strict, Apache ECharts 6
(tree-shaken imports). Styling with CSS custom properties mapped to HA's
theme variable names, light/dark via `prefers-color-scheme` plus an HA
theme bridge if the iframe exposes one (Phase 1 checks). i18n with ICU
MessageFormat via FormatJS `intl-messageformat` (ADR-0007). Tests: Vitest
for units, Playwright for E2E against the demo mode. All URLs relative to
the ingress base path.

Not decided here: component library details, state store shape.

## Consequences

- Positive: lightweight bundle for tablets; one chart library for all
  views; permissive licences.
- Negative: contributors who only know React face a small learning curve –
  mitigated by Svelte's simplicity and a documented component pattern.
- Follow-up: Phase 1 `frontend-engineer` scaffolds `frontend/`, verifies
  ingress base-path handling and theme bridging, and records bundle size in
  CI.

## Recommendation to the maintainer

Accept Svelte 5 + ECharts. If you prefer React for personal familiarity,
say so now – switching later is expensive, switching now is free.
