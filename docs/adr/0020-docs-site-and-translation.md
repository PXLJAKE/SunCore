# ADR-0020: Documentation site and translation workflow

- **Status:** proposed (Phase 7 proceeds on the recommendation; the maintainer may override).
  Supersedes ADR-0008 and the Weblate component list of ADR-0007 (the rest of ADR-0007 stands).
- **Date:** 2026-11-25
- **Deciders:** maintainer, lead engineer (`architect`), `docs-writer`, `i18n-sync`
- **Research:** Material for MkDocs blog post "MkDocs 2.0" (published 2026-02-18, updated
  2026-10-02, read 2026-11-25); Material release notes 9.7.0 … 9.8.1 on GitHub and
  `requirements.txt` on `master` (read 2026-11-25); `mkdocs-static-i18n` `pyproject.toml` and
  `__init__.py` on `main` (read 2026-11-25); ADR-0007, ADR-0008, ADR-0016; BRIEF §7; plan
  [`phase-7.md`](../plans/phase-7.md) §0.1, WP10, WP12

## Context

ADR-0008 chose MkDocs Material + `mkdocs-static-i18n`, published all of `docs/` (engineering
docs as a "Development" section) and had Weblate manage the German user docs as a `docs`
component. ADR-0007 listed the Weblate components `frontend`, `backend`, `app-options`, `docs`.
Three things changed since:

1. **The MkDocs ecosystem** (verified 2026-11-25): MkDocs 2.0 is a rewrite in pre-release
   (`2.0.dev2` on 2026-08-30) without a plugin system, with TOML instead of `mkdocs.yml`, and
   incompatible with Material. Material 9.7.5 limits MkDocs to `<2`; `requirements.txt` on
   `master` pins `mkdocs>=1.6,<2`. Material's newest release is 9.8.1 (2026-11-07); 9.8.0
   requires Python ≥ 3.11 (SunCore: 3.12 minimum, ADR-0001); critical fixes and security updates
   until **2027-05-05**, then no planned maintenance. The Material team recommends its successor
   Zensical, which is "not fully there yet" on plugin support. `mkdocs-static-i18n` 1.3.1 (MIT)
   requires `mkdocs>=1.5.2`. The newest MkDocs 1.x patch release is `UNVERIFIED` from the session
   (PyPI and the MkDocs tag page were not readable); the lockfile records what is installed.
2. **The backend catalogue is generated** from the frontend catalogues by prefix
   (`scripts/gen_backend_catalogue.py`, ADR-0016). A Weblate `backend` component would be a
   second source of truth for the same strings.
3. **The tree:** user guides en/de live in `docs/user/<lang>/` with an identical structure; the
   compatibility list is one generated file `docs/user/compatibility.md`; screenshots are in
   `docs/assets/screenshots/`; the guides link to ADRs, `templates/README.md`,
   `CONTRIBUTING.md` and `addon/suncore/DOCS.md` outside `docs/user`. There is no `mkdocs.yml`.

## Options considered

### 1. Site generator

- **A – MkDocs 1.6 + Material 9.8 + `mkdocs-static-i18n`, pinned**, with a documented exit.
  Pros: what ADR-0008 planned, Markdown stays portable, Python toolchain, `mike` versioning.
  Cons: an ecosystem in maintenance mode; fixes stop after 2027-05-05.
- **B – Zensical now.** Pros: maintained successor by the same team. Cons: plugin support
  incomplete by its own authors' statement (i18n is the plugin we need); new tool before 1.0.
- **C – Docusaurus or Sphinx** (ADR-0008 options B, C). Cons: the reasons of ADR-0008 still
  hold (Node build or gettext-only i18n).

### 2. Site root

- **A – all of `docs/`** (ADR-0008). Cons: publishes plans, STATUS and research as if they were
  user docs; the engineering docs change daily and break `--strict` builds.
- **B – `docs/user` only**; engineering docs stay on GitHub and are linked.

### 3. Translation workflow

- **A – Weblate for everything** (ADR-0007 list). Cons: a `backend` component duplicates the
  generated catalogue; Weblate's Markdown handling of long guides with screenshots, admonitions
  and code is `UNVERIFIED` and splits pages into segments that drift from the English source.
- **B – Weblate for the UI and the app options; backend generated; user docs by pull request**
  for 1.0.

## Decision

**1A, 2B, 3B.**

