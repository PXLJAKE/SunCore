# MQTT discovery

## Topics

- Single component: `<prefix>/<component>/[<node_id>/]<object_id>/config`
- Device-based (preferred for SunCore): `<prefix>/device/<object_id>/config`
- Prefix defaults to `homeassistant`; ids match `[a-zA-Z0-9_-]`.
- `<object_id>` does **not** set the entity id; use `default_entity_id`.

## Device-based payload

Root keys: `dev` (device, required), `o` (origin, required), shared
`availability`, `state_topic`, `command_topic`, `qos`, `encoding`, and
`cmps` (components). Each component needs `p` (platform), `unique_id`, and
at least one platform-specific option.

- `dev`: `ids`, `cns`, `name`, `mf`, `mdl`, `mdl_id`, `sw`, `hw`, `sn`,
  `cu` (configuration_url → the ingress URL), `sa` (suggested area)
- `o`: `name` (required), `sw`, `url`
- Entity keys (abbreviations): `name` (`null` inherits device name),
  `unique_id`/`uniq_id`, `default_entity_id`/`def_ent_id`, `state_topic`/
  `stat_t`, `command_topic`/`cmd_t`, `value_template`/`val_tpl`,
  `device_class`/`dev_cla`, `state_class`/`stat_cla`,
  `unit_of_measurement`/`unit_of_meas`, `entity_category`/`ent_cat`,
  `enabled_by_default`/`en`, `expire_after`/`exp_aft`, `icon`/`ic`, `qos`,
  `retain`/`ret`, `message_expiry_interval`/`msg_exp_int`, `~` base topic.
- Availability: `availability` list with `topic`, `payload_available`,
  `payload_not_available`, `availability_mode` (`all|any|latest`), or
  `availability_topic`; not both.

Platforms SunCore will use: `sensor`, `binary_sensor`, `switch`, `select`,
`number`, `button`, `event`. All are supported by discovery.

## Lifecycle rules

- Publish discovery on the HA birth message (`homeassistant/status` =
  `online`) with a small random delay, rather than relying on retained
  configs; retained configs can create ghost entities. If retained, set
  `message_expiry_interval`.
- Republish state after the config is accepted (state topics are subscribed
  only after the config is processed).
- Remove: empty retained payload on the config topic. Remove one component:
  publish an update keeping only `p` for it, then an update without it.
- Migration between single-component and device-based discovery is
  supported via `{"migrate_discovery": true}`.

## Broker credentials from Supervisor

`services: ["mqtt:want"]` in `config.yaml`, then
`GET http://supervisor/services/mqtt` → `host`, `port` (string), `ssl`,
`username`, `password`, `protocol` (`3.1`/`3.1.1`), `addon`. Without a
broker app the call fails; SunCore then runs without HA entities and shows a
hint. A standalone container needs manual broker settings.

## Consequence for SunCore

