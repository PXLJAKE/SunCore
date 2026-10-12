---
name: demo-screenshots
description: Generate documentation screenshots from SunCore's demo mode with Playwright (light and dark, phone and tablet) and place them under docs/assets/screenshots/. Usable from Phase 1 when the demo mode exists.
---

# Demo-mode screenshots

Documentation and PRs use screenshots from the deterministic demo mode
only (fixed scenario, fixed clock), so they are reproducible and contain
no personal data.

## Steps

1. Start the stack in demo mode:
   `SUNCORE_DEMO=1 SUNCORE_DEMO_CLOCK=2026-06-21T12:00:00+02:00 uv run python -m suncore`
   and the frontend dev server, or the built image with `-e SUNCORE_DEMO=1`.
2. Run `uv run python scripts/screenshots.py --out docs/assets/screenshots`
   (Playwright; viewports: phone 390×844, tablet 1024×768; colour schemes
   light and dark; views: now, plan, history, devices, device detail,
   log, setup steps, settings, expert mode).
3. Review the images: no overlapping text, correct theme, German variant
   generated with `--locale de` for the German docs.
4. Reference them from the docs with relative paths; keep PNG under
   300 kB (the script optimises them).
5. For a PR: attach the before/after images of the changed view.

The script fails if the demo scenario or clock is missing, so screenshots
are never taken from a live system.
