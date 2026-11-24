// SPDX-License-Identifier: Apache-2.0
// The history view's read side: one summary decides the period, the other sections load for it.
// Every section fails on its own (a missing device table must not hide the energy bars), and the
// answer of an older navigation never replaces the answer of the current one.
import { ApiError } from '../api/client';
import { historyApi, type HistoryEndpoint, type HistoryParams } from '../api/history';
import { describeError } from '../api/errors';
import type {
  Granularity,
  HistoryCycles,
  HistoryDevices,
  HistoryHeatmap,
  HistorySankey,
  HistorySkill,
  HistorySummary,
} from '../api/types';
import {
  endOfRange,
  heatmapRange,
  neighbour,
  startOfDay,
  trendRange,
  type HistoryState,
  type Range,
} from '../history/model';

export type Section = 'sankey' | 'devices' | 'heatmap' | 'cycles' | 'skill';

/** Catalogue key of a failed section load (`null` = fine). */
type Errors = Partial<Record<Section | 'summary', string>>;

export class HistoryStore {
  period = $state<Granularity>('week');
  /** Any day inside the shown period; `null` = the period of today (server clock). */
  date = $state<string | null>(null);

  summary = $state.raw<HistorySummary | null>(null);
  sankey = $state.raw<HistorySankey | null>(null);
  devices = $state.raw<HistoryDevices | null>(null);
  heatmap = $state.raw<HistoryHeatmap | null>(null);
  cycles = $state.raw<HistoryCycles | null>(null);
  skill = $state.raw<HistorySkill | null>(null);
  errors = $state.raw<Errors>({});
  loading = $state(false);
  /** The time-series database is not there (503): the whole view says so. */
  unavailable = $state(false);

  /** The ranges the analysis charts cover, for their captions and CSV links. */
  trend = $state.raw<Range | null>(null);
  heat = $state.raw<Range | null>(null);

  private seq = 0;

  get state(): HistoryState {
    return { period: this.period, date: this.date };
  }

  apply(state: HistoryState): void {
    this.period = state.period;
    this.date = state.date;
  }

  setPeriod(period: Granularity): void {
    this.period = period;
  }

  /** Move to the period before or after the shown one. */
  step(dir: -1 | 1): void {
    if (this.summary) this.date = neighbour(this.summary, dir);
  }

  today(): void {
    this.date = null;
  }

  /** Query of the endpoints that take the period. */
  periodParams(): HistoryParams {
    return { period: this.period, date: this.date ?? undefined };
  }

  /** Query of an endpoint for CSV links of the section (same range as the chart). */
  paramsFor(endpoint: HistoryEndpoint): HistoryParams {
    if (endpoint === 'heatmap' && this.heat) return heatParams(this.heat);
    if ((endpoint === 'cycles' || endpoint === 'forecast-skill') && this.trend) {
      return dayParams(this.trend);
    }
    return this.periodParams();
  }

  async load(): Promise<void> {
    const mine = ++this.seq;
    this.loading = true;
    const params = this.periodParams();
    const errors: Errors = {};
    const fresh = (): boolean => mine === this.seq;
    const fail = (section: Section | 'summary', e: unknown): void => {
      errors[section] = describeError(e).key;
      if (e instanceof ApiError && e.status === 503) this.unavailable = true;
    };
    this.unavailable = false;
    try {
      const summary = await historyApi.summary(params);
      if (!fresh()) return;
      this.summary = summary;
      const trend = trendRange(summary);
      const heat = heatmapRange(summary);
      this.trend = trend;
      this.heat = heat;
      const run = async <T>(
        section: Section,
        call: () => Promise<T>,
        set: (v: T) => void,
      ): Promise<void> => {
        try {
          const v = await call();
          if (fresh()) set(v);
        } catch (e) {
          if (fresh()) fail(section, e);
        }
      };
      await Promise.all([
        run(
          'sankey',
          () => historyApi.sankey(params),
          (v) => (this.sankey = v),
        ),
        run(
          'devices',
          () => historyApi.devices(params),
          (v) => (this.devices = v),
        ),
        run(
          'heatmap',
          () => historyApi.heatmap(heatParams(heat)),
          (v) => (this.heatmap = v),
        ),
        run(
          'cycles',
          () => historyApi.cycles(dayParams(trend)),
          (v) => (this.cycles = v),
        ),
        run(
          'skill',
          () => historyApi.skill(dayParams(trend)),
          (v) => (this.skill = v),
        ),
      ]);
    } catch (e) {
      if (!fresh()) return;
      fail('summary', e);
      this.summary = null;
    } finally {
      if (fresh()) {
        this.errors = errors;
        this.loading = false;
      }
    }
  }
}

const dayParams = (r: Range): HistoryParams => ({ from: r.from, to: r.to });

const heatParams = (r: Range): HistoryParams => ({
  from: startOfDay(r.from),
  to: endOfRange(r),
});
