# Notifications

SunCore tells you about errors, safety events, assisted proposals, finished or missed programs
and, if you want, cheap electricity and a daily summary. Messages are written in your language,
rate limited and never sent in a flood. **The notification layer never switches anything.** The
only thing a notification can do is confirm one assisted proposal, exactly like the **Confirm**
button in the panel (see [Surplus control](surplus-control.md#2-operating-modes)).

## 1. Set it up

Open **Settings → Notifications**.

1. **Persistent notifications in Home Assistant** are on by default. Errors and safety events
   appear in the Home Assistant notification list without any further setup, and disappear when
   the cause is gone.
2. For push messages add a **push target**. Two kinds:
   - a Home Assistant **notify entity**, picked from a list, or
   - a **notify service** such as `mobile_app_phone` (type the part after `notify.`). Only the
     Companion app services can carry the **Confirm** and **Reject** buttons.
   Up to eight targets. Without a target no push message is sent (persistent ones still are).
3. Choose the **categories** (table below), **quiet hours** and the **daily push limit**.
4. **Send a test message** checks the whole way. A second test within a few seconds is refused.

## 2. What is announced

| Category | Default | Examples | Quiet hours |
|---|---|---|---|
| Errors | on, persistent | a device tripped by the watchdog, three failed writes in a row, the planner has produced no plan for 1 h, MQTT lost for 15 min | not held back |
| Safety | on, persistent | emergency stop and its end, island operation and its end, grid operator limit (§14a) and its end, a Home Assistant raise that was refused | not held back |
| Proposals | on | an assisted proposal, with buttons where possible | held back |
| Programs | on | "start the washing machine now", finished, missed | only "start now" is not held back; "finished" and "missed" are dropped |
| Charging target | on | "EV target at risk": the plan cannot reach the target by its time (once per target, after it held for 10 min) | held back |
| Cheap window | off | the cheapest block of at least one hour below the 25th percentile of the next 24 h | held back |
| Daily summary | off | mode and number of switches, at 20:00 | not held back |

Defaults for the rest: quiet hours 22:00 to 07:00, **20 push messages per day** at most (0
switches push off). The same message is sent once per cause (it carries the proposal, the job,
the window or the date), errors at most every 30 minutes. Safety events never wait for quiet
hours or count against the daily limit. Island and §14a: the start is announced at once,
the end only after the signal stayed off for 2 minutes, so a flickering signal gives one start
and one end. A program's "start now" is not held back by quiet
hours, and "start now" and "missed" ignore the daily limit: for a notify-only program that
message is the only way to start the appliance.

The MQTT **Events** entity is separate: it fires for a switched device, a proposal and an
error, not for every notification (see
[Home Assistant entities](home-assistant-entities.md)).

## 3. Confirming a proposal from the phone

In assisted mode a proposal says what SunCore would do and for how long it is valid (2 minutes).
With a Companion app target the notification has **Confirm** and **Reject**.

- Each button carries a **single-use random token** that exists only in SunCore's memory. A
  restart voids all open tokens. At most 32 proposals are open at once.
- **Confirm** runs the proposal through the safety layer again; it can still be rejected (for
  example when the device's mode is no longer assisted, or the proposal expired).
- **Reject** only closes the proposal; nothing is called.
- Without a Companion app service (an entity target) or when Home Assistant does not let SunCore
  receive button taps, the message has no buttons and points to the panel. **Settings →
  Notifications** shows **Buttons work**, **Checking buttons** or **Buttons refused**.

## 4. Known limits and UNVERIFIED

- Island and §14a messages need a source: a backup entity (island) or a §14a signal in the
  grid section. A source that is unavailable counts as "no information": nothing is announced
  and nothing is called "ended". **EV target at risk** needs a charger in mode **Target** and
  a plan made after the target was set; the plan's own shortfall is the only trigger (a target
  the plan meets but the controller later misses is not announced). Quiet hours drop these
  messages; they are not delivered later.
- Proposals that arrive during quiet hours are dropped, not delivered in the morning (they
  expire after minutes anyway).
- The list of `notify` services is not read from Home Assistant; you type the name.
- Residual risk: anyone who can fire events on the Home Assistant bus and also reads a
  notification's token (the phone, or the Home Assistant event log) can confirm that one
  proposal within its lifetime. The effect is limited to what **Confirm** in the panel does.
  The token travels through the Home Assistant notify service and the push provider, as with
  every actionable notification.
- A refusal of the button subscription after a reconnect is only noticed when the renewal
  fails; the buttons then switch to plain messages.
- `UNVERIFIED` on a real Home Assistant: the payload of the `mobile_app_notification_action`
  event beyond `data.action`, whether the Supervisor token may subscribe to it (otherwise
  messages stay plain), `data.actions` on every legacy notify service.
- In shadow mode a program with the start method "notify" still asks you to start the
  appliance, because nothing is switched by SunCore.

See also: [Programs](programs.md), [Home Assistant entities](home-assistant-entities.md).
