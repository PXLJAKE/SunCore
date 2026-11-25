// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '../i18n/index.svelte';
import Banner from './Banner.svelte';
import Button from './Button.svelte';
import Card from './Card.svelte';
import Chip from './Chip.svelte';
import Disclosure from './Disclosure.svelte';
import EmptyState from './EmptyState.svelte';
import FormRow from './FormRow.svelte';
import Icon from './Icon.svelte';
import { ICONS } from './icons';
import Loading from './Loading.svelte';
import SectionHeader from './SectionHeader.svelte';
import SegmentedControl from './SegmentedControl.svelte';
import Stat from './Stat.svelte';
import CardFooter from './CardFooter.svelte';
import ToggleGroup from './ToggleGroup.svelte';

const text = (s: string) => createRawSnippet(() => ({ render: () => `<span>${s}</span>` }));

beforeEach(() => applyLanguage('en'));
afterEach(() => cleanup());

describe('Button', () => {
  it('maps the variants onto the shared classes and keeps a 44 px class', () => {
    render(Button, { props: { variant: 'secondary', testid: 'b', children: text('Save') } });
    const b = screen.getByTestId('b');
    expect(b.tagName).toBe('BUTTON');
    expect(b.getAttribute('type')).toBe('button');
    expect(b.className).toContain('btn');
    expect(b.className).toContain('secondary');
  });

  it('is disabled and announced as busy while busy', async () => {
    render(Button, { props: { busy: true, testid: 'b', children: text('Save') } });
    const b = screen.getByTestId('b') as HTMLButtonElement;
    expect(b.disabled).toBe(true);
    expect(b.getAttribute('aria-busy')).toBe('true');
    cleanup();
    render(Button, { props: { disabled: true, testid: 'c', children: text('Save') } });
    const c = screen.getByTestId('c') as HTMLButtonElement;
    expect(c.disabled).toBe(true);
    expect(c.getAttribute('aria-busy')).toBeNull();
  });

  it('renders a link with href', () => {
    render(Button, { props: { href: '#/plan', testid: 'b', children: text('Plan') } });
    expect(screen.getByRole('link', { name: 'Plan' }).getAttribute('href')).toBe('#/plan');
  });
});

describe('Card', () => {
  it('labels the section with its heading at the given level', () => {
    render(Card, { props: { title: 'Battery', level: 2, children: text('body') } });
    const heading = screen.getByRole('heading', { level: 2, name: 'Battery' });
    expect(screen.getByRole('region', { name: 'Battery' })).toBeTruthy();
    expect(heading).toBeTruthy();
  });

  it('collapses its body with a disclosure button', async () => {
    render(Card, { props: { title: 'Forecast', collapsible: true, children: text('body') } });
    const toggle = screen.getByRole('button', { name: 'Forecast' });
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const body = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    expect(body.hidden).toBe(false);
    await fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(body.hidden).toBe(true);
  });

  it('starts closed when open is false', () => {
    render(Card, {
      props: { title: 'Model', collapsible: true, open: false, children: text('body') },
    });
    expect(screen.getByRole('button', { name: 'Model' }).getAttribute('aria-expanded')).toBe(
      'false',
    );
  });
});

describe('SegmentedControl', () => {
  const options = [
    { value: '24', label: '24 h' },
    { value: '48', label: '48 h' },
    { value: '72', label: '72 h' },
  ] as const;

  it('is a radio group with one tab stop', () => {
    render(SegmentedControl, { props: { label: 'Horizon', options, value: '24' } });
    expect(screen.getByRole('radiogroup', { name: 'Horizon' })).toBeTruthy();
    const radios = screen.getAllByRole('radio');
    expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    expect(radios.map((r) => r.tabIndex)).toEqual([0, -1, -1]);
  });

  it('moves and selects with the arrow keys, Home and End', async () => {
    const onchange = vi.fn();
    render(SegmentedControl, { props: { label: 'Horizon', options, value: '24', onchange } });
    const radios = () => screen.getAllByRole('radio');
    await fireEvent.keyDown(radios()[0]!, { key: 'ArrowRight' });
    expect(onchange).toHaveBeenLastCalledWith('48');
    expect(radios()[1]!.getAttribute('aria-checked')).toBe('true');
    expect(document.activeElement).toBe(radios()[1]);
    await fireEvent.keyDown(radios()[1]!, { key: 'End' });
    expect(onchange).toHaveBeenLastCalledWith('72');
    await fireEvent.keyDown(radios()[2]!, { key: 'ArrowRight' });
    expect(onchange).toHaveBeenLastCalledWith('24'); // wraps
    await fireEvent.keyDown(radios()[0]!, { key: 'ArrowLeft' });
    expect(onchange).toHaveBeenLastCalledWith('72');
    await fireEvent.keyDown(radios()[2]!, { key: 'Home' });
    expect(onchange).toHaveBeenLastCalledWith('24');
  });

  it('selects on click and ignores a click on the current value', async () => {
    const onchange = vi.fn();
    render(SegmentedControl, { props: { label: 'Horizon', options, value: '24', onchange } });
    await fireEvent.click(screen.getByRole('radio', { name: '24 h' }));
    expect(onchange).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('radio', { name: '48 h' }));
    expect(onchange).toHaveBeenCalledWith('48');
  });
});

