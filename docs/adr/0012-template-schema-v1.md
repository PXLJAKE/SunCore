# ADR-0012: Template schema v1 – review before freeze

- **Status:** accepted (lead engineer on 2026-10-13, maintainer delegated "mach fertig")
- **Date:** 2026-10-13
- **Deciders:** maintainer, lead engineer (review by `architect`, Phase 2 WP2)
- **Research:**
  [`device-integrations.md`](../research/device-integrations.md) (LG ThinQ,
  Tibber, Solcast, verified 2026-10-13 against `home-assistant/core`
  `14f093e` and `BJReplay/ha-solcast-solar` `3ae22af`),
  [`ha-websocket-api.md`](../research/ha-websocket-api.md) (registries,
  energy prefs), BRIEF §4.3–4.5, ADR-0006, ADR-0011,
  [`plans/phase-2.md`](../plans/phase-2.md) WP2/WP3

## Context

Commit `087b5a1` delivers the first cut of the device model
(`suncore/core/devices.py`, `suncore/core/entities.py`) and the template
schema v1 (`suncore/devices/templates/{models,matcher,loader}.py`, generated
`templates/schema/*.json`). ADR-0006 says `schema_version: 1` is frozen at
the end of Phase 2 after `architect` reviewed it against what Phase 3
(controller/safety) and Phase 4 (planner, tariff, forecast, battery
adapters) will need. This ADR is that review. It judges the schema against:

- **(a) the verified integration facts.** LG ThinQ has *no* power sensor;
  the Wh counters are `sensor`s with translation keys
  `energy_usage_{today,yesterday,this_month,last_month}` (optionally
  `_for_location`), `device_class: energy`, unit `Wh`, hourly updates,
  `today` resets at local midnight, and they share the `device_id` of the
  climate entity. Tibber's price sensor has **no** `device_class`, unit
  `<CUR>/kWh` (e.g. `EUR/kWh`), state = all-in price of the current 15 min
  slot, and the price curve exists only in the response of the action
  `tibber.get_prices` (`return_response: true`, shape
  `{"prices": {"<home>": [{"start_time", "price"}]}}`). Solcast exposes the
  curve as attribute `detailedForecast` on the day sensors
  (`total_kwh_forecast_today`, `_tomorrow`, `_d3`…), 30 min slots
  `{period_start, pv_estimate, pv_estimate10, pv_estimate90}` in **kW
  average power**, and additionally the action
  `solcast_solar.query_forecast_data` (`{"data": [...]}`).
- **(b) Phase 3 needs.** Surplus allocation by priority, hysteresis with
  delay, min run/pause, max switches per hour, power limits for stepped and
  continuous loads (number entity in W, A or %), set-point shift for
  climate within a comfort band, power estimation from counters (latency,
  quantisation, uncertainty), write settle time before read-back.
- **(c) Phase 4 needs.** Price curve from an attribute *or* an action
  response, "spot only" vs "all-in" prices for the price formula, forecast
  concatenation over several day sensors and blending by skill, and the
  `battery` template kind of ADR-0006.
- **(d) Extensibility.** Which later additions are compatible with
  `schema_version: 1`, and what the `extra` policy is.
- **(e) Matcher expressiveness.** Regex vs exact translation keys, "same
  device as role X", platform lists.

Constraints: dependency rule (`core/` imports no sibling; `planner/`,
`controller/`, `safety/` depend on `core/` only), contracts of ADR-0011
(`Intent`, `Decision`, `Reason`; `Plan` still undefined) change only by
ADR, Pi 4 budgets (< 300 MB RSS, planner < 10 s), no invented HA
interfaces.

### What is good and stays

- Role matchers instead of entity ids; `EntityMeta` in `core` as the
  neutral metadata record shared by `ha` and `devices` (no cross import).
- Header with `source`, `tested_with`, `unverified` (either non-empty),
  `name` forced to the i18n key `template.<id>.name`, generated JSON
  Schemas with CI drift check, built-in templates fail hard, user
  templates fail soft per file, 256 KiB file limit, kind fixed per id.
