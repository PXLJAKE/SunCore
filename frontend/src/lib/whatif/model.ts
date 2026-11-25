// SPDX-License-Identifier: Apache-2.0
// Pure logic of the what-if view: the form, the request it builds, the checks that catch the
// obvious early (the server stays the authority), and the numbers of a finished run as rows.
// No rendering, no network.
import type {
  BacktestDay,
  BacktestFigures,
  BacktestRequest,
  BacktestResult,
  BacktestTotals,
  BatteryOverlay,
  BatteryStrategy,
  Param,
  ParamValue,
  ValidationIssue,
} from '../api/types';
import { planChanges, type Drafts, type Raw } from '../expert/model';
import { addDays, isDay } from '../history/model';

/** Longest window of one run (`MAX_DAYS` of the backend). */
export const MAX_DAYS = 30;
/** Window a form starts with (`DEFAULT_DAYS` of the backend). */
export const DEFAULT_DAYS = 14;
export const STRATEGIES: readonly BatteryStrategy[] = [
  'self_consumption',
  'prefer_full',
  'target_by_time',
];

export type GridCharge = 'keep' | 'off' | 'on';
export type TariffMode = 'stored' | 'fixed';

/** What the inputs hold: text for numbers (kWh, kW, %, currency/kWh), choices as ids. */
export interface WhatIfForm {
  start: string;
  end: string;
  perfect: boolean;
  /** Edited registry parameters (only keys the user touched). */
  params: Record<string, Raw>;
  strategy: '' | BatteryStrategy;
  autarkyWeight: number | null;
  capacityKwh: number | null;
  maxChargeKw: number | null;
  maxDischargeKw: number | null;
  reservePct: number | null;
  gridCharge: GridCharge;
  gridChargeMaxPrice: number | null;
  tariff: TariffMode;
  importPrice: number | null;
  exportPrice: number | null;
}

/** The last day of a window is yesterday at the latest: the replay needs finished days. */
export function defaultWindow(today: string): { start: string; end: string } {
  const end = addDays(today, -1);
  return { start: addDays(end, -(DEFAULT_DAYS - 1)), end };
}

export function emptyForm(today: string): WhatIfForm {
  return {
    ...defaultWindow(today),
    perfect: false,
    params: {},
    strategy: '',
    autarkyWeight: null,
    capacityKwh: null,
    maxChargeKw: null,
    maxDischargeKw: null,
    reservePct: null,
    gridCharge: 'keep',
    gridChargeMaxPrice: null,
    tariff: 'stored',
    importPrice: null,
    exportPrice: 0,
  };
}

export const windowDays = (start: string, end: string): number =>
  Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000) + 1;

/** One problem of the form: `field` is the input it belongs to (`window`, `capacityKwh`, ...). */
export type FormIssue = ValidationIssue & { loc: [string] };

const issue = (
  field: string,
  key: string,
  args: Record<string, string | number> = {},
): FormIssue => ({ loc: [field], key, args });

export const issuesOf = (issues: readonly FormIssue[], field: string): FormIssue[] =>
  issues.filter((i) => i.loc[0] === field);

export function windowIssues(form: WhatIfForm, today: string): FormIssue[] {
  if (!isDay(form.start) || !isDay(form.end)) return [issue('window', 'whatif.err.window_missing')];
  if (form.end < form.start) return [issue('window', 'api.validation.backtest_window_order')];
  if (windowDays(form.start, form.end) > MAX_DAYS) {
    return [issue('window', 'api.validation.backtest_window_too_long', { max: MAX_DAYS })];
  }
  if (form.end >= today) return [issue('window', 'api.error.backtest_window_not_past')];
  return [];
}

interface Bound {
  field: keyof WhatIfForm;
  min: number;
  max: number;
  /** Exclusive lower bound (a size or a power must be above zero). */
  exclusiveMin?: boolean;
}
const BOUNDS: readonly Bound[] = [
  { field: 'autarkyWeight', min: 0, max: 1 },
  { field: 'capacityKwh', min: 0, max: 1000, exclusiveMin: true },
  { field: 'maxChargeKw', min: 0, max: 1000, exclusiveMin: true },
  { field: 'maxDischargeKw', min: 0, max: 1000, exclusiveMin: true },
  { field: 'reservePct', min: 0, max: 100 },
  { field: 'gridChargeMaxPrice', min: -10, max: 100 },
  { field: 'importPrice', min: -10, max: 100 },
  { field: 'exportPrice', min: -10, max: 100 },
];

