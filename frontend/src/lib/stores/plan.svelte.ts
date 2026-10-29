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

  /** Server clock minus browser clock at the last fetch (ms). */
  private offsetMs = 0;
  /** Bumped once a minute so "now" moves on between fetches. */
  private tick = $state(0);
  private clock: ReturnType<typeof setInterval> | null = null;
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
      this.offsetMs = Date.parse(view.generated_at) - Date.now();
      this.startClock();
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
    // A new plan, or a new connection (a drop may have hidden plans): fetch again.
    const n = live.planUpdates + live.snapshotCount;
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
   * "Now" as the server sees it: the browser clock plus the offset measured at the last fetch (the
   * demo runs on a simulated clock, so the browser's clock alone must not decide which intervals
   * are in the past), moved on by a minute tick.
   */
  get nowMs(): number {
    void this.tick;
    return Date.now() + this.offsetMs;
  }

  private startClock(): void {
    if (this.clock !== null) return;
    this.clock = setInterval(() => (this.tick += 1), 60_000);
  }

  /** Stop the minute tick (tests). */
  stopClock(): void {
    if (this.clock !== null) clearInterval(this.clock);
    this.clock = null;
  }

  /** The plan is old, over or not followed by the control: the lists must say so. */
  get outdated(): boolean {
    const v = this.view;
    return v !== null && (v.stale || v.expired || !v.in_use);
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