MQTT discovery gives entities, services (via `command_topic` on
`button`/`switch`/`select`/`number`) and availability without a custom
integration. It does not give HA *actions with parameters* (e.g. "boost
device X for N minutes") or events with payloads beyond the `event`
platform, nor Repairs/Diagnostics; those need the companion integration
(ADR-0005).

## Verification

Verified on 2026-10-12 against `home-assistant/home-assistant.io`
`source/_integrations/mqtt.markdown` (branch `current`) and
`home-assistant/supervisor` `supervisor/services/modules/mqtt.py`,
`home-assistant/developers.home-assistant` `docs/api/supervisor/endpoints.md`.

### Phase 6 (PR 6-C), verified 2026-11-24

Read from `home-assistant/core` `dev` (`components/mqtt/{const,schemas,entity,discovery,
abbreviations,sensor,event,text}.py`, `components/sensor/const.py`) and `home-assistant/supervisor`
`main` (`supervisor/api/{services,__init__}.py`, `supervisor/api/middleware/security.py`,
`supervisor/services/modules/mqtt.py`):

- **`hassio_api` is not needed for the broker lookup.** The security middleware's
  `api_bypass` pattern lists `/services.*` (also `/discovery.*`, `/info`, `/addons/self/...`), so an
  app with the `mqtt:want` services role reaches `GET /services/mqtt` without `hassio_api`;
  `_check_access` then requires the `services_role`. SunCore keeps `hassio_api` off
  (ADR-0016 decision 2 assumed it had to be re-enabled; superseded, see the ADR note).
- Response of `GET /services/mqtt` (`api/services.py`, `SCHEMA_SERVICE_MQTT`): envelope
  `{"result": "ok", "data": {host, port (int), ssl (bool, default false), username?, password?,
  protocol ("3.1" | "3.1.1", default "3.1.1"), app}}`; the v1 route renames `app` to `addon`. A
  missing provider raises `APIError("Service not enabled")` (`{"result": "error", "message":
  ...}`). SunCore reads `host`, `port`, `ssl`, `username`, `password`, `protocol` only.
- `via_device` exists in the device schema (`schemas.py`, `CONF_VIA_DEVICE`, a string that
  is the parent's identifier; a parent that is not announced yet is stub-created in the registry,
  `entity.py` `ensure_via_device_exists`). Newer `dev` also has `child_devices` (`cdevs`) and a
  `child_device` key per entity; SunCore does not use them (one discovery message per HA device).
- `text` platform: `min`, `max`, `mode`, `pattern` (`ptrn`) and `value_template` exist
  (`text.py`); announced from PR 6-D on.
- No `translation_key` in any MQTT entity schema: names are rendered in the backend.
- `sensor`: `options` needs `device_class: enum` and forbids `state_class` and
  `unit_of_measurement` (`validate_sensor_state_and_device_class_config`); the enum state must be
  one of the options, so SunCore publishes the rendered label and lists the rendered labels.
  `monetary` allows only `state_class: total` (`DEVICE_CLASS_STATE_CLASSES`); SunCore sets none
  (the values reset daily). Whether the unit of a monetary sensor must be an ISO 4217 code is
  `UNVERIFIED` (no check in the MQTT schema); SunCore announces a monetary sensor only when the
  region's currency is known and uses it as the unit.
- `event`: `event_types` required; the state payload is JSON with `event_type` (members other than
  `event_type` become attributes); a payload received with the retain flag is ignored as a
  replay (`event.py`), so SunCore publishes events **not retained**.
- Discovery: an empty payload on the config topic removes the device's components whether or
  not it was retained (`discovery.py`, cleanup); configs are published non-retained. Abbreviations
  used: `p`, `uniq_id`, `def_ent_id`, `stat_t`, `dev_cla`, `stat_cla`, `unit_of_meas`, `sug_dsp_prc`,
  `ops`, `evt_typ`, `avty_t`, `pl_avail`, `pl_not_avail`, `cmps`, `dev`, `o`; device keys `ids`, `name`,
  `mf`, `mdl`, `sw`, `via_device`; origin keys `name`, `sw`, `url`.
- Still `UNVERIFIED`: that HA's `get_config` carries the UI language under `language` (used for
  the entity names while `region.language` is `auto`; falls back to English), that a custom birth
  topic is never configured (SunCore listens on the default `homeassistant/status`), the ingress
  URL for the device's `configuration_url` (`cu`, left out), and TLS to the Mosquitto app with
  certificate verification on (default context; a failure shows `mqtt.reason.tls_failed`).

### Phase 6 (PR 6-D), commands, 2026-11-24

The `home-assistant/core` source was **not reachable** in this session (the repository is not
configured for the tools), so these are `UNVERIFIED` and used as documented on
`home-assistant.io/integrations/mqtt`:

- Command topic key `command_topic` (abbreviation `cmd_t`, listed above); `switch` publishes
  `ON`/`OFF` by default (`payload_on`, `payload_off`), `button` `PRESS` (`payload_press`); SunCore
  sets none of them and accepts exactly these defaults.
- `number`: `min`, `max`, `step`, `mode` (`auto|box|slider`), `unit_of_measurement`; SunCore sends
  `mode: box`. `entity_category: config` for the boost duration.
- A `button` has no state topic, so none is sent for it.
- HA publishes commands with `retain` off unless the entity sets `retain`; SunCore treats every
  retained command as a replay and removes it with an empty retained payload.
- `select` and `text` commands (battery strategy, EV mode, target time, program deadline) are not
  announced yet.
- N1: `pl_prs`, `pl_on`, `pl_off`, `stat_on`, `stat_off` are announced explicitly;
  the abbreviations are `UNVERIFIED` (source not read).
