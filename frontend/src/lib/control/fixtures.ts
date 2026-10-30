// SPDX-License-Identifier: Apache-2.0
// Test fixtures for the control state (not part of the bundle: only imported by tests).
import type { BatteryControlState, ControlState, Decision, DeviceControlState } from '../api/types';

export const device = (over: Partial<DeviceControlState> = {}): DeviceControlState => ({
  device_id: 'ac',
  name: 'Living room AC',
  kind: 'climate',
  identity: 'tok-1',
  global_mode: 'automatic',
  device_mode: 'shadow',
  effective_mode: 'shadow',
  state: { on: true, setpoint_c: 24, mode: 'cool', quality: 'ok' },
  estimate: { value_w: 850, uncertainty_w: 300, quality: 'ok', age_s: 4 },
  phase: 'steady',
  level: 1,
  top: 2,
  expected_power_w: 850,
  committed_w: 850,
  base_setpoint_c: 25,
  under_control: false,
  held: false,
  held_until: null,
  release_pending: false,
  release_due: null,
  paused: null,
  reasons: [{ key: 'controller.climate_off', args: {} }],
  since: null,
  last_decision: null,
  ...over,
});

export const controlState = (over: Partial<ControlState> = {}): ControlState => ({
  ts: '2026-06-21T09:30:00+02:00',
  active: true,
  emergency_stop: false,
  global_mode: 'automatic',
  site: null,
  devices: [device()],
  proposals: [],
  ...over,
});

export const proposal = (
  id: string,
  expiresAt: string | null,
  over: Partial<Decision> = {},
): Decision =>
  ({
    schema_version: 2,
    intent: {
      id,
      ts: '2026-06-21T09:30:00+02:00',
      asset_id: 'ac',
      action: { kind: 'switch', on: true },
      reasons: [{ key: 'controller.surplus_on', args: { need_w: 750, surplus_w: 3800 } }],
      origin: 'controller',
    },
    kind: 'proposed',
    ts: '2026-06-21T09:30:00+02:00',
    reasons: [],
    mode: 'assisted',
    expires_at: expiresAt,
    held_until: null,
    observed: null,
    ...over,
  }) as Decision;

export const batteryState = (over: Partial<BatteryControlState> = {}): BatteryControlState => ({
  identity: 'bat-1',
  adapter_template_id: 'generic.battery',
  capabilities: ['min_soc', 'charge_limit', 'discharge_limit', 'grid_charge'],
  global_mode: 'automatic',
  battery_mode: 'shadow',
  effective_mode: 'shadow',
  mode_capped: false,
  limits: {
    kind: 'battery',
    min_soc_pct: 10,
    max_charge_w: 5000,
    max_discharge_w: 5000,
    grid_charge_w: 0,
  },
  held: null,
  baseline: null,
  under_control: false,
  wanted: null,
  reasons: [],
  paused: null,
  last_decision: null,
  ...over,
});