- `PowerModelKind` and `ControlKind` as separate axes (BRIEF §4.5); the
  four power models all yield an estimate with uncertainty (architecture
  §6).
- `DeviceConfig` is a config-section document (ADR-0004 migrations), so
  its shape is *not* part of the template freeze; it still needs the
  changes below because WP4/WP8 build on it.

### Findings

1. **LG ThinQ cannot be matched.** The energy role needs a regex on the
   translation key (`_for_location` suffix, four counters) and the
   constraint "same `device_id` as the climate entity"; `EntityMatcher`
   has only exact `translation_key`, `EntityMeta` has no `device_id`, and
   `RoleSpec` cannot reference another role.
2. **LG ThinQ cannot be instantiated from the template.** The counter
   updates hourly and is "never a control input" (research), so the
   default power model must be `learned` (prior `nominal_w`, trained from
   the counter). `PowerModelDefault` demands `nominal_w` for `learned` and
   `nominal`, but a template cannot know the nominal power of a user's
   model; it is user input.
3. **Tibber cannot be matched** (`units` is an exact list, the unit is
   `<CUR>/kWh`) **and cannot deliver a curve**: `TariffTemplate` has no
   notion of where the price series comes from. `ForecastTemplate` has an
   attribute map but no action alternative, although Solcast offers both
   and Tibber only the action.
4. **Solcast's curve spans several roles** (`today`, `tomorrow`, `d3`…);
   `ForecastAttributeMap` does not say which role(s) carry the attribute.
   `forecast_kind: price` duplicates the tariff kind.
5. **`ControlKind` is a bare enum.** Nothing tells the driver which role to
   write, the steps of a stepped load, the min/max/unit (W, A, %) of a
   continuous load, or the allowed set-point shift of a climate unit. The
   Phase 2 template `generic.number_load` (W, A or %) is therefore not
   expressible. `Intent.action` today knows only `SwitchAction` and
   `PowerSetpointAction`; temperature and mode actions arrive in Phase 3
   and need their own ADR (ADR-0011 contract change).
6. **Controller parameters missing** that BRIEF §4.5 and architecture §6
   list as common to all devices: hysteresis (W) with delay (s). Counter
   models lack the expected update interval (latency). No settle time for
   read-back although LG writes take ≥ 2 s and arrive by cloud push.
7. **Duplicated state.** `PowerModel` in `DeviceConfig` holds its own
   `EntityRef`s and `energy_unit`, parallel to `entities`; the duplicate
   check only covers `entities`, and the unit is config although
   `balance.py` reads units from the live state. `DeviceConfig.mode`
   duplicates the per-device mode of the `operation` section (plan WP6).
8. **`capabilities` are free slugs**; Phase 3/4 will key behaviour on
   them, so they need a vocabulary.
9. **`TimeWindow` has no polarity**: BRIEF needs both allowed windows and
   quiet hours ("Ruhezeiten").
10. **`battery` kind** (ADR-0006) is absent from `TEMPLATE_KINDS`; the
    loader silently ignores `templates/battery/*.yaml`.
11. **`EntityMatcher.domain` pattern `^[a-z_]+$`** rejects digits that HA
    allows (plan R8).
12. **`extra` policy** is `forbid` everywhere but nothing states what may
    be added within v1.

## Options considered

### Option A – Freeze as delivered, extend in schema v2 during Phase 3/4

