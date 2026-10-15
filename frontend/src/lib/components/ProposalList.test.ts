// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { controlState, proposal } from '../control/fixtures';
import { applyLanguage } from '../i18n/index.svelte';
import { control } from '../stores/control.svelte';
import ProposalList from './ProposalList.svelte';

const names = new Map([['ac', 'Living room AC']]);

beforeEach(() => {
  applyLanguage('en');
  control.errors = {};
  control.busy = {};
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
  vi.setSystemTime(new Date('2026-06-21T09:30:00Z'));
  vi.spyOn(api, 'controlState').mockResolvedValue(controlState());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('ProposalList', () => {
  it('counts down to the expiry and disables Confirm at zero', async () => {
    render(ProposalList, {
      props: { proposals: [proposal('p1', '2026-06-21T09:30:03Z')], names },
    });
    expect(screen.getByTestId('countdown').textContent?.trim()).toBe('Valid for 0:03');
    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByTestId('countdown').textContent?.trim()).toBe('Valid for 0:01');
    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByTestId('countdown').textContent?.trim()).toBe('Expired');
    expect((screen.getByTestId('proposal-confirm') as HTMLButtonElement).disabled).toBe(true);
  });

  it('names the device and the action', () => {
    render(ProposalList, { props: { proposals: [proposal('p1', '2026-06-21T09:32:00Z')], names } });
    expect(screen.getByText('Living room AC')).toBeTruthy();
    expect(screen.getByText('Switch on')).toBeTruthy();
    expect(screen.getByText('Valid for 2:00')).toBeTruthy();
  });

  it('confirms and dismisses through the API', async () => {
    const confirm = vi.spyOn(api, 'confirmProposal').mockResolvedValue({} as never);
    const dismiss = vi.spyOn(api, 'dismissProposal').mockResolvedValue(undefined);
    render(ProposalList, {
      props: {
        proposals: [proposal('p1', '2026-06-21T09:32:00Z'), proposal('p2', '2026-06-21T09:32:00Z')],
        names,
      },
    });
    await fireEvent.click(screen.getAllByTestId('proposal-confirm')[0]!);
    await fireEvent.click(screen.getAllByTestId('proposal-dismiss')[1]!);
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith('p2'));
  });

  it('renders nothing without proposals and starts no timer', () => {
    const spy = vi.spyOn(globalThis, 'setInterval');
    render(ProposalList, { props: { proposals: [], names } });
    expect(screen.queryByTestId('proposals')).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});
