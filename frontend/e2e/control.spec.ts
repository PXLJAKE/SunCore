// SPDX-License-Identifier: Apache-2.0
import { expect, test, type Page } from '@playwright/test';
import { expectNoAxeViolations, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

const card = (page: Page, id: string) => page.getByTestId(`device-${id}`);

async function openNow(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
  await expect(page.getByTestId('device-cards')).toBeVisible();
}

test.describe('Now view: devices', () => {
  test.afterEach(async ({ request }) => {
    await request.post('/api/v1/control/emergency-stop/clear');
    await resetDemo(request);
  });

  test('a card per controlled device with state, power and the reasons', async ({ page }) => {
    await openNow(page);
    const cards = page.getByTestId('device-cards').locator('article');
    await expect(cards).toHaveCount(3);
    const pump = card(page, 'pool_pump');
    await expect(pump.getByRole('heading', { name: 'Pool pump' })).toBeVisible();
    await expect(pump.getByTestId('power')).toContainText('≈');
    await expect(pump.getByTestId('power')).toContainText('±');
    await expect(pump.getByTestId('effective-mode')).toHaveText('Shadow mode');
    await expect(pump.getByTestId('mode-shadow')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('site-summary')).toContainText('Free surplus');
    await expect(page.getByTestId('site-summary')).toContainText('Deficit');

    // the reasons are rendered through the catalogue, never as raw keys
    await pump.getByText('Why?').click();
    await expect(pump.getByTestId('why')).not.toContainText('controller.');
    await expect(pump.getByTestId('why').locator('li').first()).not.toBeEmpty();
  });

  test('switching one device to automatic asks first and then takes effect', async ({ page }) => {
    await openNow(page);
    const pump = card(page, 'pool_pump');
    await pump.getByTestId('mode-automatic').click();
    const dialog = page.getByTestId('auto-dialog-pool_pump');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('really switches Pool pump');
    await expectNoAxeViolations(page);

    // cancel: nothing changes
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(pump.getByTestId('mode-shadow')).toHaveAttribute('aria-pressed', 'true');

    // the overall mode must allow it too
    await page.request.put('/api/v1/operation/mode', { data: { mode: 'automatic' } });
    await pump.getByTestId('mode-automatic').click();
    await dialog.getByRole('button', { name: 'Switch to automatic' }).click();
    await expect(pump.getByTestId('mode-automatic')).toHaveAttribute('aria-pressed', 'true');
    await expect(pump.getByTestId('effective-mode')).toHaveText('Automatic mode');
    await expect(page.getByTestId('mode-badge')).toHaveText('Automatic mode');
    expect(
      (await (await page.request.get('/api/v1/config/operation')).json()).device_modes,
    ).toEqual({ pool_pump: 'automatic' });

    // back to shadow needs no confirmation
    await pump.getByTestId('mode-shadow').click();
    await expect(pump.getByTestId('mode-shadow')).toHaveAttribute('aria-pressed', 'true');
    await expect(dialog).toBeHidden();
  });

  test('a stale identity answers 409 and the card says so', async ({ page }) => {
    await openNow(page);
    const pump = card(page, 'pool_pump');
    await page.route('**/api/v1/operation/devices/pool_pump', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'api.error.device_identity_changed' }),
      }),
    );
    await pump.getByTestId('mode-assisted').click();
    await expect(pump.getByTestId('card-error')).toContainText('changed in the meantime');
    await expect(pump.getByTestId('mode-shadow')).toHaveAttribute('aria-pressed', 'true');
  });

  test('override form validates, holds the device and resumes', async ({ page }) => {
    await openNow(page);
    const pump = card(page, 'pool_pump');
    await pump.getByText('Take over manually').click();
    const form = pump.getByTestId('override-pool_pump');
    await form.getByLabel(/^Hands off for a while/).fill('0');
    await form.getByTestId('override-boost').click();
    await expect(form.getByRole('alert')).toContainText('whole minutes from 1 to 1440');

    await form.getByLabel(/^Pause SunCore until/).fill('2020-01-01T10:00');
    await form.getByTestId('override-pause').click();
    await expect(form.getByRole('alert').last()).toContainText('must be in the future');

    await form.getByLabel(/^Hands off for a while/).fill('30');
    await form.getByTestId('override-boost').click();
    await expect(pump.getByTestId('held-badge')).toContainText('Held until');
    await pump.getByTestId('resume-now').click();
    await expect(pump.getByTestId('held-badge')).toHaveCount(0);
  });
});

