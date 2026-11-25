// SPDX-License-Identifier: Apache-2.0
// The inverter limit verification dialog (PR 7-J). The demo has no inverter limit adapter, so the
// procedure's API is mocked here; the backend procedure itself is covered by the simulator tests
// (tests/sim/test_verification_scenarios.py). The dialog never chooses a limit and never writes
// without the user's confirmation; closing it while the test runs aborts the test.
import { expect, test, type Page, type Route } from '@playwright/test';
import { expectNoAxeViolations, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

type View = Record<string, unknown>;

const BASE: View = {
  phase: 'idle',
  adapter: true,
  capability: 'export_limit',
  verified: false,
  reasons: [],
  stable_s: 0,
  stable_needed_s: 120,
  baseline: null,
  test_limit: null,
  test_limit_w: null,
  confirm_by: null,
  window_expires_at: null,
  export_w: null,
  house_w: null,
  battery_w: null,
  export_before_w: null,
  export_during_w: null,
  semantics: null,
  last: null,
};

const READY: View = {
  ...BASE,
  phase: 'ready',
  baseline: { kind: 'inverter_limit', max_export_w: 13500, max_ac_pct: null },
  test_limit: { kind: 'inverter_limit', max_export_w: 3000, max_ac_pct: null },
  test_limit_w: 3000,
  confirm_by: '2026-07-01T12:02:00+02:00',
  export_w: 4200,
  house_w: 600,
};

/** A scripted backend: records every call; `refuseFirstStart` answers the first start with 409. */
async function mockApi(page: Page, opts: { refuseFirstStart: boolean }): Promise<string[]> {
  const calls: string[] = [];
  let view: View = { ...BASE };
  let refused = !opts.refuseFirstStart;
  await page.route('**/api/v1/inverter/verification**', async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/inverter\/verification/, '');
    calls.push(`${req.method()} ${path || '/'}`);
    if (req.method() === 'GET') {
      const answer = view;
      // The poll after the confirmation sees the finished test.
      if (view.phase === 'observing') {
        view = {
          ...BASE,
          phase: 'passed',
          verified: true,
          semantics: 'export_limit',
          last: {
            outcome: 'passed',
            reason: null,
            semantics: 'export_limit',
            identity: 'x',
            at: '2026-07-01T12:03:00+02:00',
          },
        };
      }
      return route.fulfill({ json: answer });
    }
    if (path === '' && !refused) {
      refused = true;
      return route.fulfill({
        status: 409,
        json: {
          detail: 'api.error.verification_refused',
          reasons: [{ key: 'verify.export_low', args: { export_w: 400, needed_w: 2100 } }],
        },
      });
    }
    if (path === '') view = { ...READY };
    else if (path === '/confirm') view = { ...READY, phase: 'observing' };
    else if (path === '/abort') {
      view = { ...BASE, phase: 'aborted', reasons: [{ key: 'verify.aborted', args: {} }] };
    }
    return route.fulfill({ json: view });
  });
  return calls;
}

test.describe('Inverter limit verification', () => {
  test.afterAll(async ({ request }) => resetDemo(request));

  test('walks from a refusal to a confirmed write and a pass', async ({ page, request }) => {
    await resetDemo(request);
    const calls = await mockApi(page, { refuseFirstStart: true });
    await page.goto('/#/settings/system');
    const card = page.getByTestId('inverter-limit');
    await expect(card.getByTestId('inverter-verified')).toContainText('Not verified yet');
    await card.getByTestId('inverter-verify-open').click();

    const dialog = page.getByTestId('verify-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('verify-phase-idle')).toBeVisible();
    await expectNoAxeViolations(page);

    await dialog.getByTestId('verify-start').click();
    await expect(dialog.getByTestId('verify-refused')).toContainText('too low');
    expect(calls.filter((c) => c.startsWith('POST'))).toEqual(['POST /']);

    await dialog.getByTestId('verify-start').click();
    await expect(dialog.getByTestId('verify-proposal')).toContainText('3 kW');
    await expect(dialog.getByTestId('verify-refused')).toHaveCount(0);
    // Nothing is written before the confirmation: only the start went out.
    expect(calls).not.toContain('POST /confirm');
    await expectNoAxeViolations(page);

    await dialog.getByTestId('verify-confirm').click();
    await expect(dialog.getByTestId('verify-result')).toContainText('limits the export', {
      timeout: 10_000,
    });
    await dialog.getByTestId('verify-close').click();
    await expect(dialog).toBeHidden();
    // A finished test is not aborted on close.
    expect(calls).not.toContain('POST /abort');
    expect(calls.filter((c) => c.startsWith('POST'))).toEqual([
      'POST /',
      'POST /',
      'POST /confirm',
    ]);
  });

  test('closing the dialog while the test runs aborts it', async ({ page, request }) => {
    await resetDemo(request);
    const calls = await mockApi(page, { refuseFirstStart: false });
    await page.goto('/#/settings/system');
    await page.getByTestId('inverter-verify-open').click();
    const dialog = page.getByTestId('verify-dialog');
    await dialog.getByTestId('verify-start').click();
    await expect(dialog.getByTestId('verify-confirm')).toBeVisible();
    await expect(dialog.getByTestId('verify-close')).toHaveText('Cancel the test');
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    expect(calls).toContain('POST /abort');
    expect(calls).not.toContain('POST /confirm');
  });

  test('the wizard step offers the test only with an adapter', async ({ page, request }) => {
    await resetDemo(request);
    await page.goto('/#/setup/battery');
    await expect(page.getByTestId('inverter-why')).toContainText('No inverter limit adapter');
    await expect(page.getByTestId('inverter-verify-open')).toBeDisabled();
  });
});
