# Contributing to SunCore

Thanks for considering a contribution. This page covers the mechanics;
the "why" lives in [`docs/BRIEF.md`](docs/BRIEF.md) and the decisions in
[`docs/adr/`](docs/adr/).

## Ground rules

- Code, comments, commit messages, issues and primary documentation are in
  English. German is the first translation.
- One topic per pull request. Conventional Commits
  (`feat:`, `fix:`, `docs:`, `ci:`, `chore:`, `refactor:`, `test:`), the
  scope in parentheses when useful (`feat(planner): …`).
- Tests and lint are green before review. Safety-relevant changes
  (anything that writes to a device) need a documented safety review in
  the PR.
- No hardcoded UI strings; new keys in `en` and `de`.
- Never invent entity ids, services, attribute names or Modbus registers.
  Cite the source; mark what you could not verify as `UNVERIFIED`.

## Development setup

```bash
uv sync --all-groups          # Python 3.12+ virtualenv with dev tools
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

The app can be built locally with
`docker build -f addon/suncore/Dockerfile -t local/suncore .` and tested
against a Home Assistant devcontainer as described in the HA developer
documentation.

## Frontend

Svelte 5 + Vite + TypeScript in `frontend/` (details in
[`frontend/README.md`](frontend/README.md)).

```bash
cd frontend
npm ci
npm run dev           # Vite on :5173, proxies /api and /health to 127.0.0.1:8099
npm run lint          # ESLint, including the "no hardcoded UI text" rule
npm run check         # svelte-check
npm run test          # Vitest
npm run build         # production bundle in frontend/dist
npm run verify        # everything above plus format check and bundle-size budget
npm run test:e2e      # Playwright against the real demo backend (builds the SPA first)
```

The E2E tests start the backend in demo mode themselves; nothing is mocked.
Documentation screenshots come from the same setup – follow the
`demo-screenshots` skill (`.claude/skills/demo-screenshots/SKILL.md`):

```bash
cd frontend && SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots
```

Never edit screenshots by hand.

## Run the stack locally

Build the SPA once (`cd frontend && npm run build`), then start the backend
in demo mode (simulated house, no Home Assistant needed, never touches
`/data`):

```bash
SUNCORE_DEMO=1 uv run python -m suncore
```

Open <http://127.0.0.1:8099>. `SUNCORE_DEMO_START_TIME=09:30` sets the
time of day the demo starts at (default 10:00). Without a Supervisor the
backend accepts loopback peers only.

To talk to a real Home Assistant instead (`DirectTransport`), create a
long-lived access token in your HA profile and run:

```bash
SUNCORE_HA_URL=http://homeassistant.local:8123 SUNCORE_HA_TOKEN=<token> \
SUNCORE_DATA_DIR=./.data uv run python -m suncore
```

`SUNCORE_ALLOW_ANY_PEER=1` disables the peer check and is for development
only (for example to reach the dev server from another machine); never set
it in an app configuration. Settings are read from `/data/options.json`
(or `$SUNCORE_OPTIONS_PATH`), then overridden by `SUNCORE_<FIELD>`
environment variables.

## Pull requests

Use the PR template. Describe what, why, how it was tested, risks and open
questions. Keep `CHANGELOG.md` untouched – release-please generates it
from commit messages.

## Device, tariff, forecast and region templates

Templates live in `templates/<kind>/` and are validated against the JSON
Schemas in `templates/schema/`. Every template needs a `source` (the
documentation it was built from) and a simulator scenario. See
`docs/user/en/contributing/templates.md` (from Phase 2).

## Translations

Translations are managed on Weblate (from Phase 7). Until then, edit the
JSON catalogues directly. UI strings are keys only: add each key to
`frontend/src/lib/i18n/locales/en.json` (source) and `de.json` in the same
change, written as ICU MessageFormat. `uv run python scripts/check_reasons.py`
verifies that backend reason and error keys exist in both catalogues.
User documentation is English under `docs/user/en/` with the German
translation at the same path under `docs/user/de/` (ADR-0008).

## Checks before you push

```bash
./scripts/check.sh          # ruff, mypy, pytest with coverage, frontend checks
FAST=1 ./scripts/check.sh   # what the commit hook runs
```

`check.sh` also runs the frontend checks when `frontend/node_modules`
exists; E2E is opt-in with `E2E=1` and always runs in CI.

## Releases

Maintainers merge the release-please PR; images are built and signed by
GitHub Actions. See ADR-0009.

The beta app needs no manual version bump: every push to `main` that changes
the app publishes `ghcr.io/pxljake/suncore-beta:<next>-beta.<n>` and a bot
commit (`chore(beta): bump app version … [skip ci]`) writes that version to
`addon/suncore_beta/config.yaml`, which is what makes Home Assistant offer
the update (ADR-0009 addendum). Do not edit that version by hand, except to
raise the `x.y.z` base. Pull the bot commit (`git pull --rebase`) before
pushing to `main`-based work. The bump needs permission to push to `main`
(allow `github-actions[bot]` or use `RELEASE_PLEASE_TOKEN` of a bypass user).
