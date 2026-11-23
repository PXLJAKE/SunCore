# Integrations SunCore reads from or writes to

## PV forecasts

Phase 4 verification (2026-10-16): every section below carries the facts read
from source in WP1 with the repository, tag or commit and path. Core is
`home-assistant/core` `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1`
(2026.11 dev). Quantities: power in kW/W as stated, energy in Wh, time zones as stated.

### Solcast (HACS, `BJReplay/ha-solcast-solar`, Apache-2.0)

Domain `solcast_solar`. Sensors: forecast today/tomorrow/day 3–7 (kWh),
this hour, next hour, next X hours, remaining today, peak power and time,
power now / in 30 min / in 1 h; diagnostics API last polled, API limit, API
used, dampening, accuracy, hard limit.

Forecast attributes on the forecast sensors: `detailedForecast` (half-hourly,
kW) and `detailedHourly`, entries `{period_start, pv_estimate,
pv_estimate10, pv_estimate90[, dampening_factor]}`; per-site variants
`detailedForecast_<site_id>`.

Actions: `solcast_solar.query_forecast_data` (`start_date_time`,
`end_date_time`, optional `undampened`, `site`) returns the series;
`solcast_solar.query_estimate_data` returns estimated actuals.

API limits: 10 calls/day for new hobbyist accounts (50 for old ones), one
call per site per update. **SunCore never calls Solcast directly when the
integration is present**; it reads attributes or calls the query action,
which does not consume API calls.

**Phase 4 verification (2026-10-16)** – `BJReplay/ha-solcast-solar` tag `v4.6.1`
(commit `34e1d007e9d4a9b7a783520d723fcd05adb2ff9a`), `custom_components/solcast_solar/`
`{manifest.json,const.py,forecast.py,coordinator.py,sensor.py,actions.py,services.yaml,
energy.py,solcastapi.py,__init__.py,strings.json}`:

- **Attributes** (`forecast.py` `get_forecast_day`, attached by
  `coordinator.py` `get_sensor_extra_attributes`): on the day sensors
  `total_kwh_forecast_today`, `total_kwh_forecast_tomorrow`,
  `total_kwh_forecast_d3` … (names "Forecast Today", "Forecast Tomorrow",
  "Forecast Day N", `strings.json`; `unique_id` = key). Keys (constants
  `const.py:115,150-151,189-191,324`): `detailedForecast`, `detailedHourly`,
  `dataCorrect`, `dayname`, `analysis`; entries
  `{"period_start": <tz-aware datetime>, "pv_estimate": f, "pv_estimate10": f, "pv_estimate90": f}`
  (`ESTIMATE`, `ESTIMATE10`, `ESTIMATE90`, `PERIOD_START`), with an extra
  `dampening_factor` only when the advanced option asks for it
  (`forecast.py:152-167`). The adapter must ignore unknown keys.
- **Unit and interval**: values are **average power in kW** over the 30-minute
  slot (`energy_kwh = 0.5 * sum(kW)`, `forecast.py:290-291`); `detailedHourly`
  is the mean of two slots per hour. `pv_estimate` = P50, `…10` = P10,
  `…90` = P90. A sensor holds the **whole local day** (up to 48 slots; `dataCorrect`
  is `false` when fewer than 48 slots exist, including DST days).
- **Time zone**: `period_start` is converted with `astimezone(self.api.tz)`;
  `tz` is the **Home Assistant configured time zone**
  (`__init__.py:120`: `dt_util.async_get_time_zone(hass.config.time_zone)`, UTC
  fallback). Serialised by HA as ISO 8601 with the local offset.
- **Which attributes exist**: `detailedForecast` and `detailedHourly` are both
  default **on** (`__init__.py:157-158`, options `attr_brk_halfhourly`,
  `attr_brk_hourly`); the user can switch them off, then the adapter falls back to
  the action. Per-site keys `detailedForecast_<site>` only with the
  "site breakdown" option. The attributes are excluded from the recorder
  (`UNRECORDED_ATTRIBUTES`).
- **Number of day sensors**: advanced option `forecast_day_entities`, minimum 8,
  maximum 14, default `DEFAULT_FORECAST_DAY_SENSORS = 8` (`const.py:138,537-542`);
  the day sensors are `today`, `tomorrow` and `total_kwh_forecast_d3` …
  `d{N-1}` (`coordinator.py:135-137`: `range(3, advanced_day_entities)`), so by
  default **d3–d7** (resolves the earlier `UNVERIFIED` default).
- **Action `solcast_solar.query_forecast_data`** (`actions.py:370-373,397-424`,
  `services.yaml:22-37`): `SupportsResponse.ONLY`; fields `start_date_time`,
  `end_date_time` (both default to *now*, i.e. an empty/invalid range unless
  given), `undampened` (bool, default false), `site` (resource id, `_` and `-`
  accepted, default `all`). Response **`{"data": [ {"period_start", "pv_estimate",
  "pv_estimate10", "pv_estimate90"}, ... ]}`**, `period_start` in the HA time
  zone, 30-minute slots, kW. An invalid range raises `ServiceValidationError`
  (`invalid_query_range`), an unknown site `not_a_site`.
- **Energy platform**: Solcast implements `energy.py` (`async_get_solar_forecast`)
  with `wh_hours` at half-hour keys = `period_start`, value = `kW * 500` (Wh per
  30 min) for **one** confidence level chosen by the integration option, rounded to
  integers (`forecast.py:945-`). No P10/P90 through that path.
- **Licence**: Apache-2.0 (`LICENSE` at the tag).

### Forecast.Solar (Core)

