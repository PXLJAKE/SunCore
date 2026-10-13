# Contribute a device template

A template teaches SunCore a new device, tariff, forecast source or region
without changing code. It is a plain YAML file that describes **roles** (what
the device offers, for example "the switch" or "the energy counter"), how to
recognise the matching entities, and sensible defaults. The setup wizard then
ranks your entities against the roles and fills in the form.

This guide covers device templates in detail; tariff, forecast and region
templates follow the same header. The authoritative reference is
[`templates/README.md`](../../../templates/README.md) with the schema
compatibility policy; the design is in
[ADR-0006](../../adr/0006-template-and-plugin-system.md) and
[ADR-0012](../../adr/0012-template-schema-v1.md). Templates that exist
already are in the generated [compatibility list](../compatibility.md).

Please be honest about what you verified. A template that says "not tested"
is welcome; a template that pretends is not.

## What you need

- A running Home Assistant with the device, or its integration source code, or
  a Developer tools → States dump of its entities (attributes included).
- A checkout of the repository and `uv sync --all-groups` (see
  [CONTRIBUTING.md](../../../CONTRIBUTING.md)).
- Never invent entity ids, attributes, services or registers. Everything in
  the template must come from the integration source, a vendor document or
  your own device.

## File and location

`templates/<kind>/<vendor>.<model>.yaml`, with `<kind>` one of `device`,
`tariff`, `forecast`, `region`. The file name is the `id` plus `.yaml`; the
id is lowercase `<vendor>.<model>` (regions use the country code, for
example `de`). Start the file with the schema header so your editor
validates while you type:

```yaml
# yaml-language-server: $schema=../schema/device.schema.json
```

The JSON Schemas in `templates/schema/` are generated; never edit them.

## Structure of a device template

```yaml
id: vendor.model
kind: device
schema_version: 1
name: template.vendor.model.name        # i18n key, always template.<id>.name
source:                                  # required, at least one entry
  - title: "home-assistant/core vendor climate.py (what you read)"
    url: "https://github.com/home-assistant/core/blob/<commit>/..."
    date: 2026-10-13
tested_with: []                          # or [{ integration: vendor, version: "2026.10" }]
capabilities: [on_off, power_measured]
unverified:
  - "What you could not check."
device_kind: switch                      # climate, switch, number_load, ...
roles: { ... }
defaults: { ... }
```

Plain YAML only: **no anchors or aliases** (`&a`, `*a`, `<<`). The loader
rejects them, because a few bytes of aliases can expand into millions of
nodes. Repeat yourself instead.

### Roles and matchers

A role never contains an entity id. It contains a matcher; **all** given
criteria must hold, and `domain` is mandatory:

| Criterion | Meaning |
|---|---|
| `domain` | Entity domain, for example `switch`, `sensor`, `climate` |
| `platforms` | Integrations (list); the registry's platform of the entity |
| `device_class` | Device class of the entity, for example `power`, `energy` |
| `units` or `unit_pattern` | Exact units (list) or a regular expression, not both |
| `translation_key` or `translation_key_pattern` | Entity kind assigned by the integration, exact or regular expression, not both |
| `state_class` | For example `measurement` or `total_increasing` |

Regular expressions are anchored (`re.fullmatch`). Prefer
`translation_key` and `translation_key_pattern` over names: entity ids and
names change with firmware, language and nickname, translation keys do not.
Use `translation_key_pattern` when an integration has variants, for example
`'^energy_usage_today(_for_location)?$'`.

A role can set `required: false`, a `description` (an i18n key, see below)
and `same_device_as: <other role>`. With it, a candidate only matches when it
belongs to the same registry device as the entity chosen for the other role
(one level only; the other role must not use `same_device_as` itself). This
is how "the power sensor of **this** air conditioner" is found.

```yaml
roles:
  switch:
    matcher:
      domain: switch
    description: template.vendor.model.role.switch
  power:
    required: false
    same_device_as: switch
    matcher:
      domain: sensor
      device_class: power
      units: [W, kW]
    description: template.vendor.model.role.power
```

### Capabilities

`capabilities` is a closed list: `power_measured`, `energy_counter`,
`on_off`, `power_setpoint`, `temperature_setpoint`, `hvac_modes`,
`preset_modes`, `fan_modes`, `price_now`, `price_curve`, `pv_curve`,
`pv_quantiles`, `feed_in_tiers`, `negative_price_rule`. Where a validator can
check a claim (for example `on_off` needs an on/off control), an unfounded
one is an error. New members need a change to the schema; open an issue.

