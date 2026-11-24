// SPDX-License-Identifier: Apache-2.0
// Locale-aware labels of days, months and weekdays of the history view. Days are plain
// `YYYY-MM-DD` strings (the site's calendar), so they are formatted in UTC: no zone shifts them.
import type { Locale } from '../i18n/core';

const formatter = (locale: Locale, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat =>
  new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' });

const at = (day: string): Date => new Date(`${day}T00:00:00Z`);

/** `15 Jun` / `15. Juni`; with `long` also the weekday and the year. */
export function dayLabel(day: string, locale: Locale, long = false): string {
  return formatter(
    locale,
    long
      ? { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }
      : { day: 'numeric', month: 'short' },
  ).format(at(day));
}

/** `Jun` for a `YYYY-MM` key, `Jun 2026` with `long`. */
export function monthLabel(key: string, locale: Locale, long = false): string {
  return formatter(locale, long ? { month: 'long', year: 'numeric' } : { month: 'short' }).format(
    at(`${key.slice(0, 7)}-01`),
  );
}

/** Label of a bucket key: a day (`2026-06-15`) or a month (`2026-06`). */
export const bucketLabel = (key: string, locale: Locale): string =>
  key.length === 10 ? dayLabel(key, locale) : monthLabel(key, locale);

/** Short weekday name; index 0 is Monday. */
export function weekdayLabel(index: number, locale: Locale): string {
  return formatter(locale, { weekday: 'short' }).format(at(`2024-01-0${index + 1}`));
}

/** ISO week number of a `2026-W25` key, `null` for other keys. */
export function weekNumber(key: string): number | null {
  const m = /-W(\d{1,2})$/.exec(key);
  return m ? Number(m[1]) : null;
}

/** Hour of day as the locale writes a full hour (`14:00` / `2 PM`). */
export function hourLabel(hour: number, locale: Locale): string {
  return formatter(locale, { hour: 'numeric', minute: '2-digit' }).format(
    new Date(Date.UTC(2024, 0, 1, hour)),
  );
}