Sensors: energy today/remaining/tomorrow/this hour/next hour (kWh), power
now (W), peak times; disabled by default power in 1/12/24 h. Updates hourly
(free) or every 30 min (API key). Free tier: one plane, lower resolution.
The page does not document series attributes; the integration implements the
`energy` platform, so `energy/solar_forecast` returns its hourly `wh_hours`.

**Phase 4 verification (2026-10-16)** – `home-assistant/core` `dev` `5cfe9aad…`,
`homeassistant/components/forecast_solar/{energy.py,coordinator.py,manifest.json}`
(requirement `forecast-solar==5.0.1`, domain `forecast_solar`, `iot_class cloud_polling`);
PyPI wheel `forecast-solar 5.0.1` `forecast_solar/models.py`:

- `energy.py` (24 lines): `async_get_solar_forecast(hass, config_entry_id)` returns
  `{"wh_hours": {timestamp.isoformat(): val for timestamp, val in
  entry.runtime_data.data.wh_period.items() if val != 0 or
  (timestamp.hour, timestamp.minute, timestamp.second) != (0, 0, 0)}}`; `None`
  when the entry is unknown or not loaded. **Field name `wh_hours` verified.**
- `wh_period` = the API's `watt_hours_period` (`models.py` `Estimate.from_dict`):
  an int Wh per period, keys are `datetime.fromisoformat` of the API response,
  i.e. ISO strings with the offset of the plant's time zone. The library treats
  a key as the **start** of the period (`_interval_value_sum` sums
  `begin <= key < end`). Period length: hourly for the free tier; finer for paid
  plans (`UNVERIFIED`, API docs unreachable). The free tier also needs a
  rate-limit-aware refresh: the coordinator polls hourly, 30 min with an API
  key (`coordinator.py:79-82`).
- The attribute route does not exist; no quantiles. `energy/solar_forecast`
  only lists the entry when the user linked it in the Energy dashboard
  (`config_entry_solar_forecast`).

### Open-Meteo Solar Forecast (HACS, `rany2/ha-open-meteo-solar-forecast`, Apache-2.0)

Per-array declination, azimuth, module power, DC efficiency, inverter
limit, damping, horizon shading.

**Phase 4 verification (2026-10-16)** – tag `0.1.33` (commit
`7af5d681c3ffeb7d8f678fd4eadd7f5aebd2a1ac`), `custom_components/open_meteo_solar_forecast/`
`{manifest.json,const.py,sensor.py,coordinator.py,energy.py,strings.json,services.yaml}`,
README; PyPI wheel `open-meteo-solar-forecast 0.1.32`:

- **Domain `open_meteo_solar_forecast`** (`manifest.json`, `const.py`), config
  flow, `iot_class cloud_polling`, update interval 30 min (`coordinator.py:298`),
  requirement `open_meteo_solar_forecast==0.1.32`. Service device "Solar
  production forecast", manufacturer "Open-Meteo". The only action is
  `update_array_location`.
- **Sensors** (`sensor.py`): `energy_production_today`, `…_today_remaining`,
  `…_tomorrow`, `…_d2` … `…_d7` (Wh, `suggested_unit` kWh), `energy_current_hour`,
  `energy_next_hour`, `power_production_now` (W), `power_production_next_15minutes`,
  `…_30minutes`, `…_hour`, `…_12hours`, `…_24hours` (W, **disabled by default**),
  `power_highest_peak_time_today/tomorrow` (timestamp). `unique_id` =
  `<entry_id>_<key>`; the entity id is forced to
  `sensor.<slug(entry title)>_<key>` (`sensor.py` `self.entity_id = …`).
- **Series attributes** (`sensor.py` `extra_state_attributes`), on
  `energy_production_today`, `energy_production_tomorrow` and
  `energy_production_d2` … `d7` only, restricted to that local date:
  - `watts`: `{iso_timestamp: W}` (int; instantaneous power),
  - `wh_period`: `{iso_hour_start: Wh}` (hourly; mean 15-min power over the hour,
    numerically Wh of that hour),
  - `wh_period_15m`: `{iso_interval_start: Wh}` (float, `W × 0.25` per half-open
    interval `[t, t+15 min)`, inverter-clipped).
  Constants `ATTR_WATTS`, `ATTR_WH_PERIOD`, `ATTR_WH_PERIOD_15M` (`const.py`). Keys
  are ISO 8601 strings with a fixed UTC offset of the location. **Best series for
  SunCore: `wh_period_15m`** (native 15-minute grid, energy per interval).
  A single deterministic estimate: **no P10/P90**.
- **Energy platform**: `energy.py` implements `async_get_solar_forecast`, returning
  `{"wh_hours": {ts.isoformat(): wh_period value}}` (hourly), so the forecast is
  also available through `energy/solar_forecast` once linked in the Energy dashboard.
- **Azimuth in the integration**: UI 0–360 with 0 = North, 90 = East, 180 = South,
  270 = West (README, `strings.json`); the coordinator subtracts 180 before calling
  the library (`coordinator.py:230-233`), which uses the Open-Meteo API convention
  (see the built-in fallback section).

### Built-in fallback: Open-Meteo API + plant model

Open-Meteo: free for non-commercial and open-source use, fair use, contact
above 10 000 requests/day; data CC BY 4.0 (attribution "Weather data by
Open-Meteo.com" must be shown in the UI); code AGPL-3.0 (irrelevant, we only
call the API).

