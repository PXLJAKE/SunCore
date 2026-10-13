# SunCore architecture

Status: Phase 0 blueprint. Decisions referenced as ADR-NNNN live in
[`adr/`](adr/). Requirements are in [`BRIEF.md`](BRIEF.md).

## 1. System context

SunCore is one container, installed as a Home Assistant app (ADR-0010). It
reads everything through Home Assistant, writes through Home Assistant where
an integration exists, and talks to hardware directly only via explicitly
approved adapters (Kostal Modbus, evcc over MQTT).

```mermaid
flowchart LR
    subgraph HA["Home Assistant OS"]
        Core["HA Core<br/>WebSocket + REST"]
        Supervisor["Supervisor<br/>ingress, token, services API"]
        MQTT["Mosquitto app<br/>(optional)"]
        Integrations["Integrations<br/>Kostal, LG ThinQ, Tibber, Solcast, …"]
        subgraph App["SunCore app container"]
            SunCore["SunCore"]
        end
    end
    Browser["Browser / tablet<br/>HA sidebar panel"]
    Inverter["Inverter / battery<br/>(Kostal Plenticore)"]
    Devices["Climate, wallbox,<br/>heat pump, plugs"]
    evcc["evcc (optional actor)"]
    Cloud["Forecast & price APIs<br/>(fallback only)"]

    Browser -- ingress --> Supervisor --> SunCore
    SunCore <-- "subscribe_entities, call_service" --> Core
    Core <--> Integrations <--> Devices
    Integrations <--> Inverter
    SunCore -- "discovery + state" --> MQTT --> Core
    SunCore -- "Modbus TCP (adapter)" --> Inverter
    SunCore -- "MQTT set topics" --> evcc --> Devices
    SunCore -. "Open-Meteo, Energy-Charts" .-> Cloud
```

## 2. Modules

```mermaid
flowchart TB
    subgraph edge["Edge"]
        api["api/<br/>FastAPI REST + WebSocket"]
        frontend["frontend/<br/>Svelte SPA"]
        ha["ha/<br/>HAConnection, entity cache,<br/>service calls, write limiter"]
        mqttpub["ha/mqtt<br/>discovery publisher"]
    end
    subgraph domain["Domain"]
        core["core/<br/>assets, signals, units,<br/>capabilities, Plan/Intent/Decision"]
        forecast["forecast/<br/>PV, load, price, temperature<br/>adapters, blending, calibration"]
        tariff["tariff/<br/>import/export prices,<br/>price formula, regions"]
        planner["planner/<br/>LP/MILP model, heuristic,<br/>explanations"]
        controller["controller/<br/>surplus distribution,<br/>peak shaving, export limit"]
        safety["safety/<br/>invariants, guards,<br/>watchdogs, modes"]
        templates["templates/<br/>schema v1, loader,<br/>role matcher"]
        devices["devices/<br/>generic drivers,<br/>power models"]
    end
    subgraph infra["Infrastructure"]
        storage["storage/<br/>SQLite config + timeseries,<br/>decision log, migrations"]
        sim["sim/<br/>virtual house, scenarios,<br/>backtesting, demo"]
        sched["scheduler<br/>asyncio tasks, solver worker"]
    end

    frontend --> api
    api --> core
    api --> storage
    ha --> core
    forecast --> ha
    tariff --> ha
    planner --> forecast
    planner --> tariff
    planner --> core
    controller --> core
    controller --> devices
    safety --> devices
    devices --> ha
    devices --> templates
    tariff --> templates
    forecast --> templates
    api --> templates
    templates --> core
    mqttpub --> core
    storage --> core
    sim -. replaces .-> ha
    sched --> planner
    sched --> controller
    sched --> forecast
```

Dependency rule: arrows point inward to `core/`; `core/` imports nothing
from the other packages. `planner/`, `controller/` and `safety/` depend on
`core/` types only, never on `ha/`, `api/` or `storage/` – this is what
makes them unit-testable and `mypy --strict` clean (BRIEF §7).

