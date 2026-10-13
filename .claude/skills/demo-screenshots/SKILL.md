---
name: demo-screenshots
description: Generate documentation screenshots from SunCore's demo mode with Playwright (light and dark, phone and tablet) and place them under docs/assets/screenshots/. Usable from Phase 1 when the demo mode exists.
---

# Demo-mode screenshots

Documentation and PRs use screenshots from the deterministic demo mode
only (fixed scenario, fixed clock), so they are reproducible and contain
no personal data.

## Steps

1. `cd frontend && SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots`.
   Playwright builds the SPA and starts the backend itself in demo mode
   (`SUNCORE_DEMO=1 SUNCORE_DEMO_START_TIME=09:30`, scenario `summer_day`,
   see `frontend/playwright.config.ts`); nothing is mocked.
2. The spec `frontend/e2e/screenshots.spec.ts` captures phone (375×667) and
   tablet (768×1024), light and dark, English and German. Add views there
   as they are built (plan, history, devices, log, setup, expert mode).
3. Review the images: no overlapping text, correct theme, plausible daytime
   flows; the German variants (`-de`) are for the German docs.
4. Reference them from the docs with relative paths; keep each PNG under
   300 kB (long pages: tablet only).
5. For a PR: attach the before/after images of the changed view.

The spec skips unless `SCREENSHOTS_DIR` is set, and the web server it
starts always runs in demo mode, so screenshots are never taken from a live
system.
