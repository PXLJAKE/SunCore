// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { param } from '../expert/fixtures';
import { figures, result } from './fixtures';
import {
  buildRequest,
  changesOf,
  costLadder,
  dayRows,
  defaultWindow,
  emptyForm,
  issuesOf,
  metricRows,
  resultCsv,
  resultFilename,
  savingsVsActual,
  verdict,
  windowDays,
  windowIssues,
} from './model';

const TODAY = '2026-06-21';
const gap = param({
  key: 'planner.mip_gap',
  group: 'planner',
  tags: ['expert', 'whatif'],
  default: 0.01,
  value: 0.01,
  minimum: 0,
  maximum: 0.5,
});

describe('window', () => {
  it('defaults to the 14 days up to yesterday', () => {
    expect(defaultWindow(TODAY)).toEqual({ start: '2026-06-07', end: '2026-06-20' });
    expect(windowDays('2026-06-07', '2026-06-20')).toBe(14);
  });

  it('refuses today, a reversed or an overlong window and a missing day', () => {
    const f = emptyForm(TODAY);
    expect(windowIssues(f, TODAY)).toEqual([]);
    expect(windowIssues({ ...f, end: TODAY }, TODAY)[0]?.key).toBe(
      'api.error.backtest_window_not_past',
    );
    expect(windowIssues({ ...f, start: '2026-06-20', end: '2026-06-10' }, TODAY)[0]?.key).toBe(
      'api.validation.backtest_window_order',
    );
    expect(windowIssues({ ...f, start: '2026-05-01', end: '2026-06-20' }, TODAY)[0]).toMatchObject({
      key: 'api.validation.backtest_window_too_long',
      args: { max: 30 },
    });
    expect(windowIssues({ ...f, start: '' }, TODAY)[0]?.key).toBe('whatif.err.window_missing');
  });
});

describe('buildRequest', () => {
  it('sends an empty overlay for an untouched form', () => {
    const { request, issues } = buildRequest(emptyForm(TODAY), TODAY, [gap]);
    expect(issues).toEqual([]);
    expect(request).toEqual({
      start: '2026-06-07',
      end: '2026-06-20',
      params: {},
      perfect_foresight: false,
    });
  });

  it('sends only edited parameters and converts kWh and kW', () => {
    const f = emptyForm(TODAY);
    f.params = { 'planner.mip_gap': '0.05' };
    f.capacityKwh = 12.5;
    f.maxChargeKw = 4;
    f.reservePct = 15;
    f.strategy = 'prefer_full';
    f.perfect = true;
    const { request } = buildRequest(f, TODAY, [gap]);
    expect(request).toMatchObject({
      params: { 'planner.mip_gap': 0.05 },
      perfect_foresight: true,
      battery: { strategy: 'prefer_full', capacity_wh: 12500, max_charge_w: 4000, reserve_pct: 15 },
    });
  });

  it('leaves a parameter alone that was typed back to its current value', () => {
    const f = emptyForm(TODAY);
    f.params = { 'planner.mip_gap': '0.01' };
    expect(buildRequest(f, TODAY, [gap]).request?.params).toEqual({});
  });

  it('needs a price for grid charging and for a flat tariff', () => {
    const f = emptyForm(TODAY);
    f.gridCharge = 'on';
    f.tariff = 'fixed';
    const { request, issues } = buildRequest(f, TODAY, []);
    expect(request).toBeNull();
    expect(issuesOf(issues, 'gridChargeMaxPrice')[0]?.key).toBe('whatif.err.required');
    expect(issuesOf(issues, 'importPrice')[0]?.key).toBe('whatif.err.required');
  });

  it('builds the grid-charge and tariff overlays', () => {
    const f = emptyForm(TODAY);
    f.gridCharge = 'on';
    f.gridChargeMaxPrice = 0.12;
    f.tariff = 'fixed';
    f.importPrice = 0.31;
    f.exportPrice = 0.08;
    const { request } = buildRequest(f, TODAY, []);
    expect(request?.battery?.grid_charge).toEqual({ enabled: true, max_price: 0.12 });
    expect(request?.tariff).toEqual({ kind: 'fixed', import_price: 0.31, export_price: 0.08 });
    f.gridCharge = 'off';
    expect(buildRequest(f, TODAY, []).request?.battery?.grid_charge).toEqual({ enabled: false });
  });

  it('checks the bounds of the battery fields and parameters', () => {
    const f = emptyForm(TODAY);
    f.reservePct = 120;
    f.capacityKwh = 0;
    f.params = { 'planner.mip_gap': '0.9' };
    const { issues } = buildRequest(f, TODAY, [gap]);
    expect(issuesOf(issues, 'reservePct')[0]?.key).toBe('whatif.err.range');
    expect(issuesOf(issues, 'capacityKwh')).toHaveLength(1);
    expect(issuesOf(issues, 'param:planner.mip_gap')[0]?.key).toBe(
      'api.validation.param_out_of_range',
    );
  });
});

