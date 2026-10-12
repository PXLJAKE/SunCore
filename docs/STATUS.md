# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-12 · **Phase:** 0 (blueprint) · **Branch:** `phase-0/foundation`

## Done

- Repository reset to an empty history (old prototype removed; tags
  `v0.1.0`–`v0.3.0` still exist on GitHub and must be deleted by the
  maintainer).
- `docs/BRIEF.md` (product and engineering brief), `docs/plans/phase-0.md`
  (approved plan).
- `docs/research/` – 7 notes, sourced against GitHub mirrors of the HA
  developer docs, HA Core/Supervisor source, evcc/EMHASS/Predbat docs, PyPI.
- `docs/adr/0001`–`0011`, all **accepted** (maintainer delegated the choice).
- Kostal Modbus (rev 2.9) and KSEM register maps verified from the official PDFs.
- `docs/architecture.md` – 8 Mermaid diagrams, rendered with mermaid-cli.
- `docs/roadmap.md`.
- Repository scaffold: `suncore/` packages (empty, SPDX headers, smoke
  tests), `addon/suncore` + `addon/suncore_beta` (config, Dockerfile, DOCS,
  translations, icons), community files, issue/PR templates, `uv.lock`.
- CI: `ci.yml` (ruff, mypy, pytest 3.12/3.13, lockfile), `app-lint.yml`,
  `docs-lint.yml` (markdownlint, lychee offline, Mermaid render),
  `codeql.yml`, `build.yml` (official HA builder actions; amd64 smoke build
  on PRs, multi-arch publish on main/release), `release-please.yml`.
- `.claude/`: 11 agents, 6 skills, format + pre-commit hooks, permissions;
  `CLAUDE.md` (85 lines).

## Open – Phase 0

- PR #1 awaits CI results (first run of the workflows) and the maintainer's
  merge. The image build could not be run in the cloud session (no Docker
  daemon) – CI validates it.
- CodeQL is skipped while the repository is private ("Code scanning is not
  enabled for this repository"); it runs automatically once the repository is
  public, or earlier if the maintainer enables Code Security in the
  repository settings.
- `RELEASE_PLEASE_TOKEN` secret (fine-grained PAT) still to be created by
  the maintainer (ADR-0009); without it the release build must be started
  by `workflow_dispatch`.

## Next steps

1. Fix anything CI reports on PR #1; maintainer merges and deletes the old
   tags `v0.1.0`–`v0.3.0`.
2. Maintainer allows the blocked hosts in the cloud environment's network
   settings (optional, speeds up verification).
3. Phase 1 plan in `docs/plans/phase-1.md`.

## Known problems and `UNVERIFIED` items

| Item | Where | Needed to resolve |
|---|---|---|
| Kostal §3.4 set-point timeout and how external battery management is enabled (register map itself is verified from KOSTAL rev 2.9) | `research/kostal-plenticore-modbus.md` | Phase 4 measurement on the maintainer's inverter |
| Hosted Weblate free tier for libre projects | ADR-0007 | maintainer confirms on weblate.org |
| Open-Meteo tilted-irradiance parameters; Energy-Charts and aWATTar APIs and terms | `research/integrations.md` | Phase 4, session with network access |
| Forecast.Solar / Open-Meteo Solar Forecast attribute names; Octopus attributes | `research/integrations.md` | Phase 4, read integration source |
| Theme variable inheritance inside ingress iframe | `research/ha-apps.md` | Phase 1 spike |
| Registry list WebSocket command names; `power_config` shape in energy prefs | `research/ha-websocket-api.md` | Phase 1/2, read `components/config/*.py` |
| evcc REST API paths | `research/competitors.md` | Phase 6, MQTT API is verified and sufficient |
| Solve times on Raspberry Pi 4 | ADR-0003 | Phase 4 benchmark |

## Environment notes

- Cloud sessions cannot reach `home-assistant.io`, `developers.home-assistant.io`,
  `*.readthedocs.io`, `*.github.io`, `docs.evcc.io`, `open-meteo.com`,
  `weblate.org`. Use the GitHub source repositories instead (see
  `docs/research/README.md`).
- GitHub API (`api.github.com`) returns 403 from the session; use
  `raw.githubusercontent.com` and `github.com` tree pages.
