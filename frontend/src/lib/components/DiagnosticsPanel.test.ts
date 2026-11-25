// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api/client';
import type { Diagnostics } from '../api/types';
import { applyLanguage } from '../i18n/index.svelte';
import { exportFileName, exportText, sectionCount, sectionText } from '../diagnostics/present';
import DiagnosticsPanel from './DiagnosticsPanel.svelte';

const doc = (over: Partial<Diagnostics> = {}): Diagnostics =>
  ({
    schema_version: 1,
    generated_at: '2026-11-25T08:00:00+00:00',
    pseudonyms: 'salted',
    unavailable: [],
    versions: {
      app: '0.7.0',
      ha_core: '2026.11.0',
      python: '3.13',
      schemas: {},
      config_sections: {},
    },
    architecture: {
      demo: true,
      machine: 'x86_64',
      system: 'Linux',
      supervised: false,
      cpu_count: 4,
      operating_mode: 'shadow',
    },
    connection: {
      db_ok: true,
      ha_state: 'connected',
      last_update_age_s: 2,
      mqtt_state: null,
      mqtt_reason_key: null,
      registry: null,
    },
    watchdog: { connected: true, control_active: true, emergency_stop: false, scopes: [] },
    planner: { run: null, solver: null },
    timeseries: { state: 'ok', code_schema_version: 3, file_schema_version: 3, size: null },
    configuration: { site: { grid: { entity_id: 'sensor.e_1a2b3c4d' } } },
    mapped_entities: [],
    decisions: [{ kind: 'shadow' }, { kind: 'accepted' }],
    logs: [],
    ...over,
  }) as unknown as Diagnostics;

beforeEach(() => {
  applyLanguage('en');
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('diagnostics presenter', () => {
  it('counts list sections only and indents the text', () => {
    const d = doc();
    expect(sectionCount(d, 'decisions')).toBe(2);
    expect(sectionCount(d, 'logs')).toBe(0);
    expect(sectionCount(d, 'planner')).toBeNull();
    expect(sectionText(d, 'planner')).toContain('\n  "run": null');
    expect(exportText(d).endsWith('}\n')).toBe(true);
    expect(exportFileName(d)).toBe('suncore-diagnostics-2026-11-25.json');
  });
});

describe('DiagnosticsPanel', () => {
  it('says the export is pseudonymised and asks to read it first, without fetching yet', () => {
    const spy = vi.spyOn(api, 'diagnostics').mockResolvedValue(doc());
    render(DiagnosticsPanel);
    const note = screen.getByTestId('diag-privacy').textContent ?? '';
    expect(note).toContain('pseudonymised');
    expect(note).toContain('Read it before you post it publicly');
    expect(spy).not.toHaveBeenCalled();
  });

  it('loads the preview when opened and shows a section on demand', async () => {
    const spy = vi.spyOn(api, 'diagnostics').mockResolvedValue(doc());
    render(DiagnosticsPanel);
    const details = screen.getByTestId('diag-preview') as HTMLDetailsElement;
    details.open = true;
    await fireEvent(details, new Event('toggle'));
    await waitFor(() => expect(screen.getByTestId('diag-facts')).toBeTruthy());
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('diag-facts').textContent).toContain('0.7.0');
    // Closed sections render no JSON; the preview stays light.
    expect(screen.queryByLabelText('Configuration')).toBeNull();
    const section = screen.getByTestId('diag-section-decisions');
    expect(section.textContent).toContain('Latest decisions (2 entries)');
    const inner = section.querySelector('details') as HTMLDetailsElement;
    inner.open = true;
    await fireEvent(inner, new Event('toggle'));
    await waitFor(() => expect(section.querySelector('pre')).toBeTruthy());
    expect(section.querySelector('pre')?.getAttribute('tabindex')).toBe('0');
  });

  it('downloads a JSON file named after the export day', async () => {
    vi.spyOn(api, 'diagnostics').mockResolvedValue(doc());
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(DiagnosticsPanel);
    await fireEvent.click(screen.getByTestId('diag-download'));
    await waitFor(() => expect(screen.getByTestId('diag-saved')).toBeTruthy());
    expect(screen.getByTestId('diag-saved').textContent).toContain(
      'suncore-diagnostics-2026-11-25.json',
    );
    expect(click).toHaveBeenCalledTimes(1);
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0]?.[0] as Blob;
    expect(blob.type).toBe('application/json');
    expect(JSON.parse(await blob.text()).versions.app).toBe('0.7.0');
  });

  it('reports an error without a file', async () => {
    vi.spyOn(api, 'diagnostics').mockRejectedValue(new ApiError(500, null));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(DiagnosticsPanel);
    await fireEvent.click(screen.getByTestId('diag-download'));
    await waitFor(() => expect(screen.getByTestId('diag-error')).toBeTruthy());
    expect(click).not.toHaveBeenCalled();
  });

  it('lists what the export could not read', async () => {
    vi.spyOn(api, 'diagnostics').mockResolvedValue(doc({ unavailable: ['planner.solver'] }));
    render(DiagnosticsPanel);
    const details = screen.getByTestId('diag-preview') as HTMLDetailsElement;
    details.open = true;
    await fireEvent(details, new Event('toggle'));
    await waitFor(() =>
      expect(screen.getByTestId('diag-unavailable').textContent).toContain('planner.solver'),
    );
  });
});
