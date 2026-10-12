# syntax=docker/dockerfile:1
# Build context is the repository root (see .github/workflows/build.yml).
# Base image: Home Assistant Python base (Alpine, s6-overlay, bashio). ADR-0001.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.24
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

WORKDIR /opt/suncore
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY suncore ./suncore
# Wheels only: a missing aarch64/musl wheel must fail the build, not compile on a Pi.
RUN uv pip install --no-deps --only-binary=:all: . \
    && uv pip install --only-binary=:all: -r pyproject.toml

COPY addon/suncore_beta/rootfs /

LABEL \
    io.hass.name="SunCore (beta)" \
    io.hass.description="Energy management for Home Assistant" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.source="https://github.com/pxljake/suncore" \
    org.opencontainers.image.licenses="Apache-2.0"

# Supervisor watchdog uses the container health check (the `watchdog` config
# key is obsolete). Phase 1 points it at the /health endpoint.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -m suncore --version || exit 1

# Phase 1 replaces this with the s6 service that starts the application.
CMD ["python3", "-m", "suncore", "--version"]
