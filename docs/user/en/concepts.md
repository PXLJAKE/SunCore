# Concepts

This page explains the four ideas you need to understand SunCore: what the **planner** does, what
the **controller** does, what the **safety layer** guarantees and what the three **operating
modes** mean. The guides linked at the end go into detail.

## The big picture

SunCore works on two time scales.

| Part | Time scale | Job |
|---|---|---|
| Forecasts | hours to days | Expected solar production, house load and prices. |
| Planner | every 15 minutes and on events | A plan for the next 48 hours in 15 minute steps. |
| Controller | every 5 seconds | Distributes the real surplus to devices and the battery. |
| Safety layer | on every single write | Judges each action again and is the only writer. |

The planner looks ahead and sets **bands and targets**, for example "keep the battery first until
the morning buffer is reached". The controller follows those bands in real time with what the
sun and the house are actually doing. A plan that is missing or stale only means fewer
restrictions: the controller then behaves as it would without a plan.

## Planner

The planner solves an optimisation problem (linear programming with the HiGHS solver) over
forecast production, forecast load, your tariff and the battery. A rule-based plan covers the
case that the solver does not finish in its time budget. What you choose is a **strategy**
(self-consumption, keep the battery as full as possible, or a target charge by a time), a
**risk level** (cautious by default) and optional grid charging (off by default).

Every plan interval has a **reason** in plain language. The **Plan** view shows the plan against
measured values, and **Why?** explains a single interval. See
[Planning and battery control](planning-and-battery.md) and [Plan chart](plan-chart.md).

A plan is a forecast, not a promise. The planner has not run against real hardware yet.

## Controller

The controller runs every 5 seconds (app option `controller_interval_s`). It computes the free
surplus, smooths it against cloud flicker, serves devices in your priority order and applies
hysteresis and protection times. Its central rule: **controlled devices may only use real
surplus**, so SunCore itself never causes grid import. The controller only **proposes** what to
do. See [Surplus control](surplus-control.md).

## Safety layer

Every write to a device or the battery passes the safety layer. It is the only part allowed to
touch a device. It checks each action against invariants (for example no grid import caused by
SunCore, limits of the connection, minimum run and pause times) and can reject it with a reason.
It also provides:

- **Respect for manual changes:** if you change a device yourself, SunCore backs off.
- **Hand-back:** the state from before SunCore took over is restored on shutdown, on the
  emergency stop, on connection loss and when you lower a mode.
- **Emergency stop:** a button in the header of every view. It hands every device back within
  10 seconds, writes nothing until you end it and survives a restart.
- **Honest data:** values that are outdated, unavailable or invalid are marked as such and are
  not guessed.

## Operating modes

There are three modes, per device and for the whole system (the overall mode).

| Mode | What SunCore does |
|---|---|
| **Shadow** (default) | Calculates and logs what it **would have** done. Writes nothing. |
| **Assisted** | Proposes actions in the panel and waits for your confirmation. |
| **Automatic** | Acts on its own within all limits. |

A device acts in the **lower** of the overall mode and its own mode. Every new device starts in
shadow mode, an import of a configuration resets the overall mode to shadow, and a stored
document cannot raise a mode by itself. The recommended path: leave everything in shadow mode,
read the **Decision log** for a day or two, then switch **one** device to assisted, then to
automatic.

## Where to read on

- [Surplus control](surplus-control.md) for the controller and the modes.
- [Planning and battery control](planning-and-battery.md) for forecasts, tariffs and the plan.
- [Decision log and "Why?"](decision-log.md) for the reasons behind every decision.
- [Architecture](https://github.com/pxljake/suncore/blob/main/docs/architecture.md) for the
  module layout (engineering document).