- Pros: no rework now.
- Cons: the first five templates (WP3) cannot express LG ThinQ, Tibber,
  Solcast or `generic.number_load` correctly; every Phase 3/4 addition
  would be a v2 with migrations before a single community template
  exists. Contradicts ADR-0006 ("design the first templates before
  freezing").

### Option B – Required changes now, additive fields later under an explicit compatibility policy

Fix everything that is (1) needed by a Phase 2 template, (2) a *rename or
removal* (breaking later, free now) or (3) structural (`ControlSpec`,
series source, role references). Define the v1 compatibility policy so
Phase 3/4 can add optional fields and new kinds without a v2.

- Pros: Phase 2 templates are honest; Phase 3/4 extend instead of migrate.
- Cons: WP2 rework of roughly one day; a few models are more complex.

### Option C – Make templates Turing-ish (expressions, Jinja) to cover all cases

- Cons: rejected by ADR-0006 (declarative "what", drivers interpret);
  security and review cost; not needed by any verified fact.

## Decision

Option B. The list below is binding for WP2 before WP3 writes the first
template. Field names are normative; validation is described, not coded.
"Template" means `suncore/devices/templates/models.py`, "core" means
`suncore/core/devices.py` / `entities.py`.

### Required before freeze

1. **Matcher: regex and lists** (`EntityMatcher`, template).
   - Rename `platform: str | None` → `platforms: list[Slug] | None`
     (`min_length=1`, `max_length=8`); match = entity platform in list.
   - Add `translation_key_pattern: str | None` (anchored regex, compiled
     with `re.compile` in a validator, `max_length=200`, mutually
     exclusive with `translation_key`). Match = `re.fullmatch`.
   - Add `unit_pattern: str | None` (same rules, mutually exclusive with
     `units`). Match = `re.fullmatch` on the stripped unit.
   - `domain` pattern becomes `^[a-z0-9_]+$` (R8).
   - Matcher weights: `platforms` 40, `translation_key_pattern` 50
     (same as exact), `unit_pattern` 20. Keep "unknown attribute never
     matches a given criterion".
   - Compile patterns once per template (cached on the frozen model), not
     per entity, to stay cheap on the Pi.
2. **"Same device as role X"** (`RoleSpec`, template; `EntityMeta`, core).
   - Add `EntityMeta.device_id: str | None = None` (and to `EntityMeta.of`).
   - Add `RoleSpec.same_device_as: RoleName | None = None`. Validation in
     the template: the role exists, is not the role itself, and the target
     has no `same_device_as` of its own (no chains).
   - Matcher API: `rank_entities(matcher, entities, *, same_device_id:
     str | None = None)`: when given and the candidate's `device_id` is
     known, candidates with another `device_id` are dropped; when the
     candidate's `device_id` is unknown it stays, unboosted. Criterion
     name `same_device`, weight 60 (rarest, strongest signal).
3. **Power model defaults may leave `nominal_w` open** (`PowerModelDefault`,
   template). Drop the "`nominal`/`learned` needs `nominal_w`" rule there;
   `nominal_w` stays optional as a *hint*. `DeviceConfig.PowerModel`
   (core) keeps the strict rule, so the wizard must ask the user. The LG
   ThinQ template then defaults to `kind: learned`, `energy_role:
   energy_today`, no `nominal_w`.
4. **Power model references roles, unit comes from the state**
   (`PowerModel`, core). Replace `power: EntityRef | None`,
   `energy_counter: EntityRef | None`, `energy_unit: Unit | None` with
   `power_role: RoleName | None` and `energy_role: RoleName | None`, both
   validated in `DeviceConfig._consistent` to exist in `entities`. Keep
   the rules "`measured` needs `power_role`", "`counter_derived` needs
   `energy_role`", "`nominal`/`learned` need `nominal_w`", and "the
   `energy_role` entity must not have `invert_sign`". The unit is read
   from the live state like `balance.py` does (reject non-energy units at
   runtime with quality `invalid`). `entity_refs()` becomes
   `entities.values()`. This mirrors `PowerModelDefault` 1:1, so
   instantiation is a copy plus user input.
   - Add `PowerModel.counter_update_s: int | None` (ge 1, le 86 400) and
     the same field on `PowerModelDefault`: expected update cadence of
     the counter (LG: 3600). `None` = unknown, the adapter measures it.
     The controller uses it as the latency floor of the estimate.
5. **`ControlSpec` replaces the bare `ControlKind`** (core, used by both
   `DeviceConfig.control` and `DeviceDefaults.control`). Discriminated
   union on `kind` (the existing `ControlKind` values), every variant
   frozen, `extra="forbid"`, and every variant names the role it writes:
   - `OnOffControl`: `kind: "on_off"`, `role: RoleName`.
   - `StepsControl`: `kind: "steps"`, `role`, `steps: tuple[Step, ...]`
     (`min_length=2`, `max_length=16`), `Step = {value: str | int | float,
     power_w: float ge 0}`; `power_w` strictly increasing, first step may
     be 0 W ("off"). `value` is what is written to the entity (select
     option or number).
   - `ContinuousControl`: `kind: "continuous"`, `role`, `unit:
     Literal["W", "A", "%"]`, `min: float ge 0`, `max: float > min`,
     `step: float > 0 | None`, `voltage_v: float = 230` and `phases:
     Literal[1, 3] = 1` (only valid when `unit == "A"`), `%` is relative
     to `nominal_w` (validator: `%` requires a power model with
     `nominal_w` at instance level).
   - `SetpointControl`: `kind: "setpoint"`, `role` (climate or number),
     `shift_max_c: float` (gt 0, le 10, maximum shift from the user's
     set-point inside the comfort band), `step_c: float = 0.5`.
     Requires `comfort` on the device.
   - `ModeControl`: `kind: "mode"`, `role` (select / climate preset),
     `modes: tuple[Mode, ...]` (`min_length=2`, `max_length=16`), `Mode =
     {value: str, power_w: float ge 0}`; values unique.
   - Template validation: all `role`s exist in `roles`; instance
     validation: all `role`s exist in `entities`.
   - Phase 3 maps `PowerSetpointAction.power_w` onto steps/continuous in
     the driver; `setpoint` and `mode` need new `Action` variants
     (`TemperatureSetpointAction`, `ModeAction`) – **this is an ADR-0011
     contract change and gets its own ADR in Phase 3**; not decided here.
6. **Controller parameters** on `DeviceDefaults` (template) and
   `DeviceConfig` (core), all optional with `None` = controller/safety
   default: `hysteresis_w: float | None` (ge 0), `hysteresis_delay_s:
   int | None` (ge 0, le 3600), `settle_s: int | None` (ge 0, le 600;
   delay before read-back, LG template: 5). `max_switches_per_hour: None`
   must mean "safety default", never "unlimited" (note for
   `control-safety`).
7. **Series source for tariff and forecast** (template). New shared
   models:
   - `SeriesFields`: `timestamp_field: Text`, `value_field: Text`,
     `p10_field: Text | None`, `p90_field: Text | None`, `value_unit:
     Text`.
   - `AttributeSeries`: `source: "attribute"`, `roles: tuple[RoleName,
     ...]` (`min_length=1`, ordered; the lists are concatenated and
     sorted by timestamp, duplicates by timestamp keep the first),
     `attribute: Text`, `fields: SeriesFields`.
   - `ActionSeries`: `source: "action"`, `domain: Slug`, `service: Slug`,
     `start_field: Text | None`, `end_field: Text | None` (names of the
     window fields in the action data; `None` = the action takes no
     window), `response_path: Text` (dotted path into `response`; a `*`
     segment means "every value of this mapping", needed for Tibber's
     `prices.<home>`), `fields: SeriesFields`, `max_horizon_h: int`
     (ge 1, le 336; Tibber 48, Solcast 168). `return_response: true` is
     implied. Target entity (`weather.get_forecasts` style) is a Phase 4
     addition, not needed by the first templates.
   - `SeriesSource = Annotated[AttributeSeries | ActionSeries,
     Field(discriminator="source")]`.
   - `ForecastTemplate`: replace `attributes: ForecastAttributeMap` by
     `series: SeriesSource`; `forecast_kind: Literal["pv", "load"]`
     (**`price` removed**, prices belong to `tariff`; `temperature` is
     added later). Validation: `value_unit` for `pv`/`load` must be one
     of `W`, `kW`, `Wh`, `kWh` (`Unit`); power units mean *average power
     over the slot* (Solcast), energy units mean *energy per slot*.
     `resolution_min` stays.
   - `TariffTemplate`: add `price_series: SeriesSource | None = None`
     (required when `tariff_type == "dynamic"`; `value_unit` must match
     `price_unit` after `{currency}` substitution or be `{currency}/kWh`),
     add `price_includes: Literal["spot_only", "all_in"]` (required for
     `dynamic`; Tibber `all_in`, Nord Pool `spot_only`, used by the Phase
     4 price formula), make `resolution_min: Literal[15, 60] | None`
     (`None` = derive from the timestamps; Tibber delivers 15 min with
     `pyTibber 0.37`, hourly with older libraries).
   - **Safety boundary:** a template may *name* a read-only action, but
     `ha` only executes services on a built-in allowlist
     (`tibber.get_prices`, `solcast_solar.query_forecast_data`,
     `weather.get_forecasts`); a user template naming another service is
     refused at load time with a reason key. `call_service` stays
     confined to `ha`/`safety` (boundary rule 2); the response path is a
     separate `HAConnection.call_action_response(...)` method that is
     *not* an actuator and never targets a writable entity.
8. **`Capability` becomes a closed enum** in core, used by
   `TemplateHeader.capabilities: list[Capability]`. Initial members:
   `power_measured`, `energy_counter`, `on_off`, `power_setpoint`,
   `temperature_setpoint`, `hvac_modes`, `preset_modes`, `fan_modes`,
   `price_now`, `price_curve`, `pv_curve`, `pv_quantiles`,
   `feed_in_tiers`, `negative_price_rule`. Members are added per need
   (additive, see policy); a lint rejects unused members in built-in
   templates only when a validator can check them (e.g. `pv_quantiles`
   needs `p10_field` and `p90_field`).
9. **`TimeWindow.mode: Literal["allow", "block"] = "allow"`** (core). At
   most one polarity per device is *not* required; the controller treats
   `allow` windows as the union and `block` windows as subtracted.
10. **Single source for the operating mode.** Remove `DeviceConfig.mode`;
    the `operation` section (plan WP6, read by `safety` from Phase 3) is
    the only store of global and per-device mode, so "new devices start in
    shadow, also on import" is enforced in one place. The WP2 text of the
    plan is superseded on this point.
11. **Loader lint:** `scan_directory` reports `*.yaml` files in sub
    directories that are not a known kind (today `templates/battery/`) as
    errors instead of ignoring them; built-in → `BuiltinTemplateError`.
12. **Thermal kinds in one place:** export `THERMAL_KINDS` from core and
    use it in the template validator instead of the repeated tuple.

### Compatibility policy for `schema_version: 1` (decided here)

- Pydantic models of templates keep `extra="forbid"`; the generated JSON
  Schemas keep `additionalProperties: false`. Typos must fail.
- Within v1 the following are **allowed** and need no new version: adding
  an optional field with a default; adding a member to an enum or to a
  discriminated union (`kind`, `source`, `control.kind`); adding a new
  template kind (its own `<kind>.schema.json`, `schema_version` is per
  kind); relaxing a validation. Each addition is recorded in a "Schema v1
  changelog" section of `templates/README.md` with the SunCore version
  that introduced it.
- **Not allowed** within v1: removing or renaming a field, changing a
  type, narrowing a range or enum, changing the semantics of a value. Any
  of these is `schema_version: 2` with a loader migration.
- Consequence to document for contributors: a v1 template written for an
  older SunCore always loads on a newer one; a template using a newer
  optional field or enum member fails on an older SunCore with a clear
  per-field error (user templates are skipped, built-ins cannot occur).
  Add `TemplateHeader.min_app_version: str | None` (PEP 440 string) so the
  loader can say "needs SunCore ≥ x.y" before the field error.
- The same `extra="forbid"` applies to `DeviceConfig`; its evolution is
  handled by the config-section document migrations (ADR-0004), not by
  this policy.

### Can wait (v1-compatible later)

- **`battery` kind:** add in Phase 4 together with the Kostal research
  (needs the Phase 3 action vocabulary and verified registers). Adding a
  kind is additive under the policy, so nothing in v1 must change now
  except the loader lint (item 11). Expected shape for orientation only:
  roles `power`, `soc`, `charge_limit`, `discharge_limit`, `mode`;
  `driver: Literal["ha_entity", "modbus_ha", "mqtt"]`; heartbeat/timeout
  semantics; sign conversion per role via `invert_sign`.
- `DeviceTemplate.driver: Literal["ha_entity"] = "ha_entity"` (one line,
  additive; add when the second driver exists).
- `PowerModel.counter_reset: Literal["none", "daily"]` – the adapter
  derives resets from `state_class`/`last_reset` and treats any decrease
  as a reset; a hint is not needed.
- `EntityMeta.entity_category`, `hidden`, `supported_features` and matcher
  options `allow_diagnostic`, `required_features`: WP5 filters diagnostic
  and hidden entities upstream; `supported_features` passthrough is still
  `UNVERIFIED` on the Supervisor proxy.
- `SetpointControl.allowed_hvac_modes` – read `hvac_modes` from the
  climate state at runtime; LG never offers `heat_cool`.
- `ActionSeries.target_role` for `weather.get_forecasts` (temperature
  forecast, `forecast_kind: temperature`).
- Forecast metadata for blending: `horizon_h`, `update_interval_min`,
  per-source skill is learned at runtime (Phase 4).
- Role multiplicity (multi-split with shared outdoor unit, Solcast per-site
  sensors): `RoleSpec.multiple: bool`.
- `roles` with `additionalProperties: false` in the JSON Schema
  (`json_schema_extra`) for editor strictness; Pydantic already validates
  keys.
- Template-level `settle_s` for tariff/forecast actions (rate limits for
  read-only actions are a `ha` concern, 1 call per 15 min is the ceiling).

### Non-schema follow-ups found during the review

- Package layout: `tariff/` and `forecast/` will import the template
  models and loader; the architecture table lists them as depending on
  `core, ha` only. Either move `models.py`, `matcher.py`, `loader.py` into
  a new package `suncore/templates/` (depends on `core` and `yaml` only;
  `devices/`, `tariff/`, `forecast/`, `api/` import it) or amend the table.
  Recommendation: new package, before PR 2-B wires `api` to it.
- `devices/instantiate.py`: `instantiate(template, mapping, user_input)
  -> DeviceConfig` validating required roles, `same_device_as` and the
  `ControlSpec` role references in one place (WP3/WP5).
- The AST boundary test must allow `call_action_response` only in `ha`
  and assert that it is never called with a service outside the allowlist.

## Consequences

- Positive: all five Phase 2 templates are expressible with verified
  facts only; Phase 3 gets write targets and controller parameters; Phase
  4 gets price/forecast series from attributes or actions; nothing in v1
  has to be renamed later; the `extra` policy makes "v1-compatible" a
  testable statement.
- Negative / risks: `ControlSpec` and `SeriesSource` add two discriminated
  unions to the schema (more JSON Schema, more validators) – mitigated by
  keeping variants small and role-based. The action allowlist is a hard
  limit on community tariff/forecast templates – intended; new actions
  come with a PR that verifies them. Removing `DeviceConfig.mode` changes
  the WP2 text of the plan – WP6 owns the mode anyway.
- Pi budget: no runtime cost beyond regex compilation per template at
  load; action calls are ≤ 1 per 15 min per source.
- Follow-ups: `device-adapters` implements items 1–12 and the policy text
  in `templates/README.md` (WP2 rework, before WP3); `backend-engineer`
  (WP4/WP5) drops the `mode` field from the `devices` section and adds
  `call_action_response` with allowlist; `control-safety` (Phase 3) writes
  the `Action` extension ADR and treats `max_switches_per_hour: None` as a
  safety default; `optimizer-engineer` (Phase 4) adds `battery` kind,
  `temperature` forecast kind and blending metadata under the policy;
  `qa-engineer` adds property tests for `ControlSpec` (steps monotonic,
  `%` needs `nominal_w`) and for the matcher with `same_device_as`.

## Recommendation to the maintainer

Accept with the required list applied before the first template is
written; `battery` stays out of v1 and is added in Phase 4 as a
v1-compatible new kind.
