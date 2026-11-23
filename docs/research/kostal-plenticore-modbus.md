# Kostal Plenticore G3 – Modbus TCP interface and external battery control

Source: **KOSTAL "PIKO IQ/PLENTICORE – Interface description MODBUS (TCP) &
SunSpec with control information", revision 2.9, released 2026-07-17**,
valid from PLENTICORE G3 SW 3.07.00 (provided by the maintainer; the PDF is
KOSTAL copyright and is **not** committed to the repository – keep it under
`docs/research/vendor/`, which is git-ignored). Grid meter: **KOSTAL "KSEM
G1/G2 – Interface Beschreibung MODBUS"**, 2025-03-13, SW 2.6.0.

Everything below is verified against these two documents unless marked.

## Connection

- Inverter: Modbus TCP, port **1502**, Unit-ID **71** (both modifiable;
  register 4 holds the Unit-ID).
- Byte order for float/32-bit registers is configurable in the inverter
  (Settings > Modbus/Sunspec (TCP)) and readable from register **5**:
  `0x00` little-endian (CDAB, default), `0x01` big-endian (ABCD, SunSpec).
  The adapter reads register 5 first and decodes accordingly; it never
  assumes an order.
- Register 2 "MODBUS Enable" (Bool, R/W) – Modbus must be enabled in the
  inverter UI.
- KSEM: Modbus TCP slave on port **502** (TLS on 802), must be enabled in
  the KSEM UI. Registers are 32-bit unsigned integers in 0.1 W steps,
  separate `+` (import) and `−` (export) registers.

## Operating data the adapter reads (inverter, FC 0x03, Float = 2 regs)

| Addr | Description | Unit |
|---|---|---|
| 56 | Inverter state (U32) | – |
| 100 | Total DC power | W |
| 104 | State of energy manager (U32; `0x02` = emergency battery charge among others) | – |
| 106 / 108 / 116 | Home own consumption from battery / grid / PV | W |
| 118 | Total home consumption | Wh |
| 122 | Power limit from EVU | % |
| 190 | Battery charge current | A |
| 194 | Number of battery cycles | – |
| 200 | Actual battery charge (−) / discharge (+) current | A |
| 208 | Battery ready flag | – |
| 210 | Actual state of charge (SoC) | % |
| 214 / 216 | Battery temperature / voltage | °C / V |
| 252 | Total active power (powermeter); sensor position 2 (grid): (+) supply, (−) feed-in; sensor position 1 (home): (+) consumption | W |
| 260 / 270 / 280 | Power DC1 / DC2 / DC3 (PV strings; on a hybrid one DC input is the battery) | W |
| 320 / 322 | Total yield / daily yield | Wh |
| 512 | Battery gross capacity (U32) | Ah |
| 514 | Battery actual SOC (U16) | % |
| 529 | Work capacity (U32) | Wh |
| 531 / 532 | Inverter max power (U16) + scale factor | W |
| 575 / 576 | Inverter generation power (S16) + scale factor | W |
| 582 | Actual battery charge/discharge power (S16) | W |
| 1046 / 1048 | Total DC charge / discharge energy | Wh |
| 1050 / 1052 / 1054 | Total AC charge energy (AC to battery) / AC discharge energy (battery to grid) / AC charge energy (grid to battery) | Wh |
| 1056 | Total DC PV energy | Wh |
| 1064 | Total energy AC-side to grid | Wh |
| 1066 | Total DC power (sum of PV inputs) | W |

Sign convention in the document: **negative = charging, positive =
discharging** for battery current/power and for the set-points (Note 1 in
§3.4). SunCore's core uses positive = charging (ADR-0011); the adapter
negates at the boundary.

## §3.3 Active and reactive power control (volatile, lost on reset)

| Addr | Description | Format | FC |
|---|---|---|---|
| 533 | Active power setpoint, 1..100 | U16 % | 0x06 |
| 583 | Reactive power setpoint, −100..100 | S16 % | 0x06 |
| 585 | Delta cos φ setpoint | S16 | 0x06 |
| 832 / 833 | Low-priority active power setpoint (W) + read-only scale factor (`W = 832 × 10^833`) | U16 / S8 | 0x06 / 0x03 |

