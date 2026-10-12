#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test of a SunCore app image (WP9, plan risks R3/R4).
#
#   scripts/smoke_image.sh <image:tag>
#
# 1. Start the container in demo mode (SUNCORE_DEMO=1, no Supervisor token).
# 2. Wait up to HEALTH_TIMEOUT_S (90) for GET /health = 200. The API is
#    loopback-only without Supervisor, so the probe runs inside the container.
# 3. Idle IDLE_S (60), then read the memory usage (`docker stats`) and fail
#    above RSS_LIMIT_MIB (300).
# 4. `docker stop`: the container must exit 0 and log "app.stopped" (SIGTERM
#    reaches python through s6-overlay).
# Results go to the job summary when GITHUB_STEP_SUMMARY is set.
set -euo pipefail

IMAGE="${1:?usage: smoke_image.sh <image:tag>}"
NAME="suncore-smoke-$$"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-90}"
IDLE_S="${IDLE_S:-60}"
RSS_LIMIT_MIB="${RSS_LIMIT_MIB:-300}"
STOP_TIMEOUT_S="${STOP_TIMEOUT_S:-20}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

PROBE='import sys, urllib.request as u
try:
    r = u.urlopen("http://127.0.0.1:8099/health", timeout=3)
    print(r.read().decode())
    sys.exit(0 if r.status == 200 else 1)
except Exception as e:
    print(e)
    sys.exit(1)'

cleanup() { docker rm -f "${NAME}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

fail() {
  echo "::error::smoke test failed: $1"
  {
    echo "### Smoke test FAILED (${IMAGE})"
    echo "${1}"
  } >> "${SUMMARY}"
  echo "----- container logs -----"
  docker logs "${NAME}" 2>&1 | tail -n 200 || true
  exit 1
}

# Convert a docker stats value such as "45.2MiB" to MiB.
to_mib() {
  awk -v v="$1" 'BEGIN {
    match(v, /[A-Za-z]+$/); unit = substr(v, RSTART); num = substr(v, 1, RSTART - 1) + 0;
    if (unit == "B") f = 1 / 1048576; else if (unit == "kB") f = 1000 / 1048576;
    else if (unit == "KiB") f = 1 / 1024; else if (unit == "MB") f = 1000000 / 1048576;
    else if (unit == "MiB") f = 1; else if (unit == "GB") f = 1000000000 / 1048576;
    else if (unit == "GiB") f = 1024; else f = -1;
    if (f < 0) exit 1; printf "%.1f", num * f }'
}

echo "Starting ${IMAGE} in demo mode"
docker run -d --name "${NAME}" -e SUNCORE_DEMO=1 "${IMAGE}" >/dev/null

echo "Waiting up to ${HEALTH_TIMEOUT_S}s for /health"
deadline=$((SECONDS + HEALTH_TIMEOUT_S))
body=""
until body="$(docker exec "${NAME}" python3 -c "${PROBE}" 2>&1)"; do
  if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" != "true" ]; then
    fail "container exited before /health answered (exit code $(docker inspect -f '{{.State.ExitCode}}' "${NAME}"))"
  fi
  if [ "${SECONDS}" -ge "${deadline}" ]; then
    fail "/health did not return 200 within ${HEALTH_TIMEOUT_S}s (last answer: ${body})"
  fi
  sleep 2
done
echo "/health = 200 after ~$((SECONDS - deadline + HEALTH_TIMEOUT_S))s: ${body}"

echo "Idling ${IDLE_S}s before the memory reading"
sleep "${IDLE_S}"
if ! docker exec "${NAME}" python3 -c "${PROBE}" >/dev/null 2>&1; then
  fail "/health stopped answering during the idle period"
fi

usage="$(docker stats --no-stream --format '{{.MemUsage}}' "${NAME}" | awk '{print $1}')"
mib="$(to_mib "${usage}")" || fail "cannot parse memory usage '${usage}'"
arch="$(docker inspect -f '{{.Architecture}}' "${IMAGE}")"
echo "Memory after ${IDLE_S}s idle: ${usage} (${mib} MiB, limit ${RSS_LIMIT_MIB} MiB, arch ${arch})"
{
  echo "### Smoke test (${IMAGE}, ${arch})"
  echo "- /health: 200"
  echo "- Memory after ${IDLE_S} s idle: **${mib} MiB** (limit ${RSS_LIMIT_MIB} MiB)"
} >> "${SUMMARY}"
if awk -v m="${mib}" -v l="${RSS_LIMIT_MIB}" 'BEGIN { exit !(m > l) }'; then
  fail "memory ${mib} MiB exceeds ${RSS_LIMIT_MIB} MiB"
fi

echo "Stopping (SIGTERM, up to ${STOP_TIMEOUT_S}s)"
docker stop -t "${STOP_TIMEOUT_S}" "${NAME}" >/dev/null
code="$(docker inspect -f '{{.State.ExitCode}}' "${NAME}")"
if [ "${code}" != "0" ]; then
  fail "container exit code ${code} after docker stop, expected 0"
fi
if ! docker logs "${NAME}" 2>&1 | grep -q "app.stopped"; then
  fail "log line 'app.stopped' missing: SIGTERM did not reach python"
fi
echo "- Clean shutdown: exit code 0, \"app.stopped\" logged" >> "${SUMMARY}"
echo "Smoke test passed"
