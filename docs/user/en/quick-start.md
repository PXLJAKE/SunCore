# Quick start

SunCore is **pre-alpha** (Phase 3). This version helps you describe your
installation with a setup assistant, shows your energy flow live and can
control devices from your solar surplus. It starts in **shadow mode**:
nothing is switched and no set-point is written until you switch a device to
automatic yourself, see [Surplus control](surplus-control.md). The full app
description is in [`addon/suncore/DOCS.md`](../../../addon/suncore/DOCS.md).

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

Nothing is switched while you set up (and not afterwards either, until you say so).
Every sensor you pick shows its live
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

An air conditioner template has an optional **Power sensor**. Use it if
another device measures the unit's power, for example a smart plug or the
HACS integration `smartthinq_sensors` (sensor "Energy current"): SunCore then
preselects the **Measured** power model. Such sensors often update only every
few minutes (5 minutes for `smartthinq_sensors`), so the value is slow. If you
run the same air conditioner through two integrations (for example `lg_thinq`
and `smartthinq_sensors`), control it through **one** of them only: the editor
shows a hint when you pick a second entity of a unit that is configured
already, and the system check at the end warns about it. Both compare the
device names in Home Assistant and only react to two *different* integrations
with the same device name; if you renamed one of the devices in Home Assistant
(its own name), the hint and the warning stay silent.

![Hint in the device editor](../../assets/screenshots/device-duplicate-hint-tablet-light-en.png)

![System check warning](../../assets/screenshots/wizard-summary-duplicate-tablet-light-en.png)

![Step Devices](../../assets/screenshots/wizard-devices-tablet-light-en.png)

Is your device missing? See
[Contribute a device template](device-templates.md) and the generated
[compatibility list](../compatibility.md).

### Priorities and goals

Drag devices by their handle, or use the arrow buttons or the arrow keys,
to decide who gets surplus first. Position 1 is served first. Goals decide
whether the battery's charging power counts as surplus for devices (target
charge of the battery, climate before export); see
[Battery and goals](surplus-control.md#battery-and-goals). Grid charging at
night only is stored for a later version.

![Step Priorities and goals](../../assets/screenshots/wizard-priorities-tablet-light-en.png)

### Operating mode

Choose the **overall mode**: **Shadow mode** (the default: SunCore calculates
and logs, switches nothing), **Assisted mode** (it suggests, you confirm) or
**Automatic mode** (it acts by itself). This step only sets the overall mode.
Each device has its own mode, which you change on the **Now** view, and a device
acts only in the lower of the two, so automatic needs both. Leave it on shadow
for now.

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

SunCore calculates what it would do and logs it as **Would have switched**, but
switches nothing and writes no set-point. Shadow mode is the default for the
whole system and for every new device. A device is only controlled when **both**
the overall mode and the device's own mode are set to automatic (or assisted,
where you confirm every action). Run in shadow mode for a day or more, read the
log and only then switch a single device: the step-by-step guide is
[Take your first device to automatic](surplus-control.md#8-take-your-first-device-to-automatic).

## 5. The Now view

After **Finish setup** the **Now** page shows solar, grid, battery and house
with animated flows, the **Surplus** card and a card for every device with its
state, estimated power, mode switch and **Why?**. The red **Emergency stop**
button in the header hands every device back at once.

![Now view on a tablet, light theme](../../assets/screenshots/now-tablet-light-en.png)

The **Decision log** lists every decision with its reasons. How it all works,
what happens when you change a device yourself and what SunCore does when
something fails is in [Surplus control](surplus-control.md).

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
(your tariff and forecast choices are only stored), no history charts, no
battery control, no charge point (wallbox) control and no Home Assistant
entities. Its surplus control has never run against real hardware yet: that is
what your shadow-mode run is for. Compared with evcc (charge points and
heaters), EMHASS and Predbat (battery optimisation), SunCore currently
controls consumers from real surplus only. The list of open points is in
[Known limitations](surplus-control.md#known-limitations-and-unverified); the
[roadmap](../../roadmap.md) shows when each part arrives.
