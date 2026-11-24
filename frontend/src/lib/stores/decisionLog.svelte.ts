// SPDX-License-Identifier: Apache-2.0
// The decision log view: filtered and paged on the server; a slower answer of an older request
// never replaces the answer of the current one. The "Why?" answers are cached per row id.
import { controlApi } from '../api/client';
import { describeError } from '../api/errors';
import type { Decision, DecisionExplain, DecisionKind } from '../api/types';
import { emptyFilters, filtersToApi, PAGE_SIZE, type LogFilters } from '../log/filters';

export { DECISION_KINDS } from '../log/filters';

export type ExplainState =
  | { status: 'loading' }
  | { status: 'ready'; data: DecisionExplain }
  | { status: 'error'; key: string };

export class DecisionLogStore {
  items = $state.raw<Decision[]>([]);
  /** Row id of each item (same index). */
  ids = $state.raw<number[]>([]);
  total = $state(0);
  offset = $state(0);
  loading = $state(false);
  error = $state<string | null>(null);
  filters = $state<LogFilters>(emptyFilters());
  explained = $state.raw<Record<number, ExplainState>>({});

  private seq = 0;

  toggleKind(kind: DecisionKind): void {
    this.filters.kinds = this.filters.kinds.includes(kind)
      ? this.filters.kinds.filter((k) => k !== kind)
      : [...this.filters.kinds, kind];
  }

  /** Replace all filters (a new filter starts on the first page). */
  setFilters(f: LogFilters): void {
    this.filters = f;
    this.offset = 0;
  }

  /** The query of the current page. */
  params(): URLSearchParams {
    return filtersToApi(this.filters, { limit: PAGE_SIZE, offset: this.offset });
  }

  /** The query of the whole filtered log (CSV export). */
  exportParams(): URLSearchParams {
    return filtersToApi(this.filters);
  }

  get pageCount(): number {
    return Math.max(1, Math.ceil(this.total / PAGE_SIZE));
  }

  get page(): number {
    return Math.floor(this.offset / PAGE_SIZE) + 1;
  }

  goToPage(page: number): void {
    this.offset = (Math.min(Math.max(page, 1), this.pageCount) - 1) * PAGE_SIZE;
  }

  async load(): Promise<void> {
    const mine = ++this.seq;
    this.loading = true;
    try {
      const page = await controlApi.decisionPage(this.params());
      if (mine !== this.seq) return;
      this.items = page.items;
      this.ids = page.ids;
      this.total = page.total;
      this.error = null;
      // A page beyond the end (rows were pruned): back to the last one.
      if (page.items.length === 0 && page.total > 0 && this.offset > 0) {
        this.total = page.total;
        this.goToPage(this.pageCount);
        this.loading = false;
        await this.load();
        return;
      }
    } catch (e) {
      if (mine !== this.seq) return;
      this.error = describeError(e).key;
    } finally {
      if (mine === this.seq) this.loading = false;
    }
  }

  /** Fetch the "Why?" of a row once; `force` retries after an error. */
  async explain(id: number, force = false): Promise<void> {
    const have = this.explained[id];
    if (have && !(force && have.status === 'error')) return;
    this.explained = { ...this.explained, [id]: { status: 'loading' } };
    try {
      const data = await controlApi.explain(id);
      this.explained = { ...this.explained, [id]: { status: 'ready', data } };
    } catch (e) {
      this.explained = { ...this.explained, [id]: { status: 'error', key: describeError(e).key } };
    }
  }
}
