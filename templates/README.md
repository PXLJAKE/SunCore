# Templates

Declarative device, tariff, forecast and region templates (ADR-0006, ADR-0012, schema v1).
Layout: `templates/<kind>/<id>.yaml` with `kind` in `device`, `tariff`, `forecast`,
`region`; user templates in `/data/templates/<kind>/` override built-in ones by `id`.
A `*.yaml` file in any other sub directory (for example `battery/` before that kind exists)
is reported as an error. The JSON Schemas in `schema/` are generated from the Pydantic
models in `suncore/templates/models.py` and `suncore/core/devices.py` – never edit them by
hand (`uv run python scripts/gen_schemas.py`; CI fails on drift).

Step-by-step guide for contributors: [Contribute a device
template](../docs/user/en/device-templates.md); generated list of what exists:
[compatibility list](../docs/user/compatibility.md) (`scripts/gen_compat.py`, CI fails on
drift).

## Built-in templates

<!-- Keep in sync with the files below; `validate_templates.py` lints the files, not this list. -->

| Kind | Id | What | Status |
|---|---|---|---|
| device | `lg.thinq_climate` | LG ThinQ air conditioner, power from the hourly Wh counter or an optional power sensor of another device | from integration source, `UNVERIFIED` on hardware |
| device | `generic.switch` | any switch, optional power sensor | generic |
| device | `generic.climate` | any `climate` entity, power measured (sensor may be on another device) or nominal | generic |
| device | `generic.number_load` | load set through a number entity (W, A or %) | generic |
| tariff | `tibber.price` | Tibber dynamic price, curve from `tibber.get_prices` | from integration source |
| forecast | `solcast.pv` | Solcast PV Forecast (HACS), `detailedForecast` P10/P50/P90 | from integration source |
| region | `de` | Germany: EUR, `Europe/Berlin`, no remuneration at negative prices for new PV | law text `UNVERIFIED` here |
| region | `generic` | neutral international profile | – |

## Writing a template

Start the file with the schema header so editors validate while you type:

```yaml
# yaml-language-server: $schema=../schema/device.schema.json
id: vendor.model          # lowercase <vendor>.<model>; region ids may be e.g. `de`
kind: device
schema_version: 1
min_app_version: "0.3.0"  # optional: needs at least this SunCore version
name: template.vendor.model.name   # i18n key, add it to en.json and de.json
source:                            # required: documentation the template is based on
  - { title: "Integration source", url: "https://...", date: 2026-10-13 }
tested_with:
  - { integration: lg_thinq, version: "2026.10" }
capabilities: [energy_counter]     # closed vocabulary, see below
unverified: []                     # facts you could not verify; required if tested_with is empty
```

- Plain YAML only: no anchors or aliases (`&a` / `*a`); the loader rejects them (alias
  expansion could turn a few bytes into millions of nodes).
- Never write entity ids. Describe **roles** with a matcher; the wizard ranks the user's
  entities against it. Matcher criteria (all given ones must hold): `domain` (mandatory),
  `platforms` (list), `device_class`, `units` *or* `unit_pattern`, `translation_key` *or*
  `translation_key_pattern` (regular expressions are anchored, `re.fullmatch`),
  `state_class`. A role can demand `same_device_as: <other role>`: the candidate must
  belong to the same registry device as the entity chosen for that role (one level only).
- `capabilities` is a closed list: `power_measured`, `energy_counter`, `on_off`,
  `power_setpoint`, `temperature_setpoint`, `hvac_modes`, `preset_modes`, `fan_modes`,
  `price_now`, `price_curve`, `pv_curve`, `pv_quantiles`, `feed_in_tiers`,
  `negative_price_rule`. Where a validator can check the claim (for example
  `pv_quantiles` needs `p10_field` and `p90_field`) an unfounded one is an error.
- Devices write through one role named by `defaults.control` (`on_off`, `steps`,
  `continuous` in W/A/%, `setpoint` with a maximum shift, `mode`). Mode of operation
  (shadow/assisted/automatic) is **not** part of a template or a device; the `operation`
  config section is its only store.
- Tariff and forecast curves come from `series`: an entity attribute
  (`source: attribute`, lists of the given roles are concatenated) or the response of a
  read-only action (`source: action`). Only actions on the built-in allowlist
  (`suncore/core/actions.py`, currently `tibber.get_prices`,
  `solcast_solar.query_forecast_data`, `weather.get_forecasts`) are accepted; any other
  service is refused at load time with the key `template.error.action_not_allowed`.
  Power units in a forecast mean the average power over the slot, energy units the energy
  per slot.
- Do not invent attributes, services or registers. Cite the integration source or vendor
  document in `source`, otherwise list the fact under `unverified`.
- Every template needs a simulator conformance scenario under
  `tests/scenarios/<kind>/<id>.yaml` (`uv run pytest -m simulator`).
- Check locally: `uv run python scripts/validate_templates.py`.

## Compatibility policy for `schema_version: 1`

The Pydantic models of templates keep `extra="forbid"` and the generated JSON Schemas keep
`additionalProperties: false`: a typo must fail.

Allowed **within v1** (no new version needed):

- adding an optional field with a default;
- adding a member to an enum or to a discriminated union (`kind`, `source`,
  `control.kind`);
- adding a new template kind (its own `<kind>.schema.json`; `schema_version` is per kind);
- relaxing a validation.

**Not allowed** within v1 (these need `schema_version: 2` and a loader migration):
removing or renaming a field, changing a type, narrowing a range or an enum, changing the
meaning of a value.

Consequences for contributors:

- A v1 template written for an older SunCore always loads on a newer one.
- A template that uses a newer optional field or enum member fails on an older SunCore
  with a clear per-field error; the header field `min_app_version` (PEP 440 string, for
  example `"0.4.0"`) lets the loader say "needs SunCore >= 0.4.0" first. User templates
  with a problem are skipped, built-in ones cannot be affected.
- The instance model `DeviceConfig` is a config-section document; its evolution follows
  the document migrations of ADR-0004, not this policy.

Every addition is recorded below with the SunCore version that introduced it.

### Schema v1 changelog

| SunCore | Change |
|---|---|
| 0.3.0 (unreleased, Phase 2) | Initial schema v1 after the ADR-0012 review: matcher with `platforms`, `translation_key_pattern`, `unit_pattern`, digits in `domain`; `RoleSpec.same_device_as`; `PowerModelDefault` without required `nominal_w`, `counter_update_s`; `defaults.control` as `ControlSpec` (`on_off`, `steps`, `continuous`, `setpoint`, `mode`); `hysteresis_w`, `hysteresis_delay_s`, `settle_s`; `series` (`attribute` or `action`) for tariff and forecast, `price_includes`, optional `resolution_min` for tariffs; closed `capabilities` vocabulary; `min_app_version`; loader lint for unknown kind directories. |
| 0.3.0 (unreleased, Phase 2) | `region.currency` is optional for `country: generic` (`None` = currency of the Home Assistant configuration). |
