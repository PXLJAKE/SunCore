// SPDX-License-Identifier: Apache-2.0
// Locale-aware number helpers (Intl.NumberFormat). Unit strings come from the catalogue.
import type { Locale } from './core';

export type PowerUnit = 'w' | 'kw';

export interface FormattedPower {
  unit: PowerUnit;
  /** Locale-formatted number without the unit. */
  text: string;
}

const cache = new Map<string, Intl.NumberFormat>();

export function numberFormat(locale: Locale, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const id = `${locale}${JSON.stringify(options)}`;
  let nf = cache.get(id);
  if (!nf) {
    nf = new Intl.NumberFormat(locale, options);
    cache.set(id, nf);
  }
  return nf;
}

/** Below 1000 W: whole watts. From 1000 W: kilowatts with at most two decimals. */
export function formatPower(watts: number, locale: Locale): FormattedPower {
  const rounded = Math.round(watts);
  if (Math.abs(rounded) < 1000) {
    return { unit: 'w', text: numberFormat(locale, { maximumFractionDigits: 0 }).format(rounded) };
  }
  return {
    unit: 'kw',
    text: numberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(
      watts / 1000,
    ),
  };
}

/** State of charge etc.: whole percent value (0..100), without the percent sign. */
export function formatPercentValue(pct: number, locale: Locale): string {
  return numberFormat(locale, { maximumFractionDigits: 0 }).format(pct);
}

export function formatTime(date: Date, locale: Locale): string {
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(date);
}
