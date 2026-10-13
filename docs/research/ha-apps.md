# Home Assistant apps (formerly add-ons)

## Terminology

Since Home Assistant 2026.2 add-ons are called **apps** in the UI and in the
developer documentation ("app, formerly known as an add-on"). Identifiers in
configuration and APIs keep older names where noted: `hassio_api`,
`hassio_role`, the Supervisor `/addons/...` endpoints, and the
`io.hass.type=app` label value is the new form. SunCore uses "app" in prose
and the exact key names from the schema in code.

Supervisor source has been renamed accordingly: the config schema lives in
`supervisor/apps/validate.py` (`SCHEMA_APP_CONFIG`), not
`supervisor/addons/validate.py`.

## Repository layout

- `repository.yaml` at the git root with `name` (required), `url`, `maintainer`.
- One folder per app. A repository may contain several apps, which is how a
  stable and a beta channel are published (two folders, same build).
- Users add the repository through the app store; a `my.home-assistant.io`
  link in the README gives one-click add.

## `config.yaml` (verified against the Supervisor schema)

Required: `name`, `version` (string; quote numeric values), `slug`,
`description`, `arch`.

| Key | Default | Notes |
|---|---|---|
| `arch` | – | Allowed: `aarch64`, `amd64`. `armhf`, `armv7`, `i386` are `ARCH_DEPRECATED` and only trigger a warning. |
| `image` | – | Registry image name. Preferred form is a multi-arch manifest name, e.g. `ghcr.io/pxljake/suncore`; `{arch}` placeholder still works as a fallback. `version` must match the image tag. |
| `startup` | `application` | `initialize`, `system`, `services`, `application`, `once` |
| `boot` | `auto` | `auto`, `manual`, `manual_only` |
| `init` | `true` | `false` when the image brings its own init (s6-overlay v3 in the HA base images) |
| `ingress` | `false` | |
| `ingress_port` | `8099` | `0` = dynamic |
| `ingress_entry` | `/` | |
| `ingress_stream` | `false` | |
| `panel_icon` | `mdi:puzzle` | |
| `panel_title` | app name | |
| `panel_admin` | `true` | admin-only sidebar entry |
| `hassio_api` | `false` | Supervisor REST API at `http://supervisor` |
| `hassio_role` | `default` | `default`, `homeassistant`, `backup`, `manager`, `admin` |
| `homeassistant_api` | `false` | Core REST proxy at `http://supervisor/core/api` |
| `auth_api` | `false` | HA user backend |
| `watchdog` | – | still in the schema, but **the app linter (v2) rejects it as obsolete**: use a Docker `HEALTHCHECK` in the Dockerfile instead |
| `backup` | `hot` | `hot`, `cold` |
| `backup_exclude` | – | list of globs |
| `backup_pre` / `backup_post` | – | commands, ignored for `cold` |
| `map` | – | list of `{type, read_only (default true), path}`; types `homeassistant_config`, `app_config`, `all_app_configs`, `local_apps`, `ssl`, `backup`, `share`, `media`, `data`. `data` is always mapped writable. Old names (`addons`, `addon_config`, `config`, …) are migrated with a warning. |
| `services` | – | `^(mqtt\|mysql):(provide\|want\|need)$` |
| `discovery` | – | list of services the app provides to HA |
| `options` / `schema` | `{}` | see below |
| `environment` | – | dict |
| `timeout` | `10` | 10–300 s |
| `stage` | `stable` | `stable`, `experimental`, `deprecated` |
| `breaking_versions` | `[]` | versions that always need a manual update |
| `host_network`, `privileged`, `full_access`, `docker_api`, `uart`, `usb`, `gpio`, `realtime`, `journald`, `tmpfs`, `apparmor` (default `true`) | | not needed by SunCore except `apparmor` |

Unknown keys are removed (`extra=vol.REMOVE_EXTRA`), so typos fail silently:
the linter is the guard. The linter also **fails** on keys that repeat a
default (`startup: application`, `boot: auto`, `ingress_port: 8099`,
`panel_admin: true`, `hassio_role: default`, `backup: hot`) – omit them.

### Options schema syntax

Types: `str`, `str(min,max)`, `bool`, `int`, `int(min,max)`, `float`,
`float(min,max)`, `email`, `url`, `password`, `port`, `match(REGEX)`,
`list(a|b|c)`, `device`, `device(filter)`. Optional: append `?`. Required:
default `null`. Nesting of lists and dicts up to depth two.

### Translations of options

`<app>/translations/<lang>.yaml` (`.json`, `.yml` accepted):

```yaml
configuration:
  <option>:
    name: …
    description: …
    fields:          # nested options
      <sub>: {name: …, description: …}
network:
  "8099/tcp": …
```

## `build.yaml` is gone

`build.yaml` is no longer used. The Dockerfile sets the base image with an
explicit `FROM`; since Supervisor 2026.04.0 `BUILD_FROM` is **not** provided
by default. Build args available: `BUILD_VERSION`, `BUILD_ARCH`. The example
app uses `ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23` + `FROM ${BUILD_FROM}`.

