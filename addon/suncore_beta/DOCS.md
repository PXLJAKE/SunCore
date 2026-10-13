# SunCore (beta)

Energy management for Home Assistant: forecasts, a cost-optimising planner
and a real-time surplus controller in one app.

> This is the **beta channel**. It receives new versions earlier than the
> stable app and may contain bugs. Please report what you find.

## What SunCore does today (Phase 1)

SunCore is **pre-alpha**. This version shows your energy flow live and
nothing else:

- **Live energy flow** between solar, grid, battery and house, with the
  battery charge level and an animated diagram.
- **Shadow mode** is always on: SunCore never switches a device and never
  writes a set-point. The panel shows a "Shadow mode" badge.
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
3. Without a configuration the **Now** page says that no energy sources are
   configured. Click **Open settings**.

## Settings page

The settings page is a temporary one; the setup assistant of a later phase
replaces it. Pick the Home Assistant sensors SunCore should read:

- **Grid connection** – either one signed sensor, or separate import and
  export sensors.
- **Solar** – one or more PV power sensors (add as many as you have).
- **Battery** – power and state of charge, or switch the battery off.
- **House load** – optional. Without it the house consumption is
  calculated from the other values (house = PV + grid − battery).

Below every field SunCore shows the live value of the selected sensor, so
you can check it right away. Power sensors in W and kW are understood;
other units are rejected rather than guessed.

### Sign convention

SunCore uses these signs internally:

| Value | Positive means |
|---|---|
| Grid | import (power drawn from the grid) |
| Battery | charging |
| PV | generation |

If one of your sensors uses the opposite convention (for example a grid
sensor that is negative while importing), switch on **Invert sign** for
that sensor and check that the preview and the flow diagram look right: on
a sunny day with a charging battery, PV must be positive and the battery
arrow must point into the battery.

Click **Save**. The settings are stored in the app's `/data` folder and are
part of the app backup.

## Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error`. |
| `controller_interval_s` | `5` | Real-time controller tick in seconds (2–60). Not used in Phase 1, because nothing is controlled yet. |
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

No forecasts, planner, device control, tariffs, history charts or setup
assistant, and no Home Assistant entities (MQTT discovery follows in a
later phase; the Mosquitto app is not needed yet). See the
[roadmap](https://github.com/pxljake/suncore/blob/main/docs/roadmap.md).

## Privacy

SunCore runs locally. It needs no account, sends no telemetry and makes no
cloud requests in this version. Your configuration stays in the app's
`/data` folder.

## Support

Questions and bug reports: <https://github.com/pxljake/suncore/issues>.

## Licence

Apache-2.0.
