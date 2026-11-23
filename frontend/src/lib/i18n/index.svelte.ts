// SPDX-License-Identifier: Apache-2.0
// Reactive i18n: `t(key, args)` re-renders when the locale changes.
import de from './locales/de.json';
import en from './locales/en.json';
import { createTranslator, resolveLocale, type Locale, type MessageArgs } from './core';
import {
  formatDateTime,
  numberFormat,
  formatPercentValue,
  formatPower,
  formatTime,
  clockParts,
  validZone,
} from './format';

export type MessageKey = keyof typeof en;

const translator = createTranslator({ en, de });

function initialLocale(): Locale {
  if (typeof navigator === 'undefined') return 'en';
  return resolveLocale(navigator.languages?.length ? navigator.languages : navigator.language);
}

class I18n {
  locale = $state<Locale>(initialLocale());
  /** Home Assistant's time zone (region section); `undefined` = the browser's. */
  timeZone = $state<string | undefined>(undefined);

  setLocale(locale: Locale): void {
    this.locale = locale;
    if (typeof document !== 'undefined') document.documentElement.lang = locale;
  }
}

export const i18n = new I18n();

/** Show times in the configured zone, as Home Assistant does (not the tablet's own). */
export function applyTimeZone(zone: string | null | undefined): void {
  i18n.timeZone = validZone(zone);
}

/** Hour and minute of a moment in the display zone. */
export function tClock(date: Date): { hour: number; minute: number } {
  return clockParts(date, i18n.timeZone);
}

/** The language preference of the region section: `auto` follows the browser. */
export function applyLanguage(pref: 'auto' | 'en' | 'de'): void {
  i18n.setLocale(pref === 'auto' ? initialLocale() : pref);
}

/** Typed lookup: unknown keys fail `svelte-check`. */
export function t(key: MessageKey, args?: MessageArgs): string {
  return translator.render(i18n.locale, key, args);
}

/**
 * Lookup for keys that arrive from the backend (reasons, `api.error.*`). Unknown keys render
 * as `fallback` instead of exposing a raw key to the user.
 */
export function tDynamic(
  key: string,
  args: MessageArgs | undefined,
  fallback: MessageKey = 'reason.unknown',
): string {
  if (!translator.has(key)) return translator.render(i18n.locale, fallback, { key });
  return translator.render(i18n.locale, key, args);
}

/** Whether the catalogue has `key` (backend ids such as template names may not be translated). */
export function hasMessage(key: string): boolean {
  return translator.has(key);
}

export function tPower(watts: number): string {
  const p = formatPower(watts, i18n.locale);
  return t(p.unit === 'kw' ? 'unit.kw' : 'unit.w', { value: p.text });
}

export function tPercent(pct: number): string {
  return t('unit.percent', { value: formatPercentValue(pct, i18n.locale) });
}

export function tTime(date: Date): string {
  return formatTime(date, i18n.locale, i18n.timeZone);
}

export function tDateTime(date: Date): string {
  return formatDateTime(date, i18n.locale, i18n.timeZone);
}

/** Locale-aware number, e.g. `tNumber(3.5, { maximumFractionDigits: 1 })`. */
export function tNumber(value: number, options: Intl.NumberFormatOptions = {}): string {
  return numberFormat(i18n.locale, options).format(value);
}
