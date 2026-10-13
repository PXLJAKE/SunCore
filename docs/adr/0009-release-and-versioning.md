# ADR-0009: Release and versioning strategy

- **Status:** accepted (maintainer delegated the decision on 2026-10-12)
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:**
  [`ha-apps.md`](../research/ha-apps.md) (build actions,
  image naming),
  release-please README

## Context

Two app channels (stable, beta), prebuilt multi-arch images on GHCR,
SemVer, Conventional Commits, CHANGELOG automation (BRIEF §7). Later
packages (companion integration, Lovelace cards) have their own cycles.

## Options considered

### Option A – release-please (manifest mode) + tag-triggered image build

- Pros: changelog and version bumps from Conventional Commits; manifest
  mode supports several components per repo; well maintained (v4).
- Cons: resources created with `GITHUB_TOKEN` do not trigger other
  workflows, so the image build needs a PAT/GitHub App token or a
  `workflow_run` chain.

### Option B – semantic-release

- Pros: similar features.
- Cons: Node-based config, less natural for a Python repo.

### Option C – manual tags and hand-written CHANGELOG

- Pros: simple.
- Cons: error-prone; conflicts with BRIEF §7.

## Decision

Option A.

- `release-please-action@v4`, manifest mode, components: `app` (path `.`,
  release-type `python`, extra files `addon/suncore/config.yaml` version
  field) now; `card` and `integration` later or in their own repos.
- Versions: SemVer; pre-1.0 minor bumps may break; `main` is always
  releasable.
- **Stable app** `addon/suncore` (`slug: suncore`) pins `version` to the
  latest release and `image: ghcr.io/pxljake/suncore`.
- **Beta app** `addon/suncore_beta` (`slug: suncore_beta`,
  `stage: experimental`) tracks pre-releases `x.y.z-beta.n` built from
  `main` on every merge, image `ghcr.io/pxljake/suncore-beta`.
- Images: built by the official `home-assistant/builder` composite actions
  (per-arch images `ghcr.io/pxljake/{arch}-suncore[-beta]`, manifest
  `ghcr.io/pxljake/suncore[-beta]:<version>` and `latest`), signed with
  Cosign. Pull requests build without pushing and run the smoke test.
- Triggering: the release workflow runs on `release: published`; because
  release-please creates the release, the action uses a fine-grained PAT
  (`RELEASE_PLEASE_TOKEN`, contents + pull-requests write) stored as a
  repository secret – otherwise the build would not start. Alternative with
  no secret: a `workflow_run` trigger on the release-please workflow.
- CHANGELOG: root `CHANGELOG.md` by release-please; `addon/suncore/CHANGELOG.md`
  is a copy filtered to user-facing sections by a release step (HA shows
  this file in the app store).

## Consequences

- Positive: hands-off releases; two channels without two codebases.
- Negative: one secret to manage – documented in `CONTRIBUTING.md`.
- Follow-up: Phase 1 `ha-integration` wires the workflows; first beta
  release at the end of Phase 1.

## Recommendation to the maintainer

Accept; decide between PAT and `workflow_run` (PAT recommended – simpler
and lets CI run on release PRs).

## Addendum 2026-10-13: automatic beta app version

Context: the Supervisor decides that an app update exists with
`version != latest_version` on the store's `config.yaml` (string equality,
no ordering) and pulls `<image>:<version>` with the version rendered as a
string. A beta image that never changes `config.yaml` is therefore never
offered. Verified 2026-10-13 against `home-assistant/supervisor` `main`:
`supervisor/apps/app.py` (`need_update`), `supervisor/docker/interface.py`
(`install`/`update` pull `f"{image}:{version!s}"`), `supervisor/validate.py`
(`version_tag` = `AwesomeVersion(value)`, any string accepted),
`supervisor/store/git.py` (store refresh fetches and hard-resets to
`origin/<tracked branch>`), and `ludeeus/awesomeversion` (`__eq__` compares
the strings).

Decision: `build.yml` derives the version on every push to `main` and commits
it back.

- Scheme `<base>-beta.<n>` (SemVer pre-release, Docker-tag-safe: no `+`).
  `base` is the beta config's `x.y.z`; once stable reaches it (a release),
  it becomes stable minor + 1. Raise it by hand to announce another next
  version. `n = max(github.run_number, previous n + 1)`.
  Logic and tests: `scripts/beta_version.py`, `tests/test_beta_version.py`.
- `prepare` computes the version and uses it as image tag (and as the
  `app_version` tag). After the per-arch builds, smoke tests and the
  manifest succeeded, the `bump-beta` job commits only
  `addon/suncore_beta/config.yaml` to `main` as
  `chore(beta): bump app version to <v> [skip ci]`. HA is thus never offered
  a tag that cannot be pulled.
- No loop: the commit carries `[skip ci]`, the push-trigger path filter
  excludes `addon/suncore_beta/config.yaml`, and a `GITHUB_TOKEN` push does
  not start workflows anyway. Runs on `main` are serialised
  (`concurrency`), so versions are committed in publish order; the script
  refuses to move the version backwards.
- release-please is unaffected: only `addon/suncore/config.yaml` is an extra
  file; `chore` commits are hidden in the changelog and do not create a
  release.
- Stable is unchanged (`release: published`, version from `config.yaml`).
- Rejected: a separate `beta` branch (users would need a branch-qualified
  repository URL, two repository URLs for one project) and `+sha` build
  metadata (not a valid Docker tag character).

Maintainer requirement: pushing to `main` must be allowed for the bump.
If `main` is protected, either allow `github-actions[bot]` to bypass, or
make `RELEASE_PLEASE_TOKEN` (already used by release-please) belong to a user
or app that may bypass; the job prefers that secret and falls back to
`GITHUB_TOKEN`. If neither can push, the job fails after the image is
published (beta users are not offered the update until it is committed by hand
with `python3 scripts/beta_version.py write <version>`); the alternative is a
`beta` branch channel, to be decided then.