test.describe('emergency stop', () => {
  test.afterEach(async ({ request }) => {
    await request.post('/api/v1/control/emergency-stop/clear');
  });

  for (const hash of ['#/', '#/decisions', '#/settings']) {
    test(`is reachable from ${hash}`, async ({ page }) => {
      await page.goto(`/${hash}`);
      await expect(page.getByTestId('estop')).toBeVisible();
    });
  }

  test('stops without asking, shows the state, and ends only after a confirmation', async ({
    page,
  }) => {
    await openNow(page);
    await page.getByTestId('estop').click();
    await expect(page.getByTestId('estop-banner')).toContainText('Emergency stop active');
    await expect(page.getByTestId('estop')).toHaveCount(0);
    await expectNoAxeViolations(page);

    await page.getByTestId('estop-clear').click();
    const dialog = page.getByTestId('estop-dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByTestId('estop-banner')).toBeVisible();

    await page.getByTestId('estop-clear').click();
    await dialog.getByTestId('estop-dialog-confirm').click();
    await expect(page.getByTestId('estop-banner')).toHaveCount(0);
    await expect(page.getByTestId('estop')).toBeVisible();
    expect((await (await page.request.get('/api/v1/control/state')).json()).emergency_stop).toBe(
      false,
    );
  });

  test('the state is shared: a stop from elsewhere shows up', async ({ page, request }) => {
    await openNow(page);
    await request.post('/api/v1/control/emergency-stop');
    await expect(page.getByTestId('estop-banner')).toBeVisible();
  });
});

test.describe('assisted proposals', () => {
  // The demo does not produce a proposal on demand, so the live feed gets one injected; the
  // confirm and dismiss calls are answered like the server would.
  const soon = (s: number) => new Date(Date.now() + s * 1000).toISOString();
  const proposal = (id: string) => ({
    schema_version: 2,
    intent: {
      id,
      ts: new Date().toISOString(),
      asset_id: 'pool_pump',
      action: { kind: 'switch', on: true },
      reasons: [{ key: 'controller.surplus_on', args: { need_w: 750, surplus_w: 3800 } }],
      origin: 'controller',
    },
    kind: 'proposed',
    ts: new Date().toISOString(),
    reasons: [],
    mode: 'assisted',
    expires_at: soon(90),
    held_until: null,
    observed: null,
  });

  async function injectProposals(page: Page, ids: string[]): Promise<void> {
    await page.routeWebSocket('**/api/v1/live', (ws) => {
      const server = ws.connectToServer();
      server.onMessage((message) => {
        const frame = JSON.parse(String(message)) as {
          type: string;
          control?: { proposals: unknown[] };
        };
        if (frame.control) frame.control.proposals = ids.map(proposal);
        ws.send(JSON.stringify(frame));
      });
    });
  }

  test('lists, counts down, confirms and dismisses', async ({ page }) => {
    await injectProposals(page, ['p1', 'p2']);
    const calls: string[] = [];
    await page.route('**/api/v1/proposals/*/*', (route) => {
      const url = new URL(route.request().url());
      calls.push(url.pathname.split('/').slice(-2).join('/'));
      return url.pathname.endsWith('dismiss')
        ? route.fulfill({ status: 204 })
        : route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ ...proposal('p1'), kind: 'accepted' }),
          });
    });
    await page.goto('/');
    const list = page.getByTestId('proposals');
    await expect(list.getByTestId('proposal')).toHaveCount(2);
    await expect(list.getByTestId('countdown').first()).toContainText(/Valid for 1:\d\d/);
    await expect(list.getByText('Pool pump').first()).toBeVisible();
    await expectNoAxeViolations(page);

    await list.getByTestId('proposal-confirm').first().click();
    await list.getByTestId('proposal-dismiss').last().click();
    await expect.poll(() => calls).toEqual(expect.arrayContaining(['p1/confirm', 'p2/dismiss']));
  });
});

test.describe('decision log', () => {
  test.afterEach(async ({ request }) => resetDemo(request));

  test('lists decisions with rendered reasons and filters by device and kind', async ({
    page,
    request,
  }) => {
    await page.goto('/#/decisions');
    await expect(page.getByRole('heading', { name: 'Decision log', level: 2 })).toBeVisible();
    // The controller logs a shadow decision for each device within a few ticks.
    await expect(page.getByTestId('log-list').locator('li').first()).toBeVisible({
      timeout: 40_000,
    });
    await expect(page.getByTestId('log-list')).not.toContainText('safety.shadow_mode');
    await expect(page.getByTestId('log-list')).toContainText('Would have switched');
    await expectNoAxeViolations(page);

    const all = (await (await request.get('/api/v1/decisions?limit=200')).json()) as {
      intent: { asset_id: string };
    }[];
    const ids = [...new Set(all.map((d) => d.intent.asset_id))];
    expect(ids.length).toBeGreaterThan(0);

    // device filter: every row names that device (the server filters)
    const names: Record<string, string> = {
      living_room_ac: 'Living room AC',
      pool_pump: 'Pool pump',
      heating_rod: 'Heating rod',
    };
    const id = ids[0]!;
    await page.getByTestId('filter-device').selectOption(id);
    const rows = page.getByTestId('log-list').locator('li');
    await expect(rows.first()).toBeVisible();
    for (const text of await rows.allTextContents()) expect(text).toContain(names[id] ?? id);

    // kind filter: a kind nothing has happened for gives an empty list
    await page.getByTestId('filter-device').selectOption('');
    await page.getByTestId('filter-kind-failed').check();
    await expect(page.getByTestId('log-list')).toContainText('No decisions yet');
    await page.getByTestId('filter-kind-failed').uncheck();
    await page.getByTestId('filter-kind-shadow').check();
    await expect(rows.first()).toContainText('Would have switched');
  });

  test('Now links to the log', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: 'Open the decision log' }).click();
    await expect(page).toHaveURL(/#\/decisions$/);
    await expect(page.getByTestId('decision-log')).toBeVisible();
  });
});
