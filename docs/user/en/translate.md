# Translate SunCore

SunCore is written in English and translated into German first. This page says what can be
translated, where the strings live and how to contribute a translation.

## What is translated and how

| What | Where | How you contribute |
|---|---|---|
| Panel texts (the UI) | `frontend/src/lib/i18n/locales/*.json`, source `en.json` | Weblate, once it is set up; until then a pull request |
| Options of the app | `addon/suncore/translations/*.yaml` | Weblate, once it is set up; until then a pull request |
| Texts of the backend (notifications, reasons) | generated from the panel catalogues | nothing to do: translate the panel texts |
| This documentation | `docs/user/<language>/` | a pull request |

The decision is in
[ADR-0020](https://github.com/pxljake/suncore/blob/main/docs/adr/0020-docs-site-and-translation.md):
Weblate is used for the panel and the app options, not for this documentation (for 1.0).

## Weblate

A Hosted Weblate project is planned but **not set up yet**, so for now use a pull request. This
page will name the project once it exists.

How it will work:

- Three components: `frontend` (the panel, `frontend/src/lib/i18n/locales/*.json`, source
  `en.json`), `frontend-params` (the parameter names and help texts, `params.*.json`) and
  `app-options` (the options of the app, `addon/suncore/translations/*.yaml`). The values of every
  setting are in
  [docs/i18n-weblate.md](https://github.com/pxljake/suncore/blob/main/docs/i18n-weblate.md).
- Weblate checks that your translation keeps the placeholders and the ICU plural structure of
  the English text.
- Weblate pushes to the branch `weblate` and opens a pull request. A workflow then regenerates
  the backend texts (`suncore/i18n/catalogue/`) and the beta app's option translations and
  commits them to that pull request. A message that uses ICU syntax the backend does not support
  fails there with the list of keys; fix it in Weblate.
- A new language is added by a maintainer, because the panel needs code for it too. Ask in an
  issue.

### For the maintainer: set up the project

The steps are in German in the plan
([question 24](https://github.com/pxljake/suncore/blob/main/docs/plans/phase-7.md)) and on the
German page. In short: create the project on Hosted Weblate, connect GitHub with
version control `GitHub` and push branch `weblate`, create the three components with the values
from `docs/i18n-weblate.md`, optionally add the secret `WEBLATE_SYNC_TOKEN`, then make a test
change and check the pull request (`Weblate sync`, `CI`, `App lint`). Finally enable the badge in
the README and replace the "not set up yet" sentence above with the project link.

## By pull request

1. Fork the repository and read
   [CONTRIBUTING.md](https://github.com/pxljake/suncore/blob/main/CONTRIBUTING.md).
2. **Panel texts:** add every key to `en.json` (the source) and to your language file in the same
   change. Messages use ICU MessageFormat: keep placeholders such as `{count}` and plural
   structures unchanged.
3. **App options:** edit `addon/suncore/translations/<language>.yaml`, then run
   `uv run python scripts/sync_beta_translations.py` (copies the files to the beta app) and
   `uv run python scripts/gen_backend_catalogue.py` after panel text changes. Run
   `uv run python scripts/check_reasons.py`: it checks that the backend keys exist in both
   catalogues.
4. **Documentation:** the English pages are in `docs/user/en/`. Put your translation at the same
   path under `docs/user/<language>/` and keep the structure identical, so the language switcher
   finds the page. Pages that are missing in your language fall back to English.
5. Build the site to check it: `uv run --group docs mkdocs build --strict`.

## What a translation must keep

- Names of buttons and views as they appear in the panel, in bold, as in the English page.
- `UNVERIFIED` markers and the safety statements: do not soften them.
- Generated pages (the [parameter reference](reference/parameters.md) and the
  [compatibility list](compatibility.md)) are produced by `scripts/gen_params.py` and
  `scripts/gen_compat.py`. Do not edit them by hand; the language of their text lives in the
  scripts and the panel catalogues.

## A new language

A third language for the panel needs a catalogue file with every key of `en.json`. For the
documentation site a language also needs an entry under `plugins`, `i18n`, `languages` in
`mkdocs.yml`. Ask in an issue first, so that the generators and the site can be prepared.
