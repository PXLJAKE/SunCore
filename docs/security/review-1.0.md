# Security review 1.0

Phase 7, PR 7-O, work package WP14 (`docs/plans/phase-7.md`). Reviewed on 2026-11-25 on branch
`phase-7/o-security`. The branch is `phase-7/i-diagnostics-backend` with
`phase-7/h-ha-surface` merged in, so the review covers the diagnostics export (7-I) and the MQTT
event surface (7-H). The inverter verification window (7-J) does not exist on this base. Its row
is "pending 7-J".

Scope: the WP14 table. The areas are exposure, API, HA commands, verification window, secrets,
supply chain, app hardening and data. Each claim was checked against the code, the app config
and the workflows of this branch. Supervisor and Home Assistant behaviour was checked against
their source on GitHub, read on 2026-11-25: `home-assistant/supervisor` `main`,
`home-assistant/frontend` `dev`, `home-assistant/core` `dev`, and the `mosquitto/DOCS.md` of
`home-assistant/addons`. Paths of the form `supervisor/...` refer to that repository. Line
numbers are those of the files as read.

Severity: **High** means it can be exploited without prior access. **Medium** means it leaks a
secret or widens access after a plausible mistake or a first compromise. **Low** is defence in
depth. **Info** needs no change.

Status: **fixed** means fixed in this branch. **needs control-safety** means it touches a command
path; a proposal is below and nothing was changed. **open** means a proposal for a later PR.
**accepted** is explained in "Accepted risks". **verified** means it was checked and nothing was
found.

## Findings

