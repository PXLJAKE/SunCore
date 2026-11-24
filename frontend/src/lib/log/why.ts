// SPDX-License-Identifier: Apache-2.0
// Labels and values of the "Why?" panel's site readings (pure; the locale comes in as functions).
import type { MessageKey } from '../i18n/index.svelte';

const SITE: Record<string, MessageKey> = {
  'site.grid_w': 'why.input.grid',
  'site.pv_w': 'why.input.pv',
  'site.house_w': 'why.input.house',
  'site.battery_w': 'why.input.battery',
  'site.soc_pct': 'why.input.soc',
};

/** Catalogue key of a reading; `device` is set for `device.<id>.power_w` / `.on`. */
export function inputLabel(key: string): { key: MessageKey; device: string | null } {
  const site = SITE[key];
  if (site) return { key: site, device: null };
  const m = /^device\.(.+)\.(power_w|on)$/.exec(key);
  if (m) {
    return {
      key: m[2] === 'on' ? 'why.input.device_on' : 'why.input.device_power',
      device: m[1] ?? null,
    };
  }
  return { key: 'why.input.other', device: null };
}

export interface InputFormat {
  power: (w: number) => string;
  percent: (pct: number) => string;
  number: (v: number, o?: Intl.NumberFormatOptions) => string;
  none: string;
}

/** The reading as text; `share` (0..1 on-time share) is shown as a percentage. */
export function inputValue(input: { value: number | null; unit: string }, f: InputFormat): string {
  if (input.value === null) return f.none;
  switch (input.unit) {
    case 'W':
      return f.power(input.value);
    case '%':
      return f.percent(input.value);
    case 'share':
      return f.percent(input.value * 100);
    default:
      return f.number(input.value, { maximumFractionDigits: 2 });
  }
}
