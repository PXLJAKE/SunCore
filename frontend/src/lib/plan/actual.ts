// SPDX-License-Identifier: Apache-2.0
// What really happened, from `GET /history/series`: site power flows, the state of charge and the
// on/off share of every device, aligned on the steps of one resolution. Pure.
import type { HistorySeries } from '../api/types';

export const SITE_KEYS = [
  'site.pv_w',
  'site.house_w',
  'site.grid_w',
  'site.battery_w',
  'site.soc_pct',
] as const;

export const deviceKey = (id: string): string => `device.${id}.on`;

const STEP_MS: Record<HistorySeries['resolution'], number> = {
  '1m': 60_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '1d': 86_400_000,
};

export type Values = (number | null)[];

export interface ActualData {
  /** Start of every step (ms). */
  times: number[];
  stepMs: number;
  pvW: Values;
  houseW: Values;
  /** Grid power, + = import. */
  gridW: Values;
  /** Battery power, + = charging. */
  batteryW: Values;
  socPct: Values;
  /** Share of every step a device was on (0..1), by device id. */
  devices: Record<string, Values>;
}

const byKey = (res: HistorySeries | null): Map<string, Values> =>
  new Map((res?.series ?? []).map((s) => [s.key, s.values]));

/** Combine the site response and the device response (either may be missing). */
export function buildActual(
  site: HistorySeries | null,
  devices: HistorySeries | null,
  deviceIds: readonly string[],
): ActualData | null {
  const base = site ?? devices;
  if (!base || base.times.length === 0) return null;
  const times = base.times.map((s) => Date.parse(s));
  const mine = byKey(site);
  const theirs = byKey(devices);
  const sameGrid = devices !== null && devices.times.length === times.length;
  const empty = (): Values => times.map(() => null);
  const out: ActualData = {
    times,
    stepMs: STEP_MS[base.resolution],
    pvW: mine.get('site.pv_w') ?? empty(),
    houseW: mine.get('site.house_w') ?? empty(),
    gridW: mine.get('site.grid_w') ?? empty(),
    batteryW: mine.get('site.battery_w') ?? empty(),
    socPct: mine.get('site.soc_pct') ?? empty(),
    devices: {},
  };
  for (const id of deviceIds) {
    out.devices[id] = sameGrid ? (theirs.get(deviceKey(id)) ?? empty()) : empty();
  }
  return out;
}

/** Index of the step that contains `ms`, or `-1` outside the data. */
export function actualIndex(a: ActualData, ms: number): number {
  const first = a.times[0];
  if (first === undefined || ms < first) return -1;
  const i = Math.floor((ms - first) / a.stepMs);
  return i < a.times.length ? i : -1;
}

export const hasValues = (values: Values): boolean => values.some((v) => v !== null);
