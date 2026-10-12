# Integrations SunCore reads from or writes to

## PV forecasts

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

### Forecast.Solar (Core)

Sensors: energy today/remaining/tomorrow/this hour/next hour (kWh), power
now (W), peak times; disabled by default power in 1/12/24 h. Updates hourly
(free) or every 30 min (API key). Free tier: one plane, lower resolution.
The page does not document series attributes; the integration implements the
`energy` platform, so `energy/solar_forecast` returns its hourly `wh_hours`
(UNVERIFIED field name – check `forecast_solar/energy.py` in Phase 4).

### Open-Meteo Solar Forecast (HACS, `rany2/ha-open-meteo-solar-forecast`, Apache-2.0)

Domain presumably `open_meteo_solar_forecast` (UNVERIFIED). Per-array
declination, azimuth (0 = north), module power, DC efficiency, inverter
limit, damping, horizon shading. Attribute structure not documented in the
README → read the source in Phase 4.

### Built-in fallback: Open-Meteo API + plant model

Open-Meteo: free for non-commercial and open-source use, fair use, contact
above 10 000 requests/day; data CC BY 4.0 (attribution "Weather data by
Open-Meteo.com" must be shown in the UI); code AGPL-3.0 (irrelevant, we only
call the API). Variable names for tilted irradiance
(`global_tilted_irradiance`, `tilt`, `azimuth`) are UNVERIFIED in this pass
(docs site unreachable; the repo's `openapi/` folder exists). Verify in
Phase 4 before implementing.

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

### Nord Pool (Core)

Sensors current/previous/next price, daily average/min/max, peak and
off-peak blocks, currency, exchange rate, "tomorrow available". Action
`nordpool.get_prices_for_date` (`config_entry`, `date`, `areas`, `currency`)
→ per area `[{start, end, price}]` in currency/MWh (divide by 1000).
Automatically switches to 15-minute market time units; mixed 60/15-minute
series are possible during the transition. `nordpool.get_price_indices_for_date`
exists but is undocumented on the page.

### ENTSO-E (HACS, `JaccoR/hass-entso-e`, Apache-2.0)

Day-ahead prices for European zones; sensors current/next/avg/min/max with a
price list attribute; optional template for fees/VAT; needs a free
Transparency Platform API key (requested by e-mail).

### EPEX Spot (HACS, `mampfes/ha_epex_spot`, MIT)

Sources: aWATTar, Energyforecast.de, SMARD.de, smartENERGY.at, Tibber,
ENTSO-E, Energy-Charts.info, Hofer Grünstrom. Total-price sensor `data`
attribute: `[{start_time, end_time, price_per_kwh}]`; also market price,
average, median, min, max, quantile, rank sensors.

### Octopus Energy (HACS, `BottlecapDave/HomeAssistant-OctopusEnergy`, MIT)

Electricity/gas rate sensors with rate information and intelligent-tariff
controls. Attribute names (`rates` list) UNVERIFIED – Phase 4.

### Built-in fallback: Energy-Charts / aWATTar

Both APIs were unreachable from this session. `api.energy-charts.info`
`/price` (bidding zone, start, end → `unix_seconds[]`, `price[]`, `unit`,
`license_info`) and `api.awattar.de/v1/marketdata` (`data[{start_timestamp,
end_timestamp, marketprice, unit}]`) are UNVERIFIED including their terms of
use. Phase 4 verifies before shipping; the EPEX Spot integration above is a
working alternative in the meantime.

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
power (+ import), home power by source, battery power (+ discharge, −
charge), SoC, PV/DC per string, energy totals. **No charge/discharge power
set-point over REST** – active control needs Modbus (see
`kostal-plenticore-modbus.md`). Useful for SunCore: min-SoC and min-home-
consumption numbers as a "soft" battery adapter without installer access,
and `ActivePowerLimitation` for export limiting (sign/meaning UNVERIFIED –
the HA number is 0–10000 W, the maintainer's inverter is 17 kW; check the
inverter's own limit semantics in Phase 4).

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

`UNVERIFIED`: Forecast.Solar series field names, Open-Meteo Solar Forecast
attributes and domain, Open-Meteo tilted-irradiance parameters, Octopus
attribute names, Energy-Charts and aWATTar APIs and terms, semantics of
`Inverter:ActivePowerLimitation`.