function numberIssue(b: Bound, v: number | null, required: boolean): FormIssue | null {
  if (v === null) return required ? issue(b.field, 'whatif.err.required') : null;
  if (!Number.isFinite(v)) return issue(b.field, 'whatif.err.number');
  if (v < b.min || v > b.max || (b.exclusiveMin && v <= b.min)) {
    return issue(b.field, 'whatif.err.range', {
      min: b.exclusiveMin ? `> ${b.min}` : b.min,
      max: b.max,
    });
  }
  return null;
}

export interface Built {
  request: BacktestRequest | null;
  issues: FormIssue[];
}

const toW = (kw: number): number => Math.round(kw * 1000 * 1000) / 1000;

/**
 * The request of a form, or the issues that stop it. `registry` are the parameters tagged
 * `whatif` (only those the user edited go into the overlay; the server checks the tag again).
 */
export function buildRequest(form: WhatIfForm, today: string, registry: readonly Param[]): Built {
  const issues: FormIssue[] = windowIssues(form, today);

  const edited = planChanges(registry, form.params as Drafts);
  for (const [key, i] of Object.entries(edited.errors))
    issues.push({ ...i, loc: [`param:${key}`] });

  const gridOn = form.gridCharge === 'on';
  const tariffFixed = form.tariff === 'fixed';
  for (const b of BOUNDS) {
    const required =
      (b.field === 'gridChargeMaxPrice' && gridOn) || (b.field === 'importPrice' && tariffFixed);
    const skip =
      (b.field === 'gridChargeMaxPrice' && !gridOn) ||
      ((b.field === 'importPrice' || b.field === 'exportPrice') && !tariffFixed);
    if (skip) continue;
    const found = numberIssue(b, form[b.field] as number | null, required);
    if (found) issues.push(found);
  }
  if (issues.length > 0) return { request: null, issues };

  const battery: BatteryOverlay = {};
  if (form.strategy) battery.strategy = form.strategy;
  const weight = form.autarkyWeight;
  if (weight !== null) battery.autarky_weight_per_kwh = weight;
  const cap = form.capacityKwh;
  if (cap !== null) battery.capacity_wh = cap * 1000;
  const charge = form.maxChargeKw;
  if (charge !== null) battery.max_charge_w = toW(charge);
  const discharge = form.maxDischargeKw;
  if (discharge !== null) battery.max_discharge_w = toW(discharge);
  const reserve = form.reservePct;
  if (reserve !== null) battery.reserve_pct = reserve;
  if (form.gridCharge === 'off') battery.grid_charge = { enabled: false };
  if (gridOn) {
    battery.grid_charge = { enabled: true, max_price: form.gridChargeMaxPrice };
  }

  const request: BacktestRequest = {
    start: form.start,
    end: form.end,
    params: edited.changes,
    perfect_foresight: form.perfect,
  };
  if (Object.keys(battery).length > 0) request.battery = battery;
  if (tariffFixed) {
    request.tariff = {
      kind: 'fixed',
      import_price: form.importPrice ?? 0,
      export_price: form.exportPrice ?? 0,
    };
  }
  return { request, issues: [] };
}

/** One thing a request changes, for the list of settings of a run. */
export type Change =
  | { kind: 'param'; key: string; requested: ParamValue; used: ParamValue }
  | { kind: 'battery'; field: keyof BatteryOverlay; value: string | number | boolean }
  | { kind: 'tariff'; importPrice: number; exportPrice: number }
  | { kind: 'perfect' };

export function changesOf(request: BacktestRequest, result: BacktestResult | null): Change[] {
  const out: Change[] = [];
  for (const [key, requested] of Object.entries(request.params ?? {})) {
    const used = result?.params[key] ?? requested;
    out.push({ kind: 'param', key, requested, used });
  }
  const b = request.battery;
  if (b) {
    for (const [field, value] of Object.entries(b) as [keyof BatteryOverlay, unknown][]) {
      if (value === null || value === undefined) continue;
      if (field === 'grid_charge') {
        const g = b.grid_charge;
        if (g) out.push({ kind: 'battery', field, value: g.enabled ? (g.max_price ?? 0) : false });
        continue;
      }
      out.push({ kind: 'battery', field, value: value as string | number });
    }
  }
  if (request.tariff) {
    out.push({
      kind: 'tariff',
      importPrice: request.tariff.import_price,
      exportPrice: request.tariff.export_price,
    });
  }
  if (request.perfect_foresight) out.push({ kind: 'perfect' });
  return out;
}

// --- results -----------------------------------------------------------------------------------

export type MetricKind = 'money' | 'energy' | 'ratio' | 'cycles';
export type MetricId =
  | 'cost_net'
  | 'import_wh'
  | 'export_wh'
  | 'charge_wh'
  | 'discharge_wh'
  | 'autarky'
  | 'self_consumption'
  | 'cycles';

