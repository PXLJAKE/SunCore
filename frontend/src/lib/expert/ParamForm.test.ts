// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, paramsApi } from '../api/client';
import { applyLanguage } from '../i18n/index.svelte';
import { listing, param } from './fixtures';
import ParamForm from './ParamForm.svelte';

const tick = () =>
  param({ key: 'controller.tick_s', value: 5, default: 5, minimum: 1, maximum: 60 });
const guard = () =>
  param({
    key: 'controller.reserve_w',
    unit: 'W',
    value: 100,
    default: 100,
    minimum: 50,
    maximum: 500,
    safety: true,
  });
const flag = () =>
  param({
    key: 'planner.use_milp',
    group: 'planner',
    type: 'bool',
    value: false,
    default: false,
    unit: null,
    restart: true,
  });

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(paramsApi, 'list').mockResolvedValue(listing([tick(), guard(), flag()]));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function ready() {
  render(ParamForm);
  await screen.findByTestId('param-search');
}
const field = (key: string) =>
  screen.getAllByTestId('param').find((el) => el.getAttribute('data-key') === key) as HTMLElement;

describe('ParamForm', () => {
  it('generates one field per parameter with type, unit and range', async () => {
    await ready();
    expect(screen.getAllByTestId('param')).toHaveLength(3);
    const input = within(field('controller.tick_s')).getByRole('spinbutton') as HTMLInputElement;
    expect(input.min).toBe('1');
    expect(input.max).toBe('60');
    expect(within(field('planner.use_milp')).getByRole('checkbox')).toBeTruthy();
    expect(field('controller.reserve_w').textContent).toContain('Safety');
    expect(field('planner.use_milp').textContent).toContain('Needs restart');
  });

  it('searches by key and filters modified', async () => {
    await ready();
    await fireEvent.input(screen.getByTestId('param-search'), { target: { value: 'reserve' } });
    expect(screen.getAllByTestId('param')).toHaveLength(1);
    await fireEvent.input(screen.getByTestId('param-search'), { target: { value: 'nothing' } });
    expect(screen.getByTestId('param-none')).toBeTruthy();
  });

  it('saves a non-safety change without confirmation and shows the modified marker', async () => {
    const put = vi
      .spyOn(paramsApi, 'put')
      .mockResolvedValue(
        listing(
          [param({ key: 'controller.tick_s', value: 10, modified: true }), guard(), flag()],
          ['controller.tick_s'],
        ),
      );
    await ready();
    const input = within(field('controller.tick_s')).getByRole('spinbutton');
    await fireEvent.input(input, { target: { value: '10' } });
    await fireEvent.click(screen.getByTestId('param-save'));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ 'controller.tick_s': 10 }, false));
    expect(await screen.findByTestId('param-notice')).toBeTruthy();
    expect(within(field('controller.tick_s')).getByTestId('modified-marker')).toBeTruthy();
  });

  it('blocks an out-of-range value on the client', async () => {
    const put = vi.spyOn(paramsApi, 'put');
    await ready();
    await fireEvent.input(within(field('controller.tick_s')).getByRole('spinbutton'), {
      target: { value: '999' },
    });
    expect((screen.getByTestId('param-save') as HTMLButtonElement).disabled).toBe(true);
    expect(within(field('controller.tick_s')).getByTestId('field-issues').textContent).toContain(
      'between 1 and 60',
    );
    expect(put).not.toHaveBeenCalled();
  });

  it('asks for confirmation listing old and new value of a safety key before sending', async () => {
    const put = vi
      .spyOn(paramsApi, 'put')
      .mockResolvedValue(
        listing(
          [tick(), { ...guard(), value: 200, modified: true }, flag()],
          ['controller.reserve_w'],
        ),
      );
    await ready();
    await fireEvent.input(within(field('controller.reserve_w')).getByRole('spinbutton'), {
      target: { value: '200' },
    });
    await fireEvent.click(screen.getByTestId('param-save'));
    expect(put).not.toHaveBeenCalled();
    const list = screen.getByTestId('safety-list');
    expect(list.textContent).toContain('from 100 to 200');
    await fireEvent.click(screen.getByTestId('safety-confirm-confirm'));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ 'controller.reserve_w': 200 }, true));
  });

  it('shows the restart hint for a restart-tagged key', async () => {
    vi.spyOn(paramsApi, 'put').mockResolvedValue(
      listing([tick(), guard(), { ...flag(), value: true, modified: true }], ['planner.use_milp']),
    );
    await ready();
    await fireEvent.click(within(field('planner.use_milp')).getByRole('checkbox'));
    await fireEvent.click(screen.getByTestId('param-save'));
    expect(await screen.findByTestId('restart-hint')).toBeTruthy();
  });

  it('shows the API error key and the issue on its field', async () => {
    vi.spyOn(paramsApi, 'put').mockRejectedValue(
      new ApiError(422, 'api.error.validation', [
        {
          loc: ['body', 'changes', 'controller.tick_s'],
          key: 'api.validation.param_out_of_range',
          args: { param: 'controller.tick_s', min: 1, max: 60 },
        },
      ]),
    );
    await ready();
    await fireEvent.input(within(field('controller.tick_s')).getByRole('spinbutton'), {
      target: { value: '7' },
    });
    await fireEvent.click(screen.getByTestId('param-save'));
    expect((await screen.findByTestId('param-error')).textContent).toContain(
      'Some values are invalid',
    );
    expect(within(field('controller.tick_s')).getByTestId('field-issues')).toBeTruthy();
  });

  it('resets one key and a whole group', async () => {
    const modified = [{ ...tick(), value: 10, modified: true }, guard(), flag()];
    vi.mocked(paramsApi.list).mockResolvedValue(listing(modified));
    const resetKey = vi
      .spyOn(paramsApi, 'resetKey')
      .mockResolvedValue(listing([tick(), guard(), flag()], ['controller.tick_s']));
    const resetGroup = vi
      .spyOn(paramsApi, 'resetGroup')
      .mockResolvedValue(listing([tick(), guard(), flag()], []));
    await ready();
    await fireEvent.click(within(field('controller.tick_s')).getByTestId('param-reset'));
    await waitFor(() => expect(resetKey).toHaveBeenCalledWith('controller.tick_s'));
    await waitFor(() =>
      expect(within(field('controller.tick_s')).queryByTestId('modified-marker')).toBeNull(),
    );
    cleanup();
    vi.mocked(paramsApi.list).mockResolvedValue(listing(modified));
    await ready();
    await fireEvent.click(screen.getAllByTestId('group-reset')[0] as HTMLElement);
    await waitFor(() => expect(resetGroup).toHaveBeenCalledWith('controller'));
  });
});
