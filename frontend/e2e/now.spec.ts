// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';

test.describe('Now view', () => {
  test('renders all four nodes, banner, badge and live status', async ({ page }) => {
    await page.goto('/');
    for (const id of ['pv', 'grid', 'battery', 'house']) {
      await expect(page.getByTestId(`node-${id}`)).toBeVisible();
      await expect(page.getByTestId(`flow-${id}`)).toBeVisible();
    }
    await expect(page.getByTestId('demo-banner')).toBeVisible();
    await expect(page.getByTestId('mode-badge')).toHaveText('Shadow mode');
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    await expect(page.getByRole('list', { name: 'Current power values' })).toBeVisible();
  });

  test('values update over time', async ({ page }) => {
    await page.goto('/');
    const house = page.getByTestId('flow-house').locator('.val');
    await expect(house).toHaveText(/\d/);
    const seen = new Set<string>();
    await expect
      .poll(
        async () => {
          seen.add((await house.textContent()) ?? '');
          return seen.size;
        },
        { timeout: 30_000, intervals: [500] },
      )
      .toBeGreaterThan(2);
  });

  test('uses relative URLs only (works below an ingress prefix)', async ({ page }) => {
    const requests: string[] = [];
    page.on('request', (r) => requests.push(new URL(r.url()).pathname));
    page.on('websocket', (ws) => requests.push(new URL(ws.url()).pathname));
    await page.goto('/');
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    expect(requests).toContain('/api/v1/live');
    // Same page served under a prefix: the SPA must resolve everything against it.
    const html = await (await page.request.get('/')).text();
    expect(html).toContain('./assets/');
    expect(html).not.toMatch(/(src|href)="\/(?!\/)/);
  });

  test('shows the localized German UI for a German browser', async ({ browser }) => {
    const ctx = await browser.newContext({ locale: 'de-DE' });
    const page = await ctx.newPage();
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Jetzt' })).toBeVisible();
    await expect(page.getByTestId('mode-badge')).toHaveText('Schattenmodus');
    await expect(page.getByTestId('demo-banner')).toContainText('Demo-Modus');
    await ctx.close();
  });

  test('marks unavailable flows when the socket delivers bad quality', async ({ page }) => {
    await page.routeWebSocket('**/api/v1/live', (ws) => {
      const q = (value: number | null, quality: string) => ({ value, quality });
      ws.send(
        JSON.stringify({
          type: 'snapshot',
          flows: {
            grid_w: q(null, 'unavailable'),
            pv_w: q(1500, 'stale'),
            battery_w: q(900, 'ok'),
            house_w: q(200, 'invalid'),
            battery_soc_pct: q(55, 'ok'),
            ts: null,
          },
          health: {
            ok: true,
            ha_state: 'connected',
            ha_last_update_age_s: 0,
            db_ok: true,
            version: 'x',
          },
          decisions: [],
        }),
      );
    });
    await page.goto('/');
    await expect(page.getByTestId('flow-grid')).toContainText('Unavailable');
    await expect(page.getByTestId('flow-pv')).toContainText('Outdated');
    await expect(page.getByTestId('flow-pv')).toContainText('1.5');
    await expect(page.getByTestId('flow-house')).toContainText('Invalid value');
    await expect(page.getByTestId('flow-battery')).toContainText('Charging');
    await expect(page.getByTestId('node-grid')).toHaveClass(/bad/);
    await expect(page.getByTestId('node-pv')).toHaveClass(/stale/);
  });

  test('shows offline when the socket drops and recovers on reconnect', async ({ page }) => {
    let connections = 0;
    await page.routeWebSocket('**/api/v1/live', (ws) => {
      connections += 1;
      if (connections === 1) {
        ws.close();
        return;
      }
      const server = ws.connectToServer();
      void server;
    });
    await page.goto('/');
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'offline');
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live', {
      timeout: 20_000,
    });
  });
});