| ID | Area | Finding | Severity | Status | Fix PR |
|---|---|---|---|---|---|
| SEC-01 | Secrets | A rejected import or a corrupt stored `mqtt` section put `str(ValidationError)` into `ConfigCorruptError`. That text repeats `input_value`, which is the whole body with the MQTT password in clear text. The API logs it at info level (`api/app.py:315`), and the chained exception carried it into tracebacks. | Medium | fixed | 7-O, `63c9b50` |
| SEC-02 | Secrets | The MQTT broker password (manual, or the one from the Supervisor) was not registered with the log redaction. | Low | fixed | 7-O, `63c9b50` |
| SEC-03 | Secrets | The redaction did not mask password-like keys (`password`, `secret`, `api_key`, ...) in free text, or the password in `scheme://user:passwordhost`. | Low | fixed | 7-O, `63c9b50` |
| SEC-04 | Exposure | `SUNCORE_ALLOW_ANY_PEER` disabled the peer check without a log line. | Low | fixed | 7-O, `1f31af8` |
| SEC-05 | Exposure | uvicorn trusts `X-Forwarded-For` from loopback by default (`proxy_headers=True`). A local process could have claimed to be the ingress peer. | Low | fixed | 7-O, `1f31af8` |
| SEC-06 | API | Seven routes with a JSON body had no body cap: notification settings, override, the four operation modes and backtests. | Low | fixed | 7-O, `1f31af8` |
| SEC-07 | App hardening | The read-only `homeassistant_config` map was not used by any code. It exposed `secrets.yaml` and `.storage/` (auth tokens) to the container. | Medium | fixed | 7-O, `ce8ecd9` |
| SEC-08 | Supply chain | Actions were pinned by tag, and `home-assistant/actions/helpers/infomaster` ran on a moving branch in the publishing workflow. | Medium | fixed | 7-O, `fc6f080` |
| SEC-09 | Supply chain | Dependabot did not watch `frontend/package-lock.json`. | Low | fixed | 7-O, `fc6f080` |
| SEC-10 | Supply chain | CodeQL did not analyse the SPA (JavaScript/TypeScript). The job is skipped while the repository is private. | Low | fixed (language); private skip remains | 7-O, `fc6f080` |
| SEC-11 | Supply chain | `pip-audit` found certifi PYSEC-2026-4320 (dev group only) and opentelemetry-api CVE-2026-126360 (runtime, not reachable). | Low | fixed | 7-O, `f604a8f` |
| SEC-12 | API | Responses had no `X-Content-Type-Options` or `Referrer-Policy` header, and there is no CSP. | Low | fixed (headers); CSP open | 7-O, `1f31af8` |
| SEC-13 | API | No CSRF token or custom-header check. State-changing routes without a body (emergency stop and clear, proposal confirm, replan, history restore, program cancel, backtest delete) accept any content type. Two things mitigate this: the `SameSite=Strict` ingress cookie and the random ingress path. | Low | needs control-safety | – |
| SEC-14 | HA commands | Every broker user can send commands: all HA users, non-admins included, and every app through the `addons` user. The panel is admin-only. | Low | accepted (docs proposal) | – |
| SEC-15 | HA commands | Each emergency-stop message gets its own task, with no bound and no coalescing. | Low | needs control-safety | – |
| SEC-16 | HA commands | Every refused command is logged at info level, so a broker user can flood the app log. | Low | needs control-safety | – |
| SEC-17 | App hardening | The process runs as root in the container. The Supervisor sets `seccomp=unconfined` for every app. | Low | open (after 1.0) | – |
| SEC-18 | App hardening | There is no own AppArmor profile. With host AppArmor the Supervisor names none and Docker's default applies (security rating 7 of 8). | Low | accepted for 1.0 | – |
| SEC-19 | Supply chain | The base image (`BUILD_FROM`) and the `uv` image are pinned by tag, not by digest. Dependabot does not follow an `ARG` in `FROM`. | Low | open | – |
| SEC-20 | Supply chain | Images are signed with Cosign, but the Supervisor does not verify app signatures (`signed` is always false). | Info | accepted | – |
| SEC-21 | Supply chain | `npm audit --omit=dev` reports echarts 5.6.0, GHSA-fgmj-fm8m-jvvx (XSS, moderate). The fix is 6.1.0, a major update. | Low | accepted for 1.0 | – |
| SEC-22 | Secrets | The MQTT password is stored in clear text in `suncore.db` and is therefore in HA backups (question 19). Backups are encrypted only if a backup password is set. | Low | accepted (question 23) | – |
| SEC-23 | API | The `api/v1/live` WebSocket does not check `Origin`. | Info | accepted | – |
| SEC-24 | Verification window | The X3 waiver of 7-J: only with an active window, only `Origin.USER`, expiry, cleared on abort, restart and emergency stop. | – | pending 7-J | – |
| SEC-25 | Data | No telemetry; diagnostics are pseudonymised; backup exclusions. | Info | verified | – |
| SEC-26 | HA commands | Notification action tokens are single-use and expire. | Info | verified | – |

## Evidence

### Exposure

- **Ingress only, no ports.** `addon/suncore/config.yaml` sets `ingress: true` and has no
  `ports`, `host_network` or `ingress_port`. The Supervisor's default `ingress_port` is 8099
  (`supervisor/apps/validate.py:485`). "Ingress port" means the port *inside* the container:
  the proxy connects to `http://{app.ip_address}:{app.ingress_port}/`
  (`supervisor/api/ingress.py:127`). Host ports exist only for `ports` entries that have a host
  port (`supervisor/docker/app.py:311-320`). The new packaging test
  `tests/test_app_packaging.py::test_least_privilege_surface` keeps this so for both apps.
- **Admin only.** The `panel_admin` default is `True` (`supervisor/apps/validate.py:492`). The
  ingress handler validates the `ingress_session` cookie and refuses a non-admin user when
  `panel_admin` is set (`supervisor/api/ingress.py:180-197`). The session is
  `secrets.token_hex(64)` and lives 15 minutes, renewed while in use
  (`supervisor/ingress.py:134-137`). The ingress path token is `secrets.token_urlsafe`
  (`supervisor/apps/validate.py:623`).
- **Peer check.** `PeerCheckMiddleware` (`suncore/api/security.py:30-68`) is pure ASGI and covers
  HTTP and WebSocket. It allows only the configured peers. `/health` is also open to loopback,
  for the Docker `HEALTHCHECK` (`security.py:43-48`). Under Supervisor the allowed peer is
  `172.30.32.2` (`suncore/app/settings.py:35`, `suncore/app/runtime.py:1708-1716`); without
  Supervisor it is loopback. Tests: `tests/api/test_security.py`.
