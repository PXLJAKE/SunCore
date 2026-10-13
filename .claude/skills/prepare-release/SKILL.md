---
name: prepare-release
description: Prepare and verify a SunCore release – release-please PR check, app config versions, changelog copy, image build and smoke test, GitHub release notes. Usable from the first beta (Phase 1).
---

# Prepare a release

SunCore releases are cut by release-please from Conventional Commits
(ADR-0009). This skill is the human-in-the-loop checklist around it.

## Before merging the release PR

1. `./scripts/check.sh` green on `main`; CI, app lint, docs lint, CodeQL
   green.
2. Read the release PR's CHANGELOG diff: every user-visible change is
   there and understandable; fix commit messages via a follow-up commit if
   a section is misleading.
3. `addon/suncore/config.yaml` `version` is bumped by release-please
   (`x-release-please-version` marker). `addon/suncore_beta/config.yaml`
   is bumped by the `bump-beta` job of `build.yml`, never by hand.
4. Copy the user-facing sections of the new CHANGELOG entry into
   `addon/suncore/CHANGELOG.md` (HA shows this file); keep it short and in
   English.
5. `docs/STATUS.md`: update "Last update", move finished items, note the
   release.
6. Breaking changes: add the version to `breaking_versions` in both app
   configs and write a migration note in `DOCS.md`.

## After the release is published

1. Confirm `build.yml` ran for the `release` event (needs
   `RELEASE_PLEASE_TOKEN`; otherwise start it with `workflow_dispatch`).
2. Check GHCR: manifest `ghcr.io/pxljake/suncore:<version>` lists amd64 and
   aarch64; Cosign signature present.
3. Install the stable app on a test HA instance; `/health` answers; the
   Now view loads; shadow mode active by default.
4. Announce in Discussions with the changelog link.

## Beta channel

Every merge to `main` that touches the app publishes
`ghcr.io/pxljake/suncore-beta:<x.y.z>-beta.<n>`; after the manifest exists
the `bump-beta` job commits that version to `addon/suncore_beta/config.yaml`
(`chore(beta): bump app version … [skip ci]`), so HA offers the update
(ADR-0009 addendum, `scripts/beta_version.py`). After a stable release the
base moves to the next minor automatically (always stable minor + 1).
Never edit `addon/suncore_beta/config.yaml` by hand; to announce a different
next version, change how the base is derived in a PR (e.g. a major bump) –
not the file. Check that the bump commit landed on `main`; if the job failed
(branch protection), fix the push permission and re-run the job.
