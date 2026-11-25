# Grid limits: peak shaving, §14a, island, export limit

SunCore respects the limits of your grid connection. This page covers the import side (fuse,
peak shaving, shedding, section 14a EnWG, backup power) and the export side (feed-in limit,
zero export, the inverter limit). Everything is configured in the setup assistant (step *Grid
connection*, and *Battery* for the inverter) and later under **Settings**.

Two things up front:

- **Shadow mode is the default for everything here.** In shadow mode SunCore only logs what it
  would have done ("would have shed", "would have dimmed") and writes nothing.
- SunCore **respects** the grid operator's signal. It is **not** the certified control path of
  section 14a EnWG. Your operator's own equipment stays in charge.

## 1. Import side

### Connection limit and peak shaving

Under **Limits** enter the **Connection limit for import** (for example your main fuse in W).
SunCore never plans or allows a load increase that would push the grid import above it.

**Peak shaving** is off by default (in Germany most households have no capacity tariff). Switch
on **Keep the monthly grid peak low** if your operator bills the highest 15-minute draw of the
month:

- The effective import limit is the smaller of the connection limit and the **peak limit**:
  the highest 15-minute mean import so far this month, but never below the **Peak floor**
  (default 2500 W). Peaks reset at local midnight on the first of the month.
- **Capacity price** (per kW and month) is optional; it lets the planner weigh a new peak
  against its cost. 0 = only keep the peak low.
- The month's peak is shown as a badge on the **Now** view and in **History** (grid events and
  month peak, 30 days).

### Shedding order

When the import goes above the limit SunCore backs devices off in this order: continuous loads
(wallbox current, adjustable loads) first, then devices with an explicit shed rank (lower first),
then the rest in reverse priority. A shed device comes back only with real export, never while
the house imports above the limit.

Above the **connection limit** (the fuse, not the peak limit) SunCore also shuts down
non-thermal devices before their minimum run time ends and despite the switch limit. Heat pumps
and air conditioners are never cut this way. A running shiftable program is never switched off
(see [Programs](programs.md)). Reductions are always allowed; only increases are checked.

The shed rank per device has no field in the panel yet; set `shed_rank` in the device
configuration (import) if you need it.

### Section 14a EnWG (Germany)

Tick **My installation is controllable under section 14a** and choose the **Signal of the grid
operator**: a binary sensor or switch (**Contact**) or an **Allowed power (W)** value. While the
signal is active the devices that are flagged for section 14a share the allowed power (default
**4200 W**, the legal reference) in priority order. SunCore logs, shows the **§14a limit active**
badge and keeps the flagged devices below the budget.

- A device takes part only when it is flagged with `para14a` in its configuration. The device
  editor has no checkbox for this yet.
- **Unknown signal** (the entity is unavailable): flagged devices are frozen, nothing is
  raised. **No signal entity** counts as "not active" with a note.
- After the reduction ends there is a cool-down (120 s) before anything is raised again.
- Numeric signal: 0 W means "not dimmed". A positive value is used as the allowed power, at most
  your limit. `UNVERIFIED` against a real control box.
- A wallbox that SunCore releases while the reduction runs is only paused and gets its old
  state back after the reduction and the cool-down (checked every minute).
- Shadow mode logs "would have dimmed" only.

### Backup power (island)

Tick **My system can run as an island** and pick the **Backup state entity** (for example the
inverter or backup switch mode). The panel shows its live state. List the **States that mean
island operation** exactly as the entity reports them. `unavailable` and `unknown` are refused.

- Island is detected **only** from this entity. There is no guessing from the measurements.
- In island operation SunCore sheds every device that is not on the keep list, raises nothing
  and never charges the battery from the grid. After the grid is back there is the same 120 s
  cool-down.
- An unavailable entity counts as **grid connected** and the panel shows "Grid state unknown".
- The keep list (`keep_devices`) has no field in the panel yet.
- `UNVERIFIED`: which entity and states a Kostal BackUp Switch G3 shows. The simulator uses
  `island` and `on_grid`.

## 2. Export side

### Static limit, zero export, negative prices

Under **Feed-in limit** choose **In watts**, **In percent of the installed power** or **No
limit**. Your static limit stays in the inverter: SunCore plans with it but does not write it
(`enforce_static` is off). Under **Export limit chain**:

- **Zero export**: use surplus on site and never feed in on purpose.
- **0 W at negative export prices**: lowers the inverter limit while the feed-in price is
  negative. Only takes effect with a **verified** inverter adapter.

