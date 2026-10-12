---
name: new-device-template
description: Create a new device, battery, tariff, forecast or region template under templates/ with schema validation, source citation and a simulator scenario. Usable from Phase 2 when the schema exists.
---

# New template

Use when a user or issue asks for support of a device, inverter, tariff
source, forecast source or region.

## Steps

1. Read ADR-0006 and `templates/README.md`. Check `templates/<kind>/` for
   an existing template to extend instead.
2. Collect the facts – never guess:
   - HA integration: entity ids, attributes, units, services from the
     integration's source (`home-assistant/core` or the HACS repository)
     or the issue's Developer-Tools dump.
   - Vendor protocol: official document title, revision, page; registers
     with type, scaling, sign, access, fail-safe behaviour.
3. Write `templates/<kind>/<vendor>.<model>.yaml` with `id`, `kind`,
   `schema_version`, `name` (translatable key), `source` (document or
   URL + date), `tested_with`, `capabilities`, mapping and defaults. Mark
   anything unverified with `unverified: true` and a comment.
4. Validate: `uv run python scripts/validate_templates.py` (schema +
   lint).
5. Add a simulator scenario `tests/scenarios/<kind>/<id>.yaml` and the
   conformance test case; run `uv run pytest -m simulator -k <id>`.
6. Add the English and German name keys; run the `sync-translations`
   skill.
7. Regenerate the compatibility list: `uv run python scripts/gen_docs.py`.
8. If the template has a write path, request a `control-safety` review and
   put its result in the PR.

## Output

Template file, scenario, test, docs regeneration diff, PR text in German
with the sources listed.
