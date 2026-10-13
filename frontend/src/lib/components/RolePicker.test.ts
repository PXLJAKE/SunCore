// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { EntityCandidate } from '../api/types';
import { clearCandidateCache } from '../stores/candidates';
import RolePicker from './RolePicker.svelte';

const candidate = (entity_id: string): EntityCandidate =>
  ({
    entity_id,
    name: entity_id,
    state: '1',
    unit: 'W',
    quality: 'ok',
    score: 10,
    criteria: [],
  }) as unknown as EntityCandidate;

afterEach(() => {
  cleanup();
  clearCandidateCache();
  vi.restoreAllMocks();
});

describe('RolePicker', () => {
  it('asks again with hidden entities when the toggle is switched on', async () => {
    const spy = vi
      .spyOn(api, 'entitiesForRole')
      .mockImplementation(async (_role, includeHidden) => [
        candidate(includeHidden ? 'sensor.diag_power' : 'sensor.grid_power'),
      ]);
    render(RolePicker, {
      props: {
        label: 'Grid power',
        role: 'grid_power',
        field: { entity_id: '', invert_sign: false },
      },
    });
    await waitFor(() => expect(spy).toHaveBeenCalledWith('grid_power', false));
    const toggle = screen.getByLabelText('Also show hidden and diagnostic entities');
    expect((toggle as HTMLInputElement).checked).toBe(false);
    await fireEvent.click(toggle);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('grid_power', true));
    await fireEvent.focus(screen.getByRole('combobox'));
    await waitFor(() =>
      expect(screen.getByRole('option').getAttribute('data-entity')).toBe('sensor.diag_power'),
    );
  });
});
