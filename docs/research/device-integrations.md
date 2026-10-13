# Device integrations for Phase 2 templates

What the first templates (LG ThinQ air conditioner, Tibber, Solcast) can rely
on, read from the integration source. Companion to
[`integrations.md`](integrations.md) (Phase 0 overview) and
[`ha-websocket-api.md`](ha-websocket-api.md) (registries, energy prefs).

Sources, all read on 2026-10-13 from `raw.githubusercontent.com`:

- `home-assistant/core` `dev`, HEAD `14f093e` (`2026.11.0.dev0`); paths below
  start at `homeassistant/`.
- `BJReplay/ha-solcast-solar` `main`, HEAD `3ae22af`, manifest version `4.6.2`;
  paths start at `custom_components/solcast_solar/`.
- Libraries the integrations depend on (`thinqconnect==1.0.14`,
  `pyTibber==0.37.6`) were read from their PyPI wheels.

Nothing here was observed on a running HA; entity ids are derived from the
code and marked as such. Anything not verifiable is `UNVERIFIED`.

## How a template matches without entity ids

The registry gives, per entity: `platform` (`pl`), `translation_key` (`tk`),
`device_id` (`di`), entity category and hidden flag
([`ha-websocket-api.md`](ha-websocket-api.md#registries)). The state gives
`device_class`, `unit_of_measurement`, `state_class`, `supported_features`.
A role matcher is therefore a conjunction of these fields, and entities of
the same physical unit are tied together by the **same `device_id`**:

| Role | domain | platform | translation_key | device_class | unit |
|---|---|---|---|---|---|
| LG air conditioner (control) | `climate` | `lg_thinq` | `climate_air_conditioner` | – | – |
| LG energy today | `sensor` | `lg_thinq` | `energy_usage_today` | `energy` | `Wh` |
| LG energy yesterday | `sensor` | `lg_thinq` | `energy_usage_yesterday` | `energy` | `Wh` |
| Tibber price | `sensor` | `tibber` | `electricity_price` | – (none set) | `<CUR>/kWh` |
| Solcast forecast today | `sensor` | `solcast_solar` | `total_kwh_forecast_today` | `energy` | `kWh` |
| Solcast forecast tomorrow | `sensor` | `solcast_solar` | `total_kwh_forecast_tomorrow` | `energy` | `kWh` |

`translation_key` is only exposed by `config/entity_registry/list_for_display`
as `tk` and by `list` as `translation_key`; it is not in the state.

## Entity id format

`valid_entity_id` in `core.py:180-198`:

```python
_OBJECT_ID = r"(?!_)[\da-z_]+(?<!_)"
_DOMAIN = r"(?!.+__)" + _OBJECT_ID
VALID_ENTITY_ID = re.compile(r"^" + _DOMAIN + r"\." + _OBJECT_ID + r"$")
```

The lookahead `(?!.+__)` is evaluated from the start of the whole id, so a
double underscore is invalid **anywhere** in the id (domain or object id).
`tests/test_core.py:2312-2340` (`test_valid_entity_id`) confirms: invalid are
`_light.kitchen`, `light_.kitchen`, `light._kitchen`, `light.kitchen__ceiling`,
`light.kitchen_yo_`, `Light.kitchen`, `lightkitchen`; valid are `1.a`,
`1light.kitchen`, `a.1`, `light.1kitchen`, `input_boolean.hello_world_0123`.

`ENTITY_ID_PATTERN = r"^[a-z_]+\.[a-z0-9_]+$"` in `suncore/core/site.py` is
therefore **not stricter** than HA, as its comment claims: it is stricter only
in rejecting digits in the domain (HA allows them), and it **accepts** ids HA
rejects (`sensor._x`, `sensor.x_`, `sensor.a__b`, `_x.y`). Recommended
replacement, equivalent to HA's regex (checked exhaustively for all strings of
length ≤ 7 over `a`, `1`, `_`, `.`) and free of look-arounds, so it also
works in the Rust regex engine behind Pydantic's `pattern=`:

```text
^[a-z0-9]+(?:_[a-z0-9]+)*\.[a-z0-9]+(?:_[a-z0-9]+)*$
```

(The equivalence check ran locally against the regex copied from `core.py`; it
is not in HA's tests.) Note that energy preference `stat_*` fields can hold
external statistic ids such as `suncore:savings` that are **not** entity ids
(`energy/data.py` uses `valid_entity_id` to tell them apart).

## LG ThinQ (`lg_thinq`)

Cloud integration (`manifest.json`: `integration_type: hub`,
`iot_class: cloud_push`, `requirements: thinqconnect==1.0.14`).

### Device and ids

`components/lg_thinq/entity.py:25-70`: all entities have
`has_entity_name = True`; device identifiers `(lg_thinq, <coordinator.unique_id>)`
where the id is the LG `device_id` (with `_<sub_id>` for multi-unit devices);
`manufacturer = "LGE"` (`const.py:10`); `model = "<model_name> (<device_type>)"`
with `device_type` `DEVICE_AIR_CONDITIONER` for air conditioners
(`thinqconnect/const.py:13`). `unique_id` is
`<device_id>_<property_id>` (climate) and `<device_id>_<property_id>_<key>`
(energy). Entities whose property has a location other than main get the
translation key suffix `_for_location` (`entity.py:62-67`).

### Climate entity

`components/lg_thinq/climate.py`. Created for `DeviceType.AIR_CONDITIONER`
and `SYSTEM_BOILER` (`:32-47`); for the air conditioner the description has
`name=None` (entity takes the device name) and
`translation_key = key = "climate_air_conditioner"`
(`ExtendedProperty.CLIMATE_AIR_CONDITIONER`, a `StrEnum` with `auto()` in
`thinqconnect/integration/homeassistant/specification.py`; the same key
appears in `strings.json` under `entity.climate`). Derived entity id:
`climate.<device name slug>`; the property id part of the `unique_id` is
`UNVERIFIED` (built at runtime from the device profile).

- **Features** (`:110-155`): always `TARGET_TEMPERATURE | TURN_ON | TURN_OFF`;
  plus `PRESET_MODE` if the device reports a preset-type mode, `FAN_MODE` if
  it has fan modes, `TARGET_TEMPERATURE_RANGE` if
  `support_temperature_range`, `SWING_MODE`, `SWING_HORIZONTAL_MODE`.
- **HVAC modes** (`:49-57`, `:119-133`): `off` always, plus those of the
  device mapped `air_dry → dry`, `auto → auto`, `cool → cool`,
  `fan → fan_only`, `heat → heat`. `heat_cool` is **not** offered; setting it
  is mapped to `auto` (in `async_set_hvac_mode` and `async_set_temperature`).
- **Presets** (`:59`, `:127-133`, `:168-190`): `none` plus any of `air_clean`,
  `aroma`, `energy_saving`. State mapping: preset `energy_saving` shows
  `hvac_mode = cool`; `air_clean`/`aroma` show `fan_only`. `none` sets
  `cool` (from energy saving) or `fan`.
- **Fan modes** (`:68-72`, `:135-141`): device strings, with `mid → medium`
  and `nature → auto`; others pass through. Strings known from
  `strings.json`: `auto`, `breeze`, `high`, `low`, `low_mid`, `medium`,
  `mid_high`, `power`, `slow`, `slow_low`. Which exist depends on the model.
- **Temperature attributes** (`:204-244`): `current_temperature`,
  `temperature` (target), `target_temp_low` / `target_temp_high` (range, in
  heat/cool when the device has separate set-points), `current_humidity`,
  `min_temp`, `max_temp`, `target_temp_step`; unit from the device
  (`temperature_unit`, default °C). `async_set_temperature` truncates to int
  when the step is ≥ 1.
- **Behaviour to respect**: `async_set_hvac_mode` / `async_set_temperature`
  turn an unit that is off on first and then `asyncio.sleep(2)`; a service call
  can therefore take ≥ 2 s and is not atomic. State arrives by cloud push and
  may lag.

### Energy sensors

`components/lg_thinq/sensor.py:603-651` (descriptions), `:699-713` (setup).
They are created for **every** ThinQ device whose API exposes the
`energyUsage` property (`ThinQPropertyEx.ENERGY_USAGE = "energyUsage"`),
not only air conditioners. Four sensors per property:

| `key` | translation_key (`strings.json` name) | state_class | update |
|---|---|---|---|
| `today` | `energy_usage_today` ("Energy today") | `total`, `last_reset` at local midnight | hourly |
| `yesterday` | `energy_usage_yesterday` ("Energy yesterday") | `total` | daily |
| `this_month` | `energy_usage_this_month` ("Energy this month") | `total_increasing` | daily |
| `last_month` | `energy_usage_last_month` ("Energy last month") | `total` | daily |

All: `device_class = energy`, unit `Wh` (`UnitOfEnergy.WATT_HOUR`),
`suggested_display_precision = 0`. `unique_id =
<device_id>_<property_id>_<key>`; `property_id` is `energyUsage` (with
`_<sub_id>` suffix for sub-devices); the exact runtime idx string is
`UNVERIFIED`. Daily sensors fetch at a random time between 01:00 and 05:59.

Consequences for SunCore:

- There is **no instantaneous power sensor for air conditioners** in the
  integration (`DEVICE_TYPE_SENSOR_MAP[AIR_CONDITIONER]`, `:431-445`, only air
  quality, filter and timers). The template's power model cannot be
  `measured`; use `nominal` (user-entered or from model data) or `learned`
  from the hourly `energy_usage_today` increments.
- `energy_usage_today` is cloud-aggregated and updated hourly; it is a
  baseline/learning signal, never a control input.
- Match energy sensors with `translation_key` regex
  `^energy_usage_(today|yesterday|this_month|last_month)(_for_location)?$`
  and the same `device_id` as the climate entity.

## Tibber (core `tibber`)

Cloud polling (`manifest.json`: `iot_class: cloud_polling`,
`requirements: pyTibber==0.37.6`, `dependencies: application_credentials,
recorder`).

### Price sensor

`components/tibber/sensor.py:739-815` (`TibberSensorElPrice`), only created
for homes with an active subscription (`:626`):

- `translation_key = "electricity_price"` (name "Electricity price"),
  `state_class = measurement`, **no `device_class`**, icon `mdi:currency-usd`.
- Unit: `home.price_unit` = `<currency>/<consumption_unit>`
  (`pyTibber home.py:362-367`; `consumption_unit` is always `kWh`), e.g.
  `EUR/kWh`. The state is the **total price** (energy + tax) of the current
  15-minute slot, rounded to 3 decimals (`current_price_data`,
  `home.py:394-402`; the GraphQL query uses `priceInfo(resolution:
  QUARTER_HOURLY)`, `gql_queries.py:139`). The sensor refreshes on every
  15-minute boundary (`coordinator.py:331-348`).
- `unique_id = home_id`; device identifiers `(tibber, home_id)`, name = app
  nickname (else `address1`), `manufacturer = "Tibber"`, model `Price Sensor`.
  Entity id is derived from device name + "Electricity price", for example
  `sensor.<nickname>_electricity_price`; `UNVERIFIED` against a live system.
- Attributes (`sensor.py:754-815`): `app_nickname`, `grid_company`,
  `estimated_annual_consumption`, `max_price`, `avg_price`, `min_price`
  (these three over **today only**), `off_peak_1` (mean of 00:00-08:00),
  `peak` (08:00-20:00), `off_peak_2` (20:00-24:00), `intraday_price_ranking`.
  **There is no `today`/`tomorrow` list attribute any more**; the full price
  curve must come from the action below. The sensor is `unavailable` when no
  current price exists.

### Action `tibber.get_prices`

`components/tibber/services.py`, `services.yaml`. Registered with
`SupportsResponse.ONLY` (`services.py:147-152`), so it must be called with
`return_response: true`; the WebSocket `call_service` result then is
`{"context": {...}, "response": <below>}` (`websocket_api/commands.py:340-343`).

- Fields: `start`, `end` (both optional strings, parsed by
  `dt_util.parse_datetime`, `YYYY-MM-DD HH:MM:SS` or ISO 8601; invalid input
  raises `ServiceValidationError` `invalid_date`). No `target`, no `home_id`
  field: all active homes are returned.
- Defaults: `start` = start of local today, `end` = start of local today +
  1 day, i.e. **today only**. If `start >= end`, `end = start + 1 day`.
  To get tomorrow pass `end` = start of the day after tomorrow.
- Response: `{"prices": {"<home name>": [{"start_time": "<startsAt>",
  "price": <float>}, ...]}}`. `start_time` is the string Tibber delivers
  (ISO 8601 with UTC offset, slot start); `price` is the unrounded `total`
  (currency/kWh, taxes included). Items are filtered `start <= t < end`. The
  object key is the home's name (nickname), so two homes with the same name
  would collide.
- Window and availability: only today and tomorrow exist in memory
  (`pyTibber home.py:249-258`). The action refetches when the cached data
  does not cover the next day after 13:00 local (`services.py:57-70`);
  tomorrow's prices are published around 13:00 CET/CEST by the market
  (`UNVERIFIED` for Tibber's exact publication time). Slot length is not
  stated in the response; derive it from the timestamps (15 min with the
  current library, hourly in older ones).
- Errors: `HomeAssistantError` with translation keys `get_prices_timeout`,
  `get_prices_invalid_login`, `get_prices_communication_failed`;
  `ServiceValidationError` `no_config_entry` when Tibber is not set up.

Read-only: SunCore only calls `get_prices`; it never writes to Tibber.

## Solcast PV Forecast (HACS, `BJReplay/ha-solcast-solar`)

`manifest.json`: domain `solcast_solar`, name "Solcast PV Forecast",
`single_config_entry: true`, `iot_class: cloud_polling`, version `4.6.2`.
The service device is named **"Solcast PV Forecast"**, manufacturer `BJReplay`,
`entry_type: service` (`entity.py:9-19`, `const.py:279,312`).

### Sensors

`sensor.py:307-490` (descriptions) and `:626-665` (entity). Entities set
`has_entity_name = True`, `unique_id = <key>` (no domain prefix), and have a
`translation_key` equal to the key; names come from
`translations/en.json`. Derived entity id = device name + entity name, e.g.
`sensor.solcast_pv_forecast_forecast_today` (derived from the code and
`translations/en.json`; not observed live).

| translation_key (= `unique_id`) | Name | Unit | device_class | state_class | Notes |
|---|---|---|---|---|---|
| `total_kwh_forecast_today` | Forecast Today | kWh | energy | total | day total, attributes below |
| `total_kwh_forecast_tomorrow` | Forecast Tomorrow | kWh | energy | total | attributes below |
| `total_kwh_forecast_d3` … `_d7` | Forecast Day 3 … | kWh | energy | total | number controlled by the "advanced day entities" option; `UNVERIFIED` default |
| `power_now` | Power Now | W | power | measurement | |
| `power_now_30m`, `power_now_1hr` | Power in 30 Minutes / 1 Hour | W | power | measurement | |
| `peak_w_today`, `peak_w_tomorrow` | Peak Forecast Today / Tomorrow | W | power | measurement | |
| `peak_w_time_today`, `peak_w_time_tomorrow` | Peak Time … | – | timestamp | – | |
| `forecast_this_hour`, `forecast_next_hour`, `forecast_custom_hours` | … | Wh | energy | – | some sensors are disabled by default (`ENABLED_BY_DEFAULT: False`) |
| `forecast_remaining_today` (key `get_remaining_today`) | Forecast Remaining Today | kWh | energy | – | |
| `api_counter`, `api_limit`, `dampen`, `accuracy`, `last_updated` | diagnostics | | | | `entity_category: diagnostic` |

Per-site rooftop sensors use `unique_id = solcast_solcast_api_<resource_id>`
(`sensor.py:779-804`).

### Forecast attributes (on the `total_kwh_forecast_*` day sensors)

`forecast.py:204-326` (`get_forecast_day`), attached to the sensor via
`coordinator.py:135-143` and `:320-331`. Attribute keys: `dayname`,
`dataCorrect` (bool), `analysis` (dict: `estimate10_kwh`, `estimate90_kwh`,
`spread_kwh`, `confidence`, `intervals[]`), and, controlled by options
(both default **true**: `__init__.py:157-158`, `config_flow.py:379-380`):

- `detailedForecast`: list of half-hour slots,
  `{"period_start": <tz-aware datetime, local tz of the integration>,
  "pv_estimate": <kW>, "pv_estimate10": <kW>, "pv_estimate90": <kW>}`.
  Constants `ESTIMATE = "pv_estimate"`, `ESTIMATE10 = "pv_estimate10"`,
  `ESTIMATE90 = "pv_estimate90"`, `PERIOD_START = "period_start"`
  (`const.py:191-193,326`).
- `detailedHourly`: same keys, hourly, each value the mean of the two
  half-hour values (`build_hourly`, `forecast.py:217-235`), rounded to 4
  decimals. Only present if the hourly option is on.
- With the "site breakdown" option: `detailedForecast_<site>` and
  `detailedHourly_<site>` (site key formatted by `format_site_key`).

Units and semantics: values are **average power in kW over the slot**, not
energy: the integration itself converts with `0.5 * sum(...)` per half-hour
slot (`forecast.py:290-291`). The values are dampened if dampening is enabled
(service option `undampened` returns raw data). `pv_estimate` is the P50,
`pv_estimate10` the P10 (pessimistic), `pv_estimate90` the P90.
`dataCorrect` is `false` when the day has fewer than 48 half-hour slots
(`forecast.py:262-264`, so incomplete data and days shortened by DST), `true`
otherwise.
These attributes are excluded from the recorder (`sensor.py:674-682`), so
history does not contain them.

When the attributes are large, `get_states` payloads grow (48 slots × 4
keys per day sensor); prefer the action below for a defined range.

### Action `solcast_solar.query_forecast_data`

`services.yaml:22-37`, `actions.py:398-425`, registered with
`SupportsResponse.ONLY`. Fields: `start_date_time`, `end_date_time`
(datetime, UTC or local; both default to "now" which yields a single slot or
raises), `undampened` (bool, default false), `site` (resource id or `all`).
Response `{"data": [{"period_start": ..., "pv_estimate": ..., "pv_estimate10":
..., "pv_estimate90": ...}, ...]}` with `period_start` in the integration's
time zone. An invalid range raises `ServiceValidationError`
(`invalid_query_range`). `query_estimate_data` returns past estimated actuals
in the same style (default: yesterday).

`UNVERIFIED`: whether the `detailedForecast` list items carry further keys
besides the four above in all code paths (the slot dicts are passed through
with `{**forecast, ...}`, so extra keys such as dampening factors may appear
when the advanced options are on).

## Verification

Read on 2026-10-13, `home-assistant/core` `dev` HEAD `14f093e`:
`homeassistant/core.py`, `tests/test_core.py`, `components/lg_thinq/{climate,
sensor,entity,const,manifest.json,strings.json}`, `components/tibber/{sensor,
services,coordinator,const,strings.json,services.yaml,manifest.json}`,
`components/websocket_api/commands.py`. Libraries from PyPI wheels
`thinqconnect-1.0.14` (`const.py`, `integration/homeassistant/{api,
specification}.py`) and `pytibber-0.37.6` (`home.py`, `gql_queries.py`).
`BJReplay/ha-solcast-solar` `main` HEAD `3ae22af`:
`custom_components/solcast_solar/{manifest.json,sensor.py,const.py,forecast.py,
coordinator.py,actions.py,entity.py,__init__.py,config_flow.py,services.yaml,
translations/en.json}`.

`UNVERIFIED` (needs a live HA or the user's system): exact entity ids; the
`property_id` part of LG `unique_id`s; which HVAC modes, fan modes and presets
a given LG model reports; LG sub-device (`sub_id`) layouts; the Tibber
publication time of tomorrow's prices; the default number of Solcast day
sensors; whether older cores and older Tibber/Solcast versions differ in
attribute names.