- **SEC-04 (fixed).** `allow_any_peer` (`settings.py:75`) is set only through the environment.
  The app options cannot set it, because `config.yaml` has no such option and the Supervisor
  writes only options to `/data/options.json`. It now logs `app.peer_check_disabled` as a warning
  at start (`suncore/app/main.py:275-285`). Test: `test_disabled_peer_check_is_logged_as_a_warning`.
- **SEC-05 (fixed).** uvicorn 0.54 defaults to `proxy_headers=True` and trusts
  `FORWARDED_ALLOW_IPS`, which defaults to `127.0.0.1,::1`. A loopback client could then have set
  `X-Forwarded-For: 172.30.32.2`. The ingress proxy itself appends the real peer to
  `X-Forwarded-For` (`supervisor/api/ingress.py:420-425`), and 172.30.32.2 is not trusted, so
  there was no remote path. The server now sets `proxy_headers=False` (`main.py:234`). Test:
  `test_server_ignores_forwarded_headers`.
- **Who else can reach port 8099.** Other containers on the `hassio` network can open the port,
  but the peer check refuses them. Whether an app with `NET_ADMIN` could spoof 172.30.32.2 on the
  bridge is `UNVERIFIED`.

### API

- **Write routes and the peer rule.** Every route, read and write, is behind the same middleware.
  `create_app` adds `BodyLimitMiddleware`, then `PeerCheckMiddleware`, then
  `SecurityHeadersMiddleware`, so the peer check runs before any body is read
  (`suncore/api/app.py:195-197`). There is no per-user authorisation. The ingress proxy forwards
  `X-Remote-User-Id` and the user's groups, and a client cannot smuggle them in
  (`supervisor/api/ingress.py:47-63, 411-419`). SunCore does not use them, because the panel is
  admin-only.
- **Body limits (SEC-06, fixed).** `BODY_LIMITS` (`api/app.py:161-178`) now covers every route
  whose handler takes a JSON body. That includes the 6-M optional items `PUT /devices/{id}/ev`
  and `POST .../program/arm`, which were already there. `POST /config/import` counts its own
  stream against `max_import_bytes`, 1 MiB by default (`api/app.py:296-317`). The middleware
  counts the bytes that actually arrive, not `Content-Length` (`suncore/api/limits.py:40-75`). The
  test `test_every_json_body_route_has_a_body_cap` fails when a new body route has no rule.
- **422/409 do not echo input.** The validation handler returns catalogue keys and only scalar
  context values. It never returns `input` (`suncore/api/validation.py:126-143`). The import
  answers `api.error.import_rejected` and history restore answers `api.error.history_invalid`
  (`api/app.py:752-757`); the reason stays in the log. That log line held the password before
  SEC-01.
- **SEC-01 (fixed).** `ConfigStore._upgrade` now uses `describe_validation_error`
  (`suncore/storage/config_store.py:67-79`). It keeps location, type and the static message, and
  raises `from None`, so tracebacks no longer show the Pydantic error. Tests:
  `tests/storage/test_config_secret_errors.py` (import, stored row, full traceback) and
  `tests/app/test_demo.py::test_rejected_import_never_logs_the_mqtt_password` (the real API and
  its log line).
- **Static files.** `_mount_spa` resolves the path and serves it only if `is_relative_to` the
  `dist` root (`api/app.py:1028-1044`). `/docs` and `/redoc` are off. `/openapi.json` is behind
  the peer check.
- **SEC-12 (fixed in part).** `SecurityHeadersMiddleware` (`suncore/api/security.py:71-99`)
  adds `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`, including on the
  403. The ingress proxy passes response headers through; it filters only the transfer, length,
  type and encoding headers (`supervisor/api/ingress.py:65-73`). A CSP is open: it needs a check
  of the SPA and of ECharts' inline styles in a browser (`frontend-engineer`).