## Base images

`ghcr.io/home-assistant/base` (Alpine 3.22–3.24), `base-python` with Python
3.12/3.13/3.14 (`3.13-alpine3.24`, …), `base-debian` (bookworm, trixie),
`base-ubuntu`. Since 2026.03.1 images are multi-arch manifests; the old
`aarch64-*`/`amd64-*` names remain available. Built for `amd64` and `arm64`
only.

## Ingress

- Serve on port 8099 (or set `ingress_port`), bind to the container and
  accept connections only from `172.30.32.2`.
- Authentication is done by Home Assistant. Supervisor adds
  `X-Ingress-Path` (base path, use it for relative URLs), `X-Remote-User-Id`,
  `X-Remote-User-Name`, `X-Remote-User-Display-Name`.
- Ingress panels are iframes; HA theme variables are **not** inherited
  automatically (UNVERIFIED for 2026.x – to be checked in Phase 1 when the
  first page renders; fallback is reading `prefers-color-scheme`).

## Communication with Supervisor and Core

- Token: `SUPERVISOR_TOKEN` env var, sent as `Authorization: Bearer …`.
- Core REST proxy: `http://supervisor/core/api/` (needs `homeassistant_api: true`).
- Core WebSocket proxy: `ws://supervisor/core/websocket`, authenticate with
  the Supervisor token as `access_token`.
- Callable without `hassio_api`: `/core/api`, `/core/api/stream`,
  `/core/websocket`, `/addons/self/*`, `/services*`, `/discovery*`, `/info`.
- `GET /addons/self/info` returns the app's own options, `ingress_entry`,
  `ingress_url` etc.
- MQTT credentials: declare `services: ["mqtt:want"]`, then
  `GET http://supervisor/services/mqtt` → `{addon, host, port, ssl,
  username, password, protocol}`. Fails with "Service not enabled" when no
  broker app provides it; SunCore must handle that (UI works without MQTT).
- `POST /discovery` with `{service, config}` lets an app announce a service to
  Core (used by the MQTT broker app; could be used by a future companion
  integration).
- Internal hostnames: `{repo}_{slug}` with `_`→`-` for DNS; Core is
  `homeassistant`, Supervisor is `supervisor`.

## Roles and security

`hassio_role`: `default` (info calls), `homeassistant` (Core API endpoints),
`backup`, `manager`, `admin`. Security rating 1–6 derived from requested
rights. Recommendations followed by SunCore: no host network, AppArmor
profile, read-only mounts, minimal API permissions, signed images (Cosign in
the official build action), `auth_api` instead of own credentials.

## Build and publish

The legacy `home-assistant/builder` action is **deprecated**. The current
pipeline (as used by `home-assistant/apps-example`):

1. `home-assistant/actions/helpers/info@master` reads `config.yaml` →
   `architectures` (from `arch`), `image`, `version`, `name`, `description`,
   `url`. (If a legacy `build.yaml` exists it reads architectures from
   `build_from` keys and warns.)
2. `home-assistant/builder/actions/prepare-multi-arch-matrix@2026.09.0`
   (`architectures`, `image-name`, `registry-prefix`) → matrix
   `{arch, os, image}` with `amd64 → ubuntu-24.04`,
   `aarch64 → ubuntu-24.04-arm`, image `<prefix>/<arch>-<name>`.
3. `home-assistant/builder/actions/build-image@2026.09.0` per arch: inputs
   `arch`, `image`, `image-tags`, `version`, `container-registry-password`
   (`GITHUB_TOKEN` for GHCR), `context`, `file`, `build-args`, `labels`,
   `push`, `load`, `skip-existing`, `cosign` (default true), cache options.
   Labels used by the example: `io.hass.type=app`, `io.hass.name`,
   `io.hass.description`, `io.hass.url`.
4. `home-assistant/builder/actions/publish-multi-arch-manifest@2026.09.0`
   combines the per-arch images under the version and `latest` tags.
   Permissions: `contents: read`, `id-token: write`, `packages: write`.

Native arm64 runners are used, so no QEMU emulation is needed.

## Linter

`frenck/action-app-linter@v2` (renamed from `action-addon-linter`), inputs
`path` (required, app folder) and `community` (default `"false"`). Validates
`config.*` and the legacy `build.*` against JSON schemas and warns about
redundant defaults.

## Local testing

Devcontainer from the developer docs is the recommended way; alternatively
`docker build -t local/suncore .` and `docker run --rm -v /tmp/data:/data
local/suncore`, `--platform linux/arm64` for aarch64.

## Platform support

Home Assistant deprecated `i386`, `armhf`, `armv7` (and the Core/Supervised
install methods) in May 2025 with a six-month window; HAOS 17.0 dropped
32-bit armv7 boards. Only `amd64` and `aarch64` remain.

