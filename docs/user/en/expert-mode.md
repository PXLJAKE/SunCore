# Expert mode

Expert mode shows every tunable parameter of SunCore with its unit, default, allowed range and a
help text, and lets you change it. It is **off by default**, and nothing in SunCore needs it.
The defaults are the behaviour described in the other guides.

## 1. Turn it on

Use the **Expert mode** switch in the header. The choice is stored per browser. With it on,
**Settings** shows the **Expert settings** area, and the **What-if** link appears in the header.

## 2. Change a parameter

- Parameters are grouped (controller, planner, forecast, tariff). Use **Search parameters** or
  **Only modified**.
- Each field shows **Default**, **Allowed** (the hard range) and markers: **Modified**,
  **Unsaved**, **Safety**, **Needs restart**.
- **Reset** returns one parameter, or a whole group, to its default. A reset removes the stored
  override, so you follow whatever default the next version ships.
- **Save changes** sends only what you edited. Out-of-range values are rejected with a message
  per field.

### Safety parameters

Parameters marked **Safety** (switching limits, settle times, stale-data ages, the deficit
battery budget and similar) can be changed only **inside the hard bounds of the safety layer**.
A dialog shows the old and the new value and asks you to confirm. **No setting can switch a
safety check off**: every range has a positive minimum (the only 0 is the deficit budget, where 0
means no deficit is allowed). If a bound is tightened in a later version, a stored value outside
it is clamped at start and logged.

Restoring or importing a configuration takes the stored values strictly, as before.

## 3. Debug views

Under the parameters: the raw live signals as the server sends them (W, signs as documented), the
current plan as JSON with a copy button, and the solver statistics. They only read.

## 4. Parameter reference

The full, generated list is the
[parameter reference](../../reference/parameters.md) ([Deutsch](../../reference/parameters.de.md)):
unit, default, range, safety tag and help text of every parameter. It is generated from the
registry in the code. If it is wrong, the code is wrong, not the page.

## 5. Limits

- 32 parameters are registered today. Per-device minimum run and pause times are device settings,
  not expert parameters.
- Config parameters such as the negative feed-in rules are saved in their own section, which must
  exist first (finish the wizard).
- The reference for the negative-price rule is a sensor with the current spot price; later slots
  of an all-in price source keep a proxy rule.
- A "needs restart" marker exists, but no parameter uses it yet.

## See also

[What-if and backtesting](what-if-and-backtesting.md) ·
[Planning and battery control](planning-and-battery.md)