- **SEC-13 (needs control-safety).** CSRF protection rests on three things:
  - the `ingress_session` cookie is set with `SameSite=Strict` (HA frontend
    `src/data/hassio/ingress.ts:7`);
  - the ingress path contains a per-install random token;
  - the panel is admin-only.

  Routes with a JSON body refuse a form post: FastAPI passes a non-JSON body through as bytes,
  and validation then gives 422. Routes without a body do not refuse it. These are
  `POST /control/emergency-stop`, `/control/emergency-stop/clear`, `/proposals/{id}/confirm`,
  `/proposals/{id}/dismiss`, `/plan/replan`, `/config/{section}/history/{id}/restore`,
  `/devices/{id}/program/cancel` and `DELETE /backtests/{id}`. **Proposal:** a
  `RequireHeaderMiddleware` that refuses every non-GET under `/api/v1` without
  `X-SunCore-Request: 1`. A cross-site page cannot set that header without a CORS preflight, and
  SunCore answers none. The SPA's fetch helper sends the header. `control-safety` decides whether
  setting the emergency stop is exempt, so that an old client can always stop; clearing it is not
  exempt. This needs a frontend change and an e2e test, so it is not done here.
- **SEC-23 (accepted).** The live WebSocket (`api/app.py:861`) sends data only; it reads client
  messages and discards them. The peer check, the `SameSite=Strict` cookie and admin-only access
  apply to it as well.

### HA commands

- **Who can publish.** The Mosquitto app authenticates every Home Assistant user, and reserves
  `homeassistant` and `addons` for the internal ecosystem, with unrestricted access unless ACLs
  are set up (`mosquitto/DOCS.md`, lines 154 and 171). So a non-admin HA user and every app with
  the MQTT service can publish to `suncore/<iid>/<object_id>/set`. This matches the HA entity
  model: the same users can press the same entities in the HA UI. **SEC-14 accepted.** Proposal
  for the user guide (`docs-writer`): an ACL example that limits `suncore/#` to `homeassistant`,
  `addons` and SunCore's own user.
- **Rules** (`suncore/ha/mqtt/commands.py`):
  - retained commands are ignored and removed (`:234`, `service.py:387-390`);
  - the payload may be at most 64 bytes (`:47`);
  - per entity 1/s with a burst of 3, and 20 per 10 s over all entities (`:48-51`);
  - a raise needs `mqtt.allow_mode_raise`, default off (`core/config.py:645`; `commands.py:312`
    for clearing the stop and `:347` for a mode);
  - the emergency stop is never rate-limited or refused (`:184`), and a settled stop is not
    repeated.

  Queued commands use a bounded queue of 64 (`service.py:62, 269, 364`). Commands go through the
  same application services as the UI and the safety gate; this module never touches a device.
- **SEC-15 (needs control-safety).** `_route_command` (`service.py:343-358`) starts one task per
  emergency-stop message, outside the queue. A flood of stop messages creates that many tasks. The
  effect stays the same (a settled stop returns early), but memory and broker echoes are
  unbounded. **Proposal:** while a priority task is in flight, a further stop message only sets a
  "again" flag. The running task then repeats the stop at most once. This still never drops the
  first stop.
- **SEC-16 (needs control-safety).** `mqtt.command_refused` is logged at info level for every
  refused message (`service.py:393`), including rate-limited ones. **Proposal:** log the first
  refusal per entity and reason, then a count every 60 s. Nothing else changes.
- **SEC-26 (verified).** Notification action tokens (`suncore/notify/actions.py`):
  - each is a 128-bit random token (`:85`) stored as a SHA-256 digest;
  - it is bound to one proposal and its expiry (`:103-105`);
  - it is single-use, and its sibling dies with it (`:106`);
  - it is held in memory only, so a restart invalidates all tokens;
  - it never appears in a log line (`notify/service.py:382-429` logs verb and outcome only).

### Verification window

Pending 7-J. When 7-J lands, the review must check five things:

