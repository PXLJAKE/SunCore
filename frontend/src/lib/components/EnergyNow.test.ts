// SPDX-License-Identifier: Apache-2.0
import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Flows, SiteControlState } from '../api/types';
import { device } from '../control/fixtures';
import { applyLanguage } from '../i18n/index.svelte';
import EnergyNow from './EnergyNow.svelte';

beforeEach(() => applyLanguage('en'));
afterEach(() => cleanup());

const q = (value: number | null, quality = 'ok') => ({ value, quality }) as Flows['pv_w'];
const flows = (over: Partial<Flows> = {}): Flows =>
  ({
    pv_w: q(4780),
    grid_w: q(-500),
    battery_w: q(-1200),
    house_w: q(1100),
    battery_soc_pct: q(91),
    ts: null,
    ...over,
  }) as Flows;
const site = (over: Partial<SiteControlState> = {}): SiteControlState =>
  ({
    free_w: 502,
    deficit_w: 0,
    allocated_draw_w: 750,
    quality: 'ok',
    ...over,
  }) as SiteControlState;

describe('EnergyNow', () => {
  it('lists every flow once with value, state and the battery charge', () => {
    render(EnergyNow, { props: { flows: flows(), hasBattery: true } });
    const list = screen.getByRole('list', { name: 'Current power values' });
    const items = within(list).getAllByRole('listitem');
    expect(items.map((i) => i.dataset.testid)).toEqual([
      'flow-pv',
      'flow-grid',
      'flow-house',
      'flow-battery',
    ]);
    const norm = (id: string) => screen.getByTestId(id).textContent?.replace(/\s+/g, ' ').trim();
    expect(norm('flow-pv')).toBe('Solar 4.78 kW Producing');
    expect(norm('flow-grid')).toBe('Grid 500 W Exporting');
    expect(norm('flow-battery')).toMatch(/^Battery 1.2 kW Discharging Charge 91 %$/);
    // Lines are decoration only.
    const figure = screen.getByRole('figure', { name: 'Energy flow diagram' });
    for (const edge of figure.querySelectorAll('.edge')) {
      expect(edge.getAttribute('aria-hidden')).toBe('true');
    }
    expect(figure.querySelector('.e-grid')?.classList.contains('reverse')).toBe(true);
    expect(figure.querySelector('.e-pv')?.classList.contains('forward')).toBe(true);
  });

  it('marks stale and unusable values and leaves the battery out when there is none', () => {
    render(EnergyNow, {
      props: {
        flows: flows({ pv_w: q(1500, 'stale'), grid_w: q(null, 'unavailable') }),
        hasBattery: false,
      },
    });
    expect(screen.getByTestId('flow-pv').className).toContain('stale');
    expect(screen.getByTestId('flow-pv').textContent).toContain('Outdated');
    expect(screen.getByTestId('flow-grid').className).toContain('bad');
    expect(screen.getByTestId('flow-grid').textContent).toContain('Unavailable');
    expect(screen.queryByTestId('flow-battery')).toBeNull();
    expect(screen.queryByTestId('site-summary')).toBeNull();
  });

  it('shows the surplus figures and keeps the explanation behind a disclosure', () => {
    // A device in shadow mode at a planned level counts as "would run", not as running.
    const rod = device({
      committed_w: 3000,
      estimate: { value_w: 5, uncertainty_w: 10, quality: 'ok', age_s: 1 },
    });
    render(EnergyNow, {
      props: { flows: flows(), hasBattery: true, site: site(), devices: [rod] },
    });
    expect(screen.getByTestId('site-free').textContent?.trim().replace(/\s/g, ' ')).toBe('502 W');
    expect(screen.getByTestId('site-would-run').textContent?.replace(/\s/g, '')).toBe('3kW');
    const explain = screen.getByTestId('site-explain') as HTMLDetailsElement;
    expect(explain.open).toBe(false);
    expect(explain.textContent).toContain('Nothing is switched');
  });

  it('flags a deficit', () => {
    render(EnergyNow, {
      props: { flows: flows(), hasBattery: true, site: site({ deficit_w: 400 }) },
    });
    expect(screen.getByTestId('site-deficit').className).toContain('bad');
    expect(screen.queryByTestId('site-explain')).toBeNull();
  });
});
