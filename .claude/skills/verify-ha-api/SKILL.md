---
name: verify-ha-api
description: Checklist to verify a Home Assistant interface (app config, Supervisor API, WebSocket command, service, entity attribute, MQTT discovery key) against current primary sources before using it. Use whenever code touches an HA API.
---

# Verify a Home Assistant interface

Home Assistant changes quickly (apps rename, builder change, 15-minute
prices). Never rely on memory for names and shapes.

## Sources, in order of authority

| Topic | Primary source | GitHub mirror (works when the sites are blocked) |
|---|---|---|
| App config, ingress, Supervisor API | developers.home-assistant.io/docs/apps/… and /docs/api/supervisor/endpoints | `home-assistant/developers.home-assistant` `docs/apps/*.md`, `docs/api/supervisor/endpoints.md`; schema in `home-assistant/supervisor` `supervisor/apps/validate.py` |
| WebSocket commands | developers.home-assistant.io/docs/api/websocket | `home-assistant/core` `homeassistant/components/websocket_api/commands.py`, `…/<domain>/websocket_api.py` |
| Services/actions and responses | integration page on home-assistant.io | `home-assistant/core` `homeassistant/components/<domain>/services.py` and `services.yaml` |
| Entity attributes | integration page | `home-assistant/home-assistant.io` `source/_integrations/<domain>.markdown`; component source |
| MQTT discovery | home-assistant.io/integrations/mqtt | `source/_integrations/mqtt.markdown` |
| HACS integrations | the integration repository README and source | same |
| Build tooling | `home-assistant/apps-example`, `home-assistant/builder` | same |

## Procedure

1. Name the exact thing to verify (key, command type, field, topic).
2. Fetch the primary source; if unreachable, fetch the mirror. Note the
   URL, branch/commit or date.
3. Quote the exact name and shape into the relevant research note under
   `docs/research/` (add a dated line to its Verification section).
4. If nothing authoritative is reachable: use the name with an
   `UNVERIFIED` comment in code, list it in `docs/STATUS.md` and in the
   PR's open questions.
5. For anything that writes to hardware, additionally verify the fail-safe
   behaviour (timeout, fallback) from the vendor document.

## Red flags

- A name you "remember" but cannot find in the source.
- Deprecated keys (`build.yaml`, `armv7`, `addon_config`, `BUILD_FROM`
  default).
- A community forum post as the only source for a register or attribute.
