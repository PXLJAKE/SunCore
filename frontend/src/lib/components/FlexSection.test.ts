// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, controlApi, flexApi } from '../api/client';
import type { DeviceConfig, FlexView, ProgramJob } from '../api/types';
import { controlState, device } from '../control/fixtures';
import { applyLanguage } from '../i18n/index.svelte';
import { control } from '../stores/control.svelte';
import { flex } from '../stores/flex.svelte';
import { live } from '../stores/live.svelte';
import DeviceCard from './DeviceCard.svelte';

const NOW = new Date(2026, 5, 21, 9, 30);

const WALLBOX = {
  id: 'wb',
  kind: 'wallbox',
  control: { kind: 'charger' },
  ev: { default_mode: 'pv', efficiency: 0.9 },
} as unknown as DeviceConfig;
const washer = (start_method: string) =>
  ({
    id: 'wm',
    kind: 'program',
    control: { kind: 'on_off', role: 'plug' },
    program: { start_method, runtime_min: 120, idle_w: 5, profile_wh: [] },
  }) as unknown as DeviceConfig;
const HEAT_PUMP = {
  id: 'hp',
  kind: 'heat_pump',
  control: { kind: 'sg_ready' },
  thermal: { grid_preheat: false, cop: { assumed: false, points: [{ outdoor_c: 7, cop: 3.4 }] } },
} as unknown as DeviceConfig;

const job = (over: Partial<ProgramJob> = {}): ProgramJob => ({
  id: 'wm-1',
  device_id: 'wm',
  state: 'scheduled',
  deadline: new Date(2026, 5, 21, 18, 0).toISOString(),
  armed_at: NOW.toISOString(),
  armed_by: 'user',
  planned_start: new Date(2026, 5, 21, 13, 0).toISOString(),
  ...over,
});
const view = (id: string, over: Partial<FlexView> = {}): FlexView => ({
  device_id: id,
  ev: null,
  job: null,
  ...over,
});

beforeEach(() => {
  applyLanguage('en');
  flex.views = {};
  flex.errors = {};
  flex.busy = {};
  control.errors = {};
  control.busy = {};
  vi.spyOn(controlApi, 'state').mockResolvedValue(controlState());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('EV section', () => {
  const wb = (state = {}) =>
    device({
      device_id: 'wb',
      name: 'Wallbox',
      kind: 'wallbox',
      state: { quality: 'ok', connected: true, phases: 3, soc_pct: 42, ...state },
    });

  it('shows plug, phases and vehicle SoC and the stored mode', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(
      view('wb', { ev: { device_id: 'wb', mode: 'min_pv', updated_at: NOW.toISOString() } }),
    );
    render(DeviceCard, { props: { device: wb(), config: WALLBOX, now: NOW } });
    expect(screen.getByTestId('ev-plug').textContent).toBe('Plugged in');
    expect(screen.getByTestId('ev-phases').textContent?.trim()).toBe('3 phases');
    expect(screen.getByTestId('ev-soc').textContent?.replace(/\s/g, '')).toContain('42');
    await waitFor(() =>
      expect(screen.getByTestId('ev-mode-min_pv').getAttribute('aria-pressed')).toBe('true'),
    );
  });

  it('sets a mode at once', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wb'));
    const put = vi.spyOn(flexApi, 'setEv').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: wb(), config: WALLBOX, now: NOW } });
    await fireEvent.click(screen.getByTestId('ev-mode-now'));
    await waitFor(() => expect(put).toHaveBeenCalledWith('wb', 'now', null));
  });

  it('asks for a target before it sets the target mode', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wb'));
    const put = vi.spyOn(flexApi, 'setEv').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: wb(), config: WALLBOX, now: NOW } });
    await fireEvent.click(screen.getByTestId('ev-mode-target'));
    expect(put).not.toHaveBeenCalled();
    await fireEvent.input(screen.getByTestId('ev-target-value'), { target: { value: '70' } });
    await fireEvent.input(screen.getByTestId('ev-target-by'), {
      target: { value: '2026-06-22T07:00' },
    });
    await fireEvent.click(screen.getByTestId('ev-target-apply'));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith('wb', 'target', {
        soc_pct: 70,
        energy_wh: null,
        by: new Date(2026, 5, 22, 7, 0).toISOString(),
      }),
    );
  });

  it('rejects a target in the past without calling the server', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wb'));
    const put = vi.spyOn(flexApi, 'setEv').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: wb(), config: WALLBOX, now: NOW } });
    await fireEvent.click(screen.getByTestId('ev-mode-target'));
    await fireEvent.input(screen.getByTestId('ev-target-by'), {
      target: { value: '2026-06-20T07:00' },
    });
    await fireEvent.click(screen.getByTestId('ev-target-apply'));
    expect(screen.getByRole('alert').textContent).toContain('future');
    expect(put).not.toHaveBeenCalled();
  });
});