export interface MetricRow {
  id: MetricId;
  kind: MetricKind;
  actual: number | null;
  whatif: number | null;
  perfect: number | null;
}

const pick: Record<MetricId, [MetricKind, (f: BacktestFigures) => number | null]> = {
  cost_net: ['money', (f) => f.cost_net],
  import_wh: ['energy', (f) => f.import_wh],
  export_wh: ['energy', (f) => f.export_wh],
  charge_wh: ['energy', (f) => f.charge_wh],
  discharge_wh: ['energy', (f) => f.discharge_wh],
  autarky: ['ratio', (f) => f.autarky],
  self_consumption: ['ratio', (f) => f.self_consumption],
  cycles: ['cycles', (f) => f.equivalent_full_cycles],
};
export const METRICS = Object.keys(pick) as MetricId[];

export function metricRows(totals: BacktestTotals): MetricRow[] {
  return METRICS.map((id) => {
    const [kind, get] = pick[id];
    return {
      id,
      kind,
      actual: get(totals.actual),
      whatif: get(totals.whatif),
      perfect: totals.perfect ? get(totals.perfect) : null,
    };
  });
}

/** Net cost of the actual run minus the what-if: positive = the what-if is cheaper. */
export const savingsVsActual = (totals: BacktestTotals): number =>
  totals.actual.cost_net - totals.whatif.cost_net;

export type Verdict = 'cheaper' | 'dearer' | 'same';

/** Differences below half a cent are noise of rounding, not a result. */
export function verdict(delta: number): Verdict {
  if (Math.abs(delta) < 0.005) return 'same';
  return delta > 0 ? 'cheaper' : 'dearer';
}

export type LadderId = 'no_pv' | 'pv_only' | 'actual' | 'whatif' | 'perfect';

/** Net cost of the references and the runs, most expensive reference first. */
export function costLadder(totals: BacktestTotals): { id: LadderId; cost: number }[] {
  const rows: { id: LadderId; cost: number }[] = [
    { id: 'no_pv', cost: totals.baseline_no_pv_cost },
    { id: 'pv_only', cost: totals.baseline_pv_only_cost },
    { id: 'actual', cost: totals.actual.cost_net },
    { id: 'whatif', cost: totals.whatif.cost_net },
  ];
  if (totals.perfect) rows.push({ id: 'perfect', cost: totals.perfect.cost_net });
  return rows;
}

export interface DayRow {
  day: string;
  slots: number;
  pricedSlots: number;
  actual: number;
  whatif: number;
  perfect: number | null;
  delta: number;
}

export function dayRows(days: readonly BacktestDay[]): DayRow[] {
  return days.map((d) => ({
    day: d.day,
    slots: d.slots,
    pricedSlots: d.priced_slots,
    actual: d.actual.cost_net,
    whatif: d.whatif.cost_net,
    perfect: d.perfect?.cost_net ?? null,
    delta: d.actual.cost_net - d.whatif.cost_net,
  }));
}

/** Whether a run still has a result worth showing. */
export const isFinished = (status: string): boolean => status !== 'running';

const csvCell = (v: string | number | null): string => {
  if (v === null) return '';
  const s = String(v);
  return /[",\n;]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
};

/** The daily table as CSV (dot decimals, comma separated, energy in Wh, cost in currency). */
export function resultCsv(result: BacktestResult): string {
  const head = [
    'day',
    'priced_slots',
    'cost_actual',
    'cost_whatif',
    'cost_perfect',
    'cost_no_pv',
    'cost_pv_only',
    'import_wh_actual',
    'import_wh_whatif',
    'export_wh_actual',
    'export_wh_whatif',
    'autarky_actual',
    'autarky_whatif',
  ];
  const lines = [head.join(',')];
  for (const d of result.days) {
    lines.push(
      [
        d.day,
        d.priced_slots,
        d.actual.cost_net,
        d.whatif.cost_net,
        d.perfect?.cost_net ?? null,
        d.baseline_no_pv_cost,
        d.baseline_pv_only_cost,
        d.actual.import_wh,
        d.whatif.import_wh,
        d.actual.export_wh,
        d.whatif.export_wh,
        d.actual.autarky,
        d.whatif.autarky,
      ]
        .map(csvCell)
        .join(','),
    );
  }
  return `${lines.join('\n')}\n`;
}

export const resultFilename = (result: BacktestResult, ext: string): string =>
  `suncore-whatif-${result.start}_${result.end}.${ext}`;
