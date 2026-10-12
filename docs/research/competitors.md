# evcc, EMHASS, Predbat – concepts, limits, what SunCore takes from them

## evcc

- Go, MIT licence; some components require a sponsorship token (the
  optimizer among them). Runs as an HA app; REST and MQTT API.
- **Concepts:** site (grid, PV, battery meters), loadpoints (chargers +
  vehicles), modes `off` / `smart` (formerly pv/minpv, now "smart" with
  surplus, cheap-grid and plan rules) / `now` (fast) and "always charge"
  (never below min power); heat pumps (`normal`/`smart`/`boost` via SG-Ready),
  smart plugs (`off`/`smart`/`on`); per-device price thresholds
  (`smartCostLimit`) and feed-in priority thresholds; load management with
  nested `circuits` (`maxCurrent`, `maxPower`, optional meter, external
  limits such as §14a); home battery: `prioritySoc`, `bufferSoc`,
  `bufferStartSoc`, discharge lock during fast/planned charging
  (`batteryDischargeControl`), battery boost, grid charging below
  `batteryGridChargeLimit` with optional `maxsoc`; capacity-weighted SoC for
  multiple batteries.
- **Optimizer** (2026): experimental, informational only, runs as a
  stateless cloud service (`optimizer.evcc.io`, self-hostable), requires
  sponsorship. Automatic control is a planned step.
- **Limits stated in its own docs:** load management is experimental,
  loadpoint `priority` not yet honoured, no rebalancing between running
  sessions. Dynamic feed-in handles high prices (pause charging) but the docs
  page does not describe negative prices or curtailment.
- **MQTT write API:** `evcc/loadpoints/<id>/{mode,minCurrent,maxCurrent,
  phasesConfigured,limitSoc,limitEnergy,planEnergy}/set`,
  `evcc/site/{bufferSoc,prioritySoc,residualPower,batteryDischargeControl,
  batteryGridChargeLimit,smartCostLimit}/set`,
  `evcc/vehicles/<name>/{limitSoc,minSoc,planSoc}/set`; plan payload
  `{"value": 50, "time": "2026-03-05T07:00:00Z"}`.
- **What SunCore adopts:** the mode vocabulary for wallboxes and smart
  plugs, `prioritySoc`/`bufferSoc` semantics as the basis of the "battery
  first until X %" rules, the discharge-lock concept, circuits as the model
  for grid limits. **evcc as actor:** SunCore sets `mode`, `smartCostLimit`,
  plan targets and `limitSoc` over MQTT and reads `evcc/site/*` back; evcc
  keeps regulating current and phases.

## EMHASS

- Python, MIT. LP via PuLP (solver choice configurable). Add-on, Docker or
  pip install; REST `POST /action/{dayahead-optim,naive-mpc-optim,
  perfect-optim,publish-data}` with runtime parameters as JSON.
- **Model:** objective maximises profit (import cost − export revenue), or
  cost only, or self-consumption (big-M weighting); optional inverter stress
  and SoC penalties. Variables: grid import/export, battery charge/discharge,
  deferrable load power; hybrid-inverter variant with binaries. Battery:
  cumulative SoC bounds with charge/discharge efficiencies, power caps,
  derating, final SoC = initial SoC. Deferrable loads: total operating hours
  within a window, semi-continuous power, limits on on-time and start-ups;
  thermal model documented separately. No simultaneous import and export.
- **Forecasts:** PV via Open-Meteo + pvlib (default), Solcast (P10 blending
  option), Forecast.Solar, or lists; load via naive persistence, "typical"
  clustered profiles, or an ML forecaster (skforecast); prices via peak/
  off-peak periods, CSV or lists passed at runtime.
- **Modes:** day-ahead plan once a day, MPC every 5–15 min as course
  correction. Results published as `sensor.*` entities for automations.
- **Limits (own docs):** configuration surface is large and changed between
  versions (`config_emhass.yaml` + `options.json` → `config.json`,
  renamed parameters); users must build the HA automations that feed data in
  and act on the published sensors; no real-time surplus controller; no
  device drivers.
- **What SunCore adopts:** the LP structure (power balance, battery SoC
  dynamics with separate efficiencies, semi-continuous deferrable loads,
  no simultaneous import/export), the day-ahead + MPC split, P10 blending
  as a conservatism knob, the idea of a thermal model for heat loads. SunCore
  differs by owning forecasts, devices and the real-time controller itself
  and by explaining decisions.

## Predbat

- Python inside HA (HACS project folder + app), copyright Trefor Southwell,
  **free for personal use only** – not an OSI licence; a paid cloud variant
  exists. No code may be copied into SunCore (BRIEF §0.9).
- **Model:** runs every 5 min, 48 h horizon in 30-min slots; simulates the
  home (battery, PV, load, import/export) and searches charge/export windows
  for the lowest cost within limits; outputs Base/Best/Base10/Best10 plans
  (PV50 vs PV10 weighting). Load from HA history, optional neural-net load
  model. Tariffs: Octopus (incl. Intelligent), Kraken-based suppliers,
  Energidataservice, Nord Pool or any spot sensor, manual rates.
- **Modes:** Monitor → Control SoC only → Control charge → Control charge &
  discharge, plus `set_read_only`. Safety knobs: `best_soc_min` (hard floor),
  `best_soc_keep` (soft floor), `set_reserve_min`, `metric_battery_cycle`
  (virtual €/kWh cycling cost), `metric_min_improvement`.
- **Limits:** inverter-centric (per-brand control paths), no general
  consumer model beyond car/iBoost, UK-tariff centric, licence.
- **What SunCore adopts (concepts only):** the mode ladder (monitor →
  partial → full control), hard vs soft SoC floors, cycling cost and
  "minimum improvement" thresholds to avoid churn, Best vs Best10 plan
  comparison in the UI.

## Positioning for the README

- evcc: best-in-class real-time charging control and device breadth; use it
  as the charge-point actor. Its optimiser is cloud-based and sponsor-gated.
- EMHASS: strong, transparent LP; weak on setup, devices and real-time
  control.
- Predbat: strong battery planning for supported inverters; personal-use
  licence, UK focus.
- SunCore: one app that combines forecasts, planner and real-time controller
  with a guided setup, shadow mode and explanations, under Apache-2.0.

## Verification

Verified on 2026-10-12 against `evcc-io/evcc` README, `evcc-io/docs`
`src/content/docs/en/features/{battery,loadmanagement,modes,optimizer,
dynamic-feedin}.mdx` and `integrations/mqtt-api.md`; `davidusb-geek/emhass`
README, `docs/{advanced_math_model,main_core_concepts,forecasts}.md`;
`springfall2008/batpred` README, `docs/{what-does-predbat-do,customisation}.md`.

`UNVERIFIED`: evcc REST API paths (only the MQTT API page was reachable;
the REST page was not found under `integrations/`), evcc negative-price
behaviour.