Package map (ADR-0001):

| Package | Responsibility | Depends on |
|---|---|---|
| `suncore/core` | Domain model: `Site`, `Asset` (grid, pv, battery, device), `Signal` (typed time series with unit and quality), `Capability`, `Plan`, `Intent`, `Decision`, `Reason`, units and sign conventions | stdlib, pydantic |
| `suncore/ha` | `HAConnection` (Supervisor or Direct transport), entity subscription with staleness tracking, service calls with read-back, write rate limiter, registry queries, MQTT discovery publisher | core, aiohttp |
| `suncore/forecast` | Adapter protocol + Solcast, Forecast.Solar, Open-Meteo, generic-attribute, built-in model; blending by measured skill; bias calibration; load profiles | core, ha, templates |
| `suncore/tariff` | Import/export price sources (Tibber, Nord Pool, ENTSO-E/EPEX via HACS, fixed, HT/NT), price formula, region profiles, negative-price rules | core, ha, templates |
| `suncore/planner` | Model builder, HiGHS solver worker, heuristic fallback, plan explanation, what-if | core, highspy, numpy |
| `suncore/controller` | Surplus computation, priority allocation, hysteresis state machines, peak shaving, export limiting, §14a limit | core |
| `suncore/safety` | Invariant checks, operating modes, override detection, watchdogs, fail-safe transitions; the only caller of actuators | core, devices |
| `suncore/templates` | Template schema v1 (device, tariff, forecast, region; battery from Phase 4), loader with user overrides, role matcher, read-only action allowlist check (ADR-0012) | core, yaml, pydantic |
| `suncore/devices` | Generic drivers (HA entity, Modbus via HA, MQTT/evcc), power models (measured, counter-derived, learned, nominal), battery adapters | core, ha, templates |
| `suncore/api` | FastAPI app, auth (ingress headers or token), REST, WebSocket live feed, OpenAPI | core, storage, templates |
| `suncore/storage` | SQLite repositories, migrations, downsampling, export/import | core, aiosqlite |
| `suncore/sim` | Virtual house physics, scenario files, fake `HAConnection`, backtest runner | core |
| `suncore/app` | Composition root: settings, scheduler, wiring, health endpoint | all |

## 3. Data flow

```mermaid
flowchart LR
    HAin["HA states<br/>(compressed entity events)"] --> cache["Entity cache<br/>staleness, unit normalisation,<br/>outlier filter"]
    cache --> signals["core Signals<br/>grid W, pv W, battery W/SoC,<br/>house W, device W/Wh, temps"]
    prices["Price sources"] --> tariffmod["tariff/<br/>price formula → €/kWh series"]
    fc["Forecast sources"] --> blend["forecast/<br/>blend + calibrate → P10/P50/P90"]
    signals --> loadfc["forecast/load<br/>profiles from statistics"]
    signals & tariffmod & blend & loadfc & config["Config<br/>devices, limits, goals"] --> plannerN["planner/<br/>every 15 min + events"]
    plannerN --> plan["Plan<br/>bands + reasons"]
    plan & signals --> ctrl["controller/<br/>every 5–10 s"]
    ctrl --> intents["Intents"]
    intents --> safe["safety/<br/>validate, rewrite, reject,<br/>mode & overrides"]
    safe --> act["devices/ drivers<br/>write + read-back"]
    act --> HAout["HA call_service /<br/>Modbus / evcc MQTT"]
    safe --> log["Decision log"]
    plan --> log
    signals & plan & log --> ts["storage/ timeseries"]
    ts & log & plan --> ui["api/ WebSocket → UI<br/>Now, Plan, History, Why?"]
```

Sign and unit conventions (fixed in `core/units.py`, enforced by types):
power in W, energy in Wh, temperature in °C, money in the configured
currency per kWh; grid positive = import, battery positive = charging,
device power positive = consumption, PV positive = generation. Adapters
convert at the boundary (e.g. Kostal reports battery positive = discharging).

