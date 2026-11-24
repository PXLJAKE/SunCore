// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { bucketLabel, dayLabel, monthLabel, weekdayLabel, weekNumber } from './labels';

describe('history labels', () => {
  it('formats a day in the locale without a zone shifting it', () => {
    expect(dayLabel('2026-06-15', 'en')).toBe('Jun 15');
    expect(dayLabel('2026-06-15', 'de')).toMatch(/^15\. Juni?$/);
    expect(dayLabel('2026-06-15', 'en', true)).toBe('Monday, June 15, 2026');
  });

  it('formats months and bucket keys', () => {
    expect(monthLabel('2026-03', 'en')).toBe('Mar');
    expect(monthLabel('2026-03-05', 'en', true)).toBe('March 2026');
    expect(bucketLabel('2026-03', 'en')).toBe('Mar');
    expect(bucketLabel('2026-03-04', 'en')).toBe('Mar 4');
  });

  it('starts the weekdays on Monday', () => {
    expect(weekdayLabel(0, 'en')).toBe('Mon');
    expect(weekdayLabel(6, 'en')).toBe('Sun');
    expect(weekdayLabel(0, 'de')).toMatch(/^Mo/);
  });

  it('reads the ISO week of a key', () => {
    expect(weekNumber('2026-W25')).toBe(25);
    expect(weekNumber('2026-06')).toBeNull();
  });
});
