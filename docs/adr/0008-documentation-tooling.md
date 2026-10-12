# ADR-0008: Documentation tooling

- **Status:** accepted (maintainer delegated the decision on 2026-10-12)
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** BRIEF §7

## Context

A documentation site on GitHub Pages with English primary and German
translation, screenshots from the demo mode, a generated parameter
reference and device handbooks generated from templates (BRIEF §7). The
repo also keeps `docs/` as the engineering source of truth (BRIEF, ADRs,
STATUS), which should stay plain Markdown readable on GitHub.

## Options considered

### Option A – MkDocs Material + `mkdocs-static-i18n` + `mkdocstrings`

- Pros: Markdown in, Python tooling (same toolchain as the backend),
  Mermaid built in, search, versioning via `mike`, i18n plugin with
  per-language folders, generated pages are just Markdown files produced by
  our scripts; MIT licence.
- Cons: Material's insiders features are paid – not needed.

### Option B – Docusaurus

- Pros: React/MDX, great i18n.
- Cons: adds a Node build to the docs pipeline, MDX pages are not readable
  on GitHub, heavier.

### Option C – Sphinx + MyST

- Pros: strong API docs.
- Cons: more friction for Markdown authors; i18n via gettext only.

## Decision

Option A. Layout:

- `docs/` stays the engineering area (BRIEF, STATUS, ADRs, research, plans)
  and is **also** published as a "Development" section.
- `site/` (MkDocs root `docs_dir`) is not created; instead `mkdocs.yml` at
  repo root uses `docs/` with nav for user docs under `docs/user/<lang>/`
  and engineering docs under `docs/`. German user docs live in
  `docs/user/de/` and are managed by Weblate as a `docs` component.
- Generated pages: `docs/user/en/reference/parameters.md` from the Pydantic
  settings models, `docs/user/en/devices/*.md` from templates; a CI job
  fails if generated files are stale.
- Screenshots come from a Playwright script against the demo mode
  (`scripts/screenshots.py`), stored under `docs/assets/screenshots/`.
- Deploy with `mike` for versioned docs (`latest`, `x.y`) to the `gh-pages`
  branch from the release workflow.

## Consequences

- Positive: one Markdown source for GitHub and the site; Python-only docs
  toolchain.
- Negative: `mkdocs-static-i18n` folder conventions must be followed
  carefully – documented in `CONTRIBUTING.md`.
- Follow-up: Phase 7 `docs-writer`; Phase 0 only adds the `mkdocs.yml`
  skeleton if time permits (not required for the Phase 0 PR).

## Recommendation to the maintainer

Accept.