## 4. Planner, controller, safety in time

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant P as Planner
    participant C as Controller
    participant F as Safety
    participant D as Device driver
    participant H as Home Assistant

    S->>P: tick (15 min) or event (prices, forecast, deviation, config, override)
    P->>P: build model, solve (≤ 8 s) or heuristic
    P-->>S: Plan {bands per interval, reasons, assumptions}
    loop every 5–10 s
        S->>C: tick with latest Signals + current Plan
        C->>C: surplus, priorities, hysteresis, min run/pause
        C-->>F: Intents (device → target state/power, reason)
        F->>F: invariants, mode, overrides, rate limits, plausibility
        alt accepted (automatic mode)
            F->>D: apply(intent)
            D->>H: call_service / write register
            D->>H: read back
            D-->>F: result
        else shadow / assisted / rejected
            F->>F: log "would have" / "proposed" / "rejected: invariant"
        end
        F-->>S: Decisions → log, UI
    end
    Note over F,H: Watchdog: stale inputs or lost connection → release control, safe state
```

Deviation events: the controller reports when actual SoC, PV or load leave
the plan's assumption corridor; the scheduler then re-plans early (rate
limited to once per 5 minutes).

## 5. Safety state machine (per device and global)

```mermaid
stateDiagram-v2
    [*] --> Shadow
    Shadow --> Assisted: user enables
    Assisted --> Automatic: user enables
    Automatic --> Assisted: user disables
    Assisted --> Shadow: user disables
    Automatic --> Held: manual override detected
    Held --> Automatic: override timeout
    Automatic --> SafeState: invariant tripped / stale data / connection lost
    Assisted --> SafeState: invariant tripped
    SafeState --> Shadow: conditions clear + cool-down
    Automatic --> Shadow: global emergency "back to normal"
    Assisted --> Shadow: global emergency "back to normal"
    Held --> Shadow: global emergency "back to normal"
```

`SafeState` means: stop writing, release hardware control (inverter back
to self-consumption via timeout/heartbeat stop, devices left in their
current user-visible mode), notify. Invariants (BRIEF §5) are pure
functions over `(Intent, Signals, Config)` and are property-tested.

## 6. Device model

```mermaid
classDiagram
    class Asset {
        id
        kind: grid|pv|battery|device
        name
        capabilities: set
        signals: dict[str, Signal]
    }
    class DeviceConfig {
        template_id
        priority: int
        control_kind: onoff|steps|continuous|setpoint|mode
        power_model: PowerModel
        constraints: Constraints
        windows: list[TimeWindow]
        comfort: ComfortBounds
        mode: shadow|assisted|automatic
    }
    class PowerModel {
        <<interface>>
        estimate(signals) PowerEstimate
        learn(history)
    }
    class MeasuredPower
    class CounterDerivedPower {
        latency
        quantisation
    }
    class LearnedPower {
        features: mode, Δtemp, outdoor_temp
    }
    class NominalPower
    class Constraints {
        min_run_s
        min_pause_s
        max_switches_per_h
        hysteresis_w
        hysteresis_delay_s
        ramp_w_per_s
    }
    class Driver {
        <<interface>>
        read() DeviceState
        apply(Intent) WriteResult
        release()
    }
    class HAEntityDriver
    class ModbusDriver
    class EvccDriver
    class Template {
        id
        kind
        schema_version
        capabilities
        entity_mapping
        defaults
        source
    }

    Asset "1" --> "1" DeviceConfig
    DeviceConfig --> PowerModel
    DeviceConfig --> Constraints
    DeviceConfig --> Template
    PowerModel <|.. MeasuredPower
    PowerModel <|.. CounterDerivedPower
    PowerModel <|.. LearnedPower
    PowerModel <|.. NominalPower
    Driver <|.. HAEntityDriver
    Driver <|.. ModbusDriver
    Driver <|.. EvccDriver
    Template --> Driver : selects
