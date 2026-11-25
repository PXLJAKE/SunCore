---
name: sync-translations
description: Synchronise translation keys between code and the en/de catalogues (frontend, backend, app options), handle Weblate pull requests and report gaps. Run before every PR that adds or changes user-visible strings.
---

# Sync translations

Catalogues (ADR-0007, ADR-0016, ADR-0020, `docs/i18n-weblate.md`):

| Catalogue | Path | Edited by |
|---|---|---|
| Panel texts | `frontend/src/lib/i18n/locales/{en,de}.json` | people, Weblate component `frontend` |
| Parameter texts | `frontend/src/lib/i18n/locales/params.{en,de}.json` | people, Weblate `frontend-params` |
| App options | `addon/suncore/translations/{en,de}.yaml` | people, Weblate `app-options` |
| Backend texts | `suncore/i18n/catalogue/{en,de}.json` | **generated**, never by hand |
| Beta app options | `addon/suncore_beta/translations/{en,de}.yaml` | **generated**, never by hand |

## Steps

1. Run the checks (all exist; `scripts/i18n_extract.py` does not):
   - `uv run python scripts/check_reasons.py`: every backend key (`Reason(...)`, `api.error.*`,
     controller and planner key modules, parameter registry) exists in `en.json` and `de.json`;
   - `uv run python scripts/gen_backend_catalogue.py --check`: the generated backend catalogue
     is current and every backend-rendered message is inside the renderer's ICU subset
     (`suncore/i18n/renderer.py`);
   - `uv run python scripts/sync_beta_translations.py --check`: the beta copies are current;
   - `uv run pytest tests/test_app_packaging.py tests/i18n`: options, schema and translations
     agree; frontend key sets and ICU arguments match (`cd frontend && npm test`).
2. Gaps:
   - keys used but missing in `en`: blocker, add them with the author;
   - keys in `en` but missing in `de`: translate (du-form, ICU placeholders and plural
     structure unchanged, no units or numbers in the string);
   - keys in a catalogue but unused: list them, do not delete without confirmation.
3. Keep key order identical across languages (new keys at the same position in `en` and `de`),
   valid JSON/YAML, JSON indent 2 and non-ASCII characters unescaped, YAML descriptions on one
   line. Run `npm run lint` in `frontend/` for the hardcoded-string rule.
4. After editing panel texts run `uv run python scripts/gen_backend_catalogue.py`; after editing
   `addon/suncore/translations/*.yaml` run `uv run python scripts/sync_beta_translations.py`.
5. Output a table: catalogue, keys, missing en, missing de, unused.

## Weblate round trip (once the project exists, WP12)

- Weblate pushes to the branch `weblate` and opens a pull request. The workflow
  `.github/workflows/weblate-sync.yml` regenerates the backend catalogue and the beta
  translations on that branch, commits them and runs `check_reasons.py`.
- Review such a pull request for: only translation files and the two generated locations
  changed; JSON indent and umlauts intact; `de.yaml` layout intact (`App lint` green); no key
  added, removed or renamed (Weblate must not change keys; the source is `en.json`).
- A failure "message outside the backend ICU subset" lists `language:key: reason`. Fix the
  message in Weblate (or in `en.json` when several languages fail); never widen the renderer for
  one translation.
- Source changes (new or changed English text) go through a normal pull request first; Weblate
  picks them up from `main`. Never edit translated strings in the repository while a `weblate`
  pull request is open for the same keys; merge or close it first.
- Settings of the components: `docs/i18n-weblate.md`. Anything there marked `UNVERIFIED` is
  decided by the first real pull request.

Delegate the mechanical part to the `i18n-sync` agent.
