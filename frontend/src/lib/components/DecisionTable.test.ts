// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Decision, DecisionExplain } from '../api/types';
import { applyLanguage } from '../i18n/index.svelte';
import type { ExplainState } from '../stores/decisionLog.svelte';
import DecisionTable from './DecisionTable.svelte';

const decision = (id: string, kind: Decision['kind'] = 'shadow'): Decision =>
  ({
    schema_version: 1,
    intent: {
      id,
      asset_id: 'ac',
      origin: 'planner',
      action: { kind: 'switch', on: true },
    },
    kind,
    ts: '2026-06-21T09:30:00Z',
    mode: 'shadow',
    reasons: [{ key: 'safety.shadow_mode', args: {} }],
  }) as unknown as Decision;

const explain = (): DecisionExplain =>
  ({
    id: 11,
    decision: decision('a'),
    before: [{ id: 9, decision: decision('b', 'accepted') }],
    after: [],
    plan: {
      snapshot_id: 1,
      created_at: '2026-06-21T09:15:00Z',
      source: 'solver',
      interval_index: 0,
      interval: {
        start: '2026-06-21T09:30:00Z',
        end: '2026-06-21T09:45:00Z',
        devices: { ac: { allowed: true, preference: 'prefer_on', max_power_w: 900 } },
        battery: null,
        reasons: [],
        expected: { pv_p50_w: 4000, load_w: 500, import_w: 0, export_w: 1200, soc_pct: 60 },
      },
    },
    inputs: [
      { key: 'site.pv_w', value: 3800, unit: 'W' },
      { key: 'site.soc_pct', value: null, unit: '%' },
      { key: 'device.ac.on', value: 0.5, unit: 'share' },
    ],
    inputs_at: '2026-06-21T09:30:00Z',
    inputs_resolution: '15m',
  }) as unknown as DecisionExplain;

const names = new Map([['ac', 'Living room AC']]);

function mount(open: number | null, explained: Record<number, ExplainState>, ontoggle = vi.fn()) {
  return render(DecisionTable, {
    props: {
      decisions: [decision('a'), decision('c')],
      ids: [11, 10],
      names,
      explained,
      open,
      cards: new Set(['ac']),
      ontoggle,
      onretry: vi.fn(),
    },
  });
}

beforeEach(() => applyLanguage('en'));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('DecisionTable', () => {
  it('renders a real table with column headers and rendered reasons', () => {
    mount(null, {});
    expect(screen.getAllByRole('columnheader').length).toBe(7);
    expect(screen.getAllByRole('row').length).toBe(3);
    expect(screen.getAllByText('Living room AC').length).toBe(2);
    expect(screen.getAllByText('Planner').length).toBe(2);
    expect(document.body.textContent).not.toContain('safety.shadow_mode');
  });

  it('gives each Why button its context and toggles by row id', async () => {
    const ontoggle = vi.fn();
    mount(null, {}, ontoggle);
    const [first] = screen.getAllByTestId('why-button');
    expect(first!.getAttribute('aria-expanded')).toBe('false');
    expect(first!.getAttribute('aria-label')).toMatch(/^Why\? Living room AC, /);
    await fireEvent.click(first!);
    expect(ontoggle).toHaveBeenCalledWith(11);
  });

  it('shows the explanation: plan band, expected flows, readings and neighbours', () => {
    mount(11, { 11: { status: 'ready', data: explain() } });
    const panel = screen.getByTestId('why-panel');
    expect(panel.getAttribute('role')).toBe('region');
    expect(screen.getAllByTestId('why-button')[0]!.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByTestId('why-band').textContent).toContain('Device allowed to run');
    expect(screen.getByTestId('why-band').textContent).toContain('prefers on');
    expect(panel.textContent).toContain('Plan from');
    expect(panel.textContent).toContain('Optimiser');
    expect(panel.textContent).toMatch(/PV 4\skW/);
    const inputs = screen.getByTestId('why-inputs').textContent ?? '';
    expect(inputs).toContain('PV generation');
    expect(inputs).toContain('No data');
    expect(inputs).toContain('Living room AC was on');
    expect(inputs).toContain('50');
    expect(panel.textContent).toContain('Earlier decisions for this device');
    expect(screen.getByTestId('why-plan-link').getAttribute('href')).toContain('#/plan?at=');
    expect(screen.getByTestId('why-device-link').getAttribute('href')).toBe('#/?device=ac');
  });

  it('says so when no plan or readings are stored any more', () => {
    const data = { ...explain(), plan: null, inputs: [], inputs_at: null, inputs_resolution: null };
    mount(11, { 11: { status: 'ready', data } });
    const text = screen.getByTestId('why-panel').textContent ?? '';
    expect(text).toContain('No plan from before this decision is stored any more.');
    expect(text).toContain('No readings are stored');
    expect(screen.queryByTestId('why-plan-link')).toBeNull();
  });

  it('shows a translated error with a retry', async () => {
    const onretry = vi.fn();
    render(DecisionTable, {
      props: {
        decisions: [decision('a')],
        ids: [11],
        names,
        explained: { 11: { status: 'error', key: 'api.error.decision_not_found' } },
        open: 11,
        cards: new Set<string>(),
        ontoggle: vi.fn(),
        onretry,
      },
    });
    expect(screen.getByRole('alert').textContent).toContain('no longer exists');
    await fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onretry).toHaveBeenCalledOnce();
    expect(screen.queryByTestId('why-device-link')).toBeNull();
  });

  it('closes on Escape and returns the focus to the button', async () => {
    const ontoggle = vi.fn();
    mount(11, { 11: { status: 'ready', data: explain() } }, ontoggle);
    await fireEvent.keyDown(screen.getByTestId('why-panel'), { key: 'Escape' });
    expect(ontoggle).toHaveBeenCalledWith(11);
  });

  it('renders in German', () => {
    applyLanguage('de');
    mount(11, { 11: { status: 'ready', data: explain() } });
    expect(screen.getAllByTestId('why-button')[0]!.textContent?.trim()).toBe('Warum?');
    expect(screen.getByTestId('why-panel').textContent).toContain('Der Plan zu diesem Zeitpunkt');
  });
});