```

`PowerEstimate` carries a value and an uncertainty; the controller widens
hysteresis with uncertainty, which is how counter-only devices (LG ThinQ
climate) and measured devices share one control path (BRIEF §4.5).

## 7. Storage layout (ADR-0004)

```mermaid
erDiagram
    CONFIG_DOC {
        text section PK
        int schema_version
        json body
        datetime updated_at
    }
    TEMPLATE_OVERRIDE {
        text template_id PK
        yaml body
    }
    DECISION {
        int id PK
        datetime ts
        text asset_id
        text layer "planner|controller|safety"
        text kind "intent|accepted|rejected|shadow|override"
        json payload
        json reasons
    }
    PLAN_SNAPSHOT {
        int id PK
        datetime issued_at
        json plan_gz
        json assumptions
    }
    LEARNED {
        text key PK
        json value
        datetime updated_at
    }
    SAMPLE {
        datetime ts
        text signal_id
        real value
        int quality
    }
    AGG_1M {
        datetime ts
        text signal_id
        real mean
        real min
        real max
    }
    AGG_15M {
        datetime ts
        text signal_id
        real mean
        real min
        real max
        real energy_wh
    }
    FORECAST_VERSION {
        int id PK
        datetime issued_at
        text source
        text target "pv|load|price|temp"
        json series
    }
    CONFIG_DOC ||--o{ DECISION : "asset ids"
    PLAN_SNAPSHOT ||--o{ DECISION : "explains"
```

`suncore.db` holds the first five tables and is backed up; `timeseries.db`
holds the rest and is excluded from backups.

## 8. Process model inside the container

One Python process (ADR-0001): an asyncio event loop runs the HA
connection, the scheduler (controller tick, planner trigger, forecast
refresh, downsampling), the FastAPI server (Uvicorn in-process) and the MQTT
publisher. The solver runs in a `ProcessPoolExecutor` with one worker so a
slow MILP never delays the controller. s6-overlay (from the HA base image)
supervises the process; `/health` answers for the Supervisor watchdog and
reports HA connection, last controller tick age, last plan age.

```mermaid
flowchart LR
    s6["s6-overlay"] --> py["python -m suncore"]
    subgraph py["suncore process (asyncio)"]
        hacon["HA connection task"]
        tick["Controller tick 5–10 s"]
        ptrig["Planner trigger"]
        fref["Forecast refresh"]
        web["Uvicorn :8099 (ingress)"]
        pub["MQTT publisher"]
        down["Downsampler (nightly)"]
    end
    ptrig --> worker["Solver worker<br/>(ProcessPoolExecutor)"]
    web --> health["/health"]
    health --> Supervisor["Supervisor watchdog"]
```

## 9. Frontend structure (ADR-0002)

Routes: `/` Now, `/plan`, `/history`, `/devices/:id`, `/log`, `/setup`
(wizard), `/settings` (with expert toggle), `/diagnostics`. One WebSocket
subscription delivers `Signals`, current `Plan`, latest `Decisions` and
health; REST for configuration and history queries. All asset paths are
relative so the ingress base path works. Theme tokens map to HA CSS
variable names.

## 10. Cross-cutting

- **Explainability:** every `Plan` interval and every `Decision` carries
  `Reason` objects (`key`, `args`); the UI renders them through the ICU
  catalogue (ADR-0007). No free-text reasons in code.
- **Observability:** structured logs (structlog, no secrets), decision log
  in SQLite, anonymised diagnostics export (config with entity ids hashed,
  last 24 h of decisions, health).
- **Security:** ingress only by default; optional API port with token;
  Supervisor token never logged; dependencies pinned; CodeQL and Dependabot
  in CI; AppArmor profile.
- **Testing:** unit tests per package; property-based tests on Safety and
  the planner model; simulator scenarios as integration tests; Playwright
  E2E against the demo mode; CI smoke test starts the image and polls
  `/health`.
