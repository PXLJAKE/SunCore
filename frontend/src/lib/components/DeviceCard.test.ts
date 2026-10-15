// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, controlApi } from '../api/client';
import { controlState, device } from '../control/fixtures';
import { applyLanguage } from '../i18n/index.svelte';
import { control } from '../stores/control.svelte';
import DeviceCard from './DeviceCard.svelte';

beforeEach(() => {
  applyLanguage('en');
  control.errors = {};
  control.busy = {};
  vi.spyOn(controlApi, 'state').mockResolvedValue(controlState());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const modeButton = (name: RegExp) => screen.getByRole('button', { name });

describe('DeviceCard', () => {
  it('shows state, power with uncertainty and the effective mode', () => {
    render(DeviceCard, { props: { device: device() } });
    expect(screen.getByRole('heading', { name: 'Living room AC' })).toBeTruthy();
    expect(screen.getByTestId('state').textContent).toBe('Set-point 24 °C (your setting: 25 °C)');
    expect(screen.getByTestId('power').textContent?.trim().replace(/\s/g, ' ')).toBe(
      '≈ 850 W ± 300 W',
    );
    expect(screen.getByTestId('effective-mode').textContent).toBe('Shadow mode');
  });

  it('marks the pressed mode and applies assisted at once with the identity', async () => {
    const put = vi.spyOn(controlApi, 'setDeviceMode').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: device() } });
    expect(modeButton(/Shadow mode/).getAttribute('aria-pressed')).toBe('true');
    await fireEvent.click(modeButton(/Assisted mode/));
    await waitFor(() => expect(put).toHaveBeenCalledWith('ac', 'assisted', 'tok-1'));
  });

  it('asks before switching to automatic and only then writes', async () => {
    const put = vi.spyOn(controlApi, 'setDeviceMode').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: device() } });
    await fireEvent.click(modeButton(/Automatic mode/));
    const dialog = screen.getByTestId('auto-dialog-ac');
    expect(within(dialog).getByText(/really switches Living room AC/)).toBeTruthy();
    expect(within(dialog).getByText(/overall mode is currently: Automatic mode/)).toBeTruthy();
    expect(put).not.toHaveBeenCalled();
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Switch to automatic' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('ac', 'automatic', 'tok-1'));
  });

  it('writes nothing when the confirmation is cancelled', async () => {
    const put = vi.spyOn(controlApi, 'setDeviceMode').mockResolvedValue({} as never);
    render(DeviceCard, { props: { device: device() } });
    await fireEvent.click(modeButton(/Automatic mode/));
    await fireEvent.click(
      within(screen.getByTestId('auto-dialog-ac')).getByRole('button', { name: 'Cancel' }),
    );
    expect(put).not.toHaveBeenCalled();
  });

  it('sends the token that was shown when the dialog opened, and reports a 409', async () => {
    const put = vi
      .spyOn(controlApi, 'setDeviceMode')
      .mockRejectedValue(new ApiError(409, 'api.error.device_identity_changed'));
    const { rerender } = render(DeviceCard, { props: { device: device() } });
    await fireEvent.click(modeButton(/Automatic mode/));
    // The device is re-mapped while the dialog is open: the live feed delivers a new token.
    await rerender({ device: device({ identity: 'tok-2' }) });
    await fireEvent.click(
      within(screen.getByTestId('auto-dialog-ac')).getByRole('button', {
        name: 'Switch to automatic',
      }),
    );
    await waitFor(() => expect(put).toHaveBeenCalledWith('ac', 'automatic', 'tok-1'));
    const alert = await screen.findByTestId('card-error');
    expect(alert.textContent).toContain('This device changed in the meantime');
  });

  it('explains when the overall mode limits the device', () => {
    render(DeviceCard, {
      props: {
        device: device({
          device_mode: 'automatic',
          global_mode: 'shadow',
          effective_mode: 'shadow',
        }),
      },
    });
    expect(screen.getByTestId('mode-limited').textContent).toContain(
      'Running in Shadow mode because the overall mode is Shadow mode',
    );
  });

  it('shows a hold with its end and resumes on request', async () => {
    const override = vi.spyOn(controlApi, 'override').mockResolvedValue(device());
    const until = new Date(2026, 5, 22, 4, 0).toISOString();
    render(DeviceCard, {
      props: {
        device: device({ held: true, held_until: until }),
        now: new Date(2026, 5, 21, 20, 0),
      },
    });
    expect(screen.getByTestId('held-badge').textContent).toMatch(/Held until tomorrow, 0?4:00/);
    await fireEvent.click(screen.getByTestId('resume-now'));
    await waitFor(() => expect(override).toHaveBeenCalledWith('ac', { action: 'resume' }));
  });

  it('shows release pending and the pause reason', () => {
    render(DeviceCard, {
      props: {
        device: device({
          release_pending: true,
          paused: { key: 'safety.shadow_mode', args: {} },
        }),
      },
    });
    expect(screen.getByTestId('release-pending')).toBeTruthy();
    expect(screen.getByTestId('paused').textContent).toContain('Paused:');
  });

  it('renders the reasons and the last decision under "Why?"', () => {
    render(DeviceCard, {
      props: {
        device: device({
          reasons: [{ key: 'controller.surplus_on', args: { need_w: 750, surplus_w: 3800 } }],
          last_decision: {
            schema_version: 2,
            intent: {
              id: 'i1',
              ts: '2026-06-21T09:30:00+02:00',
              asset_id: 'ac',
              action: { kind: 'switch', on: true },
              reasons: [],
              origin: 'controller',
            },
            kind: 'shadow',
            ts: '2026-06-21T09:30:00+02:00',
            reasons: [{ key: 'safety.shadow_mode', args: {} }],
            mode: 'shadow',
          },
        }),
      },
    });
    const why = screen.getByTestId('why');
    expect(why.textContent).toContain('Last decision:');
    expect(why.textContent).toContain('Would have switched');
    expect(why.querySelectorAll('li').length).toBe(2);
    expect(why.textContent).not.toContain('controller.surplus_on');
  });
});
