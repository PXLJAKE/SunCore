// SPDX-License-Identifier: Apache-2.0
// The look-back data of the plan chart: the snapshot for the past part of the window (or the plan
// of a past time), what was measured, and the comparison with a second snapshot. Each part loads
// on its own and fails on its own; the answer of an older request never replaces a newer one.
import { ApiError } from '../api/client';
import { describeError } from '../api/errors';
import { planHistoryApi } from '../api/planHistory';
import type { PlanAt, PlanCompare, PlanView } from '../api/types';
import { SITE_KEYS, buildActual, deviceKey, type ActualData } from '../plan/actual';
import { needsSnapshot, referenceTime, snapshotTime } from '../plan/assemble';
import type { PlanTarget, TimeWindow } from '../plan/target';

export interface ChartRequest {
  target: PlanTarget;
  win: TimeWindow;
  /** The server's clock (the demo runs on a simulated one). */
  nowMs: number;
  live: Pick<PlanView, 'plan'> | null;
  compareMs: number | null;
}

/** Most keys one series request takes. */
const MAX_KEYS = 8;
const iso = (ms: number): string => new Date(ms).toISOString();
const isMissing = (e: unknown): boolean => e instanceof ApiError && e.status === 404;

export class PlanChartStore {
  /** The snapshot behind the past part of the window, or the plan of an `at` link. */
  snapshot = $state.raw<PlanAt | null>(null);
  /** No snapshot is stored for that time (pruned, or before the first one). */
  snapshotMissing = $state(false);
  snapshotError = $state<string | null>(null);
  actual = $state.raw<ActualData | null>(null);
  actualError = $state<string | null>(null);
  compare = $state.raw<PlanCompare | null>(null);
  compareMissing = $state(false);
  compareError = $state<string | null>(null);
  loading = $state(false);

  private planKey = '';
  private actualKey = '';
  private compareKey = '';
  private planSeq = 0;
  private actualSeq = 0;
  private compareSeq = 0;

  /** Load the snapshot the window needs (none when the live plan covers it). */
  async syncPlan(req: ChartRequest): Promise<void> {
    if (!needsSnapshot(req.target, req.win, req.live)) {
      this.planKey = '';
      this.snapshot = null;
      this.snapshotMissing = false;
      this.snapshotError = null;
      return;
    }
    const ts = snapshotTime(req.target, req.win);
    const key = String(ts);
    if (key === this.planKey) return;
    this.planKey = key;
    const seq = ++this.planSeq;
    this.loading = true;
    try {
      const out = await planHistoryApi.at(iso(ts));
      if (seq !== this.planSeq) return;
      this.snapshot = out;
      this.snapshotMissing = false;
      this.snapshotError = null;
    } catch (e) {
      if (seq !== this.planSeq) return;
      this.snapshot = null;
      this.snapshotMissing = isMissing(e);
      this.snapshotError = isMissing(e) ? null : describeError(e).key;
    } finally {
      if (seq === this.planSeq) this.loading = false;
    }
  }

  /** Load the measurements of the window's past part for the site and the given devices. */
  async syncActual(req: ChartRequest, deviceIds: readonly string[]): Promise<void> {
    const to = Math.min(req.win.toMs, req.nowMs);
    if (to <= req.win.fromMs) {
      this.actualKey = '';
      this.actual = null;
      this.actualError = null;
      return;
    }
    // The end of the window is "now" for the live view: look again every minute.
    const key = `${req.win.fromMs}|${Math.floor(to / 60_000)}|${deviceIds.join(',')}`;
    if (key === this.actualKey) return;
    this.actualKey = key;
    const seq = ++this.actualSeq;
    try {
      const from = iso(req.win.fromMs);
      const end = iso(to);
      const groups: string[][] = [];
      for (let i = 0; i < deviceIds.length; i += MAX_KEYS) {
        groups.push(deviceIds.slice(i, i + MAX_KEYS).map(deviceKey));
      }
      const [site, ...devs] = await Promise.all([
        planHistoryApi.series(SITE_KEYS, from, end),
        ...groups.map((g) => planHistoryApi.series(g, from, end)),
      ]);
      if (seq !== this.actualSeq) return;
      // Several device groups share one grid of steps; merge their series into one response.
      const devices = devs.length ? { ...devs[0]!, series: devs.flatMap((d) => d.series) } : null;
      this.actual = buildActual(site, devices, deviceIds);
      this.actualError = null;
    } catch (e) {
      if (seq !== this.actualSeq) return;
      this.actual = null;
      this.actualError = describeError(e).key;
    }
  }

  /** Load the comparison of the shown plan with the snapshot valid at `compareMs`. */
  async syncCompare(req: ChartRequest): Promise<void> {
    if (req.compareMs === null) {
      this.compareKey = '';
      this.compare = null;
      this.compareMissing = false;
      this.compareError = null;
      return;
    }
    const a = referenceTime(req.target, req.win, req.nowMs);
    const key = `${a}|${req.compareMs}`;
    if (key === this.compareKey) return;
    this.compareKey = key;
    const seq = ++this.compareSeq;
    try {
      const out = await planHistoryApi.compare(iso(a), iso(req.compareMs));
      if (seq !== this.compareSeq) return;
      this.compare = out;
      this.compareMissing = false;
      this.compareError = null;
    } catch (e) {
      if (seq !== this.compareSeq) return;
      this.compare = null;
      this.compareMissing = isMissing(e);
      this.compareError = isMissing(e) ? null : describeError(e).key;
    }
  }

  /** Forget everything (tests, leaving the view). */
  reset(): void {
    this.planKey = this.actualKey = this.compareKey = '';
    this.planSeq += 1;
    this.actualSeq += 1;
    this.compareSeq += 1;
    this.snapshot = null;
    this.actual = null;
    this.compare = null;
    this.snapshotMissing = this.compareMissing = false;
    this.snapshotError = this.actualError = this.compareError = null;
    this.loading = false;
  }
}
