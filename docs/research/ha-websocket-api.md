# Home Assistant WebSocket API, energy preferences, statistics

Core version at the time of research: 2026.11 (dev branch `MAJOR_VERSION =
2026`, `MINOR_VERSION = 11`).

## Connection and auth

From an app: `ws://supervisor/core/websocket`. Flow: server sends
`auth_required` → client sends `{"type":"auth","access_token":
"<SUPERVISOR_TOKEN or long-lived token>"}` → `auth_ok` or `auth_invalid`
(then disconnect). After auth every message carries an increasing integer
`id`; replies and subscription events reuse it. Optional first message
`{"id":1,"type":"supported_features","features":{"coalesce_messages":1}}`
batches messages. Heartbeat with `ping`/`pong`. Errors: `success: false`
with `error.code`/`error.message`.

Verified on 2026-10-12 against `home-assistant/core` (dev):

- `websocket_api/auth.py`: `auth_required` and `auth_ok` both carry `ha_version`;
  `auth_invalid` carries `message`, then the server closes the socket.
- `websocket_api/http.py`: with `supported_features` → `coalesce_messages`, several
  messages may arrive as one JSON **array** in a single text frame; clients must
  accept both an object and an array per frame.
- `websocket_api/commands.py`: `unsubscribe_events` takes the field `subscription`
  (the `id` of the subscribing command), not `id`.
- `websocket_api/commands.py` (`subscribe_entities`): the `result` message is sent
  *before* the initial `{"a": {...}}` event, so a client waits for both. An empty or
  absent `entity_ids` list means **all** entities; a client that wants none must not
  subscribe.
- `core.py`: `State.as_dict` keys are `entity_id`, `state`, `attributes`, `last_changed`,
  `last_reported`, `last_updated` (ISO strings) plus `context`. On a new state
  `last_reported` is set equal to `last_updated`.
- `core.py`: `EVENT_STATE_REPORTED` is a separate event; `subscribe_entities` does not
  carry it, so a sensor that reports an unchanged value is silent on the stream. Its
  freshness is only visible as an advancing `last_reported` in `get_states`.
- `websocket_api/messages.py`: in compressed states `lu` is omitted when it equals `lc`.

The same code path works for a standalone container against
`ws://<ha>:8123/api/websocket` with a long-lived access token, which is why
the connector takes the URL and token as parameters.

## Commands SunCore needs

