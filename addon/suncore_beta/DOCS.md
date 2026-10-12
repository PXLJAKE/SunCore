# SunCore (beta)

Energy management for Home Assistant: forecasts, a cost-optimising planner
and a real-time surplus controller in one app.

## Status

Pre-alpha. This version is a skeleton and does not control anything.
Follow the roadmap at <https://github.com/pxljake/suncore/blob/main/docs/roadmap.md>.

## Configuration

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error`. |
| `controller_interval_s` | `5` | Real-time controller tick in seconds (2–60). |

All other settings are made in the SunCore panel in the sidebar.

## MQTT

If the Mosquitto app is installed, SunCore publishes its entities to Home
Assistant through MQTT discovery. Without a broker the panel works fully;
only the entities in Home Assistant are missing.

## Support

Questions and bug reports: <https://github.com/pxljake/suncore/issues>.
Please attach the anonymised diagnostics export from the panel.

## Licence

Apache-2.0.
