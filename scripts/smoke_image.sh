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
#    above RSS_LIMIT_MIB (300). This is docker stats' cgroup memory usage, which is at
#    least the process RSS, so the check is conservative.
# 4. `docker stop`: the container must exit 0 and log "app.stopped" (SIGTERM
#    reaches python through s6-overlay).
# Results go to the job summary when GITHUB_STEP_SUMMARY is set.
#
# LOADED=1 (WP13, runs on `main` on both architectures) replaces step 3 by a loaded run:
#   - a Mosquitto container (pinned by digest, MOSQUITTO_IMAGE) on a private network;
#   - the demo with SUNCORE_DEMO_FLEX=1 (flexible loads), SUNCORE_DEMO_BATTERY_CONTROL=1 and
#     SUNCORE_DEMO_MQTT=1 (CI only: builds the MQTT entity service in demo mode);
#   - the manual broker configured through the API (`PUT /api/v1/config/mqtt`);
#   - `POST /api/v1/plan/replan` every REPLAN_EVERY_S (60) for LOADED_S (600) seconds, the
#     memory sampled every SAMPLE_S (15) seconds;
#   - the peak and the final memory must stay at or below RSS_LIMIT_MIB (300), the broker
#     connection must still be up and /health must still answer.
# SUNCORE_DEMO_FLEX exists from 7-C on; an image without it ignores the variable.
set -euo pipefail

IMAGE="${1:?usage: smoke_image.sh <image:tag>}"
NAME="suncore-smoke-$$"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-90}"
IDLE_S="${IDLE_S:-60}"
RSS_LIMIT_MIB="${RSS_LIMIT_MIB:-300}"
STOP_TIMEOUT_S="${STOP_TIMEOUT_S:-20}"
LOADED="${LOADED:-0}"
LOADED_S="${LOADED_S:-600}"
REPLAN_EVERY_S="${REPLAN_EVERY_S:-60}"
SAMPLE_S="${SAMPLE_S:-15}"
# eclipse-mosquitto 2.0.23, the multi-arch index (amd64, arm64, ...); looked up on Docker Hub
# 2026-11-25 (`docker-content-digest` of the manifest, identical to the SHA-256 of its body).
MOSQUITTO_IMAGE="${MOSQUITTO_IMAGE:-eclipse-mosquitto:2.0.23@sha256:6a332cc7a8dbe011cbc076180cbb76b37c6f0f55809682ea08e7980ef0112ad9}"
NETWORK="suncore-smoke-net-$$"
MOSQUITTO="suncore-smoke-mosquitto-$$"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

PROBE='import sys, urllib.request as u
try:
    r = u.urlopen("http://127.0.0.1:8099/health", timeout=3)
    print(r.read().decode())
    sys.exit(0 if r.status == 200 else 1)
except Exception as e:
    print(e)
    sys.exit(1)'

WORKDIR=""
cleanup() {
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  if [ "${LOADED}" = "1" ]; then
    docker rm -f "${MOSQUITTO}" >/dev/null 2>&1 || true
    docker network rm "${NETWORK}" >/dev/null 2>&1 || true
  fi
  if [ -n "${WORKDIR}" ]; then rm -rf "${WORKDIR}"; fi
}
trap cleanup EXIT

