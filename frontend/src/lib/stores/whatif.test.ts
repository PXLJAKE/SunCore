// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '../whatif/fixtures';
import { POLL_MS, WhatIfStore } from './whatif.svelte';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Server {
  runs: ReturnType<typeof run>[];
  calls: string[];
}

/** A tiny fake of the four routes plus the history summary the store reads the date from. */
function fake(server: Server) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = init?.method ?? 'GET';
    server.calls.push(`${method} ${url.pathname.split('/api/v1/')[1]}`);
    if (url.pathname.endsWith('/history/summary')) return json({ key: '2026-06-21' });
    const one = /backtests\/(\d+)$/.exec(url.pathname);
    if (one) {
      const id = Number(one[1]);
      const hit = server.runs.find((r) => r.id === id);
      if (!hit) return json({ detail: 'api.error.backtest_not_found' }, 404);
      if (method === 'DELETE') {
        if (hit.status === 'running') hit.status = 'cancelled';
        else server.runs = server.runs.filter((r) => r.id !== id);
        return json({ outcome: hit.status === 'cancelled' ? 'cancelled' : 'deleted' });
      }
      return json(hit);
    }
    if (method === 'POST') {
      const created = run({
        id: 9,
        status: 'running',
        result: null,
        progress: { done: 0, total: 2 },
      });
      server.runs.unshift(created);
      return json(created, 202);
    }
    return json(server.runs.map((r) => ({ ...r, result: null })));
  });
}

describe('WhatIfStore', () => {
  let server: Server;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('location', new URL('http://x.test/app/'));
    server = { runs: [run()], calls: [] };
    vi.stubGlobal('fetch', fake(server));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('reads the server date and opens the newest finished run with its result', async () => {
    const store = new WhatIfStore();
    await store.init();
    expect(store.today).toBe('2026-06-21');
    expect(store.selectedId).toBe(1);
    expect(store.detail?.result).not.toBeNull();
    expect(store.loaded).toBe(true);
    store.dispose();
  });

  it('polls while a run is active and fetches the result when it finishes', async () => {
    server.runs = [
      run({ id: 2, status: 'running', result: null, progress: { done: 1, total: 2 } }),
    ];
    const store = new WhatIfStore();
    await store.init();
    expect(store.running?.id).toBe(2);
    expect(store.detail?.status).toBe('running');
    server.runs[0] = run({ id: 2 });
    await vi.advanceTimersByTimeAsync(POLL_MS + 10);
    expect(store.running).toBeNull();
    expect(store.detail?.status).toBe('done');
    expect(store.detail?.result).not.toBeNull();
    const before = server.calls.length;
    await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    expect(server.calls.length).toBe(before); // polling stopped with the run
    store.dispose();
  });

  it('starts a run and selects it', async () => {
    const store = new WhatIfStore();
    await store.init();
    const ok = await store.start({
      start: '2026-06-18',
      end: '2026-06-19',
      params: {},
      perfect_foresight: false,
    });
    expect(ok).toBe(true);
    expect(store.selectedId).toBe(9);
    expect(store.running?.id).toBe(9);
    store.dispose();
  });

  it('keeps the server key of a refused start', async () => {
    const store = new WhatIfStore();
    await store.init();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ detail: 'api.error.backtest_busy' }, 409)),
    );
    expect(await store.start({ start: 'a', end: 'b', params: {}, perfect_foresight: false })).toBe(
      false,
    );
    expect(store.error).toBe('api.error.backtest_busy');
    expect(store.starting).toBe(false);
    store.dispose();
  });

  it('deletes a finished run and clears the selection; cancel keeps the run', async () => {
    const store = new WhatIfStore();
    await store.init();
    await store.remove(1);
    expect(store.runs).toHaveLength(0);
    expect(store.selectedId).toBeNull();
    expect(store.detail).toBeNull();
    store.dispose();
  });

  it('says unavailable on a 503', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ detail: 'api.error.backtest_unavailable' }, 503)),
    );
    const store = new WhatIfStore();
    await store.init();
    expect(store.unavailable).toBe(true);
    store.dispose();
  });
});