### The chain: devices, battery, inverter

SunCore uses the surplus in this order: first the controlled devices, then the hybrid inverter
charges the battery by itself (SunCore only lifts its own charge cap while you would export),
and only then does it lower the inverter's limit. A tighter limit is written only after the
export stayed above the limit for about two minutes and nothing could absorb more; a looser one
at once. When no limit is needed any more, or an input is stale or the grid is down, your own
limit is restored.

### Inverter limit and the verification test

An inverter adapter writes the limit. Templates: `kostal.plenticore_power_limit` (REST
`Inverter:ActivePowerLimitation`, an export limit in W), `kostal.plenticore_g3_modbus_limit`
(register 533, an AC output limit in %) and `generic.inverter_limit` (a number entity).

- The inverter asset starts in shadow and stays **at most assisted until the limit is
  verified.** Unverified, SunCore never writes a limit on its own (automatic mode); it only
  proposes. Only the verification test sets the flag; saving the configuration or importing
  a file cannot set it (an import resets it). The flag belongs to one adapter: changing the
  template or its entity, register or capability makes the limit unverified again.
- **Running the test.** Open the block in setup step 5 or under Settings, *Your system*, and
  choose *Run the verification test*. Set the inverter asset to **assisted** first. The test:
  1. waits until the sunshine is steady: at least 1 kW export and 1.5 kW above house load plus
     battery charging, within a 1 kW band for 2 minutes (up to 15 minutes);
  2. shows the proposed test limit, `max(house + battery charging + 700 W, export − 1 kW)`,
     next to your own limit. **Nothing is written until you confirm** (within 2 minutes);
  3. writes the test limit once, through the safety layer, as your own action, and watches the
     export for about a minute: an export limit shows up as export ≈ limit, an AC output limit
     as export ≈ limit − house load (− battery charging);
  4. restores your own limit, reads it back and checks the export comes back.
- The test passes when the observed behaviour matches the configured capability and your limit
  is back. Then the limit is verified and automatic mode becomes possible. A mismatch, an
  export that does not follow, a failed read-back or a limit that does not come back mark the
  limit **unverified**; the reason is shown in the block.
- The test ends at once and restores your limit when you close the dialog, press the
  emergency stop, the connection to Home Assistant is lost, SunCore stops or restarts, the
  house load rises so far that the limit would cause grid import, or its time window
  (at most 10 minutes) expires. Meanwhile SunCore proposes no other inverter limit.
- The test is the only place where SunCore lowers the limit below the current export while
  devices or the battery could still take the surplus. That exception holds only inside the
  test's time window, only for the one write you confirmed, and never against the other
  rules (no grid import, the range of the adapter, at most every few seconds).
- An AC limit (register 533) keeps house load plus battery charging plus 500 W free so that
  the limit itself never causes grid import.
- **Register 533 has no documented timeout.** If SunCore stops or loses the connection, an AC
  limit stays until the inverter restarts, and when the house load rises above it the
  difference is drawn from the grid. Prefer the export limit (REST number) over register 533.
- SunCore re-writes a held limit regularly (heartbeat). The register is volatile and is lost
  on an inverter reset, which SunCore detects and re-writes. A crash stops the heartbeat and
  the restart releases the limit.
- `UNVERIFIED`: whether register 533 can be read back, whether it includes battery discharge,
  what `Inverter:ActivePowerLimitation` limits and whether it survives an inverter restart.
  A frozen-PV watchdog check (PV constant at least 200 W for 15 minutes) may fire while a limit
  holds the PV output exactly constant; watch the shadow run first.

## 3. Handing back

An emergency stop, "back to normal", a stopped app, a watchdog trip or a lost connection hand
the inverter limit and every shed device back as described in
[Surplus control](surplus-control.md#5-handing-back-and-the-emergency-stop). The release of a
limit is complete only when the read-back shows your own value; until then it is retried with a
growing delay.

## 4. Known limits

- Everything on this page ran in the simulator only (fuse, peak tariff, control box, backup
  switch and inverter limit were not available). Your shadow run is the first real test.
- No notification yet for island or §14a (the badges and the history show them).
- No per-phase imbalance limits, no EEBus or LPC, no multi-inverter plants.
- Not planned for Phase 6: built-in fallbacks for PV and price data.

See also: [Surplus control](surplus-control.md),
[Planning and battery control](planning-and-battery.md), [EV charging](ev-charging.md).
