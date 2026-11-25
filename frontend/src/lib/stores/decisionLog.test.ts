// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, controlApi } from '../api/client';
import type { Decision, DecisionExplain, DecisionPage } from '../api/types';
import { emptyFilters } from '../log/filters';
import { DecisionLogStore } from './decisionLog.svelte';

afterEach(() => vi.restoreAllMocks());

const row = (id: string) =>
  ({ intent: { id }, ts: '2026-06-21T09:30:00Z', kind: 'shadow' }) as Decision;
const page = (ids: string[], total = ids.length): DecisionPage => ({
  items: ids.map(row),
  ids: ids.map((_, i) => i + 1),
  total,
});

describe('DecisionLogStore', () => {
  it('asks the server for the selected filters and one page', async () => {
    const spy = vi.spyOn(controlApi, 'decisionPage').mockResolvedValue(page(['a'], 120));
    const log = new DecisionLogStore();
    log.setFilters({ ...emptyFilters(), device: 'ac', layer: 'planner', q: 'x' });
    log.toggleKind('shadow');
    log.toggleKind('failed');
    log.toggleKind('shadow');
    await log.load();
    const p = spy.mock.calls[0]![0];
    expect(p.get('device_id')).toBe('ac');
    expect(p.getAll('kind')).toEqual(['failed']);
    expect(p.get('layer')).toBe('planner');
    expect(p.get('q')).toBe('x');
    expect(p.get('limit')).toBe('50');
    expect(p.has('offset')).toBe(false);
    expect(log.items.map((d) => d.intent.id)).toEqual(['a']);
    expect(log.total).toBe(120);
    expect(log.pageCount).toBe(3);
  });

  it('pages with an offset and a new filter returns to the first page', async () => {
    const spy = vi.spyOn(controlApi, 'decisionPage').mockResolvedValue(page(['a'], 120));
    const log = new DecisionLogStore();
    await log.load();
    log.goToPage(3);
    await log.load();
    expect(spy.mock.calls[1]![0].get('offset')).toBe('100');
    expect(log.page).toBe(3);
    log.goToPage(99);
    expect(log.page).toBe(3);
    log.setFilters({ ...emptyFilters(), q: 'y' });
    expect(log.page).toBe(1);
  });

  it('drops the answer of an older request that arrives late', async () => {
    const resolvers: ((p: DecisionPage) => void)[] = [];
    vi.spyOn(controlApi, 'decisionPage').mockImplementation(
      () => new Promise((resolve) => resolvers.push(resolve)),
    );
    const log = new DecisionLogStore();
    const slow = log.load();
    const fast = log.load();
    resolvers[1]?.(page(['new']));
    await fast;
    resolvers[0]?.(page(['old']));
    await slow;
    expect(log.items.map((d) => d.intent.id)).toEqual(['new']);
    expect(log.loading).toBe(false);
  });

  it('steps back when the page is gone because rows were pruned', async () => {
    const spy = vi
      .spyOn(controlApi, 'decisionPage')
      .mockResolvedValueOnce(page(['a'], 120))
      .mockResolvedValueOnce({ items: [], ids: [], total: 60 })
      .mockResolvedValueOnce(page(['b'], 60));
    const log = new DecisionLogStore();
    await log.load();
    log.goToPage(3);
    await log.load();
    expect(spy).toHaveBeenCalledTimes(3);
    expect(log.page).toBe(2);
    expect(log.items.map((d) => d.intent.id)).toEqual(['b']);
  });

  it('keeps the list and reports the error key when the request fails', async () => {
    vi.spyOn(controlApi, 'decisionPage').mockResolvedValueOnce(page(['a']));
    const log = new DecisionLogStore();
    await log.load();
    vi.spyOn(controlApi, 'decisionPage').mockRejectedValueOnce(
      new ApiError(503, 'api.error.history_unavailable'),
    );
    await log.load();
    expect(log.items).toHaveLength(1);
    expect(log.error).toBe('api.error.history_unavailable');
    expect(log.loading).toBe(false);
  });

  it('fetches an explanation once and retries only after an error', async () => {
    const data = { id: 7 } as DecisionExplain;
    const spy = vi
      .spyOn(controlApi, 'explain')
      .mockRejectedValueOnce(new ApiError(404, 'api.error.decision_not_found'))
      .mockResolvedValue(data);
    const log = new DecisionLogStore();
    await log.explain(7);
    expect(log.explained[7]).toEqual({ status: 'error', key: 'api.error.decision_not_found' });
    await log.explain(7);
    expect(spy).toHaveBeenCalledTimes(1);
    await log.explain(7, true);
    expect(log.explained[7]).toEqual({ status: 'ready', data });
    await log.explain(7, true);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
