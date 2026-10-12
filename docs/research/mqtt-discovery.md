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