Export limiting (BRIEF §4.6) uses 533 (percent of nominal) after
consumers and battery have absorbed what they can.

## §3.4 External battery management

| Addr | Description | Format | Access |
|---|---|---|---|
| 1024 | Battery charge power (AC) setpoint (S16, with scale factor 1025) | W | WO |
| 1025 | Power scale factor (`W = 1024 × 10^1025`) | – | RO |
| 1026 | Battery charge power (AC) setpoint, absolute | W Float | RW |
| 1028 | Battery charge current (DC) setpoint, relative (Note 7) | % Float | RW |
| 1030 | Battery charge power (AC) setpoint, relative | % Float | RW |
| 1032 | Battery charge current (DC) setpoint, absolute (Note 7) | A Float | RW |
| **1034** | **Battery charge power (DC) setpoint, absolute** (Note 7) | W Float | RW |
| 1036 | Battery charge power (DC) setpoint, relative (Note 7) | % Float | RW |
| 1038 | Battery max. charge power limit, absolute | W Float | RW |
| 1040 | Battery max. discharge power limit, absolute | W Float | RW |
| 1042 | Minimum SOC | % Float | RW |
| 1044 | Maximum SOC | % Float | RW |
| 1068 | Battery work capacity | Wh Float | RO |
| 1076 / 1078 | Max charge / discharge power limit read from battery | W Float | RO |
| **1080** | **Battery management mode** (U8): `0x00` none, `0x01` external via digital I/O, `0x02` external via Modbus | – | RO |
| 1082 | Installed sensor type (`0x03` = KSEM) | – | RO |

Notes from the document: AC set-points (1024/1026/1030) apply to
PLENTICORE BI and PLENTICORE (MP) G3 (Note 6). Note 7: in Denmark, and in
Austria for the 10-minute gradient case, the DC set-point registers are not
permanently accessible (normative gradient limitation).

**Timeout:** §3.4 does **not** state a watchdog/timeout for the set-point
registers. Community sources (LoxForum, openHAB) report that the inverter
falls back to internal control a few seconds after the last write and that
the time is adjustable in the inverter menu; this is `UNVERIFIED` against
KOSTAL documentation and must be measured on the maintainer's inverter in
Phase 4 (write a set-point once, observe when battery power returns to
self-consumption behaviour). Until measured, the adapter refreshes the
set-point every 2 s while active.

**Enabling:** the register map shows the mode only read-only (1080). How
"external battery management via Modbus" is switched on (installer menu,
per evcc docs and the PLENTICORE operating manual) is not in this
document → `UNVERIFIED` here, confirmed by the maintainer in practice.

## §3.5 Battery limitation (G3 ≥ SW 03.05, with explicit fallback)

| Addr | Description | Format |
|---|---|---|
| 1280 | Max battery charge power | W Float RW |
| 1282 | Max battery discharge power | W Float RW |
| 1284 | Max battery charge power (fallback case) | W Float RW |
| 1286 | Max battery discharge power (fallback case) | W Float RW |
| 1288 | Time until fallback, **30..10800 s** | U32 RW |

"As soon as register 1280 or 1282 has been written, they must continue to
be written cyclically. If this no longer happens, after a few seconds (time
of register 1288) the limits in 1284 and 1286 become active." This is the
documented heartbeat/fail-safe mechanism. It limits power rather than
setting it, which is enough for: discharge lock (1282 = 0), charge lock
(1280 = 0), capped grid charging, and "hold" strategies. SunCore's
**default battery adapter for G3 uses §3.5** because its fail-safe is
documented; the §3.4 set-point path is an opt-in "advanced" mode once the
timeout behaviour is measured.

## §3.6 Multi Device Control (MDC, G3 host ≥ 3.07.00)

Plant-level registers on the host inverter: 1547 plant SoC, 1549 plant
battery power (negative = charging), 1551 plant PV power, 1553 plant AC
power; writable 1557/1559 min/max plant SoC, 1561 battery DC power set-point,
1569/1571 max charge/discharge, **1573 battery control timeout until
fallback (default 30 s)**. Relevant later for multi-inverter sites; the
single-inverter adapter ignores it.

