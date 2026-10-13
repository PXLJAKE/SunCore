# Quick start

SunCore is **pre-alpha** (Phase 2). This version helps you describe your
installation with a setup assistant and shows your energy flow live. It runs
in **shadow mode**: nothing is switched and no set-point is written. The full
app description is in [`addon/suncore/DOCS.md`](../../../addon/suncore/DOCS.md).

## 1. Install

1. In Home Assistant open **Settings → Apps → App store**.
2. Menu (three dots) → **Repositories** → add
   `https://github.com/pxljake/suncore`.
3. Install **SunCore (beta)** while no stable release exists, then
   **Start** it and enable **Show in sidebar**.

Menu names differ between Home Assistant versions; older versions say
**Add-ons** and **Add-on store** instead of **Apps** and **App store**.

Want to look around first? Switch on the option `demo_mode` on the app's
**Configuration** tab and restart. A simulated, fully set-up house feeds the
panel and nothing of your installation is read.

## 2. Open the panel: the setup assistant starts

Open **SunCore** in the sidebar (administrators only). On the first start
the setup assistant opens by itself. It has ten steps, shows your progress,
saves every step and can be left at any time with **Leave setup**; you
continue where you stopped (**Continue setup** in Settings). If you used the
Phase 1 version, you land in the assistant once with your old sensors
already filled in.

Nothing is switched while you set up. Every sensor you pick shows its live
value, so you can check it right away. Sign convention used everywhere:
**grid positive = import**, **battery positive = charging**, solar positive
= generation.

## 3. The ten steps

### Region and language

Choose the **Region profile** (it sets the currency, the time zone and
rules such as feed-in remuneration) and the **Language**. Take the generic
profile if your country is not listed.

![Step Region and language](../../assets/screenshots/wizard-region-tablet-light-en.png)

### Energy dashboard

If you already set up Home Assistant's Energy dashboard, SunCore reads it
and suggests sensors for grid, solar and battery. Tick the suggestions you
want and choose **Apply N suggestions and continue** (N is the number you
ticked; with one it reads "suggestion"). Nothing is applied
without your choice, and you can change everything in the next steps. The
dashboard often has only energy counters (kWh), no power sensors; then
SunCore says so and you pick the power sensor yourself. Not set up? Skip
the step.

![Step Energy dashboard](../../assets/screenshots/wizard-energy-tablet-light-en.png)

### Grid connection

Choose **One signed sensor** or **Separate import and export sensors**.
Optional: a house consumption sensor (otherwise it is calculated from grid,
solar and battery), limits for import and feed-in, and the grid operator
signal for section 14a EnWG (Germany).

The **Check the sign** panel asks, for example, "The grid connection is importing
… right now. Is that correct?" Answer **Yes, correct** or **No, invert the
sign**. Idle at the moment? Check again when something clearly flows.

![Step Grid connection](../../assets/screenshots/wizard-grid-tablet-light-en.png)

### Solar

Add each solar array with its **Power sensor**, installed power in kWp,
tilt and orientation (azimuth: 0 = north, 180 = south). Optionally choose a
**Forecast source**, such as Solcast, if the integration is installed.

![Step Solar](../../assets/screenshots/wizard-pv-tablet-light-en.png)

### Battery

Select the battery power sensor (positive = charging) and the state of
charge sensor, or choose **No battery**. Capacity and the minimum and
maximum charge are optional. SunCore only observes the battery for now.

![Step Battery](../../assets/screenshots/wizard-battery-tablet-light-en.png)

### Tariff

Enter the **Import price** (**Fixed price** or **Dynamic price**) and the
**Feed-in remuneration** (fixed, tiered by plant size, or none). A dynamic
price needs a price sensor or a tariff template such as Tibber. Prices are
stored for later versions; the planner does not use them yet.

![Step Tariff](../../assets/screenshots/wizard-tariff-tablet-light-en.png)

### Devices

Add the devices SunCore may control later. Search the gallery, **Choose** a
template (for example an LG ThinQ air conditioner, a switch or an adjustable
load), map its entities, check the power model and the protection times, and
save. The assistant ranks your entities and tells you why a candidate
matches. New devices always start in shadow mode.

