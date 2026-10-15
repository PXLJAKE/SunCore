// SPDX-License-Identifier: Apache-2.0
// Pure helpers that turn the control state into what the cards show.
import type { Decision, DeviceControlState, OperatingMode } from '../api/types';

export const MODE_ORDER: readonly OperatingMode[] = ['shadow', 'assisted', 'automatic'];

const startOfDay = (d: Date): number =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** Whether a hold ends today, tomorrow or later (calendar days in the user's time zone). */
export function holdDay(until: Date, now: Date): 'today' | 'tomorrow' | 'later' {
  const days = Math.round((startOfDay(until) - startOfDay(now)) / 86_400_000);
  if (days <= 0) return 'today';
  return days === 1 ? 'tomorrow' : 'later';
}

/** Whole seconds until `expiresAt` (never negative); `null` when the proposal does not expire. */
export function secondsLeft(expiresAt: string | null | undefined, now: number): number | null {
  if (!expiresAt) return null;
  const ms = Date.parse(expiresAt) - now;
  return Number.isNaN(ms) ? null : Math.max(0, Math.ceil(ms / 1000));
}

/** `m:ss` for a countdown. */
export function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export type StateView =
  | { kind: 'unknown' }
  | { kind: 'on' | 'off' }
  | { kind: 'step'; step: number }
  | { kind: 'level'; value: number }
  | { kind: 'setpoint'; setpoint: number; base: number | null };

/** What to say about the device's observed state (set-point vs base for shifted climates). */
export function stateView(d: DeviceControlState): StateView {
  const s = d.state;
  if (!s) return { kind: 'unknown' };
  if (s.setpoint_c != null && d.kind === 'climate' && s.on) {
    return { kind: 'setpoint', setpoint: s.setpoint_c, base: d.base_setpoint_c };
  }
  if (s.step != null && s.on !== false) return { kind: 'step', step: s.step };
  if (s.value != null && d.kind === 'number_load') return { kind: 'level', value: s.value };
  if (s.on == null) return { kind: 'unknown' };
  return { kind: s.on ? 'on' : 'off' };
}

/** Device id -> name, to label decisions and proposals. */
export function nameMap(devices: readonly Pick<DeviceControlState, 'device_id' | 'name'>[]) {
  return new Map(devices.map((d) => [d.device_id, d.name]));
}

/** Proposals that can still be confirmed (oldest first, as the server sends them). */
export const openProposals = (proposals: readonly Decision[], now: number): Decision[] =>
  proposals.filter((p) => secondsLeft(p.expires_at, now) !== 0);