describe('Stat', () => {
  it('shows label, value and unit in a description list', () => {
    render(Stat, { props: { label: 'Solar', value: '4.5', unit: 'kW', tone: 'pv', testid: 's' } });
    const s = screen.getByTestId('s');
    expect(s.tagName).toBe('DL');
    expect(s.querySelector('dt')?.textContent?.trim()).toBe('Solar');
    expect(s.querySelector('dd')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('4.5kW');
  });

  it('spells out a unit mismatch and a stale value for screen readers', () => {
    render(Stat, { props: { label: 'Grid', value: '0 W', quality: 'mismatch', testid: 'a' } });
    expect(screen.getByTestId('a').textContent).toContain('!');
    cleanup();
    render(Stat, { props: { label: 'Grid', value: '0 W', quality: 'stale', testid: 'b' } });
    expect(screen.getByTestId('b').textContent).toContain('(out of date)');
  });
});

describe('FormRow', () => {
  it('links label, hint and error to the control', () => {
    const control = createRawSnippet((a: () => { id: string; describedby?: string }) => ({
      render: () =>
        `<input class="input" id="${a().id}" aria-describedby="${a().describedby ?? ''}" />`,
    }));
    render(FormRow, {
      props: { label: 'Capacity', hint: 'Usable', error: 'Too large', children: control },
    });
    const input = screen.getByLabelText('Capacity');
    const ids = input.getAttribute('aria-describedby')!.split(' ');
    expect(ids.map((id) => document.getElementById(id)?.textContent)).toEqual([
      'Usable',
      'Too large',
    ]);
  });
});

describe('small components', () => {
  it('Chip carries its tone', () => {
    render(Chip, { props: { tone: 'warn', testid: 'c', children: text('Held') } });
    expect(screen.getByTestId('c').className).toContain('warn');
  });

  it('Banner is a status with a title', () => {
    render(Banner, {
      props: { tone: 'error', title: 'Emergency stop active.', testid: 'b', children: text('x') },
    });
    const b = screen.getByRole('status');
    expect(b.className).toContain('error');
    expect(b.textContent).toContain('Emergency stop active.');
  });

  it('EmptyState and Loading', () => {
    render(EmptyState, { props: { title: 'Nothing yet', text: 'Come back later' } });
    expect(screen.getByText('Nothing yet')).toBeTruthy();
    render(Loading, {});
    expect(screen.getByRole('status').textContent).toContain('Loading');
  });

  it('SectionHeader renders the heading level', () => {
    render(SectionHeader, { props: { title: 'Devices', level: 2, description: 'All of them' } });
    expect(screen.getByRole('heading', { level: 2, name: 'Devices' })).toBeTruthy();
  });

  it('Disclosure toggles natively', async () => {
    render(Disclosure, { props: { summary: 'Why?', children: text('Because') } });
    const details = document.querySelector('details')!;
    expect(details.open).toBe(false);
    await fireEvent.click(screen.getByText('Why?'));
    expect(details.open).toBe(true);
  });

  it('every icon is decorative and has a path', () => {
    for (const name of Object.keys(ICONS) as (keyof typeof ICONS)[]) {
      const { container } = render(Icon, { props: { name } });
      const svg = container.querySelector(`svg[data-icon="${name}"]`)!;
      expect(svg.getAttribute('aria-hidden')).toBe('true');
      expect(svg.querySelector('path')?.getAttribute('d')).toMatch(/^M/);
      cleanup();
    }
  });
});

describe('ToggleGroup', () => {
  const options = [
    { value: 'shadow', label: 'Shadow', name: 'Shadow mode' },
    { value: 'automatic', label: 'Automatic', name: 'Automatic mode' },
  ] as const;

  it('is a named group of toggle buttons with the full name as accessible name', () => {
    render(ToggleGroup, {
      props: { label: 'Mode of Pump', options, value: 'shadow', onselect: () => {}, testid: 'm' },
    });
    expect(screen.getByRole('group', { name: 'Mode of Pump' })).toBeTruthy();
    const pressed = screen.getByRole('button', { name: 'Shadow mode' });
    expect(pressed.getAttribute('aria-pressed')).toBe('true');
    expect(pressed.textContent).toBe('Shadow');
    expect(screen.getByTestId('m-automatic').getAttribute('aria-pressed')).toBe('false');
  });

  it('reports every press and leaves the choice to the caller; disabled stops presses', async () => {
    const onselect = vi.fn();
    render(ToggleGroup, { props: { label: 'Mode', options, value: 'shadow', onselect } });
    await fireEvent.click(screen.getByRole('button', { name: 'Automatic mode' }));
    expect(onselect).toHaveBeenCalledWith('automatic');
    // No selection by itself: the pressed option is still the one the caller passed.
    expect(screen.getByRole('button', { name: 'Shadow mode' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    cleanup();
    render(ToggleGroup, {
      props: { label: 'Mode', options, value: 'shadow', onselect, disabled: true },
    });
    expect(
      (screen.getByRole('button', { name: 'Automatic mode' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe('CardFooter', () => {
  it('shows each panel on its toggle and keeps closed panels in the DOM', async () => {
    render(CardFooter, {
      props: {
        items: [
          { id: 'why', label: 'Why?', testid: 'why', panel: text('Because') },
          { id: 'manual', label: 'Take over manually', testid: 'manual', panel: text('Form') },
        ],
      },
    });
    const why = screen.getByRole('button', { name: 'Why?' });
    const panel = screen.getByTestId('why');
    expect(why.getAttribute('aria-expanded')).toBe('false');
    expect(why.getAttribute('aria-controls')).toBe(panel.id);
    expect(panel.hidden).toBe(true);
    expect(panel.textContent).toContain('Because');
    await fireEvent.click(why);
    expect(why.getAttribute('aria-expanded')).toBe('true');
    expect(panel.hidden).toBe(false);
    expect(screen.getByTestId('manual').hidden).toBe(true);
  });
});