- the X3 waiver applies only while a window opened by the test is active;
- it applies only to `Origin.USER` steps in assisted mode;
- its expiry is enforced, at most 10 minutes (question 21);
- it is cleared on abort, restart and emergency stop;
- X2 (no import) is never waived.

Owner: `control-safety`, as part of the 7-J sign-off table.

### Secrets

- **Supervisor token.** It is not a setting (`suncore/app/settings.py:9-11`). It is read from the
  environment where it is used and registered with the redaction before logging starts
  (`suncore/app/main.py:305-309`). Tests: `tests/app/test_process.py`, `tests/ha/test_redact.py`.
- **Redaction.** It runs as a structlog processor on both chains, native and stdlib
  (`suncore/app/logconfig.py:38-58`). The diagnostics log ring is attached after it and keeps
  only redacted events in memory (`logconfig.py:63`).
- **SEC-02 (fixed).** `lookup_broker` registers the manual password and the Supervisor's
  password (`suncore/ha/mqtt/broker.py:158, 180, 184-189`). Test:
  `test_broker_passwords_are_registered_for_log_redaction`.
- **SEC-03 (fixed).** `SENSITIVE_KEYS` and `_JSON_FIELD` were extended, and `_URL_USERINFO` is
  new (`suncore/ha/redact.py:20-41`). Tests: `test_passwords_in_free_text_are_masked`,
  `test_ordinary_text_about_passwords_is_kept`, `test_secret_like_keys_are_masked`. A
  registered short password (4 characters or more) also masks that substring in unrelated log
  text. This is safe, only cosmetic.
- **Masking.**
  - `MqttConfig.password` is a `SecretStr`. It is dumped in clear text only with the
    `REVEAL_SECRETS` context (`core/config.py:618, 648-655`), and only the store sets that
    context (`storage/config_store.py:229-231`).
  - API, export and history get `**********` (`config_store.py:233-242`). A mask sent back keeps
    the stored secret, or is refused when nothing is stored (`config_store.py:305`).
  - The diagnostics export masks every key that contains a secret word (`app/diagnostics.py:94,
    221, 306`) and registers the hidden values with its text scrubber (`:212`). The 7-I tests
    seed an MQTT password and a token and assert that neither appears.
- **SEC-22, question 19 (accepted).** The stored password is in `suncore.db`, which is in HA
  backups (`backup_exclude` lists only `timeseries.db*`). HA core encrypts a backup only when the
  backup configuration has a password. `create_backup.password` defaults to `None`
  (`homeassistant/components/backup/config.py:652`), and each location has its own `protected`
  flag (`:284`). So "HA backups are encrypted" is not always true; whether the onboarding sets a
  key by default is `UNVERIFIED`. Encrypting the value inside SunCore would not help, because the
  key would sit in the same `/data` and the same backup. Recommendation:
  - keep the current storage;
  - document it, and prefer the Supervisor broker, whose credentials are never stored (they are
    fetched at every connect);
  - for a manual broker, a dedicated MQTT user with an ACL;
  - backup encryption on (see question 23).

### Supply chain

- **Python.**
  - The image installs exactly `uv.lock`, hash-checked, wheels only, from PyPI alone
    (`addon/suncore/Dockerfile:50-54`).
  - CI uses `uv sync --frozen` (`.github/workflows/ci.yml`).
  - `uvx pip-audit` 2.10.1 over the full lock found certifi PYSEC-2026-4320 (GHSA-6q48-6c7w-9gw2,
    compromised emSign intermediates). It is in the dev group only, through httpx, and is not in
    the image.
  - It also found opentelemetry-api CVE-2026-126360 (GHSA-mf5v-w3w3-f6cg, `traceparent` ReDoS).
    FastAPI 0.143 pulls it in, but FastAPI's telemetry runs only when a tracer, meter or logger
    provider is configured (`fastapi/applications.py` `NativeTelemetry.enabled`). SunCore
    configures none, so it is not reachable.
  - Both were bumped in `f604a8f`; the re-run reports "No known vulnerabilities found".
