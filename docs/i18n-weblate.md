# Weblate: component configuration

Configuration of the Hosted Weblate project for SunCore (ADR-0020, Phase 7 WP12). The maintainer
creates the project; this page is what to enter and why. The steps in German are in
[`docs/plans/phase-7.md`](plans/phase-7.md) section 10, question 24.

Verified against the Weblate source (`WeblateOrg/weblate`, branch `main`, read 2026-11-25):
`weblate/formats/ttkit.py` (format ids), `weblate/trans/file_format_params.py` (format
parameters), `weblate/checks/icu.py` (ICU check), `docs/admin/projects.rst` (component fields) and
`docs/admin/code-hosting.rst` (GitHub pull requests). Items marked `UNVERIFIED` could not be
checked from source and must be tried once on the live project.

## What is translated where

| Component | Files | Format | Source file |
|---|---|---|---|
| `frontend` | `frontend/src/lib/i18n/locales/*.json` | `JSON file` | `en.json` |
| `frontend-params` | `frontend/src/lib/i18n/locales/params.*.json` | `JSON file` | `params.en.json` |
| `app-options` | `addon/suncore/translations/*.yaml` | `YAML file` | `en.yaml` |

Not in Weblate: the backend catalogue `suncore/i18n/catalogue/*.json` (generated from the
frontend catalogues by `scripts/gen_backend_catalogue.py`, ADR-0016), the beta app's
`addon/suncore_beta/translations/*.yaml` (copied from the stable app by
`scripts/sync_beta_translations.py`) and the user documentation (pull requests, ADR-0020).

## Component `frontend`

| Weblate field | Value |
|---|---|
| File format | `JSON file` (id `json`) |
| File mask | `frontend/src/lib/i18n/locales/*.json` |
| Monolingual base language file | `frontend/src/lib/i18n/locales/en.json` |
| Template for new translations | empty |
| Translation flags | `icu-message-format` |
| Adding new translation | Contact maintainers |
| File format parameters | `json_indent` = 2; sort keys: do not sort |
| Language filter | default (`^[^.]+$`) |
| Version control system | GitHub (pull requests), see below |
| Push branch | `weblate` |

Why these values:

- **`JSON file`, not `JSON nested structure file`.** The catalogues are flat: the key
  `plan.reason.grid_charge_cheaper` is one string key, not a nested object. The Weblate docs say
  both formats read and write the same files and keep the existing structure; they differ only
  when a new string is added, where the nested variant would insert `plan` -> `reason` -> ...
  into the file. A flat file must therefore use `json` (id checked in `ttkit.py`: `json` and
  `json-nested`).
- **`icu-message-format`.** The flag name is the dashed id of the check `icu_message_format`
  (`weblate/checks/icu.py`, `BaseFormatCheck` derives `enable_string` from `check_id`; added in
  Weblate 4.9). It checks syntax and placeholder agreement between source and translation. The
  companion check `icu_message_format_syntax` (source strings, off by default) uses the same
  flag. Set the flag on the component so it applies to every string. The built-in `formatjs`
  format sets the same flag by default, but it expects the Format.JS file layout, which is not
  ours.
- **`json_indent` = 2.** Weblate's default indentation is 4 spaces
  (`JSONOutputIndentation.default = 4`); the repository files use 2 and Prettier would
  otherwise rewrite them. Key sorting defaults to `none`, which keeps the file order (the
  catalogues are not sorted). The files contain non-ASCII text (German umlauts). Whether Weblate
  writes them unescaped is `UNVERIFIED`; if it writes `\uXXXX`, the first pull request shows it.
- **Language filter.** The default `^[^.]+$` skips the file `params.en.json`: its "language
  code" would be `params.en`, which has a dot. That is what keeps the parameter catalogue out of
  this component.
- **Adding new translation: contact maintainers.** A new panel language also needs code (the
  language list of the frontend and `suncore.i18n.LANGUAGES`) and a catalogue file with every
  key. Weblate must not create a file the app cannot load.
