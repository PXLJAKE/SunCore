# Release notes and upgrade

## Release notes

The complete list of changes per version is the
[changelog](https://github.com/pxljake/suncore/blob/main/CHANGELOG.md). It is generated from the
commit messages when a release is made. The
[releases page](https://github.com/pxljake/suncore/releases) has the notes of each release and,
for stable releases, this documentation as a ZIP (`suncore-docs-<version>.zip`) for older
versions.

## SunCore 1.0 (release candidate)

1.0 is not released yet. The newest tagged version is 0.2.0. Until 1.0 is out, the quick start
has you install the beta app (**SunCore (beta)**); its release candidates are numbered
`1.0.0-beta.<n>`. The stable app goes from 0.2.0 straight to 1.0.0.

### What is new since 0.2.0

- **Planning:** PV, load and price forecasts, a 24 to 48 h plan for the battery and the
  flexible loads, the **Plan** view with its reasons, the history and the decision log,
  what-if and backtesting, expert mode.
- **Devices:** wallbox and EV (also through evcc), heat pump (SG-Ready and set-point lift),
  shiftable programs such as a washing machine, battery control.
- **Grid:** import limit and peak shaving, §14a EnWG dimming, backup power (island), the export
  chain with the inverter limit and its [verification test](grid-limits.md#inverter-limit-and-the-verification-test).
- **Home Assistant:** entities and events over MQTT, notifications with buttons.
- **Panel:** a new design with four sections (Now, Plan, Analysis, Settings), light and dark,
  and a diagnostics export for bug reports (**Settings → Advanced → Diagnostics**).
- **Documentation** as a website in English and German.

### Behaviour changes: read before you update

- **Inverter limit: run the verification test again after updating.** A flag
  `limit_verified` that was set by hand or by an import (without the identity of the inverter
  adapter it was tested with) is no longer trusted. Until the test passes, the inverter limit
  runs at most in **assisted** mode. Importing a configuration also resets the flag. See
  [Inverter limit and the verification test](grid-limits.md#inverter-limit-and-the-verification-test).
- **Every write to the API needs the header `X-SunCore-Request: 1`.** The panel sends it.
  A script, a `rest_command` or a proxy that calls SunCore's API to change something must send
  it too, or it gets `403`. Reads need no header. The one exception is setting the emergency
  stop (`POST /api/v1/control/emergency-stop`), which always works; clearing it needs the
  header. If the panel shows "a required header is missing", check that a proxy in front of
  Home Assistant forwards custom headers.
- **No access to the Home Assistant configuration folder.** The app no longer mounts it (it
  never read it). Nothing changes for you; it only means SunCore cannot see `secrets.yaml`.
- **Very large requests are refused.** Every API route that takes a body has a size cap (`413`); the
  panel never comes near it.
- **No way back to 0.2.0 without a backup.** 1.0 migrates the database (`suncore.db` schema 6,
  `timeseries.db` schema 3). 0.2.0 refuses to start on it with "newer than the supported". Make
  a Home Assistant backup before you update; going back means restoring it.
- **The stable app is no longer marked experimental.**

### Checked in the simulator only

None of these has run on real hardware in the development sessions. Each starts in **shadow**
mode; use **assisted** before **automatic** and watch the decision log:

- wallbox and EV charging, evcc as actor;
- heat pump (SG-Ready contacts, set-point lift);
- shiftable programs (plug, remote start);
- the inverter limit and its verification test;
- grid shedding, peak shaving, §14a dimming and backup power;
- the first live writes with real PV surplus in automatic mode.

The full list of what is not verified yet is in the
[status](https://github.com/pxljake/suncore/blob/main/docs/STATUS.md) under `UNVERIFIED`.

### Security notes

- The password of a manual MQTT broker is stored in SunCore's database and therefore in Home
  Assistant backups. Prefer the automatic broker (the Mosquitto app; no password is stored),
  give a manual broker its own user, and turn on the encryption of your Home Assistant backups.
  See [Security](security.md).
- Anyone who can log in to the MQTT broker can stop SunCore, pause devices and lower modes.
  Raising a mode from Home Assistant stays off unless you allow it.

## Before you update

1. **Make a Home Assistant backup** that includes the SunCore app. It is the only way back to
   an older version.
2. **Export your configuration.** Open **Settings** and use **Export configuration**. The file
   can be imported again into this or a newer version.
3. **Know what the backup holds.** The configuration is part of the app backup. The
   measurement database `timeseries.db` is left out of it; SunCore rebuilds its quarter-hour
   history from Home Assistant's statistics when the file is missing.
4. **Know that a database upgrade is one-way.** A newer version migrates the database, and an
   older version cannot open a database with a newer schema. The app description names the
   database schema of your version.
5. **Read the notes** of the release for changes in the safety or mode handling (above for
   1.0).

## What an update does to your modes

- A device whose entities or template you change falls back to shadow mode. UNVERIFIED: that
  every device keeps its mode across a version update has not been tested on a real upgrade.
- Importing a configuration always resets the overall mode to shadow.
- An active emergency stop survives the update.

## Supported upgrade paths

- **0.2.0 to 1.0:** covered by tests. A frozen 0.2.0 database and a 0.2.0 export are migrated
  and imported in the test suite, and a CI job starts the 0.2.0 image with data, stops it,
  starts the new image on the same data and checks the result. UNVERIFIED: that CI job has not
  run on the release build yet.
- **A 1.0 beta to a newer beta or to 1.0:** the same migrations; not tested separately.
- **Back to an older version:** not supported. Restore the backup.

## After an update

- Open the panel and check that the overall mode is what you expect.
- Run the system check at the end of the setup assistant (**Run setup again** does not delete
  your settings; **Earlier versions** restores a previous save).
- If you use the inverter limit, run its verification test again.
- Report problems with the version number and the diagnostics export, see
  [Troubleshooting](troubleshooting.md).