- **npm.**
  - `npm ci` with the lockfile is used in CI and in the image (`Dockerfile:19-20`).
  - `npm audit --omit=dev` reports only echarts 5.6.0, GHSA-fgmj-fm8m-jvvx (CWE-79, CVSS 6.1).
    **SEC-21 accepted for 1.0.** Every tooltip is a DOM element built with `textContent`
    (`frontend/src/lib/history/chartOptions.ts:91-99`; the plan chart through `l.tooltip`). Labels
    are catalogue strings or numbers, and no `{html}` or `innerHTML` exists in `src/`. The
    advisory text could not be read in this session (GitHub advisory pages and OSV were not
    reachable), so whether SunCore uses the affected option is `UNVERIFIED`. Update to echarts 6
    after 1.0 with a visual check.
  - The full `npm audit` adds dev-only findings that are not in the image: vite 8.3.1 (dev server
    and `launch-editor`), undici, and minimatch through eslint. Dependabot now proposes them
    (SEC-09).
- **Images.**
  - The node stage is pinned by digest (`Dockerfile:15`).
  - **SEC-19 (open).** `BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.24` and
    `ghcr.io/astral-sh/uv:0.11.32` are tags (`Dockerfile:8, 40`). **Proposal:** add the digest of
    the multi-arch index to both, and add a digest check to `prepare-release`, like the 7-N
    question on the Mosquitto image of the smoke test.
- **Actions (SEC-08, fixed).** Every `uses:` is a full SHA with the version as a comment,
  resolved with `git ls-remote` on 2026-11-25. `home-assistant/actions/helpers/info` was on
  `master` in the job whose outputs decide what is published. It is now pinned to `9e9565e`.
  Dependabot (`github-actions`) updates SHA pins. Permissions are already minimal per job: the
  default is `contents: read`, and `id-token`/`packages` are only on build and manifest.
  `bump-beta` pushes with `RELEASE_PLEASE_TOKEN` and runs only on `push` to `main`.
- **CodeQL (SEC-10).** It now runs python, javascript-typescript and actions. The whole job is
  still skipped while the repository is private (`codeql.yml:18`); it runs once the repository
  is public.
- **Cosign (SEC-20).** `build-image` signs pushed images (`build.yml`, `cosign:`). The Supervisor
  does not check signatures: `AppModel.signed` returns `False` with "Currently no signing
  support" (`supervisor/apps/model.py:870-872`). The signature is therefore only useful for a
  manual `cosign verify`. Whether the builder action signs keylessly is `UNVERIFIED`; its source
  was not read.

### App hardening

- **`homeassistant_api: true`.** It is needed for the Core WebSocket at
  `ws://supervisor/core/websocket` (`suncore/ha/transport.py:26`); its default is false
  (`supervisor/apps/validate.py:525`).
