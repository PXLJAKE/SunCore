---
name: new-adapter
description: Add a tariff or forecast adapter (Python) behind the adapter protocol in suncore/tariff or suncore/forecast, with fixtures from the real integration and accuracy bookkeeping. Usable from Phase 4.
---

# New tariff or forecast adapter

Use when a price or forecast source needs code beyond a template (API
client, parsing, blending).

## Steps

1. Read ADR-0006, `docs/research/integrations.md` and the adapter
   protocol in `suncore/forecast/base.py` or `suncore/tariff/base.py`.
2. Prefer the HA integration as the source (attributes or actions with
   `return_response`). Only call an external API when no integration
   exists, and then: check terms of use, rate limits and attribution
   (e.g. Open-Meteo CC BY 4.0) and document them in the adapter docstring.
3. Record a real response as a fixture under `tests/fixtures/<adapter>/`
   with the integration version and date; redact ids.
4. Implement the adapter: typed output (`core.Signal` series with unit,
   resolution, issue time, quality), no I/O in parsing functions, time
   zones explicit, 15- and 60-minute inputs handled.
5. Register it in the adapter registry and the setup wizard's candidate
   detection (`entity_matchers`).
6. Tests: parsing from fixtures, resolution/time-zone edge cases, failure
   modes (stale, partial, `unavailable`).
7. Forecast adapters: wire into the blending and accuracy statistics so
   the UI shows skill per source.
8. Docs: add the source to the user documentation list; `en`/`de` keys.

## Output

Adapter, fixtures, tests, docs; PR text in German including the terms of
use statement for external APIs.
