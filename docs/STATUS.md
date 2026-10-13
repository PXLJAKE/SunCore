# Status

Read this first in every session, then `CLAUDE.md` (once it exists) and the
ADRs relevant to your task. Keep it short; history lives in git.

**Last update:** 2026-10-12 · **Phase:** 1 (app skeleton) · **Branch:** `phase-1/app-skeleton`

## Done

- Phase 0 merged (PR #1): brief, research, ADRs 0001–0011 accepted,
  architecture, roadmap, scaffold, CI, agent team.
- Phase 1 plan `docs/plans/phase-1.md` approved (defaults of its §6: four
  stacked PRs 1-A…1-D, `sensor.sim_*` demo entities, browser language,
  repository stays private).
- **PR 1-A** (WP1, WP2, WP4):
  - `core`: units and HA unit parsing (unknown unit → `invalid`, never
    guessed), signals with quality, `SiteConfig`, energy balance
    (`house = pv + grid − battery`, worst input quality propagates).
  - `core.decisions` + `safety`: `Reason`/`Intent`/`Decision` contracts,
    `SafetyGate` structurally fixed to shadow mode, global emergency stop,
    `Actuator` protocol without implementations; AST boundary test
    (single writer, `call_service` only in `ha`/`safety`, dependency rule).
    Signed off by `control-safety`.
  - `storage`: `/data/suncore.db` with SQL migrations (`PRAGMA
    user_version`), refusal of newer DBs, `ConfigStore` with per-section
    document migrations, atomic export/import, decision log.
  - Coverage gate ≥ 80 % for core/safety/planner/controller in
    `check.sh` and CI.
- R1 cleared: every planned Python dependency has cp313 musllinux wheels
  for aarch64 and x86_64 or is pure Python.

## Open – Phase 1

- PR 1-B (HA connector, API, simulator/demo), 1-C (frontend, i18n),
  1-D (image, smoke test, docs, beta release).
- WP8 must add the reason keys `safety.shadow_mode` and
  `safety.emergency_stop` to `en.json`/`de.json`.
- WP5 must limit the request body size of `POST /api/v1/config/import`.
- Python ICU library decision (ADR-0007 follow-up) deferred to Phase 6:
  in Phase 1 the backend only emits `Reason(key, args)`.
- Phase 3 follow-ups: extend boundary rule 2 to `suncore.devices` drivers
  (only `safety` and `suncore.app` may import them) and remove
  `test_phase_1_has_no_apply_call_anywhere`; version the decision payload
  (or deserialise tolerantly) before changing `Decision`; retention for the
  `decision` table; tables `plan_snapshot`/`template_override` and
  `timeseries.db`. Emergency stop is not persisted yet (no hardware effect
  in Phase 1). Migrations are SQL files; Python migrations when needed.
- Carried over from Phase 0: `RELEASE_PLEASE_TOKEN` secret, CodeQL while
  the repository is private, deleting old tags `v0.1.0`–`v0.3.0`.

## Next steps

1. Maintainer reviews and merges PR 1-A.
2. WP3 HA connector, WP5 API, WP6 simulator → PR 1-B.

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
