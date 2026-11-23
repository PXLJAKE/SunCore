# Templates

Declarative device, tariff, forecast and region templates (ADR-0006, ADR-0012, schema v1).
Layout: `templates/<kind>/<id>.yaml` with `kind` in `device`, `tariff`, `forecast`,
`region`, `battery`; user templates in `/data/templates/<kind>/` override built-in ones by
`id`. A `*.yaml` file in any other sub directory is reported as an error.
The JSON Schemas in `schema/` are generated from the Pydantic
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
| device | `lg.thinq_climate` | LG ThinQ air conditioner, power from the hourly Wh counter or an optional power sensor of another device | from integration source; entity ids, modes and units seen live, matcher keys `UNVERIFIED` |
| device | `generic.switch` | any switch, optional power sensor | generic |
| device | `generic.climate` | any `climate` entity, power measured (sensor may be on another device) or nominal | generic |
| device | `generic.number_load` | load set through a number entity (W, A or %) | generic |
| device | `generic.water_heater_steps` | water heater with power levels through one select entity, optional tank probe | generic, simulator only (`UNVERIFIED`: no hardware) |
| device | `generic.water_heater_relays` | water heater with 1-3 heating elements switched by relays | generic, simulator only (`UNVERIFIED`: no hardware) |
| tariff | `tibber.price` | Tibber dynamic price, curve from `tibber.get_prices` | from integration source |
| tariff | `nordpool.price` | Nord Pool day-ahead prices per area, one `nordpool.get_prices_for_date` call per date, per MWh, excl. VAT and fees | from integration source, `config_entry` entered by the user |
| tariff | `entsoe.price`, `entsoe.price_mwh` | ENTSO-E (HACS) `prices` attribute of the average-price sensor, kWh or MWh scale | from integration source; includes VAT and fees only if the user set them up |
| tariff | `epex_spot.total_price`, `epex_spot.market_price` | EPEX Spot (HACS) `data` attribute: all-in total price or bare market price, unit `€/kWh` | from integration source |
| tariff | `octopus_energy.rates` | Octopus Energy (HACS) `rates` attribute of the current-day and next-day event entities, GBP/kWh incl. VAT | from integration source |
| tariff | `generic.price_list` | any sensor with a `prices` attribute `[{start, price}]` (SunCore's own convention) | generic |
| forecast | `solcast.pv` | Solcast PV Forecast (HACS), `detailedForecast` P10/P50/P90 of today, tomorrow, d3, d4; fallback action `query_forecast_data` | from integration source (tag v4.6.1) |
| forecast | `forecast_solar.pv` | Forecast.Solar (core), hourly Wh through `energy/solar_forecast` (only if linked in the Energy dashboard), P50 only | from integration source, `UNVERIFIED` timestamp semantics |
| forecast | `open_meteo_solar_forecast.pv` | Open-Meteo Solar Forecast (HACS), `wh_period_15m` (Wh per 15 min), P50 only | from integration source (tag 0.1.33) |
| forecast | `generic.pv_forecast` | any sensor attribute with points `{start, power_w}` (copy and adapt for others) | SunCore's own contract |
| battery | `generic.battery` | battery limits through number entities (min SoC, charge/discharge caps, grid-charge power), driver `generic` | generic, simulator only (`UNVERIFIED`: no hardware) |
| battery | `kostal.plenticore` | KOSTAL PLENTICORE through the core integration: min SoC only (reserve floor, discharge lock), driver `kostal_plenticore` | from integration source; no write made on hardware yet |
| battery | `kostal.plenticore_g3_modbus` | KOSTAL PLENTICORE G3 §3.5 limits through `modbus.write_register` with the inverter's fallback timer, driver `kostal_modbus` | from the vendor document; simulator only, capped at assisted until the fallback is measured |
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
  `negative_price_rule`, and for batteries `battery_soc`, `min_soc_limit`, `charge_limit`,
  `discharge_limit`, `grid_charge`, `battery_modes`, `hardware_fallback`. Where a validator can
  check the claim (for example `pv_quantiles` needs `p10_field` and `p90_field`) an unfounded
  one is an error.
- Devices write through the role(s) named by `defaults.control` (`on_off`, `steps`,
  `relays` (1–3 switches), `continuous` in W/A/%, `setpoint` with a maximum shift, `mode`). Mode of operation
  (shadow/assisted/automatic) is **not** part of a template or a device; the `operation`
  config section is its only store.
- Tariff and forecast curves come from `series`: an entity attribute
  (`source: attribute`, lists of the given roles are concatenated) or the response of a
  read-only action (`source: action`). Only actions on the built-in allowlist
  (`suncore/core/actions.py`, currently `tibber.get_prices`, `nordpool.get_prices_for_date`,
  `solcast_solar.query_forecast_data`, `weather.get_forecasts`) are accepted; any other
  service is refused at load time with the key `template.error.action_not_allowed`.
  Power units in a forecast mean the average power over the slot, energy units the energy
  per slot. When the attribute is a mapping, `list_path` (dotted) leads to the list of
  points; the period of a point is `resolution_min`.
- Forecast templates (Phase 4): `series.shape` is `list` (objects named by `fields`) or
  `mapping` (`{iso_timestamp: value}`, `fields` holds only `value_unit`); a point is the
  value of the period that **starts** at its timestamp. A forecast template may add
  `fallback_series` (an allowlisted action) that is used when the attributes are missing.
  `series.source: energy_solar_forecast` reads the Energy dashboard's `energy/solar_forecast`
  (Forecast.Solar): the template names no entity, the user binds the source to a config entry
  (`forecast.sources[].config_entry_id`; without one the only entry the command returns is
  taken, several entries without a choice make the source report `ambiguous_entry`).
  `missing_is_zero: true` reads a period missing between two known ones as 0 (Forecast.Solar's
  integration drops a zero value that starts exactly at midnight). For an attribute or field
  names the built-in templates do not cover, copy
  `generic.pv_forecast` into `/data/templates` and change `attribute`, `fields`, `shape`,
  `list_path` and `resolution_min`.
- Battery templates (`kind: battery`) list the readable and writable roles (for example
  `soc`, `power`, `min_soc_number`, `max_charge_number`, `max_discharge_number`,
  `mode_select`, `force_charge_switch`) and in `controls` what each writable role
  expresses: `min_soc` (number in %), `max_charge` / `max_discharge` /
  `grid_charge_power` (number in W, kW or %), `mode` (select; every option has a
  `meaning`: `auto` - exactly one, the release baseline -, `force_charge`,
  `no_discharge`, `no_charge`, `other`) or `force_charge` (switch). The control kind
  fixes the entity domain (`number`, `select`, `switch`). `driver` picks the adapter:
  `generic` (default, the `controls`), `kostal_plenticore` (exactly one `min_soc` control) or
  `kostal_modbus` (no `controls`; a `modbus` register map and the read-back roles
  `external_control`, `charge_limit`, `discharge_limit`, `fallback_charge_limit`,
  `fallback_discharge_limit`, `fallback_timeout`, `own_charge_limit`, `own_discharge_limit`).
  Every write goes through the safety gate; a battery starts in shadow mode like every device.
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
| 0.4.0 (unreleased, Phase 3) | New `defaults.control` kind `relays` (additive union member, ADR-0012 policy): 1–3 switch roles (`roles`) and `steps`, each step naming the relays that are on (`relays`) and the power it draws (`power_w`, strictly increasing, relay sets distinct). `DeviceConfig` accepts the same control. Used by `generic.water_heater_relays`. |
| 0.5.0 (unreleased, Phase 4) | New template kind `battery` (`battery.schema.json`): `roles`, `controls` (union on `kind`: `min_soc`, `max_charge`, `max_discharge`, `grid_charge_power` numbers; `mode` select with option meanings; `force_charge` switch), optional `settle_s`. New capabilities `battery_soc`, `min_soc_limit`, `charge_limit`, `discharge_limit`, `grid_charge`, `battery_modes`. `series` of `source: attribute` takes an optional `list_path` for the generic attribute adapter and an optional `shape` (`list` default, or `mapping` for `{iso_timestamp: value}` attributes such as Forecast.Solar `wh_hours` and Open-Meteo Solar Forecast `wh_period_15m`; `fields` then holds only `value_unit`, so `fields.timestamp_field`/`value_field` became optional and are required only for lists of point objects). Tariff `price_unit` and `price_series.fields.value_unit` may be per MWh (`{currency}/MWh`, `EUR/MWh`); the value unit's currency must match `price_unit`'s (or be `{currency}`), and adapters convert to currency/kWh (`price_unit_factor`: MWh / 1000). Battery mode option `value` is kept verbatim (no whitespace stripping). HT/NT needs no template change: time windows and prices are contract data in the `tariff` configuration section (v3). Battery templates (WP10, additive): `driver` (`generic` default, `kostal_plenticore`, `kostal_modbus`), `modbus` register map (`kostal_modbus` only), capability `hardware_fallback`. |
| 0.5.0 (unreleased, Phase 4, WP4) | Tariff price curves (all additive): `series.fields.end_field` (slot end, slot length = `end - start`; lists only); action series take `date_field` (one call per delivery date, excludes `start_field`/`end_field`) with an optional `date_tz` (IANA zone of that delivery day, e.g. Nord Pool's CET day; default the site's zone) and `params` (`name`, `required`, `description` i18n key: request fields the user supplies in the tariff configuration, e.g. Nord Pool `config_entry`, `areas`, `currency`); `response_path` may start with `*` (every entry of the response, e.g. `*` for `{area: [...]}`); the read-only action allowlist gains `nordpool.get_prices_for_date`. The `tariff` configuration section (v3) gains optional `import.role_entities`, `import.action_params`, `import.series_key` and `import.price_includes` (the user's statement overriding the template's classification). Eight tariff templates: `tibber.price` (updated), `nordpool.price`, `entsoe.price`, `entsoe.price_mwh`, `epex_spot.total_price`, `epex_spot.market_price`, `octopus_energy.rates`, `generic.price_list`. The currency symbols `€`/`£` that EPEX Spot puts in front of `/kWh` need no schema change: the matcher lists the literal units and `suncore.tariff.units.normalise_price_unit` maps them to ISO codes. |
| 0.5.0 (unreleased, Phase 4, PV forecasts) | Forecast templates: `series.source: energy_solar_forecast` (new union member, `fields` holds only `value_unit`, optional `missing_is_zero`) for the Energy dashboard's `energy/solar_forecast`; optional `fallback_series` (an `action` series) used when the attribute yields nothing. New templates `forecast_solar.pv`, `open_meteo_solar_forecast.pv`, `generic.pv_forecast`; `solcast.pv` gained the optional roles `forecast_d3`, `forecast_d4` and the fallback action `query_forecast_data`. |