**Phase 4 verification (2026-10-16)** – `open-meteo/open-meteo` `main`
`01ceed5b028fb6ac19794c3310aed5a4f7aa6d4a` (`version.txt` `1.7.0`):
`openapi/forecast.yml`, `Sources/App/Helper/Solar/GlobalTilitedIrradiance.swift`,
`Sources/App/Controllers/VariableHourly.swift:1754,1766`,
`Sources/App/Helper/Reader/GenericReaderDerived.swift:54`, `README.md`, `LICENSE`.
`api.open-meteo.com` and `open-meteo.com` are **not reachable** from the session
(proxy `CONNECT 403`), so nothing was tested against the live API.

- **Parameters exist**: query parameters `tilt` (float, default 0) and `azimuth`
  (float, default 0) in `openapi/forecast.yml:574-587`; variables
  `global_tilted_irradiance` and `global_tilted_irradiance_instant` are listed for
  `hourly` (`:188,194`) and for `minutely_15`; `forecast_days` 0–16, default 7
  (`:543-551`).
- **Azimuth convention – the repo contradicts itself.** The OpenAPI text says
  "North=0, East=90, South=180, West=270" (`forecast.yml:585`), but the
  implementation documents and uses **0° = south, −90° = east, +90° = west**
  (`GlobalTilitedIrradiance.swift:12`: "Azimuth 0° south, -90° = east, +90° = west.
  Tilt should be from 0° horizontal to 90° vertical"), the value is passed
  through unchanged (`VariableHourly.swift:1754: azimuth: options.azimuth`;
  `GenericReaderDerived.swift:54`: `azimuth ?? 0`), and the maintained client
  `open-meteo-solar-forecast 0.1.32` states the same and rejects values outside
  [-180, 180] ("Open-Meteo convention: 0 = South, -90 = East, 90 = West, +-180 =
  North. To convert a compass bearing … subtract 180"). **SunCore uses the source
  convention: `api_azimuth = compass_bearing - 180`** (compass: N 0, E 90, S 180,
  W 270), tilt 0 = flat, 90 = vertical. `NaN` = tracking axis (client sends
  `"nan"`). The OpenAPI description is considered wrong; a live check with a
  south-facing and a west-facing array on a clear day stays a Phase 4 acceptance
  item (`UNVERIFIED` against the live API).
- **Time semantics**: radiation variables are **backward averages**
  ("Direct and diffuse radiation must be backwards averaged", Swift doc comment);
  a `minutely_15` timestamp is the *end* of the interval, and the client moves it
  back by 15 minutes (`time_start = time - 15 min`,
  `open_meteo_solar_forecast.py:557`). `*_instant` variants are instantaneous.
  Request used by the client: `minutely_15=temperature_2m,global_tilted_irradiance,
  global_tilted_irradiance_instant,diffuse_radiation,…,snow_depth`,
  `timezone=auto`, `timeformat=unixtime`, `forecast_days`, `past_days`.
- **Terms and licence (README, `main` at the SHA above)**: "Open-Meteo APIs are
  free for open-source developers and non-commercial use. We do not restrict
  access, but ask for fair use. If your application exceeds 10'000 requests per
  day, please contact us. We reserve the right to block applications and IP
  addresses that misuse our service. For commercial use … please contact us."
  Data: "Attribution 4.0 International license (CC BY 4.0)"; attribution link next
  to every place the data is displayed, e.g. `<a href="https://open-meteo.com/">
  Weather data by Open-Meteo.com</a>`. Source code AGPL-3.0 (`LICENSE`). The
  separate terms page (`open-meteo.com/en/terms`) is unreachable and stays
  `UNVERIFIED`; the README text is the verified part.
- **Consequence**: the parameters and licence are verified from the source; the
  live response was not seen. Per plan question 5 the fallback may ship only as an
  explicit opt-in (off by default), with the attribution link in the UI.

## Prices

### Tibber (Core)

Price sensor per home with `max_price`/`min_price` attributes; separate
"total price (energy + taxes)" sensor; monthly cost/consumption sensors; with
Pulse/Watty: real-time power (W), phase currents and voltages. Action
`tibber.get_prices` with optional `start`, `end` (ISO strings, local time,
default today) → `{"prices": {<home name>: [{"start_time", "price"}]}}`.
No `price_level` in the response. Whether `price` includes taxes is not
stated on the page → the wizard asks (BRIEF 4.3 "includes fees?") and shows
a live comparison with the total-price sensor.

**Phase 4 verification (2026-10-16)** – re-read `home-assistant/core` `dev`
`5cfe9aad…` `components/tibber/services.py` (152 lines): unchanged from
[`device-integrations.md`](device-integrations.md). Action `tibber.get_prices`,
`SupportsResponse.ONLY`, fields `start`/`end` (optional strings, `parse_datetime`,
local time), response `{"prices": {"<home nickname>": [{"start_time": "<startsAt string>",
"price": <float>}]}}`, items filtered `start <= t < end`, `price` = `home.price_total`
(**energy plus tax**, per the library's `total`), default window = local today,
`start >= end` -> `end = start + 1 day`, refetch when the cache lacks the next day
after 13:00 local. All matches the earlier note; the price includes taxes
(`price_total`), which answers plan question 9 for the action (a supplier
contract with additional fees stays the user's call).

### Nord Pool (Core)

Sensors current/previous/next price, daily average/min/max, peak and
off-peak blocks, currency, exchange rate, "tomorrow available". Action
`nordpool.get_prices_for_date` (`config_entry`, `date`, `areas`, `currency`)
→ per area `[{start, end, price}]` in currency/MWh (divide by 1000).
Automatically switches to 15-minute market time units; mixed 60/15-minute
series are possible during the transition. `nordpool.get_price_indices_for_date`
exists but is undocumented on the page.

**Phase 4 verification (2026-10-16)** – `home-assistant/core` `dev` `5cfe9aad…`
`components/nordpool/{services.py,services.yaml,sensor.py,coordinator.py,strings.json,manifest.json}`
(`pynordpool==0.4.0`, PyPI wheel read: `__init__.py`, `util.py`, `model.py`):

- **Action `nordpool.get_prices_for_date`** (`services.py:62-70,168-175`):
  `SupportsResponse.ONLY`; fields `config_entry` (required, config entry id),
  `date` (required, `cv.date` `YYYY-MM-DD`; "only dates from two months in the
  past to one day in the future", `strings.json`), `areas` (optional list,
  upper-cased and checked against the library's `AREAS`; **empty = the entry's
  configured areas**), `currency` (optional, `Currency` enum value, upper-cased;
  default = the entry's currency). `get_price_indices_for_date` is the same plus
  `resolution` ∈ 15/30/60 (default 60).
- **Response**: `{ "<AREA>": [ {"start": "<iso>", "end": "<iso>", "price": <float>}, ... ] }`
  for each requested area (`services.py:140-150`). `start`/`end` are
  `datetime.isoformat()` of datetimes parsed with `fromisoformat` from the API's
  `…Z` strings, i.e. **UTC with `+00:00`**. `price` is the raw `entryPerArea[area]`
  value of the Nord Pool day-ahead API. An empty response returns `{area: []}`;
  authentication/connection errors raise `ServiceValidationError`
  (`authentication_error` / `connection_error`).
- **Unit**: **currency per MWh** – the integration's own sensors divide every
  such value by 1000 and label the result `"<currency>/kWh"` (`sensor.py:38,205-243,347,413`).
  The action returns the undivided MWh value; the adapter divides by 1000.
  The values are the day-ahead market price per area; VAT, grid fees and levies are not part
  of them (no such field in the source).
- **Meaning of `date` (zone)**: the action passes `date` as `YYYY-MM-DD` to the library
  (`home-assistant/core@5cfe9aad7cb428c2c2ebb449719ae81976780de1`
  `homeassistant/components/nordpool/services.py:112-125`: `datetime.combine(asked_date,
  dt_util.utcnow().time())`), which only formats it with `strftime("%Y-%m-%d")` as the `date`
  query parameter of Nord Pool's `DayAheadPrices` (`pynordpool 0.4.0` `__init__.py:106-127`) and
  keys the raw response by `deliveryDateCET` (`__init__.py:102`). So `date` is read as the **CET
  delivery day** (Central European rules, i.e. `Europe/Berlin`), not the Home Assistant day; the
  template sets `date_tz: Europe/Berlin`. Read from source only, the query semantics of the
  Nord Pool API itself were not seen live (`UNVERIFIED`).
- **Resolution**: slot length = `end - start` per entry (the sensors compute
  `resolution = entry.end - entry.start`, `sensor.py:58`), 15 or 60 minutes; a
  coordinator tick every 15 minutes (`coordinator.py:57-73`). The adapter must not
  assume a fixed length; a series may mix lengths across the transition date.
- The action only returns what the Nord Pool day-ahead call has for that delivery
  date (today; tomorrow after publication, `UNVERIFIED` exact time, afternoon CET
  expected).

### ENTSO-E (HACS, `JaccoR/hass-entso-e`, Apache-2.0)

Day-ahead prices for European zones; sensors current/next/avg/min/max with a
price list attribute; optional template for fees/VAT; needs a free
Transparency Platform API key (requested by e-mail).

**Phase 4 verification (2026-10-16)** – tag `v0.7.5` (commit
`cbdf67ac0e73fd685a02e27ad7b6bef2c5af7ca6`), `custom_components/entsoe/{manifest.json,
sensor.py,coordinator.py,const.py,api_client.py}`:

- Domain `entsoe`, `iot_class cloud_polling`; sensors (`sensor.py:50-111`) keys
  `current_price`, `next_hour_price`, `min_price`, `max_price`, `avg_price`,
  `percentage_of_max`, `percentage_of_range`, `highest_price_time_today`,
  `lowest_price_time_today`; `unique_id`
  `entsoe.<key>` (or `entsoe.<name>_<key>`).
- **The price list sits only on the `avg_price` sensor** ("Average electricity
  price", `sensor.py:269-279`), attributes **`prices_today`**, **`prices_tomorrow`**,
  **`prices`** (today+tomorrow, or yesterday+today when only two days exist).
  Entry shape (`coordinator.py:250-256`): `{"time": "<str(datetime)>", "price": <float>}`;
  `time` is `str()` of a timezone-aware **local** datetime, i.e.
  `"2026-10-16 00:00:00+02:00"` (space separator, not `T`; `fromisoformat`
  accepts it), slot **start**. Slot length is the configured `period`
  (`PT60M` default or `PT15M`, `const.py:22,33`) and is not in the entry; infer
  it from the spacing.
- **Unit**: `"<currency>/<energy_scale>"` with `energy_scale` ∈ `kWh` (default) or
  `MWh` (`const.py:21,32`, `sensor.py:57`). **The price already includes the
  user's modifier template and VAT** (`coordinator.py:calc_price`: template result
  `* (1 + vat)`), so it can be market price or end price depending on the
  user's template; the wizard must ask.

### EPEX Spot (HACS, `mampfes/ha_epex_spot`, MIT)

Sources: aWATTar, Energyforecast.de, SMARD.de, smartENERGY.at, Tibber,
ENTSO-E, Energy-Charts.info, Hofer Grünstrom. Total-price sensor `data`
attribute: `[{start_time, end_time, price_per_kwh}]`; also market price,
average, median, min, max, quantile, rank sensors.

**Phase 4 verification (2026-10-16)** – tag `4.1.0` (commit
`2aad1eb796f9ae8b1607f5c3d33198fcc454e980`), `custom_components/epex_spot/{manifest.json,
sensor.py,const.py,localization.py,SourceShell.py,common.py,EPEXSpot/…}`, README:

- Domain `epex_spot`. Sensors "Total Price" (`key="Total Price"`) and "Market
  Price" (`key="MarketPrice"`) both expose the attribute **`data`**: list of
  `{"start_time": "<local ISO 8601>", "end_time": "<local ISO 8601>",
  "price_per_kwh": <float>}` (`const.py:6-8,14`; `sensor.py:69-82,107-119`;
  `dt_util.as_local(...).isoformat()`). The price key is `price_per_kwh` for EUR
  **and** GBP (`localization.py`).
- **Unit**: `"€/kWh"` (the literal euro sign, `const.py:57`) or `"£/kWh"`, not
  `EUR/kWh`. Slot length = `end_time - start_time`; configurable per entry
  (`duration`, default 60, Energy-Charts accepts 15 or 60).
- **Total vs market**: Total = market + percentage surcharge, then + absolute
  surcharge, then × (1 + VAT) (`SourceShell.to_total_price`), except for the Tibber
  source, which is returned as is. Market price excludes all of that. The
  `data` of the total sensor is a **user-configured all-in price**; of the market
  sensor the raw market price (kWh, converted from MWh by the source clients).
- `unique_id` = `<source.unique_id> <key>`. Licence MIT (`LICENSE`).

### Octopus Energy (HACS, `BottlecapDave/HomeAssistant-OctopusEnergy`, MIT)

Electricity/gas rate sensors with rate information and intelligent-tariff
controls.

**Phase 4 verification (2026-10-16)** – tag `v19.2.1` (commit
`ccfbccc3a6e6b75d48a955501afd232d9a9f23f7`), `custom_components/octopus_energy/`
`{manifest.json,const.py,electricity/{current_rate,rates_current_day,rates_next_day}.py,
coordinators/{__init__,electricity_rates}.py,utils/{__init__,conversions}.py}`;
core `components/event/entity.py`:

- Domain `octopus_energy`. **Current rate sensor** `electricity/current_rate.py`:
  state `value_inc_vat` in **GBP/kWh** (`native_unit_of_measurement "GBP/kWh"`,
  pounds, rounded 6 decimals from pence), attributes `mpan`, `serial_number`,
  `is_export`, `is_smart_meter`, `tariff`, `start`, `end`, `is_capped`,
  `is_intelligent_adjusted`, `current_day_min_rate`, `current_day_max_rate`,
  `current_day_average_rate`; `unique_id`
  `octopus_energy_electricity_<serial>_<mpan>[_export]_current_rate`. No rate list
  on that sensor.
- **The rate lists are on event entities** `rates_current_day.py` /
  `rates_next_day.py` / `rates_previous_day.py` (`unique_id`
  `…_current_day_rates`, `…_next_day_rates`, `…_previous_day_rates`). Core's
  `EventEntity.state_attributes` returns `{"event_type": …} | last_event_attributes`
  (`event/entity.py:145-152`), so the last event's data appear as attributes:
  **`rates`** = list of `{"start": <local datetime>, "end": <local datetime>,
  "value_inc_vat": <GBP/kWh float>[, "is_capped": bool][, "is_intelligent_adjusted": bool]}`
  (`utils/__init__.py:149-170` `private_rates_to_public_rates`), plus
  `min_rate`, `max_rate`, `average_rate`, `mpan`, `serial_number`, `tariff_code`
  (`coordinators/__init__.py:77`). The same payload is also fired on the bus as
  `octopus_energy_electricity_current_day_rates` / `…_next_day_rates` /
  `…_previous_day_rates` (`const.py:134-136`). The event only fires **when the
  rates changed** (`has_rates_changed`), and the entity restores the last event
  after a restart.
- Slot length is the rate's `end - start` (30 minutes for the usual Octopus
  tariffs; not read from a tariff list). Export rates have their own
  `…_export_…` entities (`_export_id_addition`).
- Mapping consequence: SunCore reads the `rates` attribute of the current-day and
  next-day event entities (merge by `start`), unit GBP/kWh incl. VAT.

### Built-in fallback: Energy-Charts / aWATTar

Both APIs were unreachable from this session. `api.energy-charts.info`
`/price` (bidding zone, start, end → `unix_seconds[]`, `price[]`, `unit`,
`license_info`) and `api.awattar.de/v1/marketdata` (`data[{start_timestamp,
end_timestamp, marketprice, unit}]`) are UNVERIFIED including their terms of
use.

**Phase 4 verification (2026-10-16)** – direct access: `curl` through the agent
proxy to `api.energy-charts.info`, `www.energy-charts.info`, `api.awattar.de`,
`api.awattar.at` returned `000` (CONNECT refused); **no API response and no terms
page could be read**. Secondary evidence from the clients in `mampfes/ha_epex_spot`
tag `4.1.0` (commit `2aad1eb796f9ae8b1607f5c3d33198fcc454e980`), which parse the
real responses:

- Energy-Charts (`EPEXSpot/EnergyCharts/__init__.py`): `GET https://api.energy-charts.info/price`
  with query `bzn` (e.g. `DE-LU`), `start`, `end` (ISO dates); JSON arrays
  `unix_seconds[]` (slot **start**, UTC seconds), `price[]` (**EUR/MWh**, divided by
  1000 by the client), `unit` (default `"EUR/MWh"` in the client). Slot length from
  the spacing of `unix_seconds` (15 or 60 minutes). `license_info` is **not**
  read by the client; presence in the response `UNVERIFIED`.
- aWATTar (`EPEXSpot/Awattar/__init__.py`): `GET https://api.awattar.{de|at}/v1/marketdata`
  with `start`, `end` as epoch **milliseconds**; JSON `data[]` with
  `start_timestamp`, `end_timestamp` (ms), `marketprice` (**EUR/MWh**), `unit`
  (the client asserts `"Eur/MWh"` case-insensitively). Hourly slots.
- **Terms of use, licence, rate limits, attribution of both APIs: `UNVERIFIED`.**
  Per plan question 5 the fallbacks are therefore **not shipped in Phase 4**; the
  HACS EPEX Spot integration (which uses these sources) is the supported route.

## Consumers

### LG ThinQ (Core)

Air conditioner: `climate` entity (HVAC mode, target temperature, cool/heat
set-points, fan mode, swing, preset), sensors (humidity, air quality,
filter, timers), switches (power, lighting, energy saving, air clean),
selects. Energy: `ThinQEnergySensorEntityDescription` creates
`energy_usage_today` (Wh, refreshed hourly, resets at midnight),
`energy_usage_yesterday`, `energy_usage_this_month`, `energy_usage_last_month`
for any device whose API reports `ENERGY_USAGE`; refreshes are aligned to a
random time between 01:00 and 05:59 per device. **No power (W) sensor.**
Status updates are pushed (cloud), legacy models every 5 min. This matches
the maintainer's system and is the reference case for the "power model
without power sensor" (BRIEF 4.5): hourly Wh deltas with coarse quantisation.

### Kostal Plenticore (Core, REST API via `pykoplenti`)

Entities: numbers `Battery:MinSoc` (5–100 %, step 5),
`Battery:MinHomeComsumption` (sic, 50–38000 W),
`Inverter:ActivePowerLimitation` (0–10000 W); select "Battery charging /
usage mode" (`None` | `Battery:SmartBatteryControl:Enable` |
`Battery:TimeControl:Enable`); switches battery strategy, shadow management,
manual charge (installer only). Process data (disabled by default): grid
power (+ import), home power by source, battery power (+ discharge, − charge),
SoC, PV/DC per string, energy totals. **No charge/discharge power
set-point over REST** – active control needs Modbus (see
`kostal-plenticore-modbus.md`). Useful for SunCore: min-SoC and min-home-
consumption numbers as a "soft" battery adapter without installer access,
and `ActivePowerLimitation` for export limiting (sign/meaning UNVERIFIED –
the HA number is 0–10000 W, the maintainer's inverter is 17 kW; check the
inverter's own limit semantics in Phase 4).

**Phase 4 verification (2026-10-16)** – `home-assistant/core` `dev` `5cfe9aad…`
`components/kostal_plenticore/{manifest.json,number.py,select.py,switch.py,sensor.py,coordinator.py}`
(`pykoplenti==1.5.0`, `iot_class local_polling`, domain `kostal_plenticore`):

- **Number entities** (`number.py:35-85`, class `PlenticoreDataNumber`): described
  by `key`, `module_id`, `data_id`; **no `translation_key`**; `entity_category
  config`; **all three `entity_registry_enabled_default=False`** (the user must
  enable them); `mode box`; `unique_id = "<entry_id>_<module_id>_<data_id>"`,
  `name = "<entry title> <description name>"`.

  | `key` | Name | `module_id` / `data_id` | Unit | Default min / max / step |
  |---|---|---|---|---|
  | `battery_min_soc` | Battery min SoC | `devices:local` / `Battery:MinSoc` | % | 5 / 100 / 5 |
  | `battery_min_home_consumption` | Battery min Home Consumption | `devices:local` / `Battery:MinHomeComsumption` | W (power) | 50 / 38000 / 1 |
  | `active_power_limitation` | Active Power Limitation | `devices:local` / `Inverter:ActivePowerLimitation` | W (power) | 0 / 10000 / 1 |

  `min`/`max` are then **overwritten with the values the inverter reports for the
  setting** (`SettingsData.min/max`, `number.py:150-153`), so read the `min`/`max`
  attributes of the live entity. The entity is created only when the inverter
  lists the setting. The state is polled every **30 s** (`SettingDataUpdateCoordinator`).
  `step` is informational (core does not enforce it, see Phase 3 notes).
- **Writes go to the inverter immediately**: `async_set_native_value` ->
  `coordinator.async_write_data(module_id, {data_id: value})` ->
  `client.set_setting_values` (REST), then `async_refresh()`. **A failed write is
  silent**: `async_write_data` catches `ApiException` and returns `False`
  (`coordinator.py:152-166`), the caller ignores the result, so
  `number.set_value` succeeds from HA's point of view. Read back the entity (after
  the forced refresh) before believing a write.
- **Select "Battery Charging / Usage mode"** (`select.py:27-37`):
  `key="battery_charge"`, `module_id devices:local`, options exactly
  `["None", "Battery:SmartBatteryControl:Enable", "Battery:TimeControl:Enable"]`,
  `entity_category config`, enabled by default, `unique_id "<entry_id>_devices:local"`;
  created only if both Enable settings exist on the inverter. `select_option`
  writes `"0"` to **both** Enable settings first, then `"1"` to the chosen one
  (`select.py:123-131`; not atomic) and then only calls `async_write_ha_state()`
  **without a refresh**: the state shown for up to 30 s (poll interval) is the
  *old* coordinator value. Read-back needs a wait of at least one poll interval
  (or `homeassistant.update_entity`).
- **Switches** (`switch.py:35-58`): `Battery:Strategy` (`1` = "Automatic", `2` =
  "Automatic economical"), `Battery:ManualCharge` (installer only; `1`/`0`).
- **Process data for SoC and power** (sensors poll every **10 s**,
  `sensor.py:813`; `unique_id "<entry_id>_<module_id>_<data_id>"`): `devices:local:battery`
  / `SoC` ("Battery SoC", %, device_class battery), `devices:local:battery` / `P`
  ("Battery Power", W, power, measurement), `devices:local:battery` / `Cycles`,
  `devices:local` / `HomeBat_P` ("Home Power from Battery"), `PV2Bat_P` ("PV to
  Battery Power"), `Grid_P` ("Grid Power"), `HomeGrid_P`. The HA source gives **no
  sign comment** for `Battery Power` / `Grid Power`; the earlier note
  "+ discharge, − charge" is carried over and stays `UNVERIFIED` (confirm on the
  live entity before relying on it).
- `Inverter:ActivePowerLimitation` semantics (limit in W of what? absolute AC
  limit or percent-like) are not in the HA source: still `UNVERIFIED`.

### SunSpec (HACS, `CJNE/ha-sunspec`, MIT)

Reads SunSpec models over Modbus TCP, sensors only, no writes. This is what
the maintainer uses for inverter data. Not a control path.

## Verification

Verified on 2026-10-12 against `BJReplay/ha-solcast-solar` README;
`home-assistant/home-assistant.io` `source/_integrations/{forecast_solar,
tibber,nordpool,lg_thinq,kostal_plenticore}.markdown`; `home-assistant/core`
`homeassistant/components/{tibber/services.py,lg_thinq/sensor.py,
kostal_plenticore/{number,select}.py}`; `rany2/ha-open-meteo-solar-forecast`,
`JaccoR/hass-entso-e`, `mampfes/ha_epex_spot`,
`BottlecapDave/HomeAssistant-OctopusEnergy`, `CJNE/ha-sunspec` READMEs;
`open-meteo/open-meteo` README.

**Phase 4 verification (2026-10-16, WP1).** All reads from GitHub raw files and
git clones (`raw.githubusercontent.com`, `git clone` of github.com) plus PyPI
wheels; `api.github.com`, the documentation sites, `open-meteo.com`,
`api.energy-charts.info` and `api.awattar.de` were unreachable (proxy `CONNECT 403`).
Pinned sources:

| Repository | Ref (SHA) |
|---|---|
| `home-assistant/core` | `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1` (tag `2026.10.2` = `8d7fd8b68677c2585be36ec86043308f403875b8`) |
| `BJReplay/ha-solcast-solar` | tag `v4.6.1` `34e1d007e9d4a9b7a783520d723fcd05adb2ff9a` |
| `rany2/ha-open-meteo-solar-forecast` | tag `0.1.33` `7af5d681c3ffeb7d8f678fd4eadd7f5aebd2a1ac` |
| `JaccoR/hass-entso-e` | tag `v0.7.5` `cbdf67ac0e73fd685a02e27ad7b6bef2c5af7ca6` |
| `mampfes/ha_epex_spot` | tag `4.1.0` (commit `2aad1eb796f9ae8b1607f5c3d33198fcc454e980`) |
| `BottlecapDave/HomeAssistant-OctopusEnergy` | tag `v19.2.1` `ccfbccc3a6e6b75d48a955501afd232d9a9f23f7` |
| `open-meteo/open-meteo` | `main` `01ceed5b028fb6ac19794c3310aed5a4f7aa6d4a` (`version.txt` 1.7.0) |
| PyPI | `forecast-solar 5.0.1`, `open-meteo-solar-forecast 0.1.32`, `pynordpool 0.4.0` |

Resolved in this pass (earlier `UNVERIFIED` removed): Forecast.Solar series field
name (`wh_hours`); Open-Meteo Solar Forecast domain and attributes; Open-Meteo
`tilt`/`azimuth`/`global_tilted_irradiance` parameters and licence/terms
text (README); Octopus `rates` attribute names; ENTSO-E and EPEX Spot attribute
names; Nord Pool action shape and unit; Solcast default number of day sensors;
Kostal number/select/switch definitions and the immediate-write behaviour.

Still `UNVERIFIED`: the live Open-Meteo API response and its `azimuth` convention
against the contradictory OpenAPI text (source says 0 = south); Open-Meteo's
separate terms page; Energy-Charts and aWATTar API behaviour, `license_info`, terms
and rate limits (only the field names used by `ha_epex_spot` are known); the vendor
meaning (start vs end) of Forecast.Solar `watt_hours_period` keys and its
sub-hourly periods on paid plans; the sign of Kostal `Battery Power` /
`Grid Power` sensors; the semantics of `Inverter:ActivePowerLimitation`; the
exact publication time of tomorrow's day-ahead prices (Tibber, Nord Pool); live
entity ids of all integrations (derived from code only).

## Summary: Phase 4 interfaces and consequences for the adapters

| Interface | Verified? | Source (repo@ref path) | Consequence for the adapter |
|---|---|---|---|
| `recorder/statistics_during_period` | yes | `core@5cfe9aad` `components/recorder/websocket_api.py:288-311`, `statistics.py` | Send ISO 8601 with offset, `period: "hour"` for >10 days (`5minute` only 10 days), pass `units: {"power":"W","energy":"Wh"}` (response has no unit; default = entity state unit), `start`/`end` are epoch ms, ids without data are absent |
| `recorder/list_statistic_ids` | yes | `core@5cfe9aad` `recorder/websocket_api.py:344`, `statistics.py:1136` | Use `display_unit_of_measurement`, `mean_type`, `has_sum` to choose `types` |
| `energy/solar_forecast` | yes | `core@5cfe9aad` `components/energy/{websocket_api,types}.py`; `forecast_solar/energy.py` | Result keyed by config entry id, `{wh_hours: {iso: Wh}}`; only linked entries; period length per provider (30 or 60 min) from key spacing; no P10/P90 |
| `weather.get_forecasts` | yes | `core@5cfe9aad` `components/weather/{services.py,const.py,entity.py}` | `type` required, response `{entity: {forecast: [...]}}`, only `datetime` guaranteed, fall back hourly -> daily, check `supported_features` |
| `workday` binary sensor + `workday.check_date` | yes | `core@5cfe9aad` `components/workday/{binary_sensor,entity,services}.py` | `on` = workday; `check_date` returns `{entity: {"workday": bool}}`; offset attribute must be 0 |
| `number.set_value`, `select.select_option`, `switch.turn_on/off` | yes | `core@5cfe9aad` `components/{number,select,switch}/services.py` | Unchanged semantics (range check, option check); no step enforcement |
| `modbus.write_register` | yes | `core@5cfe9aad` `components/modbus/{services.py,services.yaml,modbus.py}` | Fields `hub`, `slave` or `unit` (default 1), `address`, `value` int or list of unsigned 16-bit words; no float32 (encode two words), no read action, **errors only logged -> read back every write** |
| `nordpool.get_prices_for_date` | yes | `core@5cfe9aad` `components/nordpool/services.py`; `pynordpool 0.4.0` | Response `{AREA: [{start,end,price}]}`, UTC `+00:00`, price in currency/MWh (divide by 1000), excl. VAT/fees, 15 or 60 min per entry |
| `tibber.get_prices` | yes (re-read) | `core@5cfe9aad` `components/tibber/services.py` | `{"prices": {home: [{start_time, price}]}}`, price incl. taxes, call with `return_response` |
| `kostal_plenticore` numbers/select | yes | `core@5cfe9aad` `components/kostal_plenticore/{number,select,coordinator}.py` | Numbers disabled by default, no translation key, ids by `unique_id` suffix `devices:local_Battery:MinSoc`; limits from live `min`/`max`; failed REST write is silent; select read-back lags 30 s; no charge set-point |
| Kostal SoC / power sensors | partly | `core@5cfe9aad` `components/kostal_plenticore/sensor.py` | `devices:local:battery` `SoC` and `P`; sign of `P` UNVERIFIED -> confirm on the live entity |
| Solcast attributes + `query_forecast_data` | yes | `BJReplay/ha-solcast-solar@v4.6.1` `forecast.py`, `actions.py`, `const.py` | `detailedForecast`/`detailedHourly` entries `{period_start, pv_estimate, pv_estimate10, pv_estimate90}`, kW, 30 min, HA time zone; action `{"data": [...]}` as fallback; day sensors today, tomorrow, d3-d7 |
| Open-Meteo Solar Forecast | yes | `rany2/ha-open-meteo-solar-forecast@0.1.33` `sensor.py`, `const.py`, `energy.py` | Domain `open_meteo_solar_forecast`; attributes `watts`, `wh_period`, `wh_period_15m` on `energy_production_{today,tomorrow,d2..d7}`; use `wh_period_15m`; single estimate (no quantiles) |
| ENTSO-E | yes | `JaccoR/hass-entso-e@v0.7.5` `sensor.py:269`, `coordinator.py:250` | Attribute `prices`/`prices_today`/`prices_tomorrow` on the `avg_price` sensor, entries `{time, price}`, local `str(datetime)`, currency/kWh or MWh, may include the user's template and VAT |
| EPEX Spot | yes | `mampfes/ha_epex_spot@4.1.0` `sensor.py`, `const.py`, `SourceShell.py` | Attribute `data`: `[{start_time, end_time, price_per_kwh}]` on "Total Price" (all-in, user surcharges) and "Market Price"; unit literal `€/kWh`; slot length from `end - start` |
| Octopus Energy rates | yes | `BottlecapDave/HomeAssistant-OctopusEnergy@v19.2.1` `electricity/rates_current_day.py`, `utils/__init__.py:149` | Event entities `…_current_day_rates`/`…_next_day_rates`; attribute `rates` `[{start, end, value_inc_vat, …}]`, GBP/kWh incl. VAT, merge current + next day |
| Open-Meteo API `tilt`/`azimuth`/`global_tilted_irradiance` | source only | `open-meteo/open-meteo@01ceed5b` `openapi/forecast.yml`, `GlobalTilitedIrradiance.swift` | Convention in code: azimuth 0 = south, -90 east, +90 west (`compass - 180`); OpenAPI text contradicts it; live response not seen; opt-in only |
| Open-Meteo terms | partly | `open-meteo/open-meteo@01ceed5b` `README.md` | Free for open-source/non-commercial, fair use, 10 000 requests/day, CC BY 4.0 attribution link in the UI; separate terms page unreachable |
| Energy-Charts `/price` | **no** (unreachable) | field names only via `mampfes/ha_epex_spot@4.1.0` `EPEXSpot/EnergyCharts/__init__.py` | Not shipped in Phase 4 (plan question 5); terms/licence/limits UNVERIFIED |
| aWATTar `/v1/marketdata` | **no** (unreachable) | field names only via `mampfes/ha_epex_spot@4.1.0` `EPEXSpot/Awattar/__init__.py` | Not shipped in Phase 4; terms UNVERIFIED |
