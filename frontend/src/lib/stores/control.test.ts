// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api/client';
import { controlState, device } from '../control/fixtures';
import { ControlActions } from './control.svelte';
import { live } from './live.svelte';

let actions: ControlActions;

beforeEach(() => {
  actions = new ControlActions();
  live.control = null;
  vi.spyOn(api, 'controlState').mockResolvedValue(
    controlState({ devices: [device({ identity: 'tok-2' })] }),
  );
});
afterEach(() => vi.restoreAllMocks());

describe('device mode', () => {
  it('sends the identity token above shadow and none for shadow', async () => {
    const put = vi.spyOn(api, 'setDeviceMode').mockResolvedValue({} as never);
    await actions.setDeviceMode({ deviceId: 'ac', mode: 'automatic', identity: 'tok-1' });
    await actions.setDeviceMode({ deviceId: 'ac', mode: 'assisted', identity: 'tok-1' });
    await actions.setDeviceMode({ deviceId: 'ac', mode: 'shadow', identity: 'tok-1' });
    expect(put.mock.calls).toEqual([
      ['ac', 'automatic', 'tok-1'],
      ['ac', 'assisted', 'tok-1'],
      ['ac', 'shadow', null],
    ]);
  });

  it('keeps the token the user decided on, even when the state moved on meanwhile', async () => {
    const put = vi.spyOn(api, 'setDeviceMode').mockResolvedValue({} as never);
    live.control = controlState({ devices: [device({ identity: 'tok-9' })] });
    await actions.setDeviceMode({ deviceId: 'ac', mode: 'automatic', identity: 'tok-1' });
    expect(put).toHaveBeenCalledWith('ac', 'automatic', 'tok-1');
  });

  it('reports a changed device (409) with its catalogue key and refreshes the state', async () => {
    vi.spyOn(api, 'setDeviceMode').mockRejectedValue(
      new ApiError(409, 'api.error.device_identity_changed'),
    );
    const ok = await actions.setDeviceMode({ deviceId: 'ac', mode: 'automatic', identity: 'old' });
    expect(ok).toBe(false);
    expect(actions.errors['ac']).toBe('api.error.device_identity_changed');
    // The fresh token is there for the next, deliberate attempt.
    expect(live.control?.devices[0]?.identity).toBe('tok-2');
    expect(actions.busy['ac']).toBeUndefined();
  });

  it('clears the error on the next attempt', async () => {
    vi.spyOn(api, 'setDeviceMode')
      .mockRejectedValueOnce(new ApiError(409, 'api.error.device_identity_changed'))
      .mockResolvedValue({} as never);
    await actions.setDeviceMode({ deviceId: 'ac', mode: 'automatic', identity: 'old' });
    expect(actions.errors['ac']).toBeDefined();
    await actions.setDeviceMode({ deviceId: 'ac', mode: 'automatic', identity: 'tok-2' });
    expect(actions.errors['ac']).toBeUndefined();
  });

  it('ignores a second request while one is in flight', async () => {
    let release: () => void = () => {};
    const put = vi
      .spyOn(api, 'setDeviceMode')
      .mockImplementation(() => new Promise((resolve) => (release = () => resolve({} as never))));
    const first = actions.setDeviceMode({ deviceId: 'ac', mode: 'assisted', identity: 't' });
    expect(actions.busy['ac']).toBe(true);
    expect(await actions.setDeviceMode({ deviceId: 'ac', mode: 'automatic', identity: 't' })).toBe(
      false,
    );
    release();
    await first;
    expect(put).toHaveBeenCalledTimes(1);
  });
});

describe('other actions', () => {
  it('sets the global mode', async () => {
    const put = vi.spyOn(api, 'setGlobalMode').mockResolvedValue({} as never);
    expect(await actions.setGlobalMode('assisted')).toBe(true);
    expect(put).toHaveBeenCalledWith('assisted');
  });

  it('stops and clears the emergency stop under one scope', async () => {
    const stop = vi.spyOn(api, 'emergencyStop').mockResolvedValue({ active: true });
    const clear = vi.spyOn(api, 'clearEmergencyStop').mockResolvedValue({ active: false });
    await actions.emergencyStop();
    await actions.clearEmergencyStop();
    expect(stop).toHaveBeenCalledOnce();
    expect(clear).toHaveBeenCalledOnce();
  });

  it('keeps a failed emergency stop visible', async () => {
    vi.spyOn(api, 'emergencyStop').mockRejectedValue(new ApiError(0, 'api.error.network'));
    expect(await actions.emergencyStop()).toBe(false);
    expect(actions.errors['emergency']).toBe('api.error.network');
  });

  it('sends overrides and proposal decisions', async () => {
    const override = vi.spyOn(api, 'override').mockResolvedValue(device());
    const confirm = vi.spyOn(api, 'confirmProposal').mockResolvedValue({} as never);
    const dismiss = vi.spyOn(api, 'dismissProposal').mockResolvedValue(undefined);
    await actions.override({ device_id: 'ac' }, { action: 'boost', minutes: 30 });
    await actions.confirmProposal('p1');
    await actions.dismissProposal('p2');
    expect(override).toHaveBeenCalledWith('ac', { action: 'boost', minutes: 30 });
    expect(confirm).toHaveBeenCalledWith('p1');
    expect(dismiss).toHaveBeenCalledWith('p2');
  });

  it('turns an expired proposal (404) into a message and refreshes the list', async () => {
    vi.spyOn(api, 'confirmProposal').mockRejectedValue(
      new ApiError(404, 'api.error.unknown_proposal'),
    );
    expect(await actions.confirmProposal('gone')).toBe(false);
    expect(actions.errors['gone']).toBe('api.error.unknown_proposal');
    expect(api.controlState).toHaveBeenCalled();
  });
});
