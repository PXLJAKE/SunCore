// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import {
  emptyFilters,
  filterProblems,
  filtersFromParams,
  filtersToApi,
  filtersToParams,
  hasFilters,
  logHash,
  nextDay,
} from './filters';

describe('log filters', () => {
  it('round-trips through the hash query', () => {
    const f = {
      ...emptyFilters(),
      device: 'pool_pump',
      kinds: ['shadow' as const, 'failed' as const],
      layer: 'planner' as const,
      reason: 'safety.shadow_mode',
      q: 'pool',
      from: '2026-06-01',
      to: '2026-06-30',
    };
    const params = filtersToParams(f);
    expect(filtersFromParams(new URLSearchParams(params.toString()))).toEqual(f);
    expect(logHash({ device: 'ac' })).toBe('#/decisions?device=ac');
    expect(logHash({})).toBe('#/decisions');
  });

  it('drops invalid values from a pasted link', () => {
    const f = filtersFromParams(
      new URLSearchParams(
        'kind=shadow&kind=bogus&kind=shadow&layer=nope&reason=Bad%20Key&from=2026-13-40&to=x',
      ),
    );
    expect(f).toEqual({ ...emptyFilters(), kinds: ['shadow'] });
    expect(hasFilters(f)).toBe(true);
    expect(hasFilters(emptyFilters())).toBe(false);
  });

  it('accepts comma separated kinds', () => {
    expect(filtersFromParams(new URLSearchParams('kind=shadow,held')).kinds).toEqual([
      'shadow',
      'held',
    ]);
  });

  it('maps to the API: inclusive end date becomes the exclusive next day', () => {
    const p = filtersToApi(
      { ...emptyFilters(), device: 'ac', from: '2026-02-27', to: '2026-02-28', layer: 'user' },
      { limit: 50, offset: 100 },
    );
    expect(p.get('device_id')).toBe('ac');
    expect(p.get('from')).toBe('2026-02-27');
    expect(p.get('to')).toBe('2026-03-01');
    expect(p.get('layer')).toBe('user');
    expect(p.get('limit')).toBe('50');
    expect(p.get('offset')).toBe('100');
    expect(filtersToApi(emptyFilters(), { limit: 50 }).has('offset')).toBe(false);
  });

  it('computes the next day across month and year ends', () => {
    expect(nextDay('2026-12-31')).toBe('2027-01-01');
    expect(nextDay('2028-02-28')).toBe('2028-02-29');
  });

  it('flags a reversed range and a malformed reason key', () => {
    expect(filterProblems({ ...emptyFilters(), from: '2026-06-02', to: '2026-06-01' }).range).toBe(
      true,
    );
    expect(filterProblems({ ...emptyFilters(), reason: 'Not A Key' }).reason).toBe(true);
    expect(filterProblems(emptyFilters())).toEqual({ reason: false, range: false });
  });
});
