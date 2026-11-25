# Home Assistant entities and commands

SunCore shows its state in Home Assistant as devices and entities and accepts a small set of
commands from Home Assistant. This works through MQTT discovery, so it needs an MQTT broker,
normally the **Mosquitto broker** app. Without a broker everything else in SunCore works as
before; only these entities are missing.

This page covers setup, what you get, what Home Assistant may and may not do, and the known
limits. Safety first: **the entity surface never writes to a device.** A command from Home
Assistant goes through the same services and the same safety layer as a click in the panel.

## 1. Set it up

1. Install the **Mosquitto broker** app (**Settings → Apps → App store**), start it and let Home
   Assistant add the MQTT integration.
2. Open SunCore, **Settings → Home Assistant entities**. The status line shows **Connected** and
   the number of entities. SunCore asks the Supervisor for the broker by itself (the app needs
   no extra permission for this) and looks again every minute until one exists.
3. In Home Assistant the entities appear as one **SunCore** device plus one device per
   configured device. Nothing to configure on the Home Assistant side.

Other states you may see: **Off** (switched off in the settings), **No broker** (the Supervisor
knows none; the page says why and how to fix it), **Connecting**, **Error** (wrong user name or
password, TLS failure, broker did not answer; the page names the reason). The system check at the
end of the setup assistant has a line for this as well.

A broker that is not the Mosquitto app (or one outside Home Assistant OS) is set up under
**Settings → Home Assistant → MQTT broker**: choose **My own broker** and enter host, port
(1 to 65,535), TLS, user name and password. A manual broker needs a host, and a password needs
a user name; otherwise the page shows the refusal and nothing is saved. The password is write-only:
the page never shows it. Leave the field empty to keep the stored one, type a new one to replace
it, or tick **Remove the stored password**. It is stored as a secret and is masked in the API, in
exports and in the configuration history.
The **Publish again** button announces all entities again.

Entities go to `unavailable` when SunCore stops (the broker publishes SunCore's last will) and
are announced again when SunCore or Home Assistant restarts.

## 2. What you get

Names and enumerated states follow your language (the region setting, or Home Assistant's
language when it is set to automatic). Automations that test an enumerated state such as
"Automatic" therefore depend on the language. Use the binary sensors and the switch instead.

| Entity | Kind | Meaning |
|---|---|---|
| Status | sensor | Starting, running, emergency stop, Home Assistant unavailable |
| Operating mode | sensor | Shadow, assisted or automatic (overall mode) |
| Available surplus | sensor | The safe surplus the controller sees, in W |
| Healthy, Shadow mode, Emergency stop | binary sensors | For automations and dashboards |
| Peak import this month | sensor | The month's highest 15-minute import, in W (when the grid chain measures it) |
| Grid state | sensor | Normal, limited by the grid operator (§14a), island operation |
| Grid operator limit (§14a) active, Island operation | binary sensors | Only with a signal or backup entity; unknown while that is unavailable |
| Events | event | Device switched, proposal, error |
| Automatic | switch | Overall mode: on = automatic, off = shadow |
| Replan | button | Start a new plan |
| Emergency stop | button | Same as the panel's emergency stop |
| Clear emergency stop | button | Only announced when you allowed raising (below) |

Per configured device: **Mode**, **Power** and **Last decision** (sensors), an **Automatic**
switch, **Boost**, **Pause** and **Resume** buttons and a **Boost duration** number (minutes,
default 60). These entities are not Energy dashboard entities and carry no energy counters.

Read-only views, only where they apply:

- **Wallbox:** charging mode, target charge, target energy and target time (when set), plugged in,
  charging phases and the vehicle's charge (when the charger reports them).
- **Heat pump (SG-Ready):** SG state (1 to 4) and the set-point lift in K.
- **Program:** job state (idle, armed, running) and the planned start.
- **Battery** (own HA device, when a battery adapter exists): action (idle, charge, hold) and who
  holds the control. They show what SunCore holds, so they stay at idle and "own settings" in
  shadow mode.

These are sensors on purpose. Setting the EV mode and target, arming a program and the
comfort band from Home Assistant are not offered yet (see the limits).

## 3. Commands and what is refused

| Command | Result |
|---|---|
| Emergency stop | Always carried out, never rate limited, never refused. |
| Switch off "Automatic" (overall or per device) | Always carried out. Lowering goes to shadow. |
| Pause, Boost, Resume, Replan | Carried out like the panel. A shadow device is still not written. |
| Switch on "Automatic", clear the emergency stop | **Refused** unless you allow it. |

By default Home Assistant may **not raise** the autonomy: switching a device or the whole system
to automatic, or clearing the emergency stop, is refused and the true state is published again.
SunCore tells you once an hour per entity in a notification. To allow it, switch on **Let Home
Assistant raise a mode or clear the emergency stop** under **Settings → Home Assistant
entities**. Do this
only if you trust everyone who can publish to your broker.

Safeguards, all tested:

- Only exact payloads are accepted: `ON`, `OFF`, `PRESS` and plain numbers (at most 64 bytes).
  Anything else is ignored.
- A retained command is ignored and removed from the broker.
- At most one command per second per entity (burst of three) and 20 commands per 10 seconds in
  total. A flood cannot delay the controller. The emergency stop is exempt.
- Entities SunCore did not announce are ignored.
- Every command leaves a line in the app log with its outcome.

## 4. Known limits and UNVERIFIED

- **Not announced yet:** costs, savings, autarky, forecast error and the next action (nothing
  produces them), the battery strategy, reserve and evening target, and every EV, program and
  comfort **control** (EV mode, target, arm, cancel, finish by, comfort band). The controls
  need a command path that arms or sets things; that is open work (plan section 8, 6-O) and
  comes with the companion integration in Phase 7. Until then use the panel for these.
  The event entity only fires for a switched device, a proposal and an error.
  Config changes from Home Assistant (so no "author `mqtt`" in the configuration history) are
  not offered.
- A device raise from Home Assistant carries the device's current identity token, so the
  protection against a re-mapped device does not apply to it. Only the opt-in does.
- Anyone with broker credentials can stop SunCore, pause devices and lower modes. This is by
  design: stopping is always safe.
- `UNVERIFIED` on a real Home Assistant: the default payloads of the MQTT switch, button and
  number platforms, that Home Assistant sends commands with retain off, the `language` key used
  for names, the Supervisor answer for the broker lookup, TLS to the Mosquitto app.
- The RAM and CPU cost with MQTT running has not been measured on a Raspberry Pi
  (`UNVERIFIED`, target below 300 MB).

See also: [Notifications](notifications.md), [Surplus control](surplus-control.md).
