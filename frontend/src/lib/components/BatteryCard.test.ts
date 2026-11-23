// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, controlApi } from '../api/client';
import { batteryState, controlState } from '../control/fixtures';
import { applyLanguage } from '../i18n/index.svelte';
import { control } from '../stores/control.svelte';
import BatteryCard from './BatteryCard.svelte';

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

describe('BatteryCard', () => {
  it('shows live values, the limits of the adapter and who is in charge', () => {
    render(BatteryCard, { props: { battery: batteryState(), socPct: 83, powerW: -303 } });
    expect(screen.getByRole('heading', { name: 'Battery' })).toBeTruthy();
    expect(screen.getByTestId('battery-soc').textContent?.trim().replace(/\s/g, ' ')).toBe('83 %');
    expect(screen.getByTestId('battery-control').textContent).toMatch(/own settings/);
    const limits = (screen.getByTestId('battery-limits').textContent ?? '').replace(/\s/g, ' ');
    expect(limits).toMatch(/Minimum state of charge: 10 %/);
    expect(limits).toMatch(/Charge power limit: 5 kW/);
    expect(screen.queryByTestId('battery-cap-note')).toBeNull();
  });

  it('shows the held limits, the pause reason and the last decision', async () => {
    render(BatteryCard, {
      props: {
        battery: batteryState({
          under_control: true,
          held: { kind: 'battery', min_soc_pct: 60, max_discharge_w: 0 },
          paused: { key: 'safety.battery_soc_unknown', args: {} },
          battery_mode: 'automatic',
          effective_mode: 'automatic',
          last_decision: {
            schema_version: 2,
            kind: 'accepted',
            ts: '2026-06-21T09:30:00+02:00',
            reasons: [{ key: 'safety.battery_write_no_change', args: {} }],
            mode: 'automatic',
            intent: {},
          } as never,
        }),
      },
    });
    const held = (screen.getByTestId('battery-held').textContent ?? '').replace(/\s/g, ' ');
    expect(held).toMatch(/Minimum state of charge: 60 %/);
    expect(held).toMatch(/Discharge power limit: 0 W/);
    expect(screen.getByTestId('battery-paused').textContent).toMatch(/state of charge is unknown/);
    expect(screen.queryByTestId('battery-limits')).toBeNull();
    await fireEvent.click(screen.getByText('Why?'));
    expect(screen.getByTestId('battery-last').textContent).toMatch(/Executed/);
  });

  it('explains the assisted cap of an adapter with an unmeasured hardware fallback', () => {
    render(BatteryCard, {
      props: {
        battery: batteryState({
          adapter_template_id: 'kostal.plenticore_g3_modbus',
          capabilities: ['min_soc', 'hardware_fallback'],
          mode_capped: true,
          battery_mode: 'automatic',
          effective_mode: 'assisted',
        }),
      },
    });
    expect(screen.getByTestId('battery-cap-note').textContent).toMatch(/fallback timer/);
    expect(screen.getByTestId('battery-effective-mode').textContent).toBe('Assisted mode');
    expect(screen.getByTestId('battery-mode-limited')).toBeTruthy();
  });

  it('asks before assisted and automatic and sends the identity the user saw', async () => {
    const put = vi.spyOn(controlApi, 'setBatteryMode').mockResolvedValue({} as never);
    render(BatteryCard, { props: { battery: batteryState({ identity: 'bat-7' }) } });
    await fireEvent.click(modeButton(/Assisted mode/));
    const dialog = screen.getByTestId('battery-dialog');
    expect(within(dialog).getByText(/proposes battery limits/)).toBeTruthy();
    expect(put).not.toHaveBeenCalled();
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Switch to assisted' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('assisted', 'bat-7'));
  });

  it('writes nothing when the confirmation is cancelled and needs no token for shadow', async () => {
    const put = vi.spyOn(controlApi, 'setBatteryMode').mockResolvedValue({} as never);
    render(BatteryCard, {
      props: { battery: batteryState({ battery_mode: 'automatic', effective_mode: 'automatic' }) },
    });
    await fireEvent.click(modeButton(/Automatic mode/)); // already pressed: nothing
    expect(put).not.toHaveBeenCalled();
    await fireEvent.click(modeButton(/Assisted mode/));
    await fireEvent.click(
      within(screen.getByTestId('battery-dialog')).getByRole('button', { name: 'Cancel' }),
    );
    expect(put).not.toHaveBeenCalled();
    await fireEvent.click(modeButton(/Shadow mode/));
    await waitFor(() => expect(put).toHaveBeenCalledWith('shadow', null));
  });

  it('shows a re-mapped battery (409) with its catalogue key', async () => {
    vi.spyOn(controlApi, 'setBatteryMode').mockRejectedValue(
      new ApiError(409, 'api.error.device_identity_changed'),
    );
    render(BatteryCard, { props: { battery: batteryState() } });
    await fireEvent.click(modeButton(/Assisted mode/));
    await fireEvent.click(
      within(screen.getByTestId('battery-dialog')).getByRole('button', {
        name: 'Switch to assisted',
      }),
    );
    await waitFor(() => expect(screen.getByTestId('battery-error')).toBeTruthy());
  });
});