| Type | Fields | Use |
|---|---|---|
| `subscribe_entities` | `entity_ids` (optional list) | live states of the configured sensors, compressed format below |
| `subscribe_events` | `event_type` (one per call) | `state_changed` fallback, `call_service` observation |
| `subscribe_trigger` | `trigger` (automation trigger syntax) | manual-override detection (numeric/state triggers) |
| `call_service` | `domain`, `service`, `service_data`, `target`, `return_response` | actuation; `return_response: true` for actions that return data (`weather.get_forecasts`, `tibber.get_prices`, `solcast_solar.query_forecast_data`, `nordpool.get_prices_for_date`) |
| `get_states` | – | initial snapshot |
| `get_services` | – | capability discovery (which actions exist) |
| `get_config` | – | unit system, time zone, currency, location, version; `latitude`/`longitude` (floats, degrees) feed the PV clear-sky model of the forecast calibration – verified in `home-assistant/core` `5cfe9aad7cb428c2c2ebb449719ae81976780de1` `homeassistant/core_config.py` `Config.as_dict` (lines 706, 713) returned unchanged by `websocket_api/commands.py` `handle_get_config` (only `external_url` is removed for local-only users) |
| `config/entity_registry/list_for_display` (preferred), `config/entity_registry/list`, `config/device_registry/list`, `config/area_registry/list` | – | setup wizard auto-detection (platform, device, area, category, translation key); verified, shapes in [Registries](#registries) |
| `render_template` | `template` | last resort for integrations without actions |
| `energy/get_prefs` | – | pre-fill from the Energy dashboard |
| `energy/info`, `energy/validate`, `energy/solar_forecast` | – | diagnostics; `energy/solar_forecast` returns forecasts of integrations that implement the energy platform (core: Forecast.Solar, Victron Remote Monitoring; HACS: Solcast, Open-Meteo Solar Forecast); only for config entries listed in the Energy dashboard's solar sources, see section `energy/solar_forecast` below |
| `recorder/statistics_during_period` | `start_time`, `end_time?`, `statistic_ids[]`, `period` ∈ `5minute\|hour\|day\|week\|month\|year`, `types[]` ⊂ `change,last_reset,max,mean,min,state,sum`, `units` | load history for the load forecast, backtesting; request/response verified, see section Long-term statistics below |
| `recorder/list_statistic_ids` | `statistic_type?` | which sensors have statistics |
| `recorder/get_statistics_metadata` | `statistic_ids?` | units |
| `recorder/import_statistics` | `metadata{has_sum,name,source,statistic_id,unit_of_measurement,has_mean?,mean_type?,unit_class?}`, `stats[{start,mean?,min?,max?,state?,sum?,last_reset?}]` | optional: write savings / baseline series as external statistics (`source` must be a domain-like prefix, `statistic_id` `suncore:…`) – not admin-gated |

## Compressed entity format (`subscribe_entities`)

First event: `{"a": {entity_id: compressed_state}}`. Later events:

- `a` add: `{entity_id: compressed_state}`
- `r` remove: `[entity_id]`
- `c` change: `{entity_id: {"+": {...}, "-": {"a": [attr keys]}}}`

Compressed state keys (`homeassistant.const`): `s` state, `a` attributes,
`c` context (string id or dict), `lc` last_changed, `lu` last_updated
(omitted when equal to `lc`; in a `c` diff only when `last_changed` unchanged).
Timestamps are floats (epoch seconds).
Inside a `c` diff only changed fields appear.

## Registries

Verified on 2026-10-13 against `home-assistant/core` `dev` (HEAD `14f093e`,
`MAJOR_VERSION 2026`, `MINOR_VERSION 11`, `PATCH_VERSION 0.dev0`). Paths are
relative to `homeassistant/`; line numbers refer to that commit.

### Commands and admin requirement

| Type | Source | Admin | Result |
|---|---|---|---|
| `config/entity_registry/list_for_display` | `components/config/entity_registry.py:70-95` | no | `{entity_categories, entities[]}` compact, **without disabled entities** |
| `config/entity_registry/list` | `components/config/entity_registry.py:39-67` | no | array of full-ish entries |
| `config/entity_registry/get` / `get_entries` | same file, `:98-150` | no | `get` returns the extended dict (adds `device_class`, `original_device_class`, `capabilities`, `aliases`, `original_icon`) |
| `config/device_registry/list` | `components/config/device_registry.py:75-102` | no | array of devices **plus child devices** |
| `config/area_registry/list` | `components/config/area_registry.py:23-37` | no | array of areas |
| `config/*_registry/update`, `remove`, `create`, `delete` | same files | **yes** (`@require_admin`) | SunCore never calls them |

`require_admin` (`components/websocket_api/decorators.py:54-69`) raises
`Unauthorized` for a non-admin user. The Supervisor system user is created
with `GROUP_ID_ADMIN` (`components/hassio/__init__.py:204-216`), so the
`SUPERVISOR_TOKEN` is admin. A long-lived token of a non-admin user can still
use all list commands above because none of them is gated.

### Entity registry, `list_for_display` (preferred, cheaper)

`helpers/entity_registry.py:198-206` (`DISPLAY_DICT_OPTIONAL`), `:321-348`
(`_as_display_dict`). Each entity is a dict with short keys, optional keys
are **absent** when empty:

| Key | Meaning |
|---|---|
| `ei` | `entity_id` |
| `pl` | `platform` (integration domain, e.g. `lg_thinq`) |
| `di` | `device_id` |
| `ai` | `area_id` of the entity itself (device area is not inherited, look it up via the device) |
| `lb` | label ids (list) |
| `tk` | `translation_key` |
| `ic` | icon override |
| `np` | next name part (`area`, `device`) |
| `ec` | entity category as **index** into `entity_categories` (`{"0": "config", "1": "diagnostic"}` style map delivered in the same result) |
| `hb` | `true` when hidden (any hider) |
| `hn` | `true` when `has_entity_name` |
| `en` | entity's own name without the device prefix (`name` or `original_name_unprefixed` or `original_name`) |
| `dp` | sensor display precision |

Not present: `unique_id`, `config_entry_id`, `disabled_by` (disabled entities
are filtered out server side), `original_name`, `device_class`, unit.

### Entity registry, `list`

`as_partial_dict` (`helpers/entity_registry.py:372-402`): `area_id`,
`categories`, `config_entry_id`, `config_subentry_id`, `created_at`,
`device_id`, `disabled_by` (`config_entry|device|hass|integration|user` or
null), `entity_category` (`config|diagnostic` or null), `entity_id`,
`has_entity_name`, `hidden_by` (`integration|user` or null), `icon`, `id`,
`labels`, `modified_at`, `name`, `next_name_part`, `options`,
`original_name`, `platform`, `translation_key`, `unique_id`. All keys always
present (null when unset). **`device_class` and the unit are not in
`list`**; take them from the state attributes (`device_class`,
`unit_of_measurement` via `subscribe_entities`/`get_states`) or call
`config/entity_registry/get` per entity.

### Device registry, `list`

`DeviceEntry.dict_repr` (`helpers/device_registry.py:742-776`) returns `area_id`,
`configuration_url`, `config_entries` (deprecated), `config_entries_subentries`
(deprecated), `config_entry_id`, `config_subentry_id`, `connections`,
`created_at`, `disabled_by`, `entry_type` (`service` or null), `hw_version`,
`id`, `identifiers` (list of `[domain, id]`), `labels`, `manufacturer`,
`model`, `model_id`, `modified_at`, `name_by_user`, `name`, `next_name_part`,
`parent_device_id` (null), `serial_number`, `sw_version`, `via_device_id`.

Dev branch change to respect: a device now belongs to **one** config entry
(`config_entry_id`); `config_entries` and `primary_config_entry` stay as
deprecated compatibility keys until 2027.8/2027.10. Use `config_entry_id` and
fall back to `config_entries[0]` for older cores.

`ChildDeviceEntry.dict_repr` (`:895-913`) is a **reduced** record (`area_id`,
`config_entry_id`, `config_subentry_id`, `created_at`, `disabled_by`, `id`,
`identifiers`, `labels`, `modified_at`, `name_by_user`, `name`,
`next_name_part`, `parent_device_id`) without `manufacturer`, `model`,
`entry_type`, `via_device_id`. Parsers must treat those keys as optional.
The displayed device name is `name_by_user` or else `name`.

### Area registry, `list`

`AreaEntry.json_fragment` (`helpers/area_registry.py:88-103`): `aliases`,
`area_id`, `floor_id`, `humidity_entity_id`, `icon`, `labels`, `name`,
`picture`, `temperature_entity_id`, `created_at`, `modified_at`.

### Registry update events

| Event type | Constant | `data` | Source |
|---|---|---|---|
| `entity_registry_updated` | `helpers/entity_registry.py:85-87` | `{action: create\|remove\|update, entity_id, changes?, old_entity_id?}` (`changes` only for `update`, `old_entity_id` on rename) | `:155-170` |
| `device_registry_updated` | `helpers/device_registry.py:79-81` | `{action: create\|remove\|update, device_id, changes?, device?}` (`device` only on `remove`) | `:335-360` |
| `area_registry_updated` | `helpers/area_registry.py:36-38` | `{action: create\|remove\|update\|reorder, area_id}` (`area_id` null for `reorder`) | `:66-70` |

Subscribe with `{"type":"subscribe_events","event_type":"<name>"}`. In
`handle_subscribe_events` (`components/websocket_api/commands.py:192-224`) a
non-admin user may only subscribe to event types in `SUBSCRIBE_ALLOWLIST`
(`auth/permissions/events.py:27-48`), and all three registry events are in
that allowlist. So both the Supervisor token and a non-admin long-lived token
may subscribe. The events carry no entity states. On any event the
simplest correct reaction is to re-run the corresponding list command
(debounced); `changes` can be used for incremental updates.

### Registry-based detection recipe (what the wizard needs)

- Candidate sensors: states from `subscribe_entities` (device class, unit,
  state class from attributes) joined with `list_for_display` on `ei`.
- Skip `hb` (hidden) and `ec` set (config/diagnostic) unless the role needs
  them; disabled entities never appear in `list_for_display`.
- Integration: `pl`. Device: `di` → `config/device_registry/list` for
  `manufacturer`, `model`, `name`/`name_by_user`, `area_id`. Area of the
  entity: `ai` if present else the area of the device.
- Translation key: `tk` (only for entities that set one).

## Energy preferences (`energy/get_prefs`)

Verified 2026-10-13 against `homeassistant/components/energy/data.py` and
`websocket_api.py` (dev `14f093e`). Command `energy/get_prefs`
(`websocket_api.py:102-121`), no admin requirement (`energy/save_prefs` is
`@require_admin`, SunCore never writes).

Result `EnergyPreferences` (`data.py:260-265`):
`{energy_sources: [...], device_consumption: [...], device_consumption_water?: [...]}`.

**Not configured.** `manager.data` is `None` until the user saved the Energy
dashboard once (`data.py:769-774`, store returns `None` when no file exists).
The command then answers `success: false` with `error.code` =
`"not_found"` (`websocket_api.const.ERR_NOT_FOUND`, `websocket_api/const.py:38`)
and message `"No prefs"` (`websocket_api.py:117-119`). A wizard treats this as
"no suggestions", not as an error. If the `energy` integration is not loaded
at all the command is unknown (`unknown_command`, standard WebSocket
behaviour, `UNVERIFIED` for the exact code on this core).

**Source entries** (`energy_sources[].type`):

- `grid` – **unified format** since storage minor version 3
  (`data.py:99-148`; migration from the legacy `flow_from`/`flow_to`/`power`
  lists in `:653-760`). One entry per grid connection, several are allowed:
  `stat_energy_from` (import kWh statistic or null), `stat_energy_to`
  (export kWh or null), `stat_cost`, `entity_energy_price`,
  `number_energy_price`, `stat_compensation`, `entity_energy_price_export`,
  `number_energy_price_export`, `cost_adjustment_day`, optional `name`,
  optional **power**: `stat_rate` and `power_config`.
- `solar`: `stat_energy_from`, optional `stat_rate` (power), optional `name`,
  `config_entry_solar_forecast` (list of config entry ids or null).
- `battery`: `stat_energy_from` (discharge kWh), `stat_energy_to` (charge
  kWh), optional `stat_rate` (power, **positive when discharging, negative
  when charging**), optional `power_config`, optional `stat_soc` (sensor, %),
  optional `capacity` (kWh), optional `name` (`data.py:164-181`).
- `gas`, `water`: `stat_energy_from`, optional `stat_rate`; not used.
- `device_consumption[]`: `stat_consumption` (ever increasing energy
  statistic), optional `stat_rate` (instantaneous power, W), optional `name`,
  optional `included_in_stat` (`data.py:243-258`). There is **no
  `power_config` on devices**; the earlier note was wrong.

**Power in the Energy dashboard (new).** `stat_rate` exists on grid, solar,
battery and device entries. For grid and battery a `power_config` may be
present (`PowerConfig`, `data.py:66-96`), exactly one of:

| Form | Keys | Meaning |
|---|---|---|
| standard | `stat_rate` | single sensor, positive = discharge / from grid, negative = charge / to grid |
| inverted | `stat_rate_inverted` | single sensor with opposite polarity |
| two sensors | `stat_rate_from` + `stat_rate_to` | both positive; net = from − to (battery: discharge − charge; grid: consumption − return) |

When `power_config` is `stat_rate_inverted` or the two-sensor form, HA
creates a template transform sensor (`energy/sensor.py:317-354`,
`energy/helpers.py`) and sets the top level `stat_rate` to **that generated
entity id** (`data.py:890-919`: `generate_entity_id(...)`; inverted:
`sensor.<source>_inverted`). So `stat_rate` is always the sensor with the
HA sign convention (grid + = import, battery + = discharge) and is the one to
read; the raw sensors are in `power_config`. Sign handling for SunCore: grid
`stat_rate` matches SunCore (+ import); battery `stat_rate` is the **opposite**
of SunCore (+ = discharge in HA, + = charging in SunCore) and needs inversion.
The unit of `stat_rate` is not fixed by the prefs (code comments say kW for
grid, W for devices): always read `unit_of_measurement` from the entity state.

`stat_*` fields are statistic ids, which for sensors equal the entity id; they
can also be external statistics (`domain:name`, not a valid entity id).
The wizard must check `valid_entity_id` before treating one as an entity.
Prices (`entity_energy_price`) are entity ids.

## Weather forecasts

`weather.get_forecasts` action, field `type` ∈ `daily|hourly|twice_daily`,
target `entity_id`. Response per entity: `{"forecast": [...]}` with items
`datetime` (required), `condition`, `temperature`, `templow`,
`apparent_temperature`, `dew_point`, `humidity`, `pressure`,
`cloud_coverage`, `precipitation`, `precipitation_probability`,
`wind_bearing`, `wind_speed`, `wind_gust_speed`, `uv_index`, `is_daytime`.
Values are converted to the user's unit system.

### Phase 4 verification (2026-10-16)

Read from `home-assistant/core` `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1`
(2026.11 dev; tag `2026.10.2` = `8d7fd8b68677c2585be36ec86043308f403875b8`),
files `homeassistant/components/weather/{services.py,services.yaml,const.py,entity.py}`,
`homeassistant/helpers/service.py`.

- **Name and registration** (`weather/services.py:60-74`): the only registered
  action is **`weather.get_forecasts`** (`SERVICE_GET_FORECASTS = "get_forecasts"`,
  `weather/const.py:122`). `services.yaml` still lists a stale singular
  `get_forecast` entry; no code registers it. Schema:
  `{Required("type"): In(("daily", "hourly", "twice_daily"))}`, target is the
  entity (`required_features` = any of `FORECAST_DAILY`, `FORECAST_HOURLY`,
  `FORECAST_TWICE_DAILY`), `supports_response=SupportsResponse.ONLY`, so
  `return_response: true` is mandatory.
- **Per-type support** (`_async_get_forecasts_service`): asking for a type the
  entity lacks raises `ServiceValidationError` (`unsupported_forecast_type`,
  placeholders `entity_id`, `forecast_type`) -> WebSocket `service_validation_error`.
  A `None` result from the entity becomes an empty list.
- **Response shape**: `entity_service_call` collects `{entity_id: result}`
  (`helpers/service.py:807-811, 926`), so over WebSocket `call_service` the
  result is `{"context": {...}, "response": {"weather.home": {"forecast": [ {...}, ... ]}}}`.
  Each list item is the integration's forecast dict after `_convert_forecast`
  (`weather/entity.py:608-`): native keys are folded into `temperature`,
  `templow`, `apparent_temperature`, `dew_point`, `pressure`, `wind_speed`,
  `wind_gust_speed`, `precipitation`, converted to the **user's** unit system and
  rounded; `datetime` is passed through as the integration delivers it (ISO
  string in practice, `ATTR_FORECAST_TIME = "datetime"`). `temperature` is set
  to `null` when the entity delivered none. Keys the integration does not
  provide are simply absent: the adapter must treat every key but `datetime` as
  optional. The temperature unit is **not** in the response; read
  `unit_of_measurement`-like info from the entity's `temperature_unit` state
  attribute (not read in this pass, `UNVERIFIED`).
- Whether `hourly` is offered depends on the integration (`supported_features`
  bit `FORECAST_HOURLY = 2`); not every weather entity has it. Fall back to
  `daily` (a coarser temperature profile) or no temperature term.

## Workday

The `workday` binary sensor indicates whether the current day is a workday
(holidays per country/region). The load forecast uses it to classify days.

### Phase 4 verification (2026-10-16)

Same core commit; `homeassistant/components/workday/{binary_sensor.py,entity.py,services.py,services.yaml,const.py}`.

- State: `IsWorkdaySensor.update_data` sets `_attr_is_on = date_is_workday(now)`;
  **`on` = today is a workday, `off` = not** (weekend, public holiday or a
  configured exclusion). The state is recomputed at local midnight
  (`get_next_interval` = start of the next local day), not at HA start only.
- Attributes: `workdays` (list of weekday keys `mon`..`sun`, `holiday`),
  `excludes` (same keys), `days_offset` (int). The sensor's offset shifts the
  evaluated day (`days_offset`), so a sensor with a non-zero offset describes
  another day than "today"; the adapter should require offset 0 or warn.
- **Action `workday.check_date`** (`services.py`): entity service on the
  `binary_sensor` entities of the integration, field `check_date` (required,
  `cv.date`, `YYYY-MM-DD`), `supports_response=ONLY`; response
  **`{"<binary_sensor.entity_id>": {"workday": true|false}}`** (`entity.py`
  `check_date` returns `{"workday": bool}`, wrapped per entity id by the entity
  service machinery). It applies the sensor's own `days_offset`, workdays and
  excludes to the given date. This is enough to classify any past or future
  date (also holidays) for the load profile without a separate holiday library.
- Weekend vs holiday cannot be told apart from the boolean; the load profile
  has the three classes workday / weekend / holiday (plan WP6). To split
  "off" into weekend and holiday, combine with `date.weekday()` locally:
  `off` on Mon-Fri means holiday. `UNVERIFIED`: that a user's `workdays`/`excludes`
  configuration matches the Mon-Fri assumption (the attributes `workdays` and
  `excludes` allow checking it).

## Long-term statistics (`recorder/...`) (Phase 4 verification 2026-10-16)

Read from `home-assistant/core` `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1`:
`homeassistant/components/recorder/{websocket_api.py,statistics.py,models/statistics.py,__init__.py,purge.py}`,
`homeassistant/util/{unit_conversion.py,dt.py}`.

**`recorder/statistics_during_period`** (`websocket_api.py:288-311`), not admin-gated:

| Field | Type | Notes |
|---|---|---|
| `type` | `"recorder/statistics_during_period"` | |
| `start_time` | string, required | `dt_util.parse_datetime`; a value **without UTC offset is read in the HA time zone** (`dt_util.as_utc` assumes `DEFAULT_TIME_ZONE`); send ISO 8601 with offset. Invalid -> error `invalid_start_time` |
| `end_time` | string, optional | exclusive end; omitted = everything from `start_time` on; invalid -> `invalid_end_time` |
| `statistic_ids` | list of string, required, non-empty | for sensors equal to the entity id |
| `period` | `5minute` \| `hour` \| `day` \| `week` \| `month` \| `year`, required | `5minute` reads the short-term table, the others the long-term table; `day`/`week`/`month`/`year` are re-aggregated in **local** time, start/end are aligned to local midnight / Monday / 1st / 1 Jan |
| `types` | list ⊂ `change, last_reset, max, mean, min, state, sum`, optional | omitted = all seven. `change` is derived from `sum` (difference to the previous row) |
| `units` | object, optional | keys = unit class (e.g. `"power"`, `"energy"`, `"temperature"`), value = target unit from the class's `VALID_UNITS` (`PowerConverter`: `mW, W, kW, MW, GW, TW, BTU/h`; `EnergyConverter` includes `Wh, kWh, MWh`) |

Response `result` (`_ws_get_statistics_during_period`):

```text
{ "<statistic_id>": [ {"start": <ms>, "end": <ms>, "mean": f, "min": f, "max": f,
                       "state": f, "sum": f, "change": f, "last_reset": <ms>|null}, ... ] }
```

- `start` and `end` are **integer milliseconds since the Unix epoch** (UTC) of
  the period (`end = start + 5 min / 1 h`; for day and longer the reduced
  period). `last_reset` is also ms (or `null`). Only the requested `types`
  appear as keys. Statistic ids without data in the range are **absent** from
  the result object (not an empty list), and `{}` is returned if none has data.
  A value can be `null` (e.g. `mean` of a period without samples).
- **Units.** The response carries **no unit field**. Without `units`, values
  are converted to the entity's *current state unit* (`_get_statistic_to_display_unit_converter`:
  requested unit if given, else the unit in the live state attributes, else the
  unit stored with the statistic). A power sensor that the user switches from W
  to kW therefore changes the numbers. SunCore must either pass
  `units: {"power": "W", "energy": "Wh"}` explicitly (verified valid unit
  strings) or read `statistics_unit_of_measurement`/`display_unit_of_measurement`
  from `recorder/list_statistic_ids` first. Statistics whose `unit_class` is
  `null` (unitless or unknown units) are never converted.