## Verification

Verified on 2026-10-12 against:

- `home-assistant/developers.home-assistant` (master):
  `docs/apps/{configuration,presentation,repository,communication,publishing,security,testing}.md`
  and `docs/api/supervisor/endpoints.md`
- `home-assistant/supervisor` (main): `supervisor/apps/validate.py`,
  `supervisor/const.py`, `supervisor/services/modules/mqtt.py`
- `home-assistant/apps-example` (main): `example/config.yaml`,
  `example/Dockerfile`, `.github/workflows/{builder,build-app}.yaml`
- `home-assistant/builder`
  `actions/{build-image,prepare-multi-arch-matrix}/action.yml`,
  `home-assistant/actions` `helpers/info/action.yml`
- `home-assistant/docker-base` README
- `frenck/action-app-linter` README and `action.yaml`
- HAOS 17.0 release notes and the 2025-05-22 deprecation blog post (via search results; blog itself unreachable)

`UNVERIFIED`: theme-variable inheritance inside ingress iframes.

### Verification 2026-10-12 (WP9, image and ingress)

Sources fetched from `raw.githubusercontent.com` on 2026-10-12 (branches
`main`/`master` at that date).

- **Ingress gateway peer**: `supervisor/docker/network.py` defines
  `supervisor` as `DOCKER_IPV4_NETWORK_MASK[2]` with
  `DOCKER_IPV4_NETWORK_MASK = IPv4Network("172.30.32.0/23")`
  (`supervisor/const.py`), i.e. `172.30.32.2` is the Supervisor container.
  `supervisor/api/ingress.py` forwards with
  `http://{app.ip_address}:{app.ingress_port}/{path}` from that container.
  The developer docs (`docs/apps/presentation.md`) state the same address.
  Resolved: the peer value in `suncore/app/settings.py` is correct.
- **Defaults**: `supervisor/apps/validate.py`: `ingress_port` default `8099`,
  `panel_admin` default `True`, `init` default `True`, `ingress_stream`
  default `False`. SunCore therefore omits `ingress_port` and `panel_admin`
  (admin-only panel by default) and sets `init: false`.
- **Ingress URL and base path**: `supervisor/apps/app.py`: `ingress_url` is
  `/api/hassio_ingress/<token>/` (with trailing slash, plus `ingress_entry`
  if set), `ingress_entry` is `/api/hassio_ingress/<token>` (no slash). Core
  routes `/api/hassio_ingress/{token}/{path:.*}`
  (`core/homeassistant/components/hassio/ingress.py`) and sets
  `X-Ingress-Path: /api/hassio_ingress/<token>` (no trailing slash) there;
  the app receives the remainder as path, so the document URL always ends
  with a slash and relative asset URLs resolve below the token. No redirect
  is needed in the backend. `X-Remote-User-*` headers are set by Supervisor
  (`api/ingress.py`, `_init_header`).
- **Base image init**: `home-assistant/docker-base` `alpine/Dockerfile`
  (base of `base-python`): `ENTRYPOINT ["/init"]` (s6-overlay v3.2.3.2),
  `mkdir -p /etc/services.d`, `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`,
  `S6_CMD_WAIT_FOR_SERVICES=1`, `S6_SERVICES_READYTIME=50`, bashio 0.19.0.
  With `init: false` Supervisor does not add Docker's `--init`
  (`default_init`, `supervisor/docker/app.py`), so s6 is PID 1.
  The example app starts its service with legacy `/etc/services.d/<name>/run`
  (`#!/usr/bin/with-contenv bashio`, ends in `exec`) and a `finish` script
  (`#!/usr/bin/env bashio`) that writes
  `/run/s6-linux-init-container-results/exitcode` and runs
  `/run/s6/basedir/bin/halt` when the exit code is not 0 or 256.
  s6-overlay v3.2.3.2 README: stage 3 sends TERM to legacy services, waits
  `S6_SERVICES_GRACETIME` (default 3000 ms), then TERM to the rest and KILL
  after `S6_KILL_GRACETIME` (3000 ms); `docker stop` exit code defaults to 0.
  SunCore sets `S6_SERVICES_GRACETIME=5000` (total below Docker's 10 s).
  `base-python` (`python/3.13/Dockerfile` in `home-assistant/docker-base`)
  builds `FROM ${BUILD_FROM}` (the `alpine` base) and sets only `PATH`, no
  `ENTRYPOINT`/`CMD`, so it inherits `/init`, `/etc/services.d`,
  `with-contenv` and bashio. The smoke test proves the runtime behaviour.
- **Node base**: `node:22-alpine` index digest
  `sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402`
  (22.23.3, pushed 2026-09-23, Docker Hub tags API).
- **Builder per-arch image names**: `prepare-multi-arch-matrix` yields
  `<registry-prefix>/<arch>-<image-name>`; `build-image` tags
  `<image>:<tag>` per line of `image-tags`.
