# EV charging (wallbox, evcc)

SunCore can charge an electric car in two ways:

- **Directly** through the Home Assistant entities of your wallbox (template `generic.wallbox`):
  SunCore sets the charging current, pauses and resumes, and can switch between one and three
  phases where the wallbox allows it.
- **Through evcc** (template `evcc.loadpoint`): SunCore tells evcc what it may do and evcc
  regulates current and phases. See [section 5](#5-evcc-as-actor).

Both follow the same rule: **the car is charged from surplus unless you or the plan allow grid
power.** Like every device, a charger starts in **shadow mode** (nothing is written) and runs at
most **assisted** until you have confirmed its behaviour.

## 1. Set it up

In the setup assistant, step *Devices*, choose **Choose** on the wallbox template and map:

| Role | Entity | Needed |
|---|---|---|
| Charging current | a `number` in A | yes |
| Pause/resume | a `switch` of the same device | yes |
| Phases | a `select` (1 or 3 phases) | only for phase switching |
| Charger power | a power sensor | optional (better power estimate) |
| Vehicle charge | a battery sensor in % | optional (SoC targets) |
| Plugged in | a `plug` binary sensor | optional (a charger known to be unplugged is not charged) |

Then enter the **real** maximum current of your charger and circuit. The template's 16 A is a
placeholder. The minimum is 6 A. Tick **I checked the behaviour of this device and confirm it**
once you have watched it in shadow mode. Without it the charger never goes beyond
assisted, whatever you set. For the vehicle enter the battery capacity (needed for a SoC) and
the charging efficiency (default 90 %, the losses between wall and battery).

No integration template has been checked against its source yet; only the generic one exists
(see the limits). Check the [compatibility list](compatibility.md).

## 2. Charging modes

Choose the mode on the charger's card on the **Now** view (default **PV**).

| Mode | What SunCore does |
|---|---|
| **Off** | No charging. |
| **Now** | The maximum current at once. May draw from the grid, within your connection limit. |
| **Min + PV** | At least the smallest current (6 A, may come from the grid), the rest from surplus. |
| **PV** | Surplus only. The default. |
| **Target** | A charge level (or an amount of energy) by a time. The planner picks the cheapest windows and releases grid power only where the plan allows it, the rest comes from surplus. |

Grid power for the car is a bounded exception: **Now**, **Min + PV** and **Target** may import
only up to the stated amount and never above the import limit, §14a budget or island rules (see
[Grid limits](grid-limits.md)). The deficit back-off never goes below the 6 A floor, but
shedding above the connection limit does.

Other rules:

- **6 A floor and hysteresis.** Below 6 A SunCore pauses (0 A). Current steps follow the
  surplus with the usual hysteresis, minimum run and pause times (300 s by default).
- **1/3-phase switching** is opt-in and needs a phases entity and a three-phase charger. SunCore
  pauses first, switches only after a pause of 120 s, resumes after another 120 s, at most four
  times an hour, and never in **Now**. It goes to one phase when the usable surplus stays below
  the three-phase minimum for 5 minutes, back to three phases with 500 W more for 5 minutes.
- **Targets** use the vehicle's charge (if mapped) or energy. The efficiency counts the losses.
  With a target the card shows "Target: 80 % by 07:30".
- **Unplugged or unknown inputs:** an unplugged car is not charged, and with unknown grid or
  surplus values the current is not raised and drops to the minimum.
- **The house battery never discharges into the car.** Grid-powered charging (**Now**, floor,
  **Target** windows) only starts while there is no home battery or SunCore holds a confirmed
  discharge lock on it. While SunCore may not control the battery (shadow mode) it cannot set
  that lock, so the car charges from surplus only, even in **Now** or **Min + PV**. While the
  car charges from the grid the house is also served by the grid, not the battery.

## 3. Safety behaviour

- **Shadow:** nothing is written; the decision log shows what SunCore would have set.
- **Assisted:** SunCore proposes a current or a pause, you confirm. Every write is re-checked.
- **Hand-back:** an emergency stop, shadow mode, a watchdog trip, a stopped app or a lost
  connection restores what the wallbox had before: the current, the phases and the enable
  switch. **Back to normal** after an emergency stop does not start anything by itself.
- A release that must wait (phase pause, running §14a reduction) leaves the charger paused,
  which is the safe state, and restores it afterwards. If SunCore stops while a staged release
  waits, the charger stays paused until SunCore runs again.
- A change you make on the wallbox or in its app is recognised and respected (hold until
  04:00, see [Surplus control](surplus-control.md#4-if-you-change-something-yourself)).

## 4. Known limits (wallbox)

- Simulator only: no wallbox was available. `UNVERIFIED`: that a charger accepts a phase change
  while paused and how long it needs, defaults for cloud-polled chargers (the `wallbox`
  integration polls every 90 s; give such a charger a longer settle and run time).
- Only a generic template; an integration template needs a read of that integration's source.
- One vehicle per charger, no vehicle-to-home. No EV entities in Home Assistant yet (see
  [Home Assistant entities](home-assistant-entities.md)); mode and target are set in the panel.
- A target is planned with the time that is really left: when a plan is made late in a
  quarter hour, only the rest of that quarter counts, and a target that cannot be reached is
  reported. A hard deadline at any price is an open question.
- Without a battery lock a **Target** window cannot charge from the grid while a home battery
  exists and is in shadow mode.

## 5. evcc as actor

With the template `evcc.loadpoint` SunCore does not drive the wallbox itself. It tells evcc
what it may do over evcc's MQTT interface and evcc regulates the current and the phases.

- **What is written** (and nothing else): the loadpoint mode (`off`, `smart`, `now`), the
  maximum current and the charge limit. SunCore never touches evcc's battery settings, never
  writes a vehicle plan, never `minCurrent` or the phase configuration.
- **Mapping:** **Off** becomes `off`; **PV** becomes `smart`; **Min + PV** also becomes `smart`
  because current evcc has no minimum-power mode, so **no 6 A floor is enforced**; **Now**, or a
  planned **Target** window, becomes `now` with a maximum current that fits your allowance, the
  import limit and the §14a budget, and only while no home battery can discharge into the car.
- **Setup:** it needs MQTT (the broker of the `mqtt` section, so evcc and SunCore must use the
  same broker). The loadpoint id defaults to 1 and the topic prefix to `evcc`. The wizard has
  no field for them yet; set `loadpoint_id` and `topic_prefix` in the device configuration if
  yours differ. Keep evcc's own battery rules (buffer SoC and similar) neutral.
- **Release:** an emergency stop, shadow mode, a trip or a stopped app restores evcc's mode,
  maximum current and limit as they were at takeover, and SunCore waits for evcc's echo before
  it counts as done. A failed release is retried with a growing delay (30 s up to 10 min).
- **Your own change in evcc** (its app, its UI) is respected like any manual change. evcc
  resets the mode when the vehicle is unplugged; SunCore may read that as a takeover and wait
  until 04:00 the next day (`UNVERIFIED`, to measure with a real evcc).

### Residual risks and caveats of the evcc path

- **`smart` mode is reactive for the import limit.** evcc can import by its own rules (a
  vehicle plan, a smart-cost limit, "always charge"). SunCore only sees the measured import and
  then lowers evcc's maximum current or switches it off: an overshoot of up to about **one
  minute** (60 to 90 s with noisy import) is possible. The gate cannot prevent it, because it
  only sees what evcc *may* do. Your fuse and your grid operator's limit stay your own
  protection. To avoid flapping, SunCore switches off only after a minute over the limit and
  back on only after five minutes with some room; above the fuse limit it switches off at once.
- **No battery lock in `smart`.** evcc's own battery rules can discharge the home battery into
  the car. Only `now` and planned grid charging wait for SunCore's lock.
- **Liveness depends on evcc's publication interval.** SunCore treats evcc as gone when
  `<prefix>/updated` is older than 120 s, and then writes nothing. A very quiet evcc that
  publishes less often can trip this falsely. A clock in evcc that runs ahead makes an old
  retained timestamp look fresh. After a broker reconnect, a stale retained value never makes
  a dead evcc look alive.
- Releasing while evcc is silent is refused and retried until evcc shows the baseline again.
- `UNVERIFIED`: everything runs against a fake evcc only. Loadpoint ids from 1, the retained
  read topics, the heartbeat and the setter payloads were read from evcc's source; clearing a
  plan over MQTT, how fast evcc applies a new maximum current and the mode reset at unplugging
  were not verified.

See also: [Surplus control](surplus-control.md), [Grid limits](grid-limits.md),
[Planning and battery control](planning-and-battery.md).
