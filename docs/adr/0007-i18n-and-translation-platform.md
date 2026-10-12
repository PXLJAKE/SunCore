# ADR-0007: Internationalisation and translation platform

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** BRIEF §7; Weblate repository README (hosting page unreachable)

## Context

All UI and backend strings (including planner explanations and
notifications) must be translatable, with plurals, numbers, units and dates
localised; English is primary, German the first translation; a translation
platform should let the community contribute (BRIEF §7).

## Options considered

### Option A – ICU MessageFormat everywhere, JSON catalogues, Hosted Weblate

- Frontend: FormatJS `intl-messageformat` (BSD-3) with a small Svelte store;
  catalogues `frontend/src/locales/<lang>.json` (flat keys, ICU values).
- Backend: Python ICU formatting through `babel` (plural rules, number and
  date formatting) with a minimal ICU message parser, or the
  `PyICU`-free library `icu-messageformat`-style package to be chosen in
  Phase 1 (candidates evaluated for wheels on aarch64/musl). Catalogues
  `suncore/locales/<lang>.json`. Explanations are built from message keys
  plus structured arguments, never from concatenated strings, so they
  translate.
- App option translations: `addon/suncore/translations/<lang>.yaml` (HA
  format, ADR research).
- Platform: Hosted Weblate, which is free for libre projects (UNVERIFIED
  from this session – the pricing page was unreachable; widely documented),
  components: `frontend`, `backend`, `app-options`, `docs`.

### Option B – gettext (`.po`) with Babel

- Pros: mature tooling, Weblate native.
- Cons: plural/select handling less expressive than ICU in the frontend;
  two formats across stack or awkward gettext in Svelte.

### Option C – Fluent (Project Fluent)

- Pros: expressive.
- Cons: smaller ecosystem in Python and Svelte, Weblate support exists but
  fewer translators know it.

## Decision

Option A. Rules:

- Keys are stable identifiers (`plan.reason.grid_charge_cheaper`), never
  English text.
- Lint: ESLint rule forbidding string literals in Svelte markup outside
  `t()`; a Python check that `structlog`/API responses meant for humans go
  through the message catalogue (log messages for developers stay English
  and untranslated).
- `i18n-sync` agent/skill extracts keys, reports missing or unused keys,
  and keeps `de.json` complete for every PR (BRIEF §8).
- Units and numbers are formatted with `Intl.NumberFormat` /
  `babel.numbers` with the user's locale; the backend stores SI units only.
- Weblate pushes translation PRs to a `weblate` branch; `main` is protected.

Not decided here: the exact Python ICU library (Phase 1 spike, recorded in
STATUS).

## Consequences

- Positive: one message syntax across frontend, backend and docs; plurals
  and dates correct in German from day one.
- Negative: a custom ESLint rule and Python check cost a day – worth it,
  hardcoded strings are the most common i18n regression.
- Follow-up: Phase 1 sets up catalogues and lint; Phase 7 creates the
  Weblate project.

## Recommendation to the maintainer

Accept; confirm you are willing to own the Hosted Weblate project (it is
tied to a GitHub account).
