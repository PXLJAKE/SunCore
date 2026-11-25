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

A Hosted Weblate project is planned. Its address and the date are not published yet. The
Weblate project is not set up, so for now use a pull request. This page will name the project
once it exists.

## By pull request

1. Fork the repository and read
   [CONTRIBUTING.md](https://github.com/pxljake/suncore/blob/main/CONTRIBUTING.md).
2. **Panel texts:** add every key to `en.json` (the source) and to your language file in the same
   change. Messages use ICU MessageFormat: keep placeholders such as `{count}` and plural
   structures unchanged.
3. Run `uv run python scripts/check_reasons.py`. It checks that the backend keys exist in both
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