- **No `hassio_api`.** Its default is false (`validate.py:523`). The MQTT lookup reaches
  `/services*` through the Supervisor's `api_bypass` (`supervisor/api/middleware/security.py:107-
  112`). The role stays `default`.
- **SEC-07 (fixed).** The `homeassistant_config` map was removed from both apps. With the map,
  the Supervisor bind-mounts the HA config folder at `/homeassistant`, read-only
  (`supervisor/docker/app.py:519-528`). That folder holds `secrets.yaml` and `.storage/auth`
  (refresh tokens). No code reads it: no path under `/homeassistant` or `/config` exists in
  `suncore/`. The map came from the scaffold (`c14d714`).
- **AppArmor (SEC-18, accepted).**
  - The `apparmor` default is `True` (`validate.py:513`).
  - With an `apparmor.txt` in the app folder, the Supervisor loads it as profile `<slug>`.
    Without one the mode is `SECURITY_DEFAULT` (`supervisor/apps/model.py:463-469`), and
    `security_opt` adds no AppArmor option (`supervisor/docker/app.py:323-334`). Docker then
    applies its `docker-default` profile when the host has AppArmor; that Docker behaviour is
    `UNVERIFIED` here (Docker source not read).
  - The security rating is 5, +2 for ingress, and +1 with an own profile, which gives 7 now and 8
    with a profile (`supervisor/apps/utils.py:26-97`).
  - A profile needs a test on HA OS (Python, s6, `/data`, the socket to `supervisor`), so it comes
    after 1.0.
- **SEC-17 (open, after 1.0).** `rootfs/etc/services.d/suncore/run` execs `python3 -m suncore` as
  root. The Supervisor sets `seccomp=unconfined` for every app
  (`supervisor/docker/interface.py:203-207`). **Proposal:** a `suncore` user in the image, a
  `chown` of `/data` in a oneshot, and `s6-setuidgid suncore` in `run`. Test it with the smoke
  test and the 0.2.0 to 1.0 upgrade test of 7-N; `/data` files written by root in earlier
  versions must stay readable.

### Data

- **`/data`.**
  - `suncore.db` holds configuration (with the MQTT password), history, decisions and runtime
    keys.
  - `timeseries.db` with WAL and SHM holds measurements and forecasts.
  - `options.json` is written by the Supervisor.
  - `templates/` holds user templates, parsed with a `SafeLoader` that refuses aliases
    (`suncore/templates/loader.py:133-169`).
  - `backup_exclude` drops the timeseries files and the `.corrupt*` copies
    (`addon/suncore/config.yaml`).
- **Diagnostics.** 7-I builds the export from an allowlist:
  - entity ids, device names and areas are replaced by salted per-export HMAC pseudonyms;
  - coordinates are used only to mask text;
  - the log ring is memory only.

  Its tests seed an MQTT password, a Tibber token, a config entry id, a home name and coordinates,
  and assert that none of them appears. The free-text limits are in the STATUS `UNVERIFIED`
  table (7-I rows).
- **No telemetry** (BRIEF section 6). The only outbound connections are `http://supervisor/...`,
  `ws://supervisor/core/websocket` and the MQTT broker (`suncore/ha/mqtt/broker.py:31`,
  `suncore/ha/transport.py:26`). `suncore/` has no other HTTP client.
  - Frontend: there are no external requests, fonts or CDNs. The only external URLs are links
    with `rel="noopener noreferrer"` (`frontend/src/lib/components/ForecastPanel.svelte:283-288`).
  - `discovery.py:38` announces the project URL as the device's support link. That is text, not
    a request.

## Accepted risks

- **The MQTT command surface is open to every broker user** (SEC-14). This is the HA model:
  everyone who can use HA can press its entities. Autonomy cannot be raised without the opt-in;
  lowering it, stopping, pausing and boosting are harmless or covered by the gate.
- **MQTT password in clear text in `suncore.db` and backups** (SEC-22, question 19). A key would
  live next to it. The user guide recommends the Supervisor broker, a dedicated user with an ACL,
  and backup encryption.
- **No own AppArmor profile, root in the container, seccomp unconfined** (SEC-17, SEC-18). For
  1.0 the isolation is the Supervisor's container, ingress-only and no privileges; hardening comes
  after 1.0.
- **echarts 5.6.0 advisory** (SEC-21). Chart content is not user HTML, and the update is a major
  one.
- **Unverified signatures** (SEC-20). The Supervisor has no signature check; this is a platform
  limitation.
- **WebSocket without an Origin check** (SEC-23). It is read only and behind the same ingress
  authentication.

## Questions for the maintainer

The German questions are in `docs/plans/phase-7.md` section 10, numbers 23 to 28 (on this base;
7-N and 7-L use 23 to 25 on their branches, so the second merge renumbers).

## `UNVERIFIED`

- Whether an app with `NET_ADMIN` on the `hassio` bridge could spoof the source 172.30.32.2.
- Docker applying `docker-default` when the Supervisor names no AppArmor profile.
- Whether HA onboarding sets a backup encryption key by default, and the `protected` flag of the
  local backup location.
- The content of GHSA-fgmj-fm8m-jvvx: the affected ECharts option and whether SunCore uses it.
- Keyless Cosign signing in `home-assistant/builder` `build-image`.
- The SHA pins were not run in CI in this session; the first CI run of 7-O confirms them.
- The Mosquitto ACL default is taken from the app's `DOCS.md`, not from its source.