- **Meaning per sensor kind.** `mean`/`min`/`max` exist for `state_class:
  measurement` sensors (power in W/kW, temperature); `state`, `sum` (and `change`,
  `last_reset`) for `total`/`total_increasing` sensors (energy counters). The
  `mean` of a power sensor is the arithmetic mean of the samples in the period
  (`mean_type` `1` = arithmetic, `2` = circular, `0` = none). Which of the
  columns are filled is given by `list_statistic_ids` (`mean_type`, `has_sum`).
- **Retention.** The `5minute` table is purged with the recorder's
  `purge_keep_days` (default **10**, `recorder/__init__.py:109`); the long-term (`hour` and
  coarser) rows are not touched by the purge code, which removes only
  `statistics_runs` and short-term rows (`recorder/purge.py:93-110`). A 14-day history for
  the load profile must therefore use
  `period: "hour"`, or `5minute` only for the last 10 days. `hour` is the finest
  period beyond 10 days; sub-hourly profile slots (15 min) can come from
  `5minute` for the recent days only.

**`recorder/list_statistic_ids`** (`websocket_api.py:344-353`): optional
`statistic_type` ∈ `sum|mean`. Result: list of
`statistic_id`, `display_unit_of_measurement`, `has_mean`, `mean_type`, `has_sum`, `name`,
`source`, `statistics_unit_of_measurement`, `unit_class`
(`statistics.py:1136-1154`; `has_mean` is marked for removal with 2026.4 in the
source comment, use `mean_type`). Includes statistics that integrations will add
in the next period. **`recorder/get_statistics_metadata`** (`:456`) with optional
`statistic_ids` returns the same metadata. `recorder/statistic_during_period`
(singular, one `statistic_id`, `types` ⊂ `max, mean, min, change`) exists as
well; not needed.

