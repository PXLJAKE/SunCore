// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { GridStatus } from '../api/types';
import { eventKey, gridBadges } from './present';

const status = (over: Partial<GridStatus> = {}): GridStatus => ({
  state: 'connected',
  para14a: 'inactive',
  month: '2026-06',
  month_peak_w: null,
  peak_limit_w: null,
  ...over,
});

describe('gridBadges', () => {
  it('shows nothing for a quiet grid or no data', () => {
    expect(gridBadges(null)).toEqual([]);
    expect(gridBadges(status())).toEqual([]);
    expect(gridBadges(status({ state: 'not_configured' }))).toEqual([]);
  });

  it('shows island and §14a', () => {
    expect(gridBadges(status({ state: 'island', para14a: 'active' })).map((b) => b.kind)).toEqual([
      'island',
      'para14a',
    ]);
    expect(gridBadges(status({ state: 'unknown', para14a: 'unknown' })).map((b) => b.kind)).toEqual(
      ['island_unknown', 'para14a_unknown'],
    );
  });

  it('shows the peak only with peak shaving and warns near the floor', () => {
    expect(gridBadges(status({ month_peak_w: 3000 }))).toEqual([]);
    const calm = gridBadges(status({ month_peak_w: 1000, peak_limit_w: 2500 }));
    expect(calm).toEqual([{ kind: 'peak', peakW: 1000, limitW: 2500, near: false }]);
    const near = gridBadges(status({ month_peak_w: 2400, peak_limit_w: 2500 }));
    expect(near[0]).toMatchObject({ kind: 'peak', near: true });
  });
});

describe('eventKey', () => {
  it('maps the stored kinds and ignores unknown ones', () => {
    expect(eventKey({ kind: 'island_start' })).toBe('grid.event.island_start');
    expect(eventKey({ kind: 'peak_new' })).toBe('grid.event.peak_new');
    expect(eventKey({ kind: 'something_else' })).toBeNull();
  });
});
