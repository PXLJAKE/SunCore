// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryStore } from './history.svelte';

const summary = {
  period: 'week',
  key: '2026-W25',
  start: '2026-06-15T00:00:00+02:00',
  end: '2026-06-22T00:00:00+02:00',
  days: 7,
  days_with_data: 7,
  days_complete: 7,
  in_progress: true,
  buckets: [],
};

function respond(routes: Record<string, () => Response | Promise<Response>>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input));
    const hit = Object.entries(routes).find(([path]) => url.pathname.endsWith(path));
    return hit ? hit[1]() : new Response('{}', { status: 404 });
  });
}
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('HistoryStore', () => {
  beforeEach(() => vi.stubGlobal('location', new URL('http://x.test/app/')));
  afterEach(() => vi.unstubAllGlobals());

  it('loads the summary first and the other sections for its ranges', async () => {
    const calls: string[] = [];
    const fetchMock = respond({
      '/history/summary': () => json(summary),
      '/history/sankey': () => json({ links: [] }),
      '/history/devices': () => json({ devices: [] }),
      '/history/heatmap': () => json({ values: [] }),
      '/history/cycles': () => json({ days: [] }),
      '/history/forecast-skill': () => json({ series: [] }),
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (u: RequestInfo | URL) => {
        calls.push(String(u));
        return fetchMock(u);
      }),
    );
    const store = new HistoryStore();
    store.apply({ period: 'week', date: '2026-06-17' });
    await store.load();
    expect(calls[0]).toContain('history/summary?period=week&date=2026-06-17');
    expect(calls.find((c) => c.includes('history/cycles'))).toContain(
      'from=2026-06-15&to=2026-06-21',
    );
    expect(calls.find((c) => c.includes('history/heatmap'))).toContain(
      'from=2026-05-25T00%3A00%3A00&to=2026-06-22T00%3A00%3A00',
    );
    expect(store.summary?.key).toBe('2026-W25');
    expect(store.errors).toEqual({});
    expect(store.loading).toBe(false);
  });

  it('keeps the other sections when one fails', async () => {
    vi.stubGlobal(
      'fetch',
      respond({
        '/history/summary': () => json(summary),
        '/history/sankey': () => json({ links: [] }),
        '/history/devices': () => json({ detail: 'api.error.history_range_invalid' }, 422),
        '/history/heatmap': () => json({ values: [] }),
        '/history/cycles': () => json({ days: [] }),
        '/history/forecast-skill': () => json({ series: [] }),
      }),
    );
    const store = new HistoryStore();
    await store.load();
    expect(store.errors.devices).toBe('api.error.history_range_invalid');
    expect(store.sankey).not.toBeNull();
    expect(store.unavailable).toBe(false);
  });

  it('reports a missing time-series database', async () => {
    vi.stubGlobal(
      'fetch',
      respond({ '/history/summary': () => json({ detail: 'api.error.history_unavailable' }, 503) }),
    );
    const store = new HistoryStore();
    await store.load();
    expect(store.unavailable).toBe(true);
    expect(store.summary).toBeNull();
    expect(store.errors.summary).toBe('api.error.history_unavailable');
  });

  it('ignores the answer of an older navigation', async () => {
    let release: (r: Response) => void = () => undefined;
    const slow = new Promise<Response>((r) => (release = r));
    let n = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (u: RequestInfo | URL) => {
        const url = String(u);
        if (url.includes('history/summary')) {
          n += 1;
          return n === 1 ? slow : json({ ...summary, key: '2026-W26' });
        }
        return json({ links: [], devices: [], values: [], days: [], series: [] });
      }),
    );
    const store = new HistoryStore();
    const first = store.load();
    await store.load();
    release(json({ ...summary, key: '2026-W24' }));
    await first;
    expect(store.summary?.key).toBe('2026-W26');
  });

  it('steps to the neighbouring periods', async () => {
    vi.stubGlobal(
      'fetch',
      respond({
        '/history/summary': () => json(summary),
        '/history/sankey': () => json({}),
        '/history/devices': () => json({}),
        '/history/heatmap': () => json({}),
        '/history/cycles': () => json({}),
        '/history/forecast-skill': () => json({}),
      }),
    );
    const store = new HistoryStore();
    await store.load();
    store.step(-1);
    expect(store.date).toBe('2026-06-14');
    store.step(1);
    expect(store.date).toBe('2026-06-22');
    store.today();
    expect(store.date).toBeNull();
  });
});