![Step Devices](../../assets/screenshots/wizard-devices-tablet-light-en.png)

Is your device missing? See
[Contribute a device template](device-templates.md) and the generated
[compatibility list](../compatibility.md).

### Priorities and goals

Drag devices by their handle, or use the arrow buttons or the arrow keys,
to decide who gets surplus first. Position 1 is served first. Goals (target
charge of the battery by a time, climate before export, grid charging at
night only) are stored now and used by later versions.

![Step Priorities and goals](../../assets/screenshots/wizard-priorities-tablet-light-en.png)

### Operating mode

Choose **Shadow mode**, **Assisted mode** or **Automatic mode**. In this
version only shadow mode is active; the other choices are stored and marked
"active from a later version".

![Step Operating mode](../../assets/screenshots/wizard-mode-tablet-light-en.png)

### Summary and check

The **System check** looks at units, signs, the power balance and whether
sensors deliver current values. Fix everything marked with a cross (open the
step from the list), choose **Check again**, then **Finish setup**.
Warnings do not block you.

![Step Summary and check](../../assets/screenshots/wizard-summary-tablet-light-en.png)

The panel follows the dark theme of your device as well:

![Summary on a tablet, dark theme](../../assets/screenshots/wizard-summary-tablet-dark-en.png)

## 4. What shadow mode means

SunCore calculates what it would do and logs it, but switches nothing and
writes no set-point, whatever you choose in the operating mode step. This
is a hard rule of the safety layer in this version, not a setting you can
lose by accident. Control arrives in a later phase, device by device and
only after you switch a device on explicitly.

## 5. The Now view

After **Finish setup** the **Now** page shows solar, grid, battery and house
with animated flows.

![Now view on a tablet, light theme](../../assets/screenshots/now-tablet-light-en.png)

## 6. Change things later

Open **Settings**. It lists every section of your setup (**Region**,
**Site (grid, solar, battery)**, **Tariff**, **PV forecast**, **Devices**,
**Goals**, **Operating mode**) with a short summary and an **Open** link to
the matching step.

![Settings overview](../../assets/screenshots/settings-tablet-light-en.png)

- **Run setup again** restarts the assistant with your current values.
- **Earlier versions**: every save keeps the previous version. Use **Show
  versions** and **Restore** if a change did not work out.
- **Backup**: **Export configuration** saves everything as a file,
  **Import configuration** loads one. Use it before experiments and when
  you move to a new installation.
- Unsaved edits are guarded: leaving a page asks before they are discarded.

## Troubleshooting

- **The sign check says the balance does not close.** A sensor, unit or
  sign is wrong. The system check names the part ("Inverting the sign of
  the grid would close the power balance"); go back to that step and answer
  **No, invert the sign**.
- **A sensor is rejected for its unit.** Power sensors must report W or kW,
  energy counters Wh or kWh, charge levels %. SunCore never guesses units;
  pick another entity or fix the integration.
- **"has not updated for a while" or "is unavailable".** The source
  integration stopped delivering. Check the integration in Home Assistant;
  SunCore marks the value instead of using it.
- **No suggestions or ranking, or a hint that the registry is not
  available.** SunCore reads Home Assistant's entity, device and area
  registry for detection. If that is not available it falls back to a plain
  list of states with simpler rules: you still find your entities with the
  search field, only the ranking is less precise. Update Home Assistant if
  it persists, and report your version.
- **The Energy dashboard step is empty.** Either it is not set up in Home
  Assistant or it has only energy counters. Skip the step and choose power
  sensors yourself.
- **A device is not in the gallery.** See
  [Contribute a device template](device-templates.md), or use a generic
  template (switch, climate, adjustable load).
- More in the troubleshooting section of the
  [app description](../../../addon/suncore/DOCS.md).

## What SunCore cannot do yet

Be aware before you rely on it. This version has no forecasts or planner
(your tariff and forecast choices are only stored), does not control any
device (shadow mode only), no history charts, no battery control and no
Home Assistant entities. Compared with evcc (charge points and heaters),
EMHASS and Predbat (battery optimisation), SunCore currently only observes.
The [roadmap](../../roadmap.md) shows when each part arrives.
