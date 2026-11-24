# ADR-0016: Home Assistant surface – MQTT entities, events and notifications

- **Status:** proposed (Phase 6 proceeds on the recommendation by delegation of 2026-11-23; the
  maintainer may override)
- **Date:** 2026-11-24
- **Deciders:** maintainer, lead engineer (`architect`), `control-safety` for the command path
- **Research:** [`mqtt-discovery.md`](../research/mqtt-discovery.md) (verified 2026-10-12),
  [`ha-websocket-api.md`](../research/ha-websocket-api.md) (`subscribe_events` allowlist),
  [`ha-apps.md`](../research/ha-apps.md); ADR-0005, ADR-0007, ADR-0011, ADR-0015; BRIEF §4.11,
  §4.13; plan [`phase-6.md`](../plans/phase-6.md) WP2–WP4

## Context

ADR-0005 decided *that* SunCore appears in Home Assistant through MQTT discovery in v1 and that
the UI never depends on MQTT. Phase 6 must decide *how*: the entity model and identifiers, the
topic layout, how commands from HA reach SunCore without bypassing the safety gate, how texts
are localised without HA translation support for MQTT entities, and how notifications are sent
(BRIEF §4.13 "Benachrichtigungen", §4.11 "Benachrichtigung mit Aktion" for assisted mode).

Facts (verified unless marked):

- Device-based discovery `homeassistant/device/<object_id>/config` with `dev`, `o`, `cmps`;
  component keys `p`, `unique_id`, `default_entity_id`, `state_topic`, `command_topic`,
  availability with last will; publish on the HA birth message `homeassistant/status` =
  `online`; removal by an empty payload (research note, 2026-10-12).
- MQTT `event` entity: `state_topic` and `event_types` required; payload JSON with an
  `event_type` member from `event_types`, further members become attributes
  (`home-assistant.io` `integrations/event.mqtt`, read 2026-11-24).
- Broker credentials: `services: [mqtt:want]` (already in `addon/suncore/config.yaml`) and
  `GET http://supervisor/services/mqtt`; the Supervisor API needs `hassio_api: true`, which the
  app dropped in Phase 1 for least privilege.
- `persistent_notification.create` (`message` required, `title`, `notification_id`) and
  `.dismiss` (`notification_id`); `notify.send_message` targets `notify` entities with
  `message` and optional `title`; the legacy `notify.<service>` action takes `message`,
  `title`, `target`, `data` (`home-assistant/core` `dev`, `components/{persistent_notification,
  notify}/services.yaml`, read 2026-11-24).
- Companion app actionable notifications: `data.actions[]` with `action` and `title`
  (`uri` optional); a tap fires the event `mobile_app_notification_action` with `data.action`
  (`companion.home-assistant.io` "actionable notifications", read 2026-11-24).
- `subscribe_events` for a non-admin user is limited to `SUBSCRIBE_ALLOWLIST`
  (`auth/permissions/events.py`, `dev`, read 2026-11-24); `mobile_app_notification_action` is
  **not** in it. Whether the Supervisor's Core-proxy token is an admin user is `UNVERIFIED`.
- `UNVERIFIED` keys, to be verified from `home-assistant/core` `components/mqtt` before use:
  `via_device` in the device mapping, the `text` platform's `pattern`/`min`/`max`, whether
  MQTT entities can use a `translation_key`, the state-class rule for `monetary` sensors.

## Options considered

### 1. Command path from HA to SunCore

- **A – MQTT commands call the application services** (`suncore/api/services.py`, the same
  methods the REST API uses): config validation, config history, mode rules and the safety
  gate apply unchanged. Pros: one source of truth (ADR-0005 consequence). Cons: the services
  layer must be reachable from the MQTT bridge (composition in `app`).
- **B – MQTT bridge writes config or intents directly.** Cons: a second write path around the
  validators and the gate; rejected by ADR-0011.

### 2. Raising operating modes from HA

- **A – allowed** like in the panel. Cons: any broker client (or an automation bug) can put a
  device into automatic; the mode-raise rule needs the identity token (ADR-0013 decision 6).
- **B – lowering and emergency stop always; raising only with an explicit opt-in** per site,
  default off. Pros: HA can always stop SunCore, never silently arm it.

### 3. Entity texts

- **A – render names and text states in the backend** from the ICU catalogue in
  `region.language`, republish on a language change. Needs a Python renderer (ADR-0007 left the
  library open).
- **B – English names only.** Cons: contradicts BRIEF §7.

### 4. Python ICU renderer

- **A – `PyICU`:** complete, but a native build on musl/aarch64, large.
- **B – `babel` + own message parser:** plural rules for all locales; adds a dependency of
  about 30 MB of locale data.
- **C – own minimal renderer** for the subset the catalogue lint allows (simple arguments,
  `number`, `plural` with `=n`/`one`/`other`, `select`), plural rules for en and de plus an
  `other`-only fallback, decimal separators per locale; cross-checked against
  `intl-messageformat` by golden files. Pros: stdlib only, small. Cons: more locales need
  their plural rules added by hand (Weblate languages arrive in Phase 7).

### 5. Notification transport

- **A – HA actions** (`persistent_notification`, `notify.send_message`, `notify.<service>`)
  through a dedicated, allowlisted method of the HA connection. Pros: reaches the companion
  apps, no broker needed.
- **B – MQTT events only**, users build automations. Cons: no notification out of the box.

## Decision

**1A, 2B, 3A, 4C, 5A** (with MQTT events published in addition).