### Defaults and control specs

`defaults` is copied into every new device made from the template:

- `power_model`: how SunCore learns the power: `measured` (needs a power
  role), `counter_derived` (needs an energy role), `learned` or `nominal`
  (the wizard asks for the nominal power in W; do not guess one).
- `control`: how the device is driven, through one role:
  `on_off`, `steps` (levels), `continuous` (a number in `W`, `A` or `%`
  between `min` and `max`), `setpoint` (shift a temperature by at most
  `shift_max_c`; needs `comfort`) or `mode`.
- Protection: `min_run_s`, `min_pause_s`, `max_switches_per_hour`,
  `hysteresis_w`, `hysteresis_delay_s`, `settle_s` (time to wait before
  reading back a written value). Compressors and motors need minimum run and
  pause times; if the numbers are your design choice and not manufacturer
  data, say so under `unverified`.
- `comfort`: only for thermal device kinds.

The mode of operation (shadow, assisted, automatic) is **not** part of a
template. New devices always start in shadow mode, and SunCore does not
switch anything in this version.

### Tariff and forecast curves

Tariff and forecast templates take curves from `series`: either an entity
attribute or the response of a read-only action. Only actions on the
built-in allowlist (`tibber.get_prices`,
`solcast_solar.query_forecast_data`, `weather.get_forecasts`) are accepted;
any other service is refused at load time with
`template.error.action_not_allowed`. A new action needs a change in
`suncore/core/actions.py` with a source-verified entry.

### Sources, `tested_with` and `unverified`

- `source` is mandatory: at least one document or source file with a title,
  URL and the date you read it. Pin GitHub links to a commit.
- `tested_with` lists integration versions you ran the template against on a
  real Home Assistant.
- If `tested_with` is empty, `unverified` must list what you could not
  check. The list is also counted in the
  [compatibility list](../compatibility.md). The wizard shows templates
  without `tested_with` as "Not verified with real hardware".
- Remove an `unverified` entry only when you checked that fact.

## Test it

1. Validate the schema and lint (also checks the i18n keys):

   ```bash
   uv run python scripts/validate_templates.py
   ```

2. Add a **simulator conformance scenario**
   `tests/scenarios/<kind>/<id>.yaml`. It states what the demo
   installation must resolve to, with the expected entity, unit and matched
   criteria per role:

   ```yaml
   template: generic.switch
   sim_scenario: summer_day
   user_input:
     nominal_w: 750
   roles:
     switch:
       entity: switch.sim_pool_pump
       criteria: [domain]
   ```

   If the demo has no suitable entity, extend the simulator's registry in
   the same PR. Run it with `uv run pytest -m simulator -k <id>`.

3. Regenerate the derived files and run the full check:

   ```bash
   uv run python scripts/gen_compat.py
   ./scripts/check.sh
   ```

## Translations (i18n keys)

Every key a template uses (`name`, the role `description`s) must exist in
both catalogues, `frontend/src/lib/i18n/locales/en.json` and `de.json`:

```json
"template.vendor.model.name": "Vendor model",
"template.vendor.model.role.switch": "The switch to turn on and off"
```

`validate_templates.py` fails when a key is missing in one of them.

## Try it without a pull request: user templates

Templates in the app's `/data/templates/<kind>/` folder override the
built-in one with the same `id` or add new ones. They are loaded on start; a
broken user template is skipped with a warning in the app log and never
affects the built-in ones. You need a way to place a file in the app's data
folder; which Home Assistant tool offers that is not verified here. For
sharing, a pull request is better.

## Pull request checklist

- [ ] Template in `templates/<kind>/<id>.yaml` with the schema header, no
      anchors or aliases, no entity ids.
- [ ] `source` entries with title, URL and date; GitHub links pinned to a
      commit.
- [ ] `tested_with` filled in, or `unverified` lists everything you did not
      check.
- [ ] i18n keys in `en.json` and `de.json`.
- [ ] Conformance scenario in `tests/scenarios/<kind>/<id>.yaml`; the
      simulator test passes.
- [ ] `uv run python scripts/validate_templates.py` and
      `uv run python scripts/gen_compat.py` run; the regenerated
      `docs/user/compatibility.md` is part of the change.
- [ ] `./scripts/check.sh` is green.
- [ ] If the template writes to a device, say so in the PR; control paths get
      a safety review.
- [ ] The PR text lists what you verified and how.
