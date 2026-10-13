# Templates

Declarative device, tariff, forecast and region templates (ADR-0006, schema v1).
Layout: `templates/<kind>/<id>.yaml` with `kind` in `device`, `tariff`, `forecast`,
`region`; user templates in `/data/templates/<kind>/` override built-in ones by `id`.
The JSON Schemas in `schema/` are generated from the Pydantic models in
`suncore/devices/templates/models.py` – never edit them by hand
(`uv run python scripts/gen_schemas.py`; CI fails on drift).

## Writing a template

Start the file with the schema header so editors validate while you type:

```yaml
# yaml-language-server: $schema=../schema/device.schema.json
id: vendor.model          # lowercase <vendor>.<model>; region ids may be e.g. `de`
kind: device
schema_version: 1
name: template.vendor.model.name   # i18n key, add it to en.json and de.json
source:                            # required: documentation the template is based on
  - { title: "Integration source", url: "https://...", date: 2026-10-13 }
tested_with:
  - { integration: lg_thinq, version: "2026.10" }
capabilities: [power_counter]
unverified: []                     # facts you could not verify; required if tested_with is empty
```

- Never write entity ids. Describe **roles** with a matcher (`domain`, `platform`,
  `device_class`, `units`, `translation_key`, `state_class`); the wizard ranks the
  user's entities against it.
- Do not invent attributes, services or registers. Cite the integration source or
  vendor document in `source`, otherwise list the fact under `unverified`.
- Every template needs a simulator conformance scenario under
  `tests/scenarios/<kind>/<id>.yaml`.
- Check locally: `uv run python scripts/validate_templates.py`.