Request example (verified shape):

```json
{"id": 31, "type": "recorder/statistics_during_period",
 "start_time": "2026-10-02T00:00:00+02:00", "end_time": "2026-10-16T00:00:00+02:00",
 "statistic_ids": ["sensor.house_power"], "period": "hour",
 "types": ["mean", "min", "max"], "units": {"power": "W"}}
```

`UNVERIFIED`: the power/energy sign and unit of the user's own house-consumption
sensor (user data); whether the Supervisor proxy forwards `recorder/*` commands
(first run on HA OS; `call_service` is known to be forwarded, other commands
are expected to be).

## `energy/solar_forecast` (Phase 4 verification 2026-10-16)

Read from `home-assistant/core` `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1`:
`homeassistant/components/energy/{websocket_api.py,types.py}`,
`homeassistant/components/forecast_solar/{energy.py,coordinator.py,manifest.json}`,
`homeassistant/components/victron_remote_monitoring/energy.py`;
`BJReplay/ha-solcast-solar` tag `v4.6.1` `custom_components/solcast_solar/{energy.py,forecast.py}`;
`rany2/ha-open-meteo-solar-forecast` tag `0.1.33` `custom_components/open_meteo_solar_forecast/energy.py`;
PyPI wheels `forecast-solar 5.0.1`, `open-meteo-solar-forecast 0.1.32`.

- **Request**: `{"id": n, "type": "energy/solar_forecast"}`, no fields, no admin
  requirement (`websocket_api.py:187-190`).
- **Which entries**: the command reads the Energy preferences
  (`manager.data`); for every `energy_sources[]` entry of `type == "solar"`
  it collects the ids in **`config_entry_solar_forecast`** and asks the
  integration of each such config entry (`config_entry.domain` must have an
  `energy` platform with `async_get_solar_forecast`). Consequences: (a) empty
  result `{}` when the Energy dashboard was never saved, has no solar source or
  the solar source has no forecast linked; (b) a forecast integration that is
  installed but **not linked** in the Energy dashboard is invisible here;
  (c) `energy/info` lists the available platform domains
  (`solar_forecast_domains`, `:165`) - use it for auto-detection of what *could*
  be linked.
- **Response**: `{ "<config_entry_id>": {"wh_hours": {"<ISO 8601 timestamp>": <number>, ...}}, ... }`
  (`types.py:9-12`: `SolarForecastType = {wh_hours: dict[str, float | int]}`);
  entries returning `None` are omitted. The key is the **config entry id**
  (not the domain, not an entity id); map it to the domain via the config entry
  or `energy/get_prefs`.
- **Semantics of `wh_hours` per provider** (the type is only "Wh per timestamp",
  the period length differs):
  - `forecast_solar` (`energy.py`): `wh_period` of the `forecast-solar` library,
    Wh, ISO strings with the offset of the **API time zone** (the plant's
    location), one entry per hour in the free tier; the library sums values
    with `begin <= timestamp < end` for "current hour"
    (`forecast-solar 5.0.1` wheel, `forecast_solar/models.py` `_interval_value_sum`), i.e. it
    treats the key as the *start* of
    the period. Only an entry with value 0 that starts exactly at 00:00:00 is
    dropped; every other zero is listed (kept: `val != 0 or time != 00:00:00`).
    `UNVERIFIED`: whether the vendor API itself
    labels `watt_hours_period` keys as period start or end (api.forecast.solar
    unreachable); the HA library's own usage implies start.
  - `open_meteo_solar_forecast` (HACS, `energy.py`): `wh_period`, hourly, key =
    start of the hour (`open_meteo_solar_forecast.py:610-617` groups by
    `time.replace(minute=0)`), value = mean power of the hour in W (numerically
    Wh for that hour), no zero filter, ISO strings in the location's fixed
    offset.
  - `solcast_solar` (HACS, `energy.py` -> `forecast.py:945-`): `make_energy_dict`
    emits **half-hourly** keys = `period_start`, value =
    `pv_estimate{,10,90} (kW) * 500` = Wh per 30 min, rounded to integers, for
    **one** confidence level selected by the integration's option
    (`use_forecast_confidence`: estimate, 10 or 90), not all three. So no P10/P90
    band here; use the Solcast attributes for the band.
  - `victron_remote_monitoring` (core): `data.get_dict_isoformat`, not analysed.
- **Resampling rule for SunCore**: sum Wh per key into 15-minute slots by
  splitting each period's energy uniformly over its length; infer the length
  from the key spacing (30 or 60 min), never assume it. Only the energy sum is
  trustworthy; there is no unit field (`UNVERIFIED` only in the sense that the
  type has none; all four providers above deliver Wh).
- **No quantiles** over this command in any provider. P10/P90 only from Solcast
  attributes/`query_forecast_data`.

## Modbus write action (Phase 4 verification 2026-10-16)

Read from `home-assistant/core` `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1`:
`homeassistant/components/modbus/{services.py,services.yaml,modbus.py,const.py,strings.json,websocket_api.py,__init__.py}`,
`homeassistant/helpers/config_validation.py`.

