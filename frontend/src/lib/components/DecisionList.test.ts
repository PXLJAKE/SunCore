// SPDX-License-Identifier: Apache-2.0
import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { proposal } from '../control/fixtures';
import { applyLanguage } from '../i18n/index.svelte';
import DecisionList from './DecisionList.svelte';

beforeEach(() => applyLanguage('en'));
afterEach(() => cleanup());

const shadow = { key: 'safety.shadow_mode', args: {} };
const entry = (id: string, reasons = [shadow]) => proposal(id, null, { kind: 'shadow', reasons });

describe('DecisionList', () => {
  it('shows at most four entries and the shared shadow note once', () => {
    const list = ['a', 'b', 'c', 'd', 'e'].map((id) => entry(id));
    render(DecisionList, {
      props: { decisions: list, names: new Map([['ac', 'Living room AC']]) },
    });
    expect(screen.getAllByTestId('decision-item')).toHaveLength(4);
    expect(screen.getByTestId('decisions-shared').textContent).toContain('nothing was switched');
    expect(screen.getAllByText(/nothing was switched/)).toHaveLength(1);
    expect(screen.getAllByTestId('decision-item')[0]?.textContent).toContain('Living room AC');
  });

  it('keeps a reason on its own line when not every entry has it', () => {
    const blocked = { key: 'safety.shadow_mode', args: {} };
    render(DecisionList, {
      props: { decisions: [entry('a', [blocked]), entry('b', [])] },
    });
    expect(screen.queryByTestId('decisions-shared')).toBeNull();
    expect(screen.getAllByText(/nothing was switched/)).toHaveLength(1);
  });

  it('says so when there is nothing yet', () => {
    render(DecisionList, { props: { decisions: [] } });
    expect(screen.getByText('No decisions yet.')).toBeTruthy();
  });
});
