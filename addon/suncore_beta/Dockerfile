# syntax=docker/dockerfile:1
# Build context is the repository root (see .github/workflows/build.yml).
# Stage 1 builds the SPA, stage 2 is the app image on the Home Assistant Python
# base (Alpine, s6-overlay v3, bashio). ADR-0001.

# Global build argument: an ARG used in a FROM line must be declared before the
# first FROM, otherwise it is scoped to the previous stage and empty here.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.24

# ---------------------------------------------------------------------------
# Stage 1: frontend (runs on the build host, the output is architecture-independent)
# node:22-alpine = 22.23.3-alpine, pushed 2026-09-23, digest looked up on Docker Hub
# 2026-10-12. Bump the digest deliberately (Renovate/Dependabot or by hand).
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS frontend

WORKDIR /src/frontend
# Dependencies first (layer cache); .npmrc carries legacy-peer-deps.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: app image
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SUNCORE_STATIC_DIR=/opt/suncore/frontend/dist \
    SUNCORE_TEMPLATES_DIR=/opt/suncore/templates \
    S6_SERVICES_GRACETIME=5000 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

WORKDIR /opt/suncore
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY suncore ./suncore
# Dependencies exactly as locked in uv.lock (hash-checked) and as wheels only:
# a missing aarch64/musl wheel must fail the build, not compile on a Pi.
# The base image adds the HA wheels index via UV_EXTRA_INDEX_URL; uv would
# then take only that index's versions, which may lag behind the lock, so the
# locked set is installed from PyPI alone. SunCore itself is pure Python.
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && env -u UV_EXTRA_INDEX_URL -u PIP_EXTRA_INDEX_URL \
       uv pip install --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
    && uv pip install --no-deps . \
    && rm /tmp/requirements.txt

COPY --from=frontend /src/frontend/dist /opt/suncore/frontend/dist
# Built-in templates, read at startup (SUNCORE_TEMPLATES_DIR).
COPY templates /opt/suncore/templates

COPY addon/suncore_beta/rootfs /

LABEL \
    io.hass.name="SunCore (beta)" \
    io.hass.description="Energy management for Home Assistant" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.source="https://github.com/pxljake/suncore" \
    org.opencontainers.image.licenses="Apache-2.0"

# The Supervisor watchdog uses the container health check (the `watchdog`
# config key is obsolete). /health answers 200 unless the database failed,
# authentication failed or the connection is closed; it is open to loopback
# even under Supervisor (suncore/api/security.py). No curl needed.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "-c", "import sys, urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8099/health', timeout=5).status == 200 else 1)"]

# No CMD: the base image's ENTRYPOINT is s6-overlay's /init, which starts the
# service in rootfs/etc/services.d/suncore (run, finish).