- **Actions** registered by `async_setup_services`: `modbus.reload`,
  `modbus.write_coil`, `modbus.write_register`, `modbus.stop`. There is **no
  read action**: reading is only possible through `modbus:` YAML sensors /
  binary sensors (polled by the hub) which become normal HA entities. The WebSocket
  command `modbus/connections/list` (admin only) lists connections and unit
  ids, nothing else.
- **`modbus.write_register` fields** (`services.py:_write_service_schema`,
  `services.yaml`):

  | Field | Type | Notes |
  |---|---|---|
  | `address` | int >= 0, required | register address; the schema has **no upper bound or 16-bit check** (`cv.positive_int`) |
  | `value` | required | either a single non-negative int, or a list of non-negative ints; **each is one 16-bit register word as an unsigned int** (negative numbers are rejected by `NonNegative`, so signed values (S16) must be sent as two's complement `v & 0xFFFF`); a value above 65535 is not range-checked by HA and fails inside pymodbus |
  | `slave` **or** `unit` | int >= 0, optional, mutually exclusive (`Exclusive(…, "unit")`) | Modbus device/unit id; `slave` is the documented field (`services.yaml` min 1 max 255), `unit` is an accepted alias (`ATTR_UNIT = "unit"`); default **1** when both are absent (`service.data.get(ATTR_SLAVE, service.data.get(ATTR_UNIT, 1))`). The hub's own `slave` config option is **not** used as default for the service |
  | `hub` | string, default `modbus_hub` (`DEFAULT_HUB`) | the hub `name` from the YAML `modbus:` entry |

- **Single vs multiple registers**: `value` as int -> function code 0x06 (write
  single register, `write_register`); `value` as list -> 0x10 (write multiple
  registers, `write_registers`) (`services.py:_async_write_register`). A list of
  length 1 uses 0x10 too.
- **No float32 / typed values.** The action only writes raw register words.
  SunCore must encode IEEE 754 float32 itself into two words and know the
  device's word order. For the Kostal inverter (Float = 2 registers, byte order
  register 5, see `kostal-plenticore-modbus.md`) that is the adapter's job;
  e.g. `1038` (max charge power) gets `[hi, lo]` words as `value`.
- **Errors are not raised.** `ModbusHub.async_pb_call` -> `low_level_pb_call`
  (`modbus.py:325-361`) catches `ModbusException`, a `None`/error response and
  `result.isError()`, **logs the error and returns `None`**; the service returns
  normally. An unknown hub raises `KeyError` inside the service (HA reports
  `unknown_error`); Modbus not set up raises `ServiceValidationError`
  `not_loaded`. A failed write is therefore **silent** to the caller: read-back
  of the register (via a modbus sensor) or of the battery power is the only
  confirmation. A client that is not connected makes `async_pb_call` return
  `None` immediately (`if not self._client: return None`).
- **Concurrency**: all calls of a hub are serialised by a lock and an optional
  `message_wait_milliseconds` pause (`modbus.py:363-378`). A heartbeat writer
  (Kostal §3.5, every 20 s) shares the lock with all polled entities.
- **Admin**: `write_register` is registered with a plain `hass.services.async_register`
  (no `async_register_admin_service`), so the Supervisor's user may call it;
  `reload` is admin-only.
- `UNVERIFIED`: the exact exception HA returns for an unknown `hub` name (derived
  from `hass.data[...][name]` lookup, not run); `modbus` hub setup over the UI
  (the source shows YAML plus a new `connection.py` with `async_get_unit` for
  config-entry based integrations; whether users can add a plain hub in the UI is
  not established, the docs site is unreachable).

## Write services and contexts (Phase 3)

Verified on 2026-10-14 against `home-assistant/core` `dev` at commit
`c433453ac565f083a4515ec5edfbdbede48ebf24` (`MAJOR_VERSION = 2026`, `MINOR_VERSION = 11`) and
`home-assistant/supervisor` `main` at `a2627a1ce37032ecd5c6509813f70cf5b95d32c2`. Paths are
relative to `homeassistant/` in core; permalinks:
`https://github.com/home-assistant/core/blob/c433453ac565f083a4515ec5edfbdbede48ebf24/homeassistant/<path>`.
`dev` uses the `probatio` validation library (a `voluptuous` successor with the same API shape);
error text below is unchanged by that. Every statement below is source reading only; nothing was
run against a live HA unless it says so.

### Common behaviour of entity services

- All services below are *entity services* (`helpers/service.py`: `async_register_entity_service`,
  `entity_service_call`). Their schema is the listed fields plus the target fields
  `entity_id`, `device_id`, `area_id`, `floor_id`, `label_id` (`helpers/config_validation.py`
  `ENTITY_SERVICE_FIELDS`), with at least one of them required and extra keys rejected
  (`PREVENT_EXTRA`). `entity_id` is a string or a list of strings (`comp_entity_ids`).
- **A non-existing or currently unavailable entity does not raise.** `_resolve_entity_service_call_entities`
  drops entities with `available == False`, logs "Referenced entities … are missing or not
  currently available" and, if nothing is left, returns `None`: the WebSocket call **succeeds**
  (`success: true`) without doing anything. The only exception is `return_response: true`, which
  then raises `HomeAssistantError("Service call requested response data but did not match any
  entities")`. Consequence: a successful result never proves a write; SunCore must read the
  state back (and treat `unavailable`/`unknown` as "write not applied").
- **`required_features` is checked per entity.** The registration lists feature bit-sets; the
  entity qualifies if *any* listed set is fully contained in `supported_features`
  (`entity.supported_features & set == set`). If an explicitly referenced entity does not qualify,
  `ServiceNotSupported` is raised (see error shapes); an entity reached only through
  `device_id`/`area_id` is silently skipped.
