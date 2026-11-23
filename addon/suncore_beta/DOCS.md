# SunCore (beta)

Energy management for Home Assistant: forecasts, a cost-optimising planner
and a real-time surplus controller in one app.

> This is the **beta channel**. It receives new versions earlier than the
> stable app and may contain bugs. Please report what you find.

## What SunCore does today (Phase 4)

SunCore is **pre-alpha**. This version helps you describe your installation,
shows your energy flow live, distributes your solar surplus to devices, plans
the next 48 hours and can set limits on your home battery:

- **Setup assistant**: ten steps for region, Energy dashboard import, grid,
  solar (with forecast sources), battery (adapter, model, strategy, risk
  level), tariff (price formula, HT/NT), devices, priorities and goals (grid
  charging, self-sufficiency weight), operating mode and a system check with a
  first plan preview. Entities are detected and ranked, and a live sign check
  catches inverted sensors.
- **Device templates** (LG ThinQ air conditioner, generic switch, climate and
  adjustable load, water heaters, Tibber, Nord Pool, ENTSO-E, EPEX Spot,
  Octopus Energy, Solcast, Forecast.Solar, Open-Meteo Solar Forecast, battery
  adapters for generic entities and Kostal Plenticore, regions) with a gallery;
  see the
  [compatibility list](https://github.com/pxljake/suncore/blob/main/docs/user/compatibility.md).
- **Forecasts**: several solar forecast sources blended by their measured
  accuracy, a bias calibration that becomes active after 30 days, clipping
  recognised, a load forecast learnt from Home Assistant's statistics (base
  load without the controlled devices, workday and weather inputs), and an
  accuracy panel.
- **Tariffs**: dynamic prices from Tibber, Nord Pool, ENTSO-E, EPEX Spot and
  Octopus Energy, fixed and HT/NT prices, a price formula for spot prices,
  fixed, tiered or no feed-in remuneration and the negative-price rule of the
  region.
- **Planner**: a 48 hour plan in 15 minute steps, recomputed every 15 minutes
  and on events, with an optimiser (HiGHS) and a rule-based fallback, a risk
  level (cautious by default: targets met in about 9 of 10 cases), morning
  buffer, evening target, bridging a poor next day, strategies
  (self-consumption, keep the battery as full as possible, target by a time),
  optional grid charging (off by default) and a reason for every interval
  ("Why?"). The **Plan** view shows it.
- **Battery control**: through adapters (generic Home Assistant entities,
  Kostal Plenticore through the core integration, Kostal Plenticore G3 through
  Modbus) SunCore sets **limits only**, never power set-points, behind the
  safety layer with read-back, release to the inverter's own control, respect
  for manual changes and the effective reserve. The battery starts in **shadow
  mode**; the Kostal Modbus adapter is capped at assisted until the inverter's
  fallback timer has been measured.
- **Live energy flow** between solar, grid, battery and house.
- **Surplus control** every 5 seconds: free surplus by priority, with hysteresis,
  minimum run and pause times and smoothing against cloud flicker, and no
  grid import caused by SunCore. Climate units get their set-point shifted
  inside your comfort band; switches, adjustable loads and stepped water
  heaters (simulator only) follow the surplus. The plan sets bands (battery
  first until the morning buffer) that the controller follows.
- **Shadow mode is the default**, for the whole system, for every new device
  and for the battery: SunCore only logs what it *would have* done. A device
  or the battery is controlled only after you set the overall mode **and** its
  own mode to automatic (or to assisted, where you confirm every action).
- **Safety layer**: invariants, manual changes are respected, watchdogs,
  hand-back of devices and the battery on shutdown, crash recovery and
  connection loss, and a persisted **Emergency stop** in the header of every
  view.
- **"Why?" and decision log**: every decision and every plan interval has its
  reasons in plain language.
- Values that are outdated, unavailable or invalid are marked as such
  instead of being guessed.

## Installation