describe('EV target and the server clock', () => {
  const wb = () =>
    device({ device_id: 'wb', name: 'Wallbox', kind: 'wallbox', state: { quality: 'ok' } });

  it('defaults the target time from the server clock, not the browser clock', async () => {
    live.control = controlState({ ts: '2031-03-10T09:30:00+01:00' });
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wb'));
    const put = vi.spyOn(flexApi, 'setEv').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: wb(), config: WALLBOX } });
    await fireEvent.click(screen.getByTestId('ev-mode-target'));
    expect((screen.getByTestId('ev-target-by') as HTMLInputElement).value).toBe('2031-03-11T07:00');
    await fireEvent.click(screen.getByTestId('ev-target-apply'));
    await waitFor(() => expect(put).toHaveBeenCalled());
    live.control = null;
  });

  it('shows the reason when the server refuses the target', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wb'));
    vi.spyOn(flexApi, 'setEv').mockRejectedValue(new ApiError(422, 'api.error.ev_target_far'));
    render(DeviceCard, { props: { device: wb(), config: WALLBOX, now: NOW } });
    await fireEvent.click(screen.getByTestId('ev-mode-target'));
    await fireEvent.click(screen.getByTestId('ev-target-apply'));
    const err = await screen.findByTestId('flex-error');
    expect(err.textContent).toBeTruthy();
    expect(err.textContent).not.toBe('api.error.ev_target_far');
  });
});

describe('heat pump section', () => {
  it('shows the SG-Ready state and the COP range', () => {
    render(DeviceCard, {
      props: {
        device: device({
          device_id: 'hp',
          name: 'Heat pump',
          kind: 'heat_pump',
          state: { quality: 'ok', step: 3 },
        }),
        config: HEAT_PUMP,
        now: NOW,
      },
    });
    expect(screen.getByTestId('hp-sg').textContent?.trim()).toBe('3: raised');
    expect(screen.getByTestId('hp-cop').textContent).toContain('3.4');
    expect(screen.getByTestId('hp-model').textContent).toBe('Not learned yet');
  });
});

describe('program section', () => {
  const dev = () => device({ device_id: 'wm', name: 'Washer', kind: 'program' });

  it('arms a job with the chosen deadline', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wm'));
    const arm = vi.spyOn(flexApi, 'armProgram').mockResolvedValue(job());
    render(DeviceCard, { props: { device: dev(), config: washer('plug'), now: NOW } });
    await fireEvent.input(screen.getByTestId('program-deadline-input'), {
      target: { value: '2026-06-21T18:00' },
    });
    await fireEvent.click(screen.getByTestId('program-arm'));
    await waitFor(() =>
      expect(arm).toHaveBeenCalledWith('wm', new Date(2026, 5, 21, 18, 0).toISOString()),
    );
  });

  it('shows the job and its planned start', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wm', { job: job() }));
    render(DeviceCard, { props: { device: dev(), config: washer('plug'), now: NOW } });
    await waitFor(() =>
      expect(screen.getByTestId('program-job-state').textContent).toBe('Scheduled'),
    );
    expect(screen.getByTestId('program-planned').textContent).not.toContain('Not planned');
  });

  it('warns before cancelling a plug program: the plug switches ON', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wm', { job: job() }));
    const cancel = vi.spyOn(flexApi, 'cancelProgram').mockResolvedValue(job());
    render(DeviceCard, { props: { device: dev(), config: washer('plug'), now: NOW } });
    await fireEvent.click(await screen.findByTestId('program-cancel'));
    const dialog = screen.getByTestId('program-cancel-dialog-wm');
    expect(within(dialog).getByTestId('program-cancel-warning').textContent).toContain(
      'starts immediately',
    );
    expect(cancel).not.toHaveBeenCalled();
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel job' }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('wm'));
  });

  it('gives no plug warning for a notify-only program', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wm', { job: job() }));
    render(DeviceCard, { props: { device: dev(), config: washer('notify'), now: NOW } });
    await fireEvent.click(await screen.findByTestId('program-cancel'));
    expect(screen.queryByTestId('program-cancel-warning')).toBeNull();
  });

  it('warns before "back to normal" of a held plug program', async () => {
    vi.spyOn(flexApi, 'flex').mockResolvedValue(view('wm'));
    const override = vi.spyOn(controlApi, 'override').mockResolvedValue({} as never);
    render(DeviceCard, {
      props: { device: { ...dev(), held: true }, config: washer('plug'), now: NOW },
    });
    await fireEvent.click(screen.getByTestId('resume-now'));
    const dialog = screen.getByTestId('resume-dialog-wm');
    expect(within(dialog).getByTestId('resume-warning').textContent).toContain('ON');
    expect(override).not.toHaveBeenCalled();
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Back to normal' }));
    await waitFor(() => expect(override).toHaveBeenCalledWith('wm', { action: 'resume' }));
  });
});
