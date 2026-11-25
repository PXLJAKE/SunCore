// SPDX-License-Identifier: Apache-2.0
// Test fixtures of the what-if view: a finished two-day run.
import type { BacktestFigures, BacktestResult, BacktestRun } from '../api/types';

export function figures(over: Partial<BacktestFigures> = {}): BacktestFigures {
  return {
    pv_wh: 20000,
    house_wh: 12000,
    import_wh: 3000,
    export_wh: 9000,
    charge_wh: 4000,
    discharge_wh: 3500,
    curtailed_wh: 0,
    import_cost: 1,
    export_revenue: 0.5,
    cost_net: 0.5,
    autarky: 0.8,
    self_consumption: 0.55,
    equivalent_full_cycles: 0.4,
    ...over,
  };
}

export function result(over: Partial<BacktestResult> = {}): BacktestResult {
  const actual = figures();
  const whatif = figures({ cost_net: 0.3, import_wh: 2000 });
  return {
    schema_version: 1,
    label: 'plan_with_perfect_execution',
    start: '2026-06-18',
    end: '2026-06-19',
    tz: 'Europe/Berlin',
    currency: 'EUR',
    tariff: 'stored',
    params: { 'planner.mip_gap': 0.02 },
    params_requested: { 'planner.mip_gap': 0.02 },
    totals: {
      days: 2,
      actual: figures({ cost_net: 1 }),
      whatif: figures({ cost_net: 0.6 }),
      perfect: null,
      baseline_no_pv_cost: 6,
      baseline_pv_only_cost: 1.5,
    },
    days: [
      {
        day: '2026-06-18',
        slots: 96,
        priced_slots: 96,
        actual,
        whatif,
        perfect: null,
        baseline_no_pv_cost: 3,
        baseline_pv_only_cost: 0.8,
      },
      {
        day: '2026-06-19',
        slots: 96,
        priced_slots: 96,
        actual,
        whatif,
        perfect: null,
        baseline_no_pv_cost: 3,
        baseline_pv_only_cost: 0.7,
      },
    ],
    skipped: [],
    hourly: {
      starts: ['2026-06-17T22:00:00Z', '2026-06-17T23:00:00Z'],
      actual_soc_pct: [50, 55],
      whatif_soc_pct: [50, 60],
      perfect_soc_pct: null,
      actual_grid_w: [100, null],
      whatif_grid_w: [80, 0],
      actual_battery_w: [0, 500],
      whatif_battery_w: [0, 900],
    },
    limitations: [{ key: 'backtest.limit.perfect_execution', args: {} }],
    stats: { replans: 48, fallbacks: 0, solve_s_total: 1.2, solve_s_max: 0.1 },
    ...over,
  };
}

export function run(over: Partial<BacktestRun> = {}): BacktestRun {
  return {
    id: 1,
    status: 'done',
    created_at: '2026-06-21T08:00:00Z',
    finished_at: '2026-06-21T08:00:09Z',
    start: '2026-06-18',
    end: '2026-06-19',
    request: {
      start: '2026-06-18',
      end: '2026-06-19',
      params: { 'planner.mip_gap': 0.02 },
      perfect_foresight: false,
    },
    progress: null,
    result: result(),
    error: null,
    ...over,
  };
}
