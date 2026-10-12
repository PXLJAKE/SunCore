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

Frontend (from Phase 1): `cd frontend && npm ci && npm run dev`.

The app can be built locally with
`docker build -f addon/suncore/Dockerfile -t local/suncore .` and tested
against a Home Assistant devcontainer as described in the HA developer
documentation.

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
JSON catalogues directly and run the sync script.

## Releases

Maintainers merge the release-please PR; images are built and signed by
GitHub Actions. See ADR-0009.