1. **Package and boundaries.** `suncore/ha/mqtt/` holds the broker client, discovery
   publisher and command dispatcher (it depends on `core` only, plus the client library);
   `suncore/notify/` holds rules, rate limits and rendering (depends on `core`, `i18n`);
   `suncore/i18n/` holds the renderer and the generated backend catalogue (stdlib only). The
   composition (`app`) connects them to the services layer. Neither package calls an actuator;
   notification actions go through a new `HAConnection.notify(...)` that only accepts
   `persistent_notification.create|dismiss`, `notify.send_message` and `notify.<name>` for
   names the user configured (AST boundary test, like `call_action_response`).
2. **Client and broker.** `aiomqtt` on `paho-mqtt` 2.x (licences and wheels checked in the PR,
   `NOTICE` entry), MQTT 3.1.1, QoS 1 for commands and configs, QoS 0 for states. Broker from
   the Supervisor services API (`hassio_api: true` is re-enabled for this one call) or manual
   settings (host, port, TLS, user, password – the password is a secret, redacted in exports
   and logs). **No broker → the feature is `unavailable` with a hint**, everything else works.
3. **Identity and topology.** A random install id `iid` (8 hex, persisted in `suncore.db`, not
   derived from anything personal). One HA device for the site (`suncore_<iid>`), one per
   configured SunCore device (`suncore_<iid>_<device_id>`) and one for the battery
   (`suncore_<iid>_battery`); device-based discovery per HA device. `unique_id` =
   `suncore_<iid>_<object>`; `default_entity_id` = `<platform>.suncore_<object>`. Topics under
   `suncore/<iid>/`: `status` (availability, retained, last will `offline`),
   `<object>/state` (retained), `<object>/set` (commands), `event` (event entity).
   Discovery configs are **not retained** and are published on connect and on every HA birth
   message after a random 1–5 s delay; states are republished after the configs. Removing a
   device or disabling the feature publishes empty configs.
4. **Entity catalogue v1** (the plan lists every object id): site sensors (status, global mode,
   next action, plan cost today, savings today and this month, autarky today, PV forecast
   error, month peak, grid state, available surplus), binary sensors (healthy, shadow mode,
   emergency stop, §14a active, island), buttons (replan, emergency stop), selects (battery
   strategy), numbers (battery reserve, evening target SoC), per device sensors (mode, power,
   last decision), switch (`auto`), buttons (boost, pause, resume) with a number for the boost
   minutes, and the device-kind entities of ADR-0017 (EV mode select, target SoC number,
   target time text, program arm/start buttons, comfort numbers). One event entity with the
   event types of the plan. **No energy sensors** that mirror the site meters (BRIEF §4.13: no
   double counting in the energy dashboard).
5. **Commands** call the services layer and nothing else: config changes carry the author
   `mqtt` in the config history and pass the same validators and `ParamSpec` bounds
   (ADR-0015); overrides and proposal confirmations go through the gate as `Origin.USER`.
   Lowering a mode, the emergency stop and pause are always accepted. **Raising a mode (the
   `auto` switches, clearing the emergency stop) needs `mqtt.allow_mode_raise`, default
   `false`**; a refused command republishes the true state and raises a persistent
   notification with the reason key. Commands are rate-limited (1 per second per entity, burst
   3) and never block the control tick.
6. **Texts.** Entity names, enum states and notification texts are rendered in the backend by
   the minimal ICU renderer from `suncore/i18n/catalogue/<lang>.json`, generated from the
   frontend catalogues by prefix (`mqtt.`, `notify.`, the reason families) with a `--check`
   in CI; a golden test compares the Python output with `intl-messageformat` for every
   generated key. A language change republishes the configs.
7. **Notifications.** Sinks: persistent notification (on by default for errors and safety
   events) and user-selected push targets (`notify.send_message` entities or legacy
   `notify.<service>`, off until chosen). Categories, defaults, quiet hours (22:00–07:00 for
   non-critical), per-key dedupe and a daily push cap are in the plan. **Actionable
   notifications** (assisted proposals, "start the washing machine now") are used only when
   SunCore can subscribe to `mobile_app_notification_action`; the action id carries a random
   single-use token bound to the proposal and its expiry; the confirmation runs
   `Services.confirm_proposal` like the panel. If the subscription is refused (non-admin
   token, `UNVERIFIED`), notifications are sent without actions and point to the panel.
8. **Load on the Pi.** States are published on change with deadbands (power 10 W or 1 %),
   at most one message per entity per 5 s, a full refresh only on a birth message. Target:
   < 10 MB RSS for client and publisher, no work on the control tick's path.

**Not decided here:** the companion integration (Phase 7, ADR-0005), MQTT for anything but HA
entities and evcc (ADR-0017), Repairs and Diagnostics (need the companion integration), push
channels outside HA (e-mail, Telegram).

## Consequences

- Positive: HA users get entities, events and notifications without a second package; every
  HA command is judged exactly like a panel command; texts are localised; SunCore without a
  broker stays fully functional.
- Negative / risks:
  - `hassio_api: true` widens the app's API access; mitigated by using it for
    `GET /services/mqtt` only (test) and documenting it in the app description.
  - Anyone with broker credentials can command SunCore; mitigated by the mode-raise opt-in,
    the gate, and documentation (the Mosquitto app requires authentication).
  - Non-retained configs depend on the birth message; a broker without it shows no entities
    until SunCore reconnects – the settings page offers "republish".
  - A small own ICU renderer can diverge from `intl-messageformat`; the golden test catches it.
- Follow-ups: plan Phase 6 PRs 6-C (client, discovery, sensors, renderer, hint), 6-D
  (commands, `control-safety` review), 6-E (notifications, `control-safety` review of the
  actionable confirm path); `ha-integration` verifies the `UNVERIFIED` keys from
  `home-assistant/core` before using them; Phase 7 companion integration migration.

## Recommendation to the maintainer

Accept; please confirm that raising modes from Home Assistant stays off by default and that the
app may re-enable `hassio_api` for the broker lookup.
