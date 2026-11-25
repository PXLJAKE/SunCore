# FAQ

## Does SunCore switch my devices as soon as I install it?

No. Shadow mode is the default for the whole system, for every new device and for the battery.
SunCore calculates and logs what it **would have** done and writes nothing. A device is controlled
only after you set the overall mode **and** the device's own mode to automatic (or to assisted,
where you confirm every action). See [Concepts](concepts.md).

## Does it need an account or the cloud?

No. SunCore runs locally, needs no account, sends no telemetry and makes no cloud requests of its
own. Forecasts and prices come from the integrations you already run in Home Assistant.

## Which devices, tariffs and forecasts are supported?

The [compatibility list](compatibility.md) lists every built-in template. A template does not
mean the device is controlled: a template that is only tested in the simulator has never run
against real hardware. If your device is missing, use a generic template or
[contribute one](device-templates.md).

## Is this tested on real hardware?

Not yet. The planner, battery control, EV charging, heat pump and program control, the grid
limits and the notifications have run in the simulator only. That is what the shadow-mode phase is
for. The guides list what is `UNVERIFIED`.

## How does SunCore compare with evcc, EMHASS and Predbat?

- **evcc** is the best choice for charge-point control and has years of hardware experience.
  SunCore can use it as an actor (see [EV charging](ev-charging.md)).
- **EMHASS** offers a strong linear programme that you wire up with your own automations.
- **Predbat** is strong for battery arbitrage on supported inverters, mostly on UK tariffs. Its
  licence allows personal use only.
- **SunCore** puts forecasts, a planner, real-time surplus control and a safety layer in one app
  with a guided setup, shadow mode and a reason for every decision. Its planner and its wallbox
  control are new and unproven next to those tools.

## What can SunCore not do yet?

See [What SunCore cannot do yet](quick-start.md#what-suncore-cannot-do-yet). In short: nothing
has run on real hardware, a Kostal Modbus battery is capped at assisted mode until its fallback
timer has been measured, the inverter limit stays off until it is verified, and there is no
companion integration or custom Lovelace card (see the [Dashboard recipe](dashboard-recipe.md)).

## Can I run it without a battery, a wallbox or a heat pump?

Yes. The battery step offers **No battery**, and wallboxes, heat pumps and other consumers are
devices you add only if you have them (see the [Quick start](quick-start.md)). UNVERIFIED: every
combination of missing parts has been tried in the simulator only.

## Why does a device do nothing in assisted mode?

Assisted mode only **proposes**. Look under **Suggestions waiting for you** on the **Now** view and
confirm or dismiss. A proposal is valid for 2 minutes. See [Surplus control](surplus-control.md).

## Can I use SunCore without Home Assistant OS?

SunCore is built as a Home Assistant app (formerly add-on). Other installation types are not
documented here. UNVERIFIED: standalone use outside the Supervisor.

## Where is my data?

In the app's `/data` folder: `suncore.db` with your configuration and decision log and
`timeseries.db` with measurements, forecasts and prices. The configuration is part of the app
backup; `timeseries.db` is left out, and SunCore rebuilds its quarter-hour history from Home
Assistant's statistics when the file is missing.

## How do I report a problem or ask a question?

Open an issue at <https://github.com/pxljake/suncore/issues>. See
[Troubleshooting](troubleshooting.md) for what to attach. Security issues go through the private
route described in [Security](security.md).