describe('changesOf', () => {
  it('lists parameters with the value the run used, the battery, the tariff and the bound', () => {
    const changes = changesOf(
      {
        start: '2026-06-18',
        end: '2026-06-19',
        params: { 'planner.budget_s': 30 },
        battery: { capacity_wh: 8000, strategy: null, grid_charge: { enabled: false } },
        tariff: { kind: 'fixed', import_price: 0.3, export_price: 0.08 },
        perfect_foresight: true,
      },
      result({ params: { 'planner.budget_s': 10 } }),
    );
    expect(changes).toEqual([
      { kind: 'param', key: 'planner.budget_s', requested: 30, used: 10 },
      { kind: 'battery', field: 'capacity_wh', value: 8000 },
      { kind: 'battery', field: 'grid_charge', value: false },
      { kind: 'tariff', importPrice: 0.3, exportPrice: 0.08 },
      { kind: 'perfect' },
    ]);
  });
});

describe('result rows', () => {
  const totals = result().totals!;

  it('compares the figures and keeps perfect foresight empty when not replayed', () => {
    const rows = metricRows(totals);
    expect(rows.find((r) => r.id === 'cost_net')).toMatchObject({
      actual: 1,
      whatif: 0.6,
      perfect: null,
    });
    expect(rows.map((r) => r.kind)).toContain('ratio');
  });

  it('computes the saving and a verdict that ignores rounding noise', () => {
    expect(savingsVsActual(totals)).toBeCloseTo(0.4);
    expect(verdict(0.4)).toBe('cheaper');
    expect(verdict(-0.4)).toBe('dearer');
    expect(verdict(0.001)).toBe('same');
  });

  it('orders the references and adds perfect foresight when present', () => {
    expect(costLadder(totals).map((r) => r.id)).toEqual(['no_pv', 'pv_only', 'actual', 'whatif']);
    const withPerfect = { ...totals, perfect: figures({ cost_net: 0.2 }) };
    expect(costLadder(withPerfect).at(-1)).toEqual({ id: 'perfect', cost: 0.2 });
  });

  it('turns days into rows with the saving per day', () => {
    const rows = dayRows(result().days);
    expect(rows[0]).toMatchObject({ day: '2026-06-18', actual: 0.5, whatif: 0.3 });
    expect(rows[0]?.delta).toBeCloseTo(0.2);
  });

  it('exports the daily table as CSV with a stable name', () => {
    const csv = resultCsv(result());
    const [head, first] = csv.split('\n');
    expect(head).toContain('cost_whatif');
    expect(first?.startsWith('2026-06-18,96,0.5,0.3,,3,0.8')).toBe(true);
    expect(resultFilename(result(), 'csv')).toBe('suncore-whatif-2026-06-18_2026-06-19.csv');
  });
});
