// SPDX-License-Identifier: Apache-2.0
// Human readable form of an intent's action (catalogue key + arguments).
import type { Decision } from '../api/types';
import type { MessageKey } from '../i18n/index.svelte';

export interface ActionText {
  key: MessageKey;
  args: Record<string, string | number>;
}

type Action = Decision['intent']['action'];

/** `power` is a formatter so this module stays free of the locale. */
export function describeAction(action: Action, power: (w: number) => string): ActionText {
  switch (action.kind) {
    case 'switch':
      return { key: action.on ? 'action.switch_on' : 'action.switch_off', args: {} };
    case 'power_setpoint':
      return { key: 'action.power_setpoint', args: { power: power(action.power_w) } };
    case 'setpoint':
      return {
        key: 'action.setpoint',
        args: { value: action.setpoint_c, base: action.base_c },
      };
    case 'step':
      return { key: 'action.step', args: { step: action.step } };
    case 'mode':
      return { key: 'action.mode', args: { mode: action.mode } };
    case 'release':
      return { key: 'action.release', args: {} };
    case 'battery':
      return { key: 'action.battery', args: {} };
  }
}

/** The limits a battery action sets, one line each (`None` = the adapter's own baseline). */
export function batteryParts(
  action: Extract<Action, { kind: 'battery' }>,
  power: (w: number) => string,
): ActionText[] {
  const parts: ActionText[] = [];
  if (action.min_soc_pct != null) {
    parts.push({ key: 'action.battery.min_soc', args: { soc: action.min_soc_pct } });
  }
  if (action.max_charge_w != null) {
    parts.push({ key: 'action.battery.max_charge', args: { power: power(action.max_charge_w) } });
  }
  if (action.max_discharge_w != null) {
    parts.push(
      action.max_discharge_w === 0
        ? { key: 'action.battery.discharge_lock', args: {} }
        : { key: 'action.battery.max_discharge', args: { power: power(action.max_discharge_w) } },
    );
  }
  if (action.grid_charge_w != null && action.grid_charge_w > 0) {
    parts.push({ key: 'action.battery.grid_charge', args: { power: power(action.grid_charge_w) } });
  }
  return parts;
}
