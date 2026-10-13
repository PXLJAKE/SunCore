// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { clearCandidateCache, loadCandidates, queryKey } from './candidates';

afterEach(() => {
  vi.restoreAllMocks();
  clearCandidateCache();
});

describe('candidate queries', () => {
  it('keeps the normal and the "also hidden" list apart', () => {
    expect(queryKey({ role: 'grid_power' })).not.toBe(
      queryKey({ role: 'grid_power', includeHidden: true }),
    );
    expect(queryKey({ role: 'grid_power', includeHidden: false })).toBe(
      queryKey({ role: 'grid_power' }),
    );
  });
  it('asks the backend for hidden entities only when requested', async () => {
    const role = vi.spyOn(api, 'entitiesForRole').mockResolvedValue([]);
    const tpl = vi.spyOn(api, 'entitiesForTemplate').mockResolvedValue([]);
    await loadCandidates({ role: 'pv_power' });
    await loadCandidates({ role: 'pv_power', includeHidden: true });
    await loadCandidates({ role: 'switch', template: 'generic.switch', includeHidden: true });
    expect(role.mock.calls).toEqual([
      ['pv_power', false],
      ['pv_power', true],
    ]);
    expect(tpl.mock.calls).toEqual([['generic.switch', 'switch', undefined, true]]);
  });
  it('serves the same query from the cache', async () => {
    const role = vi.spyOn(api, 'entitiesForRole').mockResolvedValue([]);
    await loadCandidates({ role: 'pv_power' }, 1000);
    await loadCandidates({ role: 'pv_power' }, 2000);
    expect(role).toHaveBeenCalledTimes(1);
  });
});
