# Troubleshooting

Start with the symptom. Most problems are a missing or wrong source entity, and SunCore marks such
values instead of guessing.

## Connection and panel

- **"Home Assistant not connected".** SunCore cannot reach Home Assistant and reconnects on its
  own with a growing delay. Check the app's **Log** tab. If Home Assistant was restarting, the
  message disappears when it is back. If it persists, restart the app.
- **"Offline, reconnecting..." in the panel.** The browser lost the connection to SunCore and
  retries automatically.
- **"Access denied. Open SunCore from Home Assistant."** Use the sidebar entry. Direct access to
  the app's port is blocked on purpose.
- **The app is shown as unhealthy.** The app serves `/health` internally. It reports a failure when
  the database is unusable, Home Assistant rejects the app's credentials, or the connection was
  closed for good. A Home Assistant restart alone does not count.

## Values and setup

- **A value is marked "Outdated".** The sensor has not updated for a while. Check the source
  integration.
- **A value is marked "Unavailable" or "Invalid".** The entity is `unavailable` or `unknown`, not
  numeric, or has an unsupported unit. Pick another entity or fix the integration.
- **The sign check says the balance does not close.** A sensor, unit or sign is wrong. The system
  check names the part; go back to that step and answer **No, invert the sign**.
- **A sensor is rejected for its unit.** Power sensors must report W or kW, energy counters Wh or
  kWh, charge levels %. SunCore never guesses units.
- **The assistant finds no suggestions.** If Home Assistant's registry is not available, SunCore
  falls back to a plain list of entities with simpler rules. Use the search field.
- **A device is not in the gallery.** See [Contribute a device template](device-templates.md) or
  use a generic template (switch, climate, adjustable load). The
  [compatibility list](compatibility.md) shows what exists.

## Plan and control

- **No plan, a rule-based plan, a stale plan, a spot price without a formula.** See the
  troubleshooting section of
  [Planning and battery control](planning-and-battery.md#9-troubleshooting).
- **A device does nothing.** Check the modes first: a device acts in the lower of the overall
  mode and its own mode, and in shadow mode it only logs. Then read the **Decision log**: every
  decision, also "nothing to do", has a reason. See [Surplus control](surplus-control.md).
- **Something went wrong and you want everything to stop.** Press **Emergency stop** in the
  header. Devices are handed back within 10 seconds and nothing is written until you end the stop
  with **Back to normal**.
- **No Home Assistant entities appear.** They need the Mosquitto broker app. See
  [Home Assistant entities](home-assistant-entities.md).

## Logs and what to attach to a report

1. Open the app's **Log** tab. Set the option `log_level` to `debug` for more detail and restart
   the app. Tokens are redacted.
2. In **Settings**, use **Export configuration** to keep a copy of your setup before you try
   anything bigger.
3. Attach the diagnostics export: **Settings → Advanced → Diagnostics → Download
   diagnostics**. It holds the versions, the configuration with secrets masked, the connection,
   planner and watchdog state, the latest decisions and the latest warnings and errors. Entity
   ids, device names and areas are replaced by codes that change with every export; passwords,
   tokens and coordinates are left out. The panel shows a preview: read it before you post it,
   because a log line can still say something about your system. If your version has no
   **Diagnostics** panel, attach the log and describe your setup in words.
4. Open an issue at <https://github.com/pxljake/suncore/issues>. Do not post tokens, passwords or
   your real entity ids you want to keep private. For security problems use the private route in
   [Security](security.md).

## Still stuck

The [FAQ](faq.md) answers the common questions. The app description on GitHub has a
[troubleshooting section](https://github.com/pxljake/suncore/blob/main/addon/suncore/DOCS.md)
that follows the app version.
