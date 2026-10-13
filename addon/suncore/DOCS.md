# SunCore

Energy management for Home Assistant: forecasts, a cost-optimising planner
and a real-time surplus controller in one app.

## What SunCore does today (Phase 2)

SunCore is **pre-alpha**. This version helps you describe your installation
and shows your energy flow live:

- **Setup assistant**: ten steps for region, Energy dashboard import, grid,
  solar, battery, tariff, devices, priorities and goals, operating mode and
  a system check. Entities are detected and ranked, and a live sign check
  catches inverted sensors.
- **Device templates** (LG ThinQ air conditioner, generic switch, climate and
  adjustable load, Tibber, Solcast, regions) with a gallery; see the
  [compatibility list](https://github.com/pxljake/suncore/blob/main/docs/user/compatibility.md).
- **Live energy flow** between solar, grid, battery and house.
- **Shadow mode** is always on: SunCore never switches a device and never
  writes a set-point. Your choice of operating mode is stored for a later
  version.
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
backup.

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

## Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error`. |
| `controller_interval_s` | `5` | Real-time controller tick in seconds (2–60). Not used yet, because nothing is controlled. |
| `demo_mode` | `false` | Run with simulated data instead of your Home Assistant (see below). |

Change options on the app's **Configuration** tab and restart the app.

## Demo mode

With `demo_mode` switched on, SunCore does not read your entities. A
simulated house (a sunny summer day) feeds the panel, a banner says "Demo
mode", and nothing from your installation is used or modified. Use it to
look around, to take screenshots or to report a UI problem. Switch it off
again and restart to see your own data.

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
- **Logs**: the app's **Log** tab. Set `log_level` to `debug` for more
  detail and attach the log when you report a problem. Tokens are redacted.
- **Health**: the app serves `/health` internally; Home Assistant uses it
  to mark the app as unhealthy. It reports a failure when the database is
  unusable, Home Assistant rejects the app's credentials, or the connection
  was closed for good. A Home Assistant restart alone does not count.

## What is not there yet

No forecasts, planner, device control, history charts or tariff prices in
use (they are only stored), and no Home Assistant entities (MQTT discovery
follows in a later phase; the Mosquitto app is not needed yet). See the
[roadmap](https://github.com/pxljake/suncore/blob/main/docs/roadmap.md).

## Privacy

SunCore runs locally. It needs no account, sends no telemetry and makes no
cloud requests in this version. Your configuration stays in the app's
`/data` folder.

## Support

Questions and bug reports: <https://github.com/pxljake/suncore/issues>.

## Licence

Apache-2.0.
