# SunCore documentation

SunCore is a Home Assistant app that forecasts solar production, load and prices, plans the next
24 to 48 hours and distributes your real solar surplus every few seconds to consumers and the
battery. Every write to a device passes a safety layer, and shadow mode is the default: nothing
is switched until you say so.

SunCore is **pre-alpha**. Nothing of the planner, the battery control, EV charging, heat pump
control or the grid limits has run against real hardware yet. Read
[What SunCore cannot do yet](quick-start.md#what-suncore-cannot-do-yet) before you rely on it.

## Start here

- [Quick start](quick-start.md): install the app and go through the setup assistant.
- [Concepts](concepts.md): planner, controller, safety layer and the three operating modes.
- [FAQ](faq.md) and [Troubleshooting](troubleshooting.md).

## Guides

- [Surplus control](surplus-control.md), [Planning and battery control](planning-and-battery.md),
  [EV charging and evcc](ev-charging.md), [Heat pump](heat-pump.md), [Programs](programs.md) and
  [Grid limits](grid-limits.md).
- [History and analysis](history-and-analysis.md), [Decision log and "Why?"](decision-log.md),
  [Plan chart](plan-chart.md), [What-if and backtesting](what-if-and-backtesting.md) and
  [Expert mode](expert-mode.md).
- [Home Assistant entities](home-assistant-entities.md),
  [Notifications](notifications.md) and the [Dashboard recipe](dashboard-recipe.md).

## Reference

- [Compatibility list](compatibility.md) and
  [Contribute a device template](device-templates.md).
- [Parameter reference](reference/parameters.md).
- [Release notes and upgrade](release-notes.md), [Security](security.md) and
  [Translate](translate.md).

## Engineering documents

The specification, the architecture, the decision records (ADRs) and the roadmap are not part of
this site. They live in the
[repository on GitHub](https://github.com/pxljake/suncore/tree/main/docs) and change with the
code.
