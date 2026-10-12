# ADR-0009: Release and versioning strategy

- **Status:** proposed
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`ha-apps.md`](../research/ha-apps.md) (build actions, image naming), release-please README

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
