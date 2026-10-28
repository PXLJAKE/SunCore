// SPDX-License-Identifier: Apache-2.0
// The planner's read side for the views: plan, plan status, forecast risk, PV forecast with blend
// weights, accuracy and the configured forecast sources. The intervals are fetched on demand and
// again whenever the live feed announces a new plan (one WebSocket, no polling).
import { ApiError, api, planApi } from '../api/client';
import { describeError } from '../api/errors';
import type {
  ForecastAccuracyView,
  ForecastConfig,
  PlanStatus,
  PlanView,
  PvForecastView,
  ReplanAck,
  RiskView,
} from '../api/types';
import { live } from './live.svelte';

export type ReplanState = 'idle' | 'busy' | 'accepted' | 'limited' | 'error';

/** How long the replan button stays disabled after a request (debounced feedback). */
export const REPLAN_COOLDOWN_MS = 3000;

export class PlanStore {
  view = $state.raw<PlanView | null>(null);
  status = $state.raw<PlanStatus | null>(null);
  risk = $state.raw<RiskView | null>(null);
  pv = $state.raw<PvForecastView | null>(null);
  accuracy = $state.raw<ForecastAccuracyView | null>(null);
  forecastConfig = $state.raw<ForecastConfig | null>(null);
  loaded = $state(false);
  loading = $state(false);
  /** Catalogue key of the last failed load; `api.error.planning_unavailable` = no planner. */
  error = $state<string | null>(null);

  replanState = $state<ReplanState>('idle');
  replanAck = $state.raw<ReplanAck | null>(null);
  replanError = $state<string | null>(null);

  private seen = -1;
  private cooldown: ReturnType<typeof setTimeout> | null = null;

  /** Plan and status: the part the Now view needs. */
  async refresh(): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    try {
      const [view, status] = await Promise.all([planApi.plan(48), planApi.status()]);
      this.view = view;
      this.status = status;
      this.error = null;
    } catch (e) {
      this.error = e instanceof ApiError && e.key ? e.key : describeError(e).key;
    } finally {
      this.loading = false;
      this.loaded = true;
    }
  }

  /** Forecast panel and target lines of the plan view; each part is optional. */
  async refreshForecasts(): Promise<void> {
    const [risk, pv, accuracy, forecastConfig] = await Promise.all([
      planApi.forecastRisk().catch(() => null),
      planApi.forecastPv(48).catch(() => null),
      planApi.forecastAccuracy().catch(() => null),
      api.getSection<ForecastConfig>('forecast').catch(() => null),
    ]);
    this.risk = risk;
    this.pv = pv;
    this.accuracy = accuracy;
    this.forecastConfig = forecastConfig;
  }

  /** Refetch when the live feed announced a plan since the last look (call from an effect). */
  syncWithFeed(withForecasts = false): void {
    const n = live.planUpdates;
    if (n === this.seen) return;
    const first = this.seen === -1;
    this.seen = n;
    if (first) return; // the view's own first load covers the state as it is
    void this.refresh();
    if (withForecasts) void this.refreshForecasts();
  }

  /** Ask for a new plan; the button is disabled while the request runs and briefly after. */
  async replan(): Promise<void> {
    if (this.replanState === 'busy') return;
    this.replanState = 'busy';
    this.replanError = null;
    try {
      const ack = await planApi.replan();
      this.replanAck = ack;
      this.replanState = ack.accepted === false ? 'limited' : 'accepted';
    } catch (e) {
      this.replanError = describeError(e).key;
      this.replanState = 'error';
    }
    if (this.cooldown) clearTimeout(this.cooldown);
    this.cooldown = setTimeout(() => {
      this.cooldown = null;
      if (this.replanState !== 'busy') this.replanState = 'idle';
    }, REPLAN_COOLDOWN_MS);
    void this.refresh();
  }

  /**
   * "Now" as the server sees it (the plan's hand-over time): the demo runs on a simulated clock,
   * so the browser's clock must not decide which intervals are in the past.
   */
  get nowMs(): number {
    return this.view ? Date.parse(this.view.generated_at) : Date.now();
  }

  /** The planner is not running (no site set up): the API answers 409. */
  get unavailable(): boolean {
    return this.error === 'api.error.planning_unavailable';
  }

  /** An Open-Meteo based source is configured (its data needs an attribution, CC BY 4.0). */
  get usesOpenMeteo(): boolean {
    return (this.forecastConfig?.sources ?? []).some((s) => s.template_id.startsWith('open_meteo'));
  }
}

export const plan = new PlanStore();
