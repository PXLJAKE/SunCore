# Kostal Plenticore G3 – external battery control over Modbus TCP

## Status: `UNVERIFIED` – official document not reachable

The authoritative source is KOSTAL's **"PIKO IQ / PLENTICORE – Interface
description MODBUS (TCP) & SunSpec with control information"**, revision 2.7
of 2025-11-20, valid for PLENTICORE G3 firmware ≥ 3.06.00. Its table of
contents (seen in search results) has "3.4 External battery management" and
"3.5 Battery limitation". The PDF is hosted on
`kostal-solar-electric.com` (download centre) and mirrored by third parties
(taphome.com). Neither host was reachable from this session, so **no register
address below is verified**. BRIEF §4.4 requires registers exclusively from
this document: the battery adapter stays `UNVERIFIED` until the maintainer
provides the PDF (open question in the Phase 0 PR).

## What the secondary sources agree on (to be confirmed against the PDF)

- Modbus TCP port **1502**, unit id **71** (TapHome, evcc docs). Byte order
  is configurable in the inverter (two TapHome pages contradict each other:
  little-endian CDAB vs big-endian ABCD) – read the setting, do not assume.
- "External battery control via Modbus (TCP)" must be **enabled by an
  installer** (service menu / installer access) (evcc docs, LoxForum).
  evcc also warns that only one system may control the inverter.
- A **power set-point register** (community posts name holding register
  **1034**, negative = charge) is written; the inverter has an adjustable
  **timeout** (default reported as 3 s, can be increased, cannot be
  disabled): if the value is not re-written within the timeout, internal
  control resumes (LoxForum, openHAB forum). This is the fail-safe SunCore
  relies on: stop writing → inverter returns to self-consumption mode.
- The operating manual (PLENTICORE BI) also describes external battery
  control via digital inputs as an alternative to Modbus.

## Consequences for the adapter design (independent of exact registers)

1. A heartbeat writer that refreshes the set-point at < timeout/2 while a
   plan is active, and deliberately stops on shutdown, on safety trips and in
   shadow mode.
2. Read-back after every write; compare with the measured battery power
   (sign convention: HA Kostal integration reports battery power + for
   discharge, − for charge) to detect a hung inverter or a disabled external
   control.
3. Write-rate limit and "only write on change" apply to **limit** registers
   (likely EEPROM-backed); the periodic heartbeat set-point is a RAM value by
   design of the timeout mechanism – confirm in the PDF before shipping.
4. Register map, data types, scaling and the enable flag live in a YAML
   template (`templates/battery/kostal-plenticore-g3.yaml`) with a
   `source:` field citing the PDF revision, never in code.
5. The REST integration's `Battery:MinSoc` / `Battery:MinHomeComsumption`
   numbers are a weaker but installer-free fallback adapter.

## Known hardware behaviour from the maintainer (BRIEF §9)

The inverter occasionally hangs (web server, display and battery link
drop). Detection: stale Modbus/REST values, battery power frozen while grid
power moves, SunSpec sensors `unavailable`. Reaction: pause battery control,
alert, never retry writes in a tight loop.

## Verification

Secondary sources seen on 2026-10-12 via search snippets only:
evcc docs "Kostal Plenticore Hybrid" meter page, TapHome compatibility pages,
LoxForum thread "Ansteuerung Kostal Plenticore für externes
Batteriemanagement", openHAB forum "Modbus Binding for Kostal inverters".
The evcc meter template for Kostal was not found under the guessed paths.

**Everything in this note is `UNVERIFIED`.**
