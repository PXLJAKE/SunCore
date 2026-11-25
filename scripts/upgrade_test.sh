#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Upgrade test 0.2.0 -> candidate (WP13, release gate of plan phase 7, decision 10).
#
#   scripts/upgrade_test.sh <candidate-image:tag> [<old-image>]
#
# 1. Start the released image on a fresh Docker volume as /data (no Home Assistant: the app
#    comes up and keeps retrying the connection), seed it ONLY through the 0.2.0 API
#    (scripts/upgrade_probe.py seed: PUT /api/v1/config/site, export, info, decisions), stop it.
# 2. Start the candidate on the same volume and verify (upgrade_probe.py verify): /health
#    answers with db_ok, the mode is still shadow, the site, the export and the decision log
#    carry over. Stop it: exit code 0 and "app.stopped".
# 3. Start the released image again on the upgraded volume: it must refuse the newer
#    database (exit code != 0, "newer than the supported"): downgrades are not supported.
#
# The old image defaults to the 0.2.0 stable image on GHCR, pinned by the digest of its
# multi-arch index (tag 0.2.0, looked up 2026-11-25; amd64 and arm64 inside), so a moved tag
# cannot change the test. 0.2.0 never writes decisions (nothing submits to its gate), so the
# decision log is empty in this run; rows are covered by the frozen dump test
# (tests/storage/test_release_upgrade.py).
set -euo pipefail

IMAGE="${1:?usage: upgrade_test.sh <candidate-image:tag> [<old-image>]}"
OLD_IMAGE="${2:-${OLD_IMAGE:-ghcr.io/pxljake/suncore@sha256:05641188949aa90c083f6de0f4de0592d1c6c7ba6fcf0f56d3048dfecdec3b32}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOP_TIMEOUT_S="${STOP_TIMEOUT_S:-20}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"
SUFFIX="$$"
VOLUME="suncore-upgrade-data-${SUFFIX}"
NAME="suncore-upgrade-${SUFFIX}"
WORKDIR="$(mktemp -d)"
# No Home Assistant in CI: a direct transport to a closed port keeps the app non-supervised
# (loopback API) and lets it retry the connection, exactly like an HA that is still starting.
HA_ENV=(-e SUNCORE_HA_URL=http://127.0.0.1:9 -e SUNCORE_HA_TOKEN=ci-unused)

cleanup() {
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  docker volume rm -f "${VOLUME}" >/dev/null 2>&1 || true
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

fail() {
  echo "::error::upgrade test failed: $1"
  { echo "### Upgrade test FAILED (${OLD_IMAGE} -> ${IMAGE})"; echo "${1}"; } >> "${SUMMARY}"
  echo "----- container logs -----"
  docker logs "${NAME}" 2>&1 | tail -n 200 || true
  exit 1
}

# start <image>: the image on the volume, probe directory mounted read-only.
start() {
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  docker run -d --name "${NAME}" -v "${VOLUME}:/data" -v "${WORKDIR}:/probe:ro" \
    "${HA_ENV[@]}" "$1" >/dev/null
}

probe() { docker exec "${NAME}" python3 /probe/upgrade_probe.py "$@"; }

# stop_clean: SIGTERM, exit code 0 and the shutdown log line.
stop_clean() {
  docker stop -t "${STOP_TIMEOUT_S}" "${NAME}" >/dev/null
  local code logs
  code="$(docker inspect -f '{{.State.ExitCode}}' "${NAME}")"
  [ "${code}" = "0" ] || fail "$1: exit code ${code} after docker stop, expected 0"
  logs="$(docker logs "${NAME}" 2>&1)"
  grep -q "app.stopped" <<<"${logs}" || fail "$1: log line 'app.stopped' missing"
}

cp "${HERE}/upgrade_probe.py" "${WORKDIR}/upgrade_probe.py"
docker volume create "${VOLUME}" >/dev/null

echo "== 1. released app: ${OLD_IMAGE}"
docker pull "${OLD_IMAGE}" >/dev/null
start "${OLD_IMAGE}"
probe wait --timeout 90 >/dev/null || fail "the released app did not come up"
probe seed > "${WORKDIR}/seed.json" || fail "seeding through the 0.2.0 API failed"
cat "${WORKDIR}/seed.json"
stop_clean "released app"

echo "== 2. candidate on the same volume: ${IMAGE}"
start "${IMAGE}"
verified="$(probe verify --seed /probe/seed.json)" || fail "verification: ${verified:-see the log}"
echo "${verified}"
stop_clean "candidate"

echo "== 3. the released app must refuse the upgraded database"
start "${OLD_IMAGE}"
deadline=$((SECONDS + 60))
while [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" = "true" ]; do
  [ "${SECONDS}" -lt "${deadline}" ] || fail "the released app still runs on a newer database"
  sleep 2
done
code="$(docker inspect -f '{{.State.ExitCode}}' "${NAME}")"
logs="$(docker logs "${NAME}" 2>&1)"
[ "${code}" != "0" ] || fail "the released app exited 0 on a newer database"
grep -q "newer than the supported" <<<"${logs}" || fail "the refusal message is missing"

{
  echo "### Upgrade test (${OLD_IMAGE} -> ${IMAGE})"
  echo "- seeded through the 0.2.0 API, candidate on the same volume: site, export, decision log, mode (shadow) verified"
  echo "- candidate stopped cleanly; the released image refuses the upgraded database (exit ${code})"
} >> "${SUMMARY}"
echo "Upgrade test passed"
