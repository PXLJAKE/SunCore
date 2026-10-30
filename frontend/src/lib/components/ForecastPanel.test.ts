// SPDX-License-Identifier: Apache-2.0
import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ForecastAccuracyView, LoadModelView } from '../api/types';
import { applyLanguage } from '../i18n/index.svelte';
import ForecastPanel from './ForecastPanel.svelte';

beforeEach(() => applyLanguage('en'));
afterEach(() => cleanup());

const text = (id: string): string =>
  (screen.getByTestId(id).textContent ?? '').replace(/\s+/g, ' ').trim();

function model(overrides: Partial<LoadModelView> = {}): LoadModelView {
  return {
    mode: 'shadow',
    in_use: 'profile',
    reason: { key: 'forecast.load_model.ml_shadow', args: { days: 12, min_days: 30 } },
    ml_ready: true,
    ml_days: 12,
    min_history_days: 30,
    profile_mae_w: 118.4,
    ml_mae_w: null,
    compared_n: 0,
    compared_at: null,
    ...overrides,
  };
}

function accuracy(load: LoadModelView | null): ForecastAccuracyView {
  const record = (source_id: string, mae_w: number) => ({
    source_id,
    horizon: '0_6h' as const,
    mae_w,
    bias_w: -4,
    n: 1344,
  });
  return {
    min_samples: 96,
    sources: [
      {
        source_id: 'load_profile',
        computed_at: null,
        window_days: 14,
        records: [record('load_profile', 118)],
      },
      {
        source_id: 'load_ml',
        computed_at: null,
        window_days: 14,
        records: [record('load_ml', 97)],
      },
    ],
    load_model: load,
  };
}

function show(load: LoadModelView | null) {
  return render(ForecastPanel, { props: { accuracy: accuracy(load), pv: null, openMeteo: false } });
}

describe('ForecastPanel: ML vs profile', () => {
  it('lists the ML model as its own accuracy row', () => {
    show(model());
    const table = screen.getByTestId('fc-accuracy');
    expect(within(table).getByText('Base load profile')).toBeTruthy();
    expect(within(table).getByText('Machine-learning load model')).toBeTruthy();
  });

  it('shadow: compares both errors, the plan keeps the profiles', () => {
    show(model());
    expect(text('fc-ml-mode')).toBe('Mode: comparison only');
    expect(text('fc-ml-in-use')).toBe('The plan uses the profiles.');
    expect(text('fc-ml-reason')).toMatch(/runs for comparison only \(12 of 30 days of history\)/);
    const compare = screen.getByTestId('fc-ml-compare');
    const profile = compare.querySelector('tr[data-model="profile"]')?.textContent ?? '';
    const ml = compare.querySelector('tr[data-model="ml"]')?.textContent ?? '';
    expect(profile).toMatch(/118/);
    expect(compare.textContent).toMatch(/none compared yet/);
    expect(ml).toMatch(/not scored yet/);
    expect(text('fc-ml-days')).toBe('12 of 30 days of history');
  });

  it('auto: says when the plan uses the ML model and why', () => {
    show(
      model({
        mode: 'auto',
        in_use: 'ml',
        reason: {
          key: 'forecast.load_model.ml_in_use',
          args: { ml_mae_w: 97, profile_mae_w: 118 },
        },
        ml_days: 45,
        ml_mae_w: 97.2,
        compared_n: 1344,
        compared_at: '2026-10-30T02:00:00Z',
      }),
    );
    expect(text('fc-ml-mode')).toBe('Mode: automatic');
    expect(text('fc-ml-in-use')).toBe('The plan uses the machine-learning model.');
    expect(text('fc-ml-reason')).toMatch(/from the machine-learning model.*97 W against 118 W/);
    expect(text('fc-ml-days')).toBe('45 of 30 days of history');
    expect(screen.getByTestId('fc-ml-compare').textContent).toMatch(/same 1,344 quarter hours/);
  });

  it('auto with too little history: explains the fallback to the profiles', () => {
    show(
      model({
        mode: 'auto',
        ml_ready: false,
        ml_days: 3,
        reason: { key: 'forecast.load_model.ml_collecting', args: { days: 3, min_days: 30 } },
      }),
    );
    expect(text('fc-ml-in-use')).toBe('The plan uses the profiles.');
    expect(text('fc-ml-reason')).toMatch(/has 3 of the 30 days of history it needs/);
    expect(text('fc-ml-days')).toBe(
      '3 of 30 days of history · Not enough history to forecast yet.',
    );
  });

  it('off: no comparison', () => {
    show(model({ mode: 'off', reason: { key: 'forecast.load_model.ml_off', args: {} } }));
    expect(text('fc-ml-mode')).toBe('Mode: off');
    expect(screen.queryByTestId('fc-ml-compare')).toBeNull();
    expect(text('fc-ml-reason')).toMatch(/is off/);
  });

  it('without a load forecast only the explanation is shown', () => {
    show(null);
    expect(text('fc-ml')).toMatch(/similar past days/);
    expect(screen.queryByTestId('fc-ml-mode')).toBeNull();
  });

  it('is translated', () => {
    applyLanguage('de');
    show(
      model({
        mode: 'auto',
        reason: {
          key: 'forecast.load_model.ml_not_better',
          args: { ml_mae_w: 120, profile_mae_w: 118 },
        },
      }),
    );
    expect(text('fc-ml-in-use')).toBe('Der Plan nutzt die Profile.');
    expect(text('fc-ml-reason')).toMatch(/nicht deutlich besser/);
  });
});
