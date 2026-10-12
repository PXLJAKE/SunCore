# SunCore

**Energy management for Home Assistant: forecasts, a cost-optimising
planner and a real-time surplus controller in one app – explainable, safe
by default, set up in minutes.**

> Status: **pre-alpha, Phase 1 (app skeleton)**. A beta app with a live
> energy-flow view is available once the first beta is released. It runs in
> shadow mode and switches nothing. See the [roadmap](docs/roadmap.md) and
> [status](docs/STATUS.md).

![SunCore "Now" view: live energy flow between solar, grid, battery and house](docs/assets/screenshots/now-tablet-light-en.png)

*Screenshot from the built-in demo mode (simulated data).*

SunCore runs as a Home Assistant app (formerly add-on) on Home Assistant
OS. It reads your PV, battery, grid and consumer entities, forecasts
production, load and prices, plans the next 24–48 h with a linear
optimiser, and distributes real surplus every few seconds to air
conditioners, heat pumps, water heaters, wallboxes and switchable loads –
while a safety layer guarantees hard limits are never crossed.

## Why another EMS?

| | evcc | EMHASS | Predbat | SunCore |
|---|---|---|---|---|
| Real-time surplus control | excellent (chargers, heaters, plugs) | – | – | yes, for all consumer types |
| Forward-looking optimisation | cloud optimiser, sponsor-gated, informational | strong LP, you wire it up | strong for supported inverters | built in, local, explainable |
| Setup | YAML + UI | JSON config + automations | HA entities + YAML | guided wizard, auto-detection, shadow mode |
| Explains decisions | partly | no | plan tables | every decision has a "why" |
| Licence | MIT (+ sponsor features) | MIT | personal use only | Apache-2.0 |

When to use what: keep **evcc** if you want best-in-class charge-point
control – SunCore can use it as an actor. Use **EMHASS** if you like
building your own automations around an LP. Use **Predbat** for GivEnergy &
co. battery arbitrage on UK tariffs. SunCore aims at the household that
wants all of it with a 15-minute setup, and it is honest about being
young: see the roadmap.

## Principles

- Productive in 15 minutes: entity auto-detection, live sign and unit
  checks, shadow mode first.
- Explainable: every plan interval and every switch has a reason in plain
  language.
- Safe: shadow → assisted → automatic per device; fail-safe states;
  watchdogs; invariants that are property-tested.
- Simple for beginners, deep for experts: good defaults, an expert switch
  that exposes every parameter.
- Local and private: no cloud requirement, no telemetry.
- Community-extensible: devices, tariffs, forecasts, regions as YAML
  templates with schema validation.

## Documentation

- [Brief](docs/BRIEF.md) – the product and engineering specification
- [Architecture](docs/architecture.md) · [ADRs](docs/adr/) ·
  [Research notes](docs/research/)
- [Quick start](docs/user/en/quick-start.md) ·
  [Schnellstart](docs/user/de/quick-start.md)
- [Roadmap](docs/roadmap.md) · [Status](docs/STATUS.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) ·
  [Code of Conduct](CODE_OF_CONDUCT.md)

## Installing

Once the first beta is released, add `https://github.com/pxljake/suncore`
as an app repository in Home Assistant (Settings → Apps → App store →
menu → Repositories) and install **SunCore (beta)**. Phase 1 only shows the
live energy flow; there are no forecasts, planner or device control yet.
Start with the [quick start](docs/user/en/quick-start.md)
([Deutsch](docs/user/de/quick-start.md)).

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
