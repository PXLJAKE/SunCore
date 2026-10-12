# Research notes

Short, sourced notes that back the ADRs in [`../adr/`](../adr/). Each note
ends with a verification block. Facts that could not be verified from a
primary source are marked `UNVERIFIED` and listed in
[`../STATUS.md`](../STATUS.md).

| Note | Covers |
|---|---|
| [`ha-apps.md`](ha-apps.md) | App (formerly add-on) repository layout, `config.yaml`, ingress, Supervisor API, build and publish tooling, linter, architectures |
| [`ha-websocket-api.md`](ha-websocket-api.md) | WebSocket API commands, entity subscription format, energy preferences, long-term statistics, weather forecasts |
| [`mqtt-discovery.md`](mqtt-discovery.md) | MQTT discovery payloads, device-based discovery, broker credentials via Supervisor |
| [`competitors.md`](competitors.md) | evcc, EMHASS, Predbat: concepts, limits, licences, what SunCore adopts |
| [`integrations.md`](integrations.md) | Solcast, Forecast.Solar, Open-Meteo Solar Forecast, Tibber, Nord Pool, ENTSO-E, EPEX Spot, Octopus, LG ThinQ, Kostal Plenticore (REST), SunSpec |
| [`kostal-plenticore-modbus.md`](kostal-plenticore-modbus.md) | External battery control over Modbus TCP: what is known, what is `UNVERIFIED` |
| [`solver-on-aarch64.md`](solver-on-aarch64.md) | HiGHS / `highspy` and SciPy wheel availability for the target platforms |

## Network access during Phase 0

This research was done from a cloud session whose network policy allows
`github.com`, `raw.githubusercontent.com`, `pypi.org` and `registry.npmjs.org`
but **not** `home-assistant.io`, `developers.home-assistant.io`,
`docs.evcc.io`, `*.readthedocs.io`, `*.github.io`, `open-meteo.com`,
`weblate.org`, `api.energy-charts.info`, `api.awattar.de` or
`kostal-solar-electric.com`.

Where the rendered documentation was unreachable, the notes cite the
documentation **source files** in the official GitHub repositories
(`home-assistant/developers.home-assistant`, `home-assistant/home-assistant.io`,
`home-assistant/core`, `home-assistant/supervisor`, `evcc-io/docs`,
`davidusb-geek/emhass`, `springfall2008/batpred`). These are the same content
the sites render. Where no source file was reachable the item is `UNVERIFIED`.