- **GitHub pull requests with a push branch.** With the `GitHub` version control backend and
  `Push branch` set, Weblate pushes to the upstream repository on that branch and opens a pull
  request (`docs/admin/code-hosting.rst`). Without `Push branch` it forks the repository. The
  workflow `weblate-sync.yml` expects the head branch to be `weblate` in this repository.

## Component `frontend-params`

Same as `frontend`, with:

| Weblate field | Value |
|---|---|
| File mask | `frontend/src/lib/i18n/locales/params.*.json` |
| Monolingual base language file | `frontend/src/lib/i18n/locales/params.en.json` |

The parameter catalogue (names, help and groups of the parameter registry, about 70 keys) is a
second pair of files that the panel loads on demand. Create it as a component that reuses the
repository of `frontend`: in the field "Source code repository" enter
`weblate://<project>/frontend` (the form `weblate://project/component` is in
`docs/admin/projects.rst`), so that both components share one clone and one push branch.

## Component `app-options`

| Weblate field | Value |
|---|---|
| File format | `YAML file` (id `yaml`) |
| File mask | `addon/suncore/translations/*.yaml` |
| Monolingual base language file | `addon/suncore/translations/en.yaml` |
| Template for new translations | empty |
| Translation flags | none (plain text, no ICU) |
| Adding new translation | Contact maintainers |
| File format parameters | `yaml_indent` = 2; `yaml_line_wrap` = no line wrapping |
| Language filter | default |
| Push branch | `weblate` |

Why these values:

- **`YAML file`, not `Ruby YAML file`.** Home Assistant's layout is
  `configuration: <option>: name / description` with no language code at the top. Weblate's
  `ruby-yaml` format (id checked in `ttkit.py`) expects the language code as the root key
  (`en:`). The plain `yaml` format has nested keys, so the contexts become
  `configuration->log_level->name`. That this round trip keeps Home Assistant's layout byte for
  byte is `UNVERIFIED`: the first pull request must pass the `App lint` workflow
  (`frenck/action-app-linter`) and `tests/test_app_packaging.py`.
- **No line wrapping.** The long `description` values are single lines in the repository;
  Weblate's default wraps at 80 characters (`yaml_line_wrap` default 80, choice 65535 = "No line
  wrapping").
- **Nothing for the beta app.** `scripts/sync_beta_translations.py` copies the stable files to
  `addon/suncore_beta/translations/` as long as both apps have the same options (CI checks it;
  `weblate-sync.yml` runs it). If the beta app gets options the stable app lacks, the script
  fails with a message; then add a second component `app-options-beta`.

## What the repository does with a Weblate pull request

The workflow `.github/workflows/weblate-sync.yml` runs on pull requests whose head branch is
`weblate` in this repository (not on forks):

1. `scripts/gen_backend_catalogue.py` regenerates `suncore/i18n/catalogue/<lang>.json` from the
   frontend catalogues. A message outside the backend renderer's ICU subset (literal text,
   `{arg}`, `number` with a few skeletons, `plural`, `select`; see
   `suncore/i18n/renderer.py`) fails the run with the list of `language:key: reason` lines.
   The translator fixes the message in Weblate; the message in `en.json` is the usual cause when
   several languages fail.
2. `scripts/sync_beta_translations.py` regenerates the beta app's translations.
3. If files changed, the workflow commits them to the pull request branch as
   `github-actions[bot]`.
4. `scripts/check_reasons.py` checks that every backend key exists in `en.json` and `de.json`
   (a translator deleting or renaming a key makes it fail).

A push made with the default `GITHUB_TOKEN` does not start other workflows, so the checks of
`CI` do not run on the regenerated commit. To have them run, store a token as the repository
secret `WEBLATE_SYNC_TOKEN` (fine-grained, contents write for this repository only); the workflow
uses it when it exists. Without it, close and reopen the pull request after the sync commit, or
merge only after a local `./scripts/check.sh`.

## Order of work for the maintainer

The German step list is in [`docs/plans/phase-7.md`](plans/phase-7.md), section 10, question 24;
the same list is on the German page "SunCore übersetzen".