- After the entity method returns, entities with `should_poll = True` get an immediate
  `async_update_ha_state(True)` (with the call's context re-applied). Push/coordinator entities
  (`should_poll = False`, e.g. all `lg_thinq` entities) do not; their state changes whenever
  the integration next writes (see "Context propagation").

### Services and fields

| Service | Fields (exact names) | Registration / validation (source) |
|---|---|---|
| `switch.turn_on`, `switch.turn_off` | none (target only) | `switch/services.py`; `ToggleEntity.async_turn_on/off`; no `required_features` |
| `input_boolean.turn_on`, `input_boolean.turn_off` | none (target only) | `input_boolean/services.py` (`component.async_register_entity_service(SERVICE_TURN_ON, None, "async_turn_on")`) |
| `climate.set_temperature` | `temperature` (float), or `target_temp_low` + `target_temp_high` (both floats, `Inclusive` group), optional `hvac_mode` (`Coerce(HVACMode)`) | `climate/services.py` `SET_TEMPERATURE_SCHEMA`; `required_features` = `[TARGET_TEMPERATURE, TARGET_TEMPERATURE_RANGE]` (either) |
| `climate.set_hvac_mode` | `hvac_mode` (required, `Coerce(HVACMode)`) | `climate/services.py`; no `required_features`; `async_handle_set_hvac_mode_service` |
| `climate.turn_on` | none | `required_features = [ClimateEntityFeature.TURN_ON]` |
| `climate.turn_off` | none | `required_features = [ClimateEntityFeature.TURN_OFF]` |
| `number.set_value` | `value` (required, `Coerce(float)`) | `number/services.py` `_async_set_value` |
| `select.select_option` | `option` (required, `cv.string`) | `select/services.py` -> `async_handle_select_option` |
| `water_heater.set_temperature` | `temperature` (required, float), optional `operation_mode` (string) | `water_heater/services.py`; `required_features = [WaterHeaterEntityFeature.TARGET_TEMPERATURE]` |
| `water_heater.set_operation_mode` | `operation_mode` (required, string) | `required_features = [WaterHeaterEntityFeature.OPERATION_MODE]` |
| `water_heater.turn_on`, `water_heater.turn_off` | none | `required_features = [WaterHeaterEntityFeature.ON_OFF]` |

Details that matter for the drivers:

- **Field name pitfalls**: climate uses `target_temp_high` / `target_temp_low` (not `_max`/`_min`);
  the climate state attributes `min_temp`, `max_temp`, `target_temp_step` are separate from the
  service fields. `HVACMode` values: `off, heat, cool, heat_cool, auto, dry, fan_only`
  (`climate/const.py`). `ClimateEntityFeature` bits: `TARGET_TEMPERATURE = 1`,
  `TARGET_TEMPERATURE_RANGE = 2`, `TARGET_HUMIDITY = 4`, `FAN_MODE = 8`, `PRESET_MODE = 16`,
  `SWING_MODE = 32`, `TURN_OFF = 128`, `TURN_ON = 256`, `SWING_HORIZONTAL_MODE = 512`
  (`climate/const.py:195-206`; consistent with the live `supported_features` 937 =
  1 + 8 + 32 + 128 + 256 + 512 on the maintainer's `lg_thinq` entity, see
  `device-integrations.md`).
  `WaterHeaterEntityFeature`: `TARGET_TEMPERATURE = 1`, `OPERATION_MODE = 2`, `AWAY_MODE = 4`,
  `ON_OFF = 8`.
- **`climate.set_temperature` validation** (`_async_service_temperature_set`): `temperature`
  requires the `TARGET_TEMPERATURE` bit and `target_temp_low` the `TARGET_TEMPERATURE_RANGE` bit,
  otherwise `ServiceValidationError` (`missing_target_temperature_entity_feature` /
  `missing_target_temperature_range_entity_feature`). `temperature` and the low/high pair are
  mutually exclusive (`Exclusive`/`Inclusive`), at least one is needed (`AtLeastOne`); low > high
  raises `low_temp_higher_than_high_temp`. Values are converted from the HA unit system to the
  entity's `native_temperature_unit` and checked against the entity's `min_temp`..`max_temp`:
  outside raises `temp_out_of_range` ("Provided temperature {check_temp} is not valid. Accepted
  range is {min_temp} to {max_temp}."). **There is no step validation or rounding in core**;
  `target_temp_step` is informational only. `hvac_mode` passed to `set_temperature` is *not*
  checked against `hvac_modes` by core; the entity decides (the `lg_thinq` entity does, see
  `device-integrations.md`). The service is typed with the HA unit system: the value sent is
  interpreted in `hass.config.units.temperature_unit`, not in the device's unit.
- **`climate.set_hvac_mode`**: `async_handle_set_hvac_mode_service` raises
  `not_valid_hvac_mode` ("HVAC mode {mode} is not valid. Valid HVAC modes are: {modes}.") unless
  the mode is in the entity's `hvac_modes`. An unknown mode string fails earlier in the schema
  (`Coerce(HVACMode)`) as `invalid_format`.
- **`climate.turn_on` / `turn_off`**: registered only for entities that advertise
  `TURN_ON` / `TURN_OFF`. For an explicitly targeted entity without the bit the call fails with
  `ServiceNotSupported` -> `service_validation_error`, translation key `service_not_supported`,
  message "Entity {entity_id} does not support action climate.turn_on." (`homeassistant/strings.json`).
  Core no longer auto-adds the bits (no backwards-compat helper left in `climate/__init__.py`), so an
  entity that never declares them cannot be switched through `turn_on/off`; use `set_hvac_mode`
  (`off` / a heating mode) for such entities. The base implementation (when the integration does not
  override `turn_on`/`turn_off`) falls back to `set_hvac_mode`: `turn_on` picks the only non-`off`
  mode if exactly two modes exist, otherwise the first of `heat_cool`, `heat`, `cool` that is
  offered, otherwise `NotImplementedError`; `turn_off` uses `HVACMode.OFF` if offered. A device
  that lacks `off` in `hvac_modes` therefore cannot be switched off with `turn_off`.
- **`number.set_value`**: the value is checked against the *converted* `min_value`/`max_value`
  (state-unit range); outside raises `out_of_range` ("Value {value} for {entity_id} is outside
  valid range {min_value} - {max_value}."). The value is then converted to the native unit and
  **clamped** to `native_min_value`/`native_max_value`. **The `step` is not enforced.** Defaults
  when an integration does not set them: min 0.0, max 100.0, step 1.0 (`number/const.py`); the
  `native_step` attribute is informational.
- **`select.select_option`**: raises `not_valid_option` ("Option {option} is not valid for entity
  {entity_id}, valid options are: {options}.") if `option` is not in the entity's `options`
  (case-sensitive; also raised when `options` is empty).
- **`water_heater.set_temperature`**: the `temperature` is unit-converted but **not range-checked**
  in core (unlike climate and number); the integration may reject it. `operation_mode` is passed
  through. `set_operation_mode` raises `operation_list_not_defined` / `not_valid_operation_mode`
  ("Operation mode {operation_mode} is not valid for {entity_id}. Valid operation modes are:
  {operation_list}.") when the mode is not in `operation_list`.

### WebSocket `call_service`: message, result, errors

Request (`websocket_api/commands.py` `handle_call_service`, schema):

```json
{"id": 24, "type": "call_service", "domain": "climate", "service": "set_temperature",
 "target": {"entity_id": "climate.schlafen"},
 "service_data": {"temperature": 22.5},
 "return_response": false}
```

- `domain`, `service`: required strings. `target` (optional) uses `ENTITY_SERVICE_FIELDS`:
  keys `entity_id` (string or list), `device_id`, `area_id`, `floor_id`, `label_id`; no
  entity-registry UUIDs here. `service_data` (optional dict). `return_response` defaults to
  `false`. `hass.services.async_call` merges `target` into `service_data`
  (`service_data.update(target)`, `core.py` `async_call`) before schema validation, so
  `entity_id` inside `service_data` works too. The call is always `blocking=True`: the result
  arrives only after the service handler has finished (the `lg_thinq` climate can take >= 2 s).
- Passing `return_response: true` to a service that does not declare `supports_response`
  (all services in the table above) raises `ServiceValidationError`
  (`service_does_not_support_response`); leave the field out.
- The context is created by the server: `connection.context(msg)` returns
  `Context(user_id=<connection user id>)` (`websocket_api/connection.py:109`). The client can not
  choose the context id; `parent_id` is `None`. Via the Supervisor proxy (`ws://supervisor/core/websocket`)
  the connection to Core is made by the Supervisor with its own access token
  (`supervisor/homeassistant/api.py` `connect_websocket`; `supervisor/api/proxy.py`), so
  `user_id` is the Supervisor's system user, shared by all apps. `UNVERIFIED`: its exact id and
  that the Core-side user is the same for the unix-socket variant; the `user_id` therefore does
  **not** identify SunCore, the context `id` does.
- The Supervisor proxy rejects commands whose `type` starts with `supervisor/` or `hassio/`
  (`{"type":"result","success":false,"error":{"code":"unauthorized","message":"Unauthorized"}}`);
  `call_service` is forwarded unchanged.

Success result (**the result carries the context**):

```json
{"id": 24, "type": "result", "success": true,
 "result": {"context": {"id": "01K…", "parent_id": null, "user_id": "<supervisor user id>"}}}
```

With `return_response: true` (only for services that support it) `result.response` is added.
For entity services the response is keyed by entity id (`helpers/service.py`
`entity_service_call`, `response_data[entity.entity_id] = result`), e.g.
`{"weather.home": {"forecast": [...]}}`; for plain services it is the handler's
dict (`{"prices": {...}}`, `{"data": [...]}`). Phase 4 verification 2026-10-16,
core `5cfe9aad`.
`Context.as_dict()` is `{"id", "parent_id", "user_id"}` (`core.py:1322-1346`; JSON encoder
`helpers/json.py` uses `as_dict`).

Error result (`websocket_api/messages.py` `error_message`):

```json
{"id": 24, "type": "result", "success": false,
 "error": {"code": "service_validation_error", "message": "Validation error: …",
           "translation_key": "…", "translation_placeholders": {…}, "translation_domain": "…"}}
```

`translation_*` are present only when the exception has a `translation_key`. Codes
(`websocket_api/const.py`, mapping in `handle_call_service`):

| Cause | `error.code` | message |
|---|---|---|
| Service does not exist | `not_found` | "Service {domain}.{service} not found." |
| Schema error (`probatio.Invalid`: missing/extra/mistyped field, bad `HVACMode`) | `invalid_format` | `str(err)` of the schema error |
| `ServiceValidationError` (range, unknown option/mode, `ServiceNotSupported`, missing feature, integration validation) | `service_validation_error` | "Validation error: {text}" |
| Other `HomeAssistantError` (e.g. `lg_thinq` connection error) | `home_assistant_error` | `str(err)` |
| Permission | `unauthorized` | "Unauthorized" |
| Anything else | `unknown_error` | `str(err)` |

`ServiceNotFound` and `ServiceNotSupported` are `ServiceValidationError` subclasses;
`ServiceNotFound` is mapped to `not_found` first. A `ServiceValidationError` is a user/input
error (not retried, logged at error level by Core); `home_assistant_error` may be transient.
There is no timeout on the server side of `call_service`; SunCore must apply its own.

### Context propagation and override detection

Facts from `helpers/service.py`, `helpers/entity.py`, `core.py`, `websocket_api/messages.py`:

1. `entity_service_call` calls `entity.async_set_context(call.context)` before running the entity
   method (`_async_handle_entity_calls`). The entity stores the context with a timestamp
   (`_context`, `_context_set`).
2. The next `Entity.async_write_ha_state()` passes `self._context` to `StateMachine.async_set_internal`,
   **but only if the write happens within `CONTEXT_RECENT_TIME_SECONDS = 5` of
   `async_set_context`** (`helpers/entity.py:87, 1328-1333`); otherwise the stored context is
   dropped and the new state gets a fresh `Context(id=<ULID>)` with `user_id = None` and
   `parent_id = None`.
3. The state's context is only replaced when the state actually changes. If a write has the same
   state and the same attributes, no `state_changed` event is fired and the stored state (including its
   context) is kept; only `last_reported` moves (`core.py:2485-2502`).
4. State changes that an integration produces itself (push from the cloud or the device, polling,
   coordinator refresh) are written without a context: `Context(id=<ULID>)`, `user_id = None`,
   `parent_id = None` (`core.py:2482-2483`). A change started in the HA frontend has `user_id` =
   the logged-in user, `parent_id = None` (`connection.context`). A call from our WebSocket
   connection has `user_id` = Supervisor user and `parent_id = None`. A change started by an
   automation or script has a `parent_id` pointing at the run's context; `UNVERIFIED`: where
   exactly `parent_id` is set (`automation/__init__.py` and `helpers/script.py` were searched
   but the construction site was not found in those files, only the propagation of
   `self._context`), so do not rely on `parent_id` semantics.

Consequences, in the order SunCore will use them:

- **A context match is positive proof, a mismatch is not.** If the new state's `c.id` equals the
  `context.id` returned by our `call_service`, the change was caused by our call. The reverse does
  not hold: for push/coordinator integrations the state write after our command usually arrives
  later than 5 s or is triggered by the cloud echo, and then carries a fresh id-only context
  (indistinguishable from a physical remote). **Override detection must therefore be value-based**
  (observed value differs from the last value we wrote and no expected change is pending), with the
  context as supporting evidence only: `c.id == one of our recent ids` -> ours; `user_id` set and
  not the Supervisor user -> human via UI/app; `parent_id` set -> automation; both `None` and id
  not ours -> integration push (physical remote, app, schedule, or a delayed echo of our own write).
- **Race**: a push write within 5 s after our call (including one caused by an unrelated physical
  action) inherits our context id. A physical change right after our command can look like ours.
  The value check catches it only if the value differs from what we wrote.
- If the device never changes state after our command (device refused, offline, same value), there
  is no state change and no context to see; absence of a state update within the settle time is the
  failure signal.

Context in `subscribe_entities` (`const.py:914-918`, `messages.py:195-254`, `core.py:2062-2088`):

- Initial `{"a": {entity_id: {...}}}` and later `a` additions: key `c` is a **string** (the
  context id) when `parent_id` and `user_id` are both `None`, otherwise a dict
  `{"id", "parent_id", "user_id"}`.
- `c` (change) events: `{"c": {entity_id: {"+": {...}, "-": {"a": [...]}}}}`. In `"+"`, key `c`
  is present only if the context changed, and then only carries **the fields that differ from the
  old state's context** (`_state_diff_event`): a plain string `"<id>"` if only `id` differs, in
  which case `parent_id`/`user_id` are *unchanged* from the previous state (they are **not**
  `None`: the same human changing a value twice yields a bare string); otherwise a dict with
  the differing keys among `parent_id`, `user_id` and, if it differs, `id` (a dict can lack
  `id` only if the id is identical, which does not happen for a new state). Example: UI change
  by user U, then a physical-remote push: `{"c": {"user_id": null, "id": "01K…"}}`. The client
  must keep the previous context per entity and merge (dict keys override, string overrides
  `id` only). `parent_id`/`user_id` that did not change are never repeated.
- `lc` is sent when `last_changed` differs, `lu` only when `last_changed` is unchanged but
  `last_updated` differs (attribute-only change). If `lc` is present, `lu` equals `lc`.
- The compressed format has **no `lr`/`last_reported` key** and no parent/user for the old state.

### `last_reported`, `last_updated`, `last_changed`

(`core.py` `State`, `StateMachine.async_set_internal`, `1881-1993`, `2431-2546`)

- `last_changed`: the `state` string changed (also when `force_update` is true).
- `last_updated`: state **or attributes** changed (a new `State` object, a `state_changed` event).
- `last_reported`: every write of the entity, including writes where state and attributes are
  identical. In that case Core does **not** create a new state and fires **no `state_changed`**:
  it mutates `old_state.last_reported = now` and fires `EVENT_STATE_REPORTED`
  (`state_reported`, data `entity_id`, `last_reported`, `old_last_reported`, `new_state`).
  So a polled or coordinator-driven sensor whose value (and attributes) did not change shows a moving
  `last_reported` while `last_changed`/`last_updated` stay put. Entities with `force_update = True`
  fire `state_changed` on every write instead.
- Not delivered by `subscribe_entities` (no `lr` key; it only listens to `state_changed`,
  `commands.py` `handle_subscribe_entities`). `subscribe_events` with `event_type: "state_reported"`
  is **refused by Core**: `Bus.async_listen` raises `HomeAssistantError("Event filter is required
  for event state_reported")` for listeners without a filter (`core.py:1747-1751`), which
  `handle_subscribe_events` does not provide; the client gets `home_assistant_error`
  (`UNVERIFIED` at runtime; derived from the source). `last_reported` is exposed in
  `get_states` and `State.as_dict` (`last_reported` ISO string; equals `last_changed` when
  identical) and in the REST state endpoints.
- Consequence for SunCore: the entity cache can only know "last *changed*". For a slow sensor
  (the 5-minute `smartthinq_sensors` power sensor) an unchanged value must not be treated as
  stale just because no `state_changed` arrived. Options: poll `get_states` for the few
  entities involved at the sensor's period and read `last_reported`, or define staleness per
  entity as a template-configured maximum age counted from the last observed change **plus**
  an optional `get_states` refresh. The Phase 1 cache currently derives age from the
  subscription stream; Phase 3 must add the refresh for slow sensors.

**Implemented in Phase 3 WP9** (`suncore/app/cadence.py`, `suncore/ha/websocket.py`):

- The client parses `c` into `EntityState.context` (string id, or dict; `c` diffs are merged onto
  the previous context, see above) and the `context` dict of `get_states`
  (`context_from_wire`, `merge_context`); `ControlStack.on_update` hands the context id to
  `gate.observe`.
- No extra periodic `get_states` for slow entities: the client's 60 s resync *is* that refresh
  (a full `get_states` whose `last_reported` is compared with the cached copy), so a sensor that
  keeps reporting an unchanged value is refreshed at least every 60 s. A sensor that is quiet
  *and* does not re-report (nothing moves `last_reported`) cannot be told from a dead one, hence
  the cadences: power sensor `power_model.power_update_s` or 60 s, energy counter
  `power_model.counter_update_s` or 300 s (`UNVERIFIED` default); stale after `cadence x 2` in the
  cache and in the watchdog. The control layer only watches the entities the power model reads.

### State attributes and presets the drivers read back (Phase 3 WP6)

Read on 2026-10-14 against the same `dev` commit `c433453ac565f083a4515ec5edfbdbede48ebf24`
(`homeassistant/components/{number,select,climate}/{__init__,const}.py`,
`climate/services.py`, `climate/services.yaml`):

- `number` capability attributes (`NumberEntityCapabilityAttribute`): `min`, `max`, `step`,
  `mode`; the unit is the usual `unit_of_measurement` state attribute.
- `select` capability attribute (`SelectEntityCapabilityAttribute`): `options`; the state is the
  current option (or `unknown`).
- `climate` capability attributes (`ClimateEntityCapabilityAttribute`): `hvac_modes`, `min_temp`,
  `max_temp`, `target_temp_step` (only present when the entity has a step), `preset_modes`
  (with the `PRESET_MODE` feature); state attributes (`ClimateEntityStateAttribute`):
  `current_temperature`, `temperature`, `target_temp_high`, `target_temp_low`, `preset_mode`;
  `supported_features` is the integer bit set.
- `climate.set_preset_mode`: field `preset_mode` (required, string), registered with
  `required_features = [ClimateEntityFeature.PRESET_MODE]` (`climate/services.py`).
- `input_select.select_option` / `input_number.set_value` were **not** read; the drivers use
  only `select` and `number`.

### Verification (Phase 3 WP1)

Read on 2026-10-14, `home-assistant/core` `dev` `c433453ac565f083a4515ec5edfbdbede48ebf24`:
`homeassistant/components/{switch,input_boolean,climate,number,select,water_heater}/{__init__,services,const}.py`
(+ `strings.json` of climate, number, select, water_heater, homeassistant),
`homeassistant/components/websocket_api/{commands,connection,messages,const}.py`,
`homeassistant/helpers/{service,entity,config_validation,json,target}.py`, `homeassistant/core.py`,
`homeassistant/exceptions.py`, `homeassistant/const.py`; `home-assistant/supervisor` `main`
`a2627a1ce37032ecd5c6509813f70cf5b95d32c2`: `supervisor/api/proxy.py`,
`supervisor/homeassistant/api.py`.

`UNVERIFIED` (needs a live HA): the Core-side user id behind the Supervisor proxy; the runtime
error code for `subscribe_events` `state_reported`; whether the 5 s context window is hit in
practice for `lg_thinq` pushes (timing of the cloud echo); `SUBSCRIBE_ALLOWLIST` content
(`auth/permissions/events.py` was not read; the Supervisor user is assumed to be admin, which
bypasses it); behaviour of cores
older than 2026.11 (before the `probatio` change and with the earlier turn-on/off compat code).

## Verification

Verified on 2026-10-12 against `home-assistant/developers.home-assistant`
`docs/api/websocket.md` and `home-assistant/core` (dev):
`homeassistant/components/websocket_api/{commands,messages}.py`,
`homeassistant/const.py`, `homeassistant/components/energy/{websocket_api,
data}.py`, `homeassistant/components/recorder/websocket_api.py`,
`homeassistant/components/weather/{__init__.py,services.yaml}`,
`homeassistant/components/workday/__init__.py`.

Verified on 2026-10-13 (WP1 of Phase 2) against `home-assistant/core` `dev`
HEAD `14f093e`, raw files: `homeassistant/components/config/{entity,device,
area}_registry.py`, `homeassistant/helpers/{entity,device,area}_registry.py`,
`homeassistant/components/websocket_api/{commands,decorators,const}.py`,
`homeassistant/auth/permissions/events.py`,
`homeassistant/components/hassio/__init__.py`,
`homeassistant/components/energy/{data,websocket_api,sensor,helpers}.py`.
This replaces the earlier `UNVERIFIED` markers for the registry list command
names and the `power_config` shape.

Verified on 2026-10-13 (Phase 2 WP5) against `home-assistant/core` `dev`, raw files
`components/websocket_api/commands.py` (`handle_call_service`: with `return_response: true` the
result is `{"context": ..., "response": ...}`; `handle_subscribe_events`: `event_type` string,
empty result, events as `{"id": <subscribe id>, "type": "event", "event": {...}}`),
`components/websocket_api/messages.py` (`cached_event_message`),
`components/config/entity_registry.py` (`list_for_display` takes no fields, `ec` indexes the
delivered `entity_categories`). SunCore uses them in `suncore/ha/websocket.py`
(`subscribe_registry_updates`, `call_action_response`).

`UNVERIFIED`: the shape on cores older than the dev branch (the device
registry's single `config_entry_id` and the unified grid source are recent;
cores 2025.x and early 2026.x may still return `config_entries` only and the
legacy `flow_from`/`flow_to` grid lists, which the parser should tolerate);
`unknown_command` as the error code when the `energy` integration is not
loaded; behaviour of the Supervisor Core proxy for these commands (first run
on HA OS, same open item as `supported_features`).

### Phase 4 verification (2026-10-16, WP1)

Read on 2026-10-16 from `home-assistant/core` `dev`
`5cfe9aad7cb428c2c2ebb449719ae81976780de1` (raw.githubusercontent.com; the
doc sites and `api.github.com` are unreachable): `components/recorder/{websocket_api,statistics,purge,__init__,util}.py`,
`components/recorder/models/statistics.py`, `components/energy/{websocket_api,types}.py`,
`components/forecast_solar/{energy,coordinator,manifest.json}`, `components/victron_remote_monitoring/energy.py`,
`components/weather/{services.py,services.yaml,const.py,entity.py}`,
`components/workday/{binary_sensor,entity,services,const}.py`,
`components/modbus/{services.py,services.yaml,modbus.py,const.py,websocket_api.py,strings.json,__init__.py}`,
`components/{number,select,switch,event}/{services,entity}.py`,
`helpers/{service,config_validation}.py`, `util/{dt,unit_conversion}.py`.
HACS sources at tags: `BJReplay/ha-solcast-solar` `v4.6.1`,
`rany2/ha-open-meteo-solar-forecast` `0.1.33`. PyPI wheels `forecast-solar 5.0.1`
and `open-meteo-solar-forecast 0.1.32`.

Resolved: the `recorder/statistics_during_period` request and response shape and
unit behaviour; `energy/solar_forecast` request, response and the `wh_hours`
key format and period length per provider; `weather.get_forecasts` name,
`supports_response` and response shape; the `workday` state meaning and
`workday.check_date`; `modbus.write_register` fields, multi-register form and
the absence of float32 and of a read action; `number.set_value` and
`select.select_option` payloads (unchanged from Phase 3, now with the dev
`probatio` schemas: `value` is `Coerce(float)`, `option` is a string; `number`
still range-checks against the entity's `min_value`/`max_value` and clamps to the
native range, `select` still validates against `options`); `switch.turn_on/off/toggle`
take no fields.

Still `UNVERIFIED`: the Supervisor proxy forwarding of `recorder/*` and
`energy/*` commands; the temperature unit of a `weather.get_forecasts` response
(taken from the entity's attributes, not read); the vendor meaning of the
Forecast.Solar `watt_hours_period` keys (start vs end of the period, API
unreachable); the HA error for an unknown Modbus hub name; whether a plain Modbus
hub can be added in the UI.
