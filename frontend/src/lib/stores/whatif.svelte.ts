// SPDX-License-Identifier: Apache-2.0
// The what-if view's state: the kept runs, the selected run with its result, and the polling
// while a run is active. The server keeps one run at a time (a second start is a 409) and the
// last five results; the store never invents a result. The answer of an older request never
// replaces the answer of a newer one.
import { ApiError } from '../api/client';
import { describeError } from '../api/errors';
import { backtestApi } from '../api/backtests';
import { historyApi } from '../api/history';
import type { BacktestRequest, BacktestRun } from '../api/types';

/** Milliseconds between two polls while a run is active. */
export const POLL_MS = 1000;

export class WhatIfStore {
  /** Today in the site's time zone (server clock; the browser's clock may differ). */
  today = $state<string | null>(null);
  runs = $state.raw<BacktestRun[]>([]);
  selectedId = $state<number | null>(null);
  /** The selected run with its result (the list carries no results). */
  detail = $state.raw<BacktestRun | null>(null);
  loaded = $state(false);
  starting = $state(false);
  /** The time-series database or the site configuration is missing (503). */
  unavailable = $state(false);
  /** Catalogue key of the last failed action (`null` = fine). */
  error = $state<string | null>(null);

  private timer: ReturnType<typeof setTimeout> | undefined;
  private listSeq = 0;
  private detailSeq = 0;
  private disposed = false;

  get running(): BacktestRun | null {
    return this.runs.find((r) => r.status === 'running') ?? null;
  }

  async init(): Promise<void> {
    await this.loadToday();
    await this.refresh(true);
    this.loaded = true;
  }

  dispose(): void {
    this.disposed = true;
    clearTimeout(this.timer);
  }

  private async loadToday(): Promise<void> {
    try {
      this.today = (await historyApi.summary({ period: 'day' })).key;
    } catch (e) {
      this.fail(e);
    }
  }

  private fail(e: unknown): void {
    if (e instanceof ApiError && e.status === 503) this.unavailable = true;
    this.error = describeError(e).key;
  }

  /** Reload the list; `pick` also selects (and loads) the newest finished or the active run. */
  async refresh(pick = false): Promise<void> {
    const seq = ++this.listSeq;
    let runs: BacktestRun[];
    try {
      runs = await backtestApi.list();
    } catch (e) {
      if (seq === this.listSeq) this.fail(e);
      return;
    }
    if (seq !== this.listSeq || this.disposed) return;
    this.unavailable = false;
    this.runs = runs;
    const current =
      this.selectedId === null ? undefined : runs.find((r) => r.id === this.selectedId);
    if (this.selectedId !== null && !current) {
      this.selectedId = null;
      this.detail = null;
    }
    if (pick && this.selectedId === null) {
      const first =
        runs.find((r) => r.status === 'running') ?? runs.find((r) => r.status === 'done');
      if (first) await this.select(first.id);
    } else if (current && current.status !== 'running' && this.detail?.status === 'running') {
      await this.select(current.id); // the selected run just finished: fetch its result
    }
    this.schedule();
  }

  private schedule(): void {
    clearTimeout(this.timer);
    if (this.disposed || !this.running) return;
    this.timer = setTimeout(() => void this.refresh(), POLL_MS);
  }

  async select(id: number): Promise<void> {
    this.selectedId = id;
    const seq = ++this.detailSeq;
    try {
      const run = await backtestApi.get(id);
      if (seq !== this.detailSeq || this.selectedId !== id) return;
      this.detail = run;
      this.error = null;
    } catch (e) {
      if (seq !== this.detailSeq) return;
      this.detail = null;
      this.fail(e);
      if (e instanceof ApiError && e.status === 404) await this.refresh();
    }
  }

  /** Start a run; the new run becomes the selected one. Returns whether the server took it. */
  async start(request: BacktestRequest): Promise<boolean> {
    if (this.starting) return false;
    this.starting = true;
    this.error = null;
    try {
      const run = await backtestApi.start(request);
      this.selectedId = run.id;
      this.detail = run;
      await this.refresh();
      return true;
    } catch (e) {
      this.fail(e);
      return false;
    } finally {
      this.starting = false;
    }
  }

  /** Cancel a running run or delete a finished one. */
  async remove(id: number): Promise<void> {
    this.error = null;
    try {
      const { outcome } = await backtestApi.remove(id);
      if (outcome === 'deleted' && this.selectedId === id) {
        this.selectedId = null;
        this.detail = null;
      }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) this.fail(e);
    }
    await this.refresh();
    if (this.selectedId === id) await this.select(id);
  }
}