1. **Tooling.** MkDocs 1.6.x, Material 9.8.x, `mkdocs-static-i18n` 1.3.x and `mike`, in a
   `docs` dependency group of `pyproject.toml`, locked in `uv.lock`; Dependabot may bump patch
   versions only. **Exit plan:** before 2027-05-05 the `architect` evaluates Zensical
   (i18n, search, versioning, our `--strict` build) in a new ADR; the Markdown sources use only
   features both tools support (admonitions, tables, Mermaid, tabs), no MkDocs macros.
2. **Layout.** `mkdocs.yml` at the repository root with `docs_dir: docs/user`; static-i18n in
   folder structure (`en/` default, `de/`); shared assets in `docs/user/assets/` (screenshots move
   there from `docs/assets/screenshots/`); generated pages per language
   (`<lang>/reference/parameters.md` from `gen_params.py`, `<lang>/compatibility.md` from
   `gen_compat.py`, both with `--check` in CI); links to files outside `docs/user` become
   absolute links to the repository on GitHub. Old paths keep a stub for one release. **Fallback**
   if the 7-L spike shows that folder mode cannot share assets or switch languages as needed:
   suffix structure (`page.md`, `page.de.md`) in one folder, decided in the first commit of 7-L
   and recorded as an update here.
3. **Build and deploy.** `mkdocs build --strict` on every pull request that touches `docs/user`,
   `mkdocs.yml` or the generators; `mike deploy` of `latest` and `x.y` to `gh-pages` from the
   release workflow. Screenshots come from the demo through the `demo-screenshots` skill.
4. **Weblate components:** `frontend` (monolingual JSON, `frontend/src/lib/i18n/locales/*.json`,
   source `en.json`) and `app-options` (HA translation YAML, `addon/suncore/translations/*.yaml`;
   the beta copy generated by a sync script when the option sets are equal). The backend
   catalogue stays generated; a `weblate-sync` workflow re-runs the generator and
   `check_reasons.py` on every Weblate pull request. Weblate pushes to the `weblate` branch
   (ADR-0007 rule unchanged). Checked against the Weblate source in 7-M (2026-11-25): the ICU
   flag is `icu-message-format`, the file formats are `json` and `yaml`, and the parameter
   catalogue is a third component `frontend-params`; details in `docs/i18n-weblate.md`. That the
   `yaml` round trip keeps Home Assistant's layout stays `UNVERIFIED` until the first pull
   request.
5. **User docs** en and de are written and translated by pull request for 1.0 (the `docs-writer`
   keeps both structures identical, as today). A Weblate `docs` component is reconsidered after
   1.0 when translators for further languages appear.

**Not decided here:** the docs host beyond GitHub Pages; a third site language; the Zensical
migration itself.

## Consequences

- Positive: a site that builds in CI and is versioned per release; one source of truth per
  string; translators see only UI and option strings, which are short and ICU-checked.
- Negative / risks:
  - Toolchain end of maintenance (plan R7): pinned and locked, portable Markdown, evaluation date
    set.
  - Layout surprises in static-i18n folder mode (plan R15): spike first, fallback named.
  - German user docs depend on the `docs-writer` keeping pace; the "Translate" page says so.
- Contract changes: none.
- Follow-ups: 7-L (`docs-writer`, `backend-engineer` for CI), 7-M (`i18n-sync`, maintainer
  creates the Weblate project), `architect` before 2027-05-05 (Zensical ADR).
- Supersession: ADR-0008 status → superseded by ADR-0020; ADR-0007 status notes that its
  component list is superseded by ADR-0020 (decision text of ADR-0007 otherwise unchanged).

## Recommendation to the maintainer

Accept; please confirm that only the user docs are published and that you will create the Hosted
Weblate project (UI and app options only).

## Addendum 2026-11-25: hosting and versions (7-L)

The maintainer hosts the site on his own web server (Plesk), not on GitHub Pages. Decision 3 changes
as follows: no `mike`, no `gh-pages` branch and no version switcher. A stable release (not a
pre-release) builds the site with `mkdocs build --strict` and attaches it as
`suncore-docs-<version>.zip`; an optional job syncs it to the server with rsync over SSH when the
repository secrets `DOCS_DEPLOY_HOST`, `_USER`, `_KEY`, `_PATH` and `_KNOWN_HOSTS` exist (host key
checking is never disabled, `--delete` acts inside the target path only). `site_url` comes from the
environment variable `SUNCORE_DOCS_URL` (for example `https://<domain>/docs/suncore/`, a subpath
next to the maintainer's portfolio). The site shows the newest stable documentation; older
versions are in the release ZIPs. The spike outcome: the folder layout works, the fallback is not
needed; a Markdown file in the root of `docs/user` must not share a name with a language page.
