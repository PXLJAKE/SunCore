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
| `get_config` | – | unit system, time zone, currency, location, version |
| `config/entity_registry/list_for_display` (preferred), `config/entity_registry/list`, `config/device_registry/list`, `config/area_registry/list` | – | setup wizard auto-detection (platform, device, area, category, translation key); verified, shapes in [Registries](#registries) |
| `render_template` | `template` | last resort for integrations without actions |
| `energy/get_prefs` | – | pre-fill from the Energy dashboard |
| `energy/info`, `energy/validate`, `energy/solar_forecast` | – | diagnostics; `energy/solar_forecast` returns forecasts of integrations that implement the energy platform (Forecast.Solar, Solcast) |
| `recorder/statistics_during_period` | `start_time`, `end_time?`, `statistic_ids[]`, `period` ∈ `5minute\|hour\|day\|week\|month\|year`, `types[]` ⊂ `change,last_reset,max,mean,min,state,sum`, `units` | load history for the load forecast, backtesting |
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

## Workday

The `workday` binary sensor indicates whether the current day is a workday
(holidays per country/region). The load forecast uses it to classify days.

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
