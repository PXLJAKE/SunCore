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
| `config/entity_registry/list`, `config/device_registry/list`, `config/area_registry/list` | – | setup wizard auto-detection (device class, unit, area, integration) – command names UNVERIFIED in this pass, they live in `homeassistant/components/config/*.py` |
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

## Energy preferences (`energy/get_prefs`)

`EnergyPreferences = {energy_sources: [...], device_consumption: [...],
device_consumption_water?}`. Source types (`type` field):

- `grid`: `flow_from[{stat_energy_from, stat_cost, entity_energy_price,
  number_energy_price}]`, `flow_to[{stat_energy_to, stat_compensation,
  entity_energy_price, number_energy_price}]`, `cost_adjustment_day`,
  `power_config?`, `stat_rate?`, `name?`
- `solar`: `stat_energy_from`, `config_entry_solar_forecast` (list of config
  entry ids providing forecasts), `stat_rate?`
- `battery`: `stat_energy_from`, `stat_energy_to`, `stat_soc?`, `capacity?`,
  `power_config?`, `stat_rate?`
- `gas`, `water`: not used by SunCore
- `device_consumption[{stat_consumption, name?, included_in_stat?, stat_rate?}]`

`stat_*` fields are statistic ids, which for sensors equal the entity id.
This gives the wizard: grid import/export energy sensors, solar energy
sensors, battery energy in/out (and sometimes SoC + capacity), and per-device
consumption sensors. Power sensors must still be chosen by the user unless
`power_config` is set (its shape is UNVERIFIED).

`energy/save_prefs` is admin-only; SunCore never writes preferences.

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

`UNVERIFIED`: exact registry list command names (`config/*_registry/list`),
shape of `power_config` in energy preferences.
