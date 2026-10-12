---
name: sync-translations
description: Synchronise translation keys between code and the en/de catalogues (frontend, backend, app options) and report gaps. Run before every PR that adds or changes user-visible strings. Usable from Phase 1.
---

# Sync translations

## Steps

1. Extract keys from code:
   `uv run python scripts/i18n_extract.py --report` (Phase 1 adds the
   script; it scans `frontend/src/**/*.{svelte,ts}` for `t("…")` and
   `suncore/**/*.py` for `Reason("…")` / `msg("…")`).
2. Compare with `frontend/src/locales/en.json`, `suncore/locales/en.json`
   and `addon/*/translations/en.yaml`:
   - keys used but missing in `en` → blocker, add them with the author;
   - keys in `en` but missing in `de` → translate (du-form, ICU
     placeholders unchanged, no units or numbers in strings);
   - keys in catalogues but unused → list for removal.
3. Keep the same key order in all languages; keep files valid; run the
   lint rule against hardcoded strings
   (`npm run lint` and `uv run ruff check` with the project plugin).
4. For app options: `addon/suncore/translations/{en,de}.yaml` must cover
   every key in `schema:`; mirror to `addon/suncore_beta/`.
5. Output a table: catalogue, keys, missing en, missing de, unused.

Delegate the mechanical part to the `i18n-sync` agent.
