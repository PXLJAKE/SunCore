# Shiftable programs (washing machine, dishwasher, dryer)

A shiftable program is a job that has to run once and does not care when, as long as it is
finished in time: a washing machine, a dishwasher, a dryer. You load the appliance, select the
programme and press start (or arm it), and tell SunCore **when it must be finished**. SunCore
picks the start: with surplus if the forecast allows it, otherwise the cheapest window, and in
any case early enough to meet your deadline.

A short description of the plug and the safety net is also in
[Surplus control](surplus-control.md#shiftable-programs-washing-machine-dishwasher).

## 1. Start methods

| Method (template) | How SunCore starts the appliance |
|---|---|
| **Smart plug** (`generic.program_plug`) | A `switch` plug in front of the appliance. SunCore switches the plug **off** right after arming (while the appliance still draws almost nothing) and **on** at the planned start. The appliance must resume its programme when power returns. |
| **Remote start** (`generic.program_remote`) | A `switch` entity that starts the appliance. It is switched on once and **never off**. A `button` entity is not supported yet. |
| **Notify** (`generic.program_notify`) | SunCore writes nothing. At the planned start it sends a "start the washing machine now" notification and you start the appliance. |

## 2. Set it up

In the setup assistant, step *Devices*, choose the template and map the entities (the switch,
and a **power sensor** of the appliance; a fresh power reading is required for a start). Enter:

- the **run time** of the programme in minutes (the template's 120 is a placeholder),
- the appliance's **peak power** (placeholder in the template) and, if known, the energy per 15
  minutes of a run,
- the **idle power** in W (default 5): below it the appliance counts as finished or idle.

For the **plug** method you must confirm **per appliance** that it resumes its programme after
power returns (the box **The appliance resumes by itself after the power comes back**). Test it by
hand first: start a programme, pull the plug, put it back. Many appliances do not resume, then use
**Notify** or **Remote start**. Also confirm the behaviour (the *behaviour confirmed* checkbox) once
shadow mode looked right; until then the program device runs at most assisted.

## 3. Use it

On the program's card on the **Now** view: load the appliance, set **Finished by** and press
**Arm**. The card shows the job state (armed, scheduled, running, finished, cancelled) and the
planned start. SunCore plans the start as soon as a fresh plan exists. If there is no plan, the
latest start that still meets the deadline applies. **Cancel job** forgets a job that has not
started; a running programme cannot be cancelled by SunCore.

If the deadline passes without a start the job is **missed** and you get a notification.
If the power sensor shows the appliance may have run unseen, the message says so.

## 4. What SunCore never does, and safety behaviour

- **It never interrupts a running programme.** It never switches the plug or a remote-start
  switch off while the job runs, while the appliance draws the idle power or more, while its
  power is unknown, or within the run time plus 15 minutes after SunCore started it. The
  emergency stop and the grid shedding do not touch a running program either.
- **The plug's safe state is ON.** Whenever SunCore lets go of the plug (you cancel the job,
  **Back to normal**, a shutdown or update, a crash, a failed power sensor, a missed job), it
  switches the plug **on**, and a waiting machine then starts at once: "better too early than
  never". The same happens when you lower the mode to shadow.
- **Power-down only at arming:** the plug is switched off only within 10 minutes after arming,
  and only while the appliance is idle. A plug that is on later (you, a release, the recovery)
  stays on for that job.
- **Start conditions:** a fresh power reading of the appliance, a trusted grid reading, not in
  island operation, not blocked by the emergency stop or a watchdog pause. A start that your
  deadline forces may import up to the program's peak power but never beyond the connection
  limit, §14a budget or island rules (see [Grid limits](grid-limits.md)).
- **Shadow:** nothing is switched, the appliance runs as you left it. A **Notify** program still
  asks you to start because SunCore writes nothing in any mode.
- **Crash while waiting:** at the next start the plug is switched on and the programme starts.

### The Home Assistant automation for a plug that stays off

If SunCore is **not running at all** while the plug is off (stopped app, a broken Home
Assistant), nobody switches the plug on. Add a Home Assistant automation that switches it on
when it has been off longer than you would ever delay a programme. The YAML is in
[Surplus control](surplus-control.md#shiftable-programs-washing-machine-dishwasher); replace the
entity id and choose the hours to fit your longest "finished by". SunCore does not create this
automation itself.

## 5. Known limits and UNVERIFIED

- Simulator only: no real washing machine or dishwasher behind a plug.
- Whether an appliance resumes after power returns is unverified per appliance (you confirm it).
- Remote start works with a `switch` only. No integration was checked for a remote-start
  switch.
- Arming works in the panel only. There are no HA entities or MQTT commands for it yet (see
  [Home Assistant entities](home-assistant-entities.md)).
- The plug may be switched off only in the first 10 minutes after arming. Arm just after
  loading.
- An appliance that draws more than the idle power right after start (door lock, pump) counts
  as running, and SunCore does not delay it any more.
- Placeholders in the templates (run time 120 min, idle 5 W, peak power) must be set by you.

See also: [Notifications](notifications.md), [Grid limits](grid-limits.md).