fail() {
  echo "::error::smoke test failed: $1"
  {
    echo "### Smoke test FAILED (${IMAGE})"
    echo "${1}"
  } >> "${SUMMARY}"
  echo "----- container logs -----"
  docker logs "${NAME}" 2>&1 | tail -n 200 || true
  # The tail again as one annotation: readable through the checks API even
  # when the raw job log is not reachable.
  local tail_lines
  tail_lines="$(docker logs "${NAME}" 2>&1 | tail -n 40 | sed -e 's/%/%25/g' | awk '{printf "%s%%0A", $0}')" || true
  echo "::warning title=smoke container log tail::${tail_lines}"
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

# One API call inside the container (the API is loopback-only without the Supervisor).
# api METHOD PATH [JSON]: prints the body; non-2xx prints "<code> <body>" and returns 1.
api() {
  docker exec "${NAME}" python3 -c 'import sys, urllib.request as u, urllib.error as e
method, path = sys.argv[1], sys.argv[2]
body = sys.argv[3].encode() if len(sys.argv) > 3 else None
req = u.Request("http://127.0.0.1:8099" + path, data=body, method=method,
                headers={"Content-Type": "application/json", "X-SunCore-Request": "1"})
try:
    print(u.urlopen(req, timeout=10).read().decode())
except e.HTTPError as err:
    print(err.code, err.read().decode())
    sys.exit(1)' "$@"
}

if [ "${LOADED}" = "1" ]; then
  echo "Starting ${MOSQUITTO_IMAGE} and ${IMAGE} (loaded demo)"
  WORKDIR="$(mktemp -d)"
  printf 'listener 1883\nallow_anonymous true\n' > "${WORKDIR}/mosquitto.conf"
  chmod 644 "${WORKDIR}/mosquitto.conf"
  docker network create "${NETWORK}" >/dev/null
  docker run -d --name "${MOSQUITTO}" --network "${NETWORK}" \
    -v "${WORKDIR}/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
    "${MOSQUITTO_IMAGE}" >/dev/null
  docker run -d --name "${NAME}" --network "${NETWORK}" \
    -e SUNCORE_DEMO=1 -e SUNCORE_DEMO_FLEX=1 -e SUNCORE_DEMO_BATTERY_CONTROL=1 \
    -e SUNCORE_DEMO_MQTT=1 "${IMAGE}" >/dev/null
else
  echo "Starting ${IMAGE} in demo mode"
  docker run -d --name "${NAME}" -e SUNCORE_DEMO=1 "${IMAGE}" >/dev/null
fi

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

loaded_phase() {
  local body status peak=0 final=0 usage mib next_replan start end now connected=0
  body="{\"broker\": \"manual\", \"host\": \"${MOSQUITTO}\", \"port\": 1883}"
  api PUT /api/v1/config/mqtt "${body}" >/dev/null || fail "PUT /api/v1/config/mqtt was refused"
  for _ in $(seq 1 30); do
    status="$(api GET /api/v1/ha-entities/status || true)"
    if grep -q '"state": *"connected"' <<<"${status}"; then connected=1; break; fi
    sleep 2
  done
  [ "${connected}" = "1" ] || fail "the MQTT entity service did not connect: ${status}"
  echo "MQTT connected: ${status}"

  echo "Loaded phase: ${LOADED_S}s, a replan every ${REPLAN_EVERY_S}s, memory every ${SAMPLE_S}s"
  start="${SECONDS}"
  end=$((start + LOADED_S))
  next_replan="${start}"
  while now="${SECONDS}"; [ "${now}" -lt "${end}" ]; do
    if [ "${now}" -ge "${next_replan}" ]; then
      api POST /api/v1/plan/replan >/dev/null || fail "POST /api/v1/plan/replan was refused"
      next_replan=$((now + REPLAN_EVERY_S))
    fi
    usage="$(docker stats --no-stream --format '{{.MemUsage}}' "${NAME}" | awk '{print $1}')"
    mib="$(to_mib "${usage}")" || fail "cannot parse memory usage '${usage}'"
    echo "  t=$((now - start))s memory ${mib} MiB"
    if awk -v m="${mib}" -v p="${peak}" 'BEGIN { exit !(m > p) }'; then peak="${mib}"; fi
    final="${mib}"
    if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" != "true" ]; then
      fail "the container stopped during the loaded phase (exit code $(docker inspect -f '{{.State.ExitCode}}' "${NAME}"))"
    fi
    sleep "${SAMPLE_S}"
  done
  docker exec "${NAME}" python3 -c "${PROBE}" >/dev/null 2>&1 || fail "/health stopped answering"
  status="$(api GET /api/v1/ha-entities/status || true)"
  grep -q '"state": *"connected"' <<<"${status}" || fail "the MQTT connection was lost: ${status}"
  arch="$(docker inspect -f '{{.Architecture}}' "${IMAGE}")"
  echo "Loaded: peak ${peak} MiB, final ${final} MiB (limit ${RSS_LIMIT_MIB} MiB, arch ${arch})"
  {
    echo "### Loaded smoke test (${IMAGE}, ${arch})"
    echo "- ${LOADED_S} s, replan every ${REPLAN_EVERY_S} s, MQTT on (${MOSQUITTO_IMAGE})"
    echo "- Memory: peak **${peak} MiB**, final **${final} MiB** (limit ${RSS_LIMIT_MIB} MiB)"
  } >> "${SUMMARY}"
  if awk -v m="${peak}" -v l="${RSS_LIMIT_MIB}" 'BEGIN { exit !(m > l) }'; then
    fail "peak memory ${peak} MiB exceeds ${RSS_LIMIT_MIB} MiB"
  fi
}

if [ "${LOADED}" = "1" ]; then
  loaded_phase
else
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
fi

echo "Stopping (SIGTERM, up to ${STOP_TIMEOUT_S}s)"
docker stop -t "${STOP_TIMEOUT_S}" "${NAME}" >/dev/null
code="$(docker inspect -f '{{.State.ExitCode}}' "${NAME}")"
if [ "${code}" != "0" ]; then
  fail "container exit code ${code} after docker stop, expected 0"
fi
# Read the whole log first: `grep -q` exits on the first match, which can
# SIGPIPE `docker logs` and fail the pipeline under `pipefail`.
logs="$(docker logs "${NAME}" 2>&1)"
if ! grep -q "app.stopped" <<<"${logs}"; then
  fail "log line 'app.stopped' missing: SIGTERM did not reach python"
fi
echo "- Clean shutdown: exit code 0, \"app.stopped\" logged" >> "${SUMMARY}"
echo "Smoke test passed"
