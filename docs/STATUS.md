# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-12 · **Phase:** 0 done, Phase 1 plan awaiting approval · **Branch:** `phase-1/plan`

## Done

- Repository reset to an empty history (old prototype removed; old tags
  deleted by the maintainer).
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

- PR #1 merged (2026-10-12). First push to `main` built and pushed
  `ghcr.io/pxljake/{amd64,aarch64}-suncore-beta` on native runners; the
  `ubuntu-24.04-arm` runner works for this repository.
- `docs/plans/phase-1.md` drafted.

## Open

- **release-please is blocked:** "GitHub Actions is not permitted to create
  or approve pull requests". Maintainer enables *Settings → Actions →
  General → Workflow permissions → Allow GitHub Actions to create and
  approve pull requests*, or adds the `RELEASE_PLEASE_TOKEN` PAT (which
  also lets releases trigger the image build). The branch
  `release-please--branches--main--components--suncore` it created is
  reused on the next run.
- CodeQL is skipped while the repository is private ("Code scanning is not
  enabled for this repository"); it runs automatically once the repository is
  public, or earlier if the maintainer enables Code Security in the
  repository settings.
- `RELEASE_PLEASE_TOKEN` secret (fine-grained PAT) still to be created by
  the maintainer (ADR-0009); without it the release build must be started
  by `workflow_dispatch`.

## Next steps

1. Maintainer approves `docs/plans/phase-1.md` (or amends it).
2. Maintainer fixes the release-please permission (above).
3. Maintainer allows the blocked hosts in the cloud environment's network
   settings (optional, speeds up verification).
4. Phase 1, PR 1 (bootstrap, s6, health, smoke test) and PR 2 (core model,
   safety skeleton) start in parallel.

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