1. In Home Assistant open **Settings → Apps → App store** (older versions:
   **Add-ons → Add-on store**).
2. Open the menu (three dots, top right) → **Repositories** and add
   `https://github.com/pxljake/suncore`.
3. Reload the store, open **SunCore** (or **SunCore (beta)**), click
   **Install**, then **Start**. Enable **Show in sidebar** if it is off.

Supported architectures: `amd64` and `aarch64`.

## First start

1. Start the app and check the **Log** tab: once connected, SunCore logs an
   `ha.connected` line.
2. Open **SunCore** in the sidebar. The panel is visible to administrators
   only.
3. The setup assistant opens by itself. Follow the ten steps; every step is
   saved, and you can leave and continue later. The
   [quick start](https://github.com/pxljake/suncore/blob/main/docs/user/en/quick-start.md)
   ([Deutsch](https://github.com/pxljake/suncore/blob/main/docs/user/de/quick-start.md))
   explains each step with screenshots.

## Settings

**Settings** lists every section of your setup with a summary and an
**Open** link to the matching assistant step. **Run setup again** restarts
the assistant; **Earlier versions** restores a previous save; **Export
configuration** and **Import configuration** back up everything as a file.
The settings are stored in the app's `/data` folder and are part of the app
backup. This version uses database schema 4, which earlier versions cannot open:
export the configuration before updating. Measurements, forecasts and prices
live in a separate `timeseries.db`, which is left out of the app backup; SunCore
rebuilds its quarter-hour history from Home Assistant's statistics when the file
is missing.

### Sign convention

SunCore uses these signs internally:

| Value | Positive means |
|---|---|
| Grid | import (power drawn from the grid) |
| Battery | charging |
| PV | generation |

The assistant asks whether the live direction is right and inverts the sign
for you if not. On a sunny day with a charging battery, PV must be positive
and the battery arrow must point into the battery. Power sensors in W and kW
are understood; other units are rejected rather than guessed.

## Planning and battery control

Forecasts, tariffs, how the plan is made, risk levels and buffers, grid
charging, the battery modes, the adapters (including how to enable the Kostal
`Battery:MinSoc` entity and how to measure the Kostal Modbus fallback timer),
a walk-through for your first battery in assisted mode and the known
limitations are in
[Planning and battery control](https://github.com/pxljake/suncore/blob/main/docs/user/en/planning-and-battery.md)
([Deutsch](https://github.com/pxljake/suncore/blob/main/docs/user/de/planning-and-battery.md)).
Short version: keep everything in shadow mode first, read the **Plan** view for
a day or two, then switch the battery to **assisted** and confirm the first
proposals yourself. Planner and battery control have never run against real
hardware yet; the guide lists what is unverified.

## Surplus control

How the controller decides, the three operating modes, what happens when you
change a device yourself, the emergency stop, the fail-safe behaviour and a
step-by-step guide for your first device are in
[Surplus control](https://github.com/pxljake/suncore/blob/main/docs/user/en/surplus-control.md)
([Deutsch](https://github.com/pxljake/suncore/blob/main/docs/user/de/surplus-control.md)).
Short version: leave everything in shadow mode first, read the **Decision log**
for a day, then switch **one** device to automatic. Surplus control has never
run against real hardware yet; the guide lists what is unverified.

## Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error`. |
| `controller_interval_s` | `5` | Real-time controller tick in seconds (2–60). |
| `demo_mode` | `false` | Run with simulated data instead of your Home Assistant (see below). |

Change options on the app's **Configuration** tab and restart the app.

## Expert settings (environment variables)

Some timing and budget settings of the price service and the planner are not app options
(keep the Configuration tab simple). Experts can set them as environment variables of the container:

| Variable | Default | Description |
|---|---|---|
| `SUNCORE_TARIFF_REFRESH_INTERVAL_S` | `3600` | Spacing of the regular price refresh in seconds (60–86400). |
| `SUNCORE_TARIFF_TOMORROW_AFTER` | `13:00` | Local time from which tomorrow's day-ahead prices are expected. |
| `SUNCORE_TARIFF_TOMORROW_POLL_S` | `900` | Spacing of the asks between that time and the arrival of tomorrow's prices. |
| `SUNCORE_PLANNER_BUDGET_S` | `8` | Time budget of one planner run in seconds (0.5–120); the rule-based plan covers a miss. |
| `SUNCORE_PLANNER_MIP_GAP` | `0.01` | Accepted distance from the optimum of the planner's solver (0–0.5). |

## Demo mode

With `demo_mode` switched on, SunCore does not read your entities. A
simulated house (a sunny summer day) feeds the panel, a banner says "Demo
mode", and nothing from your installation is used or modified. The demo
includes simulated prices, solar forecasts and a plan. Use it to look around,
to take screenshots or to report a UI problem. Switch it off again and restart
to see your own data. Experts can also set `SUNCORE_DEMO_START_TIME` (time of
day the demo starts, default `10:00`) and `SUNCORE_DEMO_BATTERY_CONTROL=1`
(gives the simulated house a controllable inverter, so the battery card can be
tried; off by default).

## Troubleshooting

- **"Home Assistant not connected"**: SunCore
  cannot reach Home Assistant. It reconnects on its own with a growing
  delay. Check the app **Log** tab; if Home Assistant was restarting, the
  message disappears when it is back. If it persists, restart the app.
- **The assistant finds no suggestions**: if Home Assistant's registry is
  not available, SunCore falls back to a plain list of entities with simpler
  rules; use the search field. See the quick start for sign and unit checks.
- **A value is marked "Outdated"**: the sensor has not updated for a while.
  Check the source integration.
- **A value is marked "Unavailable" or "Invalid"**: the entity is
  `unavailable`/`unknown`, not numeric, or has an unsupported unit. Pick
  another entity or fix the integration.
- **"Offline, reconnecting…"** in the panel: the browser lost the
  connection to SunCore; it retries automatically.
- **"Access denied. Open SunCore from Home Assistant."**: use the sidebar
  entry; direct access to the app's port is blocked on purpose.
- **No plan, a rule-based plan, a stale plan, a spot price without a formula**:
  see the troubleshooting section of
  [Planning and battery control](https://github.com/pxljake/suncore/blob/main/docs/user/en/planning-and-battery.md#9-troubleshooting).
- **Logs**: the app's **Log** tab. Set `log_level` to `debug` for more
  detail and attach the log when you report a problem. Tokens are redacted.
- **Health**: the app serves `/health` internally; Home Assistant uses it
  to mark the app as unhealthy. It reports a failure when the database is
  unusable, Home Assistant rejects the app's credentials, or the connection
  was closed for good. A Home Assistant restart alone does not count.

## What is not there yet

Nothing of the planner and the battery control has run on real hardware yet.
Kostal Plenticore cannot be told to charge from the grid (the plan only shows
it), and a Kostal Modbus battery stays capped at assisted mode until you have
measured the inverter's fallback timer. There are no history charts, what-if
analysis or backtesting (the plan view is minimal), no machine-learning load
model, no built-in internet fallbacks for solar forecasts and prices, no thermal
models for air conditioners and water heaters in the plan, no export limiting
through the inverter, no charge point (wallbox), heat pump or shiftable program
control, and no Home Assistant entities (MQTT discovery follows in a later
phase; the Mosquitto app is not needed yet). See the
[roadmap](https://github.com/pxljake/suncore/blob/main/docs/roadmap.md).

## Privacy

SunCore runs locally. It needs no account, sends no telemetry and makes no
cloud requests of its own: forecasts and prices come from the integrations you
already run in Home Assistant. Your configuration stays in the app's
`/data` folder.

## Support

Questions and bug reports: <https://github.com/pxljake/suncore/issues>.

## Licence

Apache-2.0.
