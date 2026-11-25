# Heat pump

SunCore can use surplus solar power to heat a hot-water store a little more than usual. It does
this in one of two ways:

- **SG-Ready** (template `generic.heat_pump_sg_ready`): two potential-free contacts, switched
  through two Home Assistant `switch` entities, put the heat pump into "normal" or "raised"
  operation.
- **Set-point lift** (template `generic.heat_pump_dhw_setpoint`): a `water_heater` entity whose
  target temperature SunCore raises within a band you set.

**This is the least tested part of SunCore.** No heat pump was available: it has run in the
simulator only. Like every device it starts in shadow mode and runs at most assisted until you
confirm the behaviour.

## 1. Set it up

In the setup assistant, step *Devices*, choose the template and map the entities:

- **SG-Ready:** the two contact switches (A and B) and, strongly recommended, a **store
  temperature probe** (a temperature sensor of the hot-water store). Enter the electrical power
  the pump draws while it runs. Without a probe SunCore never goes beyond state 2.
- **Set-point lift:** the `water_heater` entity. SunCore only writes values inside the entity's
  own minimum and maximum.
- Both: the **comfort band** (default 45 to 55 °C) and the *behaviour confirmed* checkbox
  ("I checked the behaviour of this device and confirm it") once shadow mode looked right.

The default contact mapping is **normal = A off, B off; raised = A off, B on; forced = A on, B
on; (block = A on, B off)**. This is the common wiring but the SG-Ready label document was not
read: **check your heat pump's manual before you switch to assisted.**

## 2. What SunCore does

| Aspect | Behaviour |
|---|---|
| SG state 2 (normal) | The resting state. |
| SG state 3 (raised) | Used when there is surplus and the store is below the comfort maximum. |
| SG state 4 (forced on) | **Only with your explicit opt-in** (`allow_forced`). Off by default. |
| SG state 1 (utility block) | **Never.** SunCore does not write it, and the two contacts are switched one after the other so that a change never passes through it. |
| Dwell | At least 10 minutes between two state changes, besides the minimum run (20 min), the minimum pause (10 min) and 3 switches per hour. |
| Comfort band | Never raised at or above the band's maximum, and not with an unknown or stale probe. Restart hysteresis: 3 K below the maximum. |
| Set-point lift | Up to 5 K in 0.5 K steps, inside the comfort band, never below your own set-point. |
| Fail-safe | Back to state 2 (or your own set-point) at once, ignoring dwell and switch limits, when the probe is unknown or stale, the heat limit is reached, there is no probe, §14a is active (device flagged) or the house is in island operation. Nothing is raised while such a cause holds. |
| Release | An emergency stop, shadow mode, a trip or a stopped app restore the contacts or the set-point you had before. A lock someone else set (state 1, or state 4 without opt-in) is left as it is, never lifted. |

## 3. Planning, model and COP

The planner treats the store as a first-order thermal model. When the learner has enough history
(about two weeks of probe, power and weather data, 15-minute resolution) it fits the heat loss
against the **outdoor** temperature and the pump's **COP** against the outdoor temperature. Until
then the planner uses a generic air-to-water COP curve and an energy-only model. The device card
shows the **SG state**, the **lift**, the COP range (marked "assumed" when it is the generic
curve) and the learned model's error. It does not show the COP at the current weather yet.

The plan pre-heats the store with surplus. Pre-heating with cheap **grid** power is planned
only if you opt in (`grid_preheat`), and **the controller does not execute it yet.**

## 4. Known limits and UNVERIFIED

- Simulator only. The contact mapping, the vendor's SG-Ready behaviour and which integrations
  expose hot water as a `water_heater` (and how fast they apply a target) are unverified.
- The template defaults (dwell 600 s, minimum run 1200 s, pause 600 s, 3 per hour, comfort band
  45 to 55 °C, restart 3 K) are conservative design values, not manufacturer or legionella
  rules.
- The model learns losses against the outdoor temperature. A store indoors loses to the room,
  and a small fast store stays energy-only with 15-minute data.
- A set-point lift with an unknown temperature has no extra fail-safe: the set-point itself is
  bounded by the comfort band and the entity's range.
- There is no SG-Ready-through-a-select template and no heat-pump entities in Home Assistant
  yet (see [Home Assistant entities](home-assistant-entities.md)). Heating circuits, cooling
  and space heating are not controlled.

See also: [Surplus control](surplus-control.md), [Grid limits](grid-limits.md),
[Planning and battery control](planning-and-battery.md).