## SunSpec models implemented (§4.2)

1 Common, 103 Three-phase inverter, 113 (float), 120 Nameplate, 123
Immediate controls, 160 Multiple MPPT, 203 Wye meter, 802 Battery base
model, 65535 End. The maintainer's HA SunSpec integration reads these; the
Kostal-specific registers above are **not** in SunSpec.

## KSEM registers for the grid connection (TCP slave, FC 0x03)

| Addr | Description | Type / unit |
|---|---|---|
| 0–1 | Active power+ (import) | uint32, 0.1 W |
| 2–3 | Active power− (export) | uint32, 0.1 W |
| 26–27 | Supply frequency | uint32, 0.001 Hz |
| 40–41 / 42–43 | Active power+ / − L1 | uint32, 0.1 W |
| 80–81 / 82–83 | Active power+ / − L2 | uint32, 0.1 W |
| 120–121 / 122–123 | Active power+ / − L3 | uint32, 0.1 W |
| 60–61, 100–101, 140–141 | Current L1/L2/L3 | uint32, 0.001 A |
| 62–63, 102–103, 142–143 | Voltage L1/L2/L3 | uint32, 0.001 V |
| 512–515 | Active energy+ (import) | uint64, 0.1 Wh |

Formula from the document: `P_import [W] = (reg0 × 2^16 + reg1) × 0.1`.
Per-phase import/export registers give the phase imbalance signal for
BRIEF §4.6. In practice the KSEM values arrive in HA through the Kostal
integration or a Modbus integration; SunCore reads HA entities first and
uses direct Modbus only if the user opts in (ADR-0006).

## Consequences for the adapter template `templates/battery/kostal-plenticore-g3.yaml`

1. Read 5 (byte order), 1080 (mode), 1082 (sensor), 529/1068 (capacity),
   1076/1078 (battery limits) at connect; refuse to write if 1080 ≠ 0x02 and
   explain in the UI what the installer must enable.
2. Default control path: §3.5 limits with 1288 = 60 s, fallback registers
   1284/1286 = battery's own limits (1076/1078), heartbeat every 20 s,
   stop writing on shadow mode, safety trip, shutdown.
3. Advanced path: §3.4 DC set-point 1034 (W, negative = charge) with
   measured timeout; heartbeat at ≤ timeout/2.
4. Export limit: 533 (%), applied last in the export-limit chain.
5. Read-back after every write; plausibility against 582/200/210.
6. Hung-inverter detection: 56 (state), stale 252/582 while KSEM/HA grid
   power moves.

## Verification

Verified on 2026-10-12 against the two KOSTAL PDFs named above (text
extracted with `pdftotext`).

`UNVERIFIED`: timeout behaviour of §3.4 set-points; how external battery
management is enabled; whether 533 limits AC output including battery
discharge (to be tested in Phase 4).

### Phase 4 verification (2026-10-16): writing through Home Assistant

`home-assistant/core` `dev` `5cfe9aad7cb428c2c2ebb449719ae81976780de1`,
`components/modbus/{services.py,services.yaml,modbus.py}` (details in
[`ha-websocket-api.md`](ha-websocket-api.md), section "Modbus write action"):
the `modbus.write_register` action writes raw unsigned 16-bit words (single value
-> function 0x06, list -> 0x10), has no float32 support and no read action, and
**does not raise on a failed write** (the hub logs the error). For the register
map above this means: the adapter encodes every `Float` (2 registers) into two
words itself (word order per the byte-order register 5), sends them as one list
`value` to the start address (e.g. 1280/1282 for the §3.5 limits), uses `slave`
(unit id of the inverter, the action defaults to 1, not the hub's configured id),
and **must read the value back** (modbus sensors or battery power) because a failed
write is invisible to the caller. The heartbeat (20 s) shares the hub's lock with
all polled entities. `UNVERIFIED` still: the fallback timer behaviour on the
maintainer's inverter (measurement pending).
