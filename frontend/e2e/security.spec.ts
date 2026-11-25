// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';

// SEC-13: every write needs `X-SunCore-Request: 1`, except setting the emergency stop. The
// config sends the header for the specs' own calls; these contexts are built without it, like
// a cross-site form or an old client.
test.describe('request header (SEC-13)', () => {
  test.describe.configure({ mode: 'serial' });

  test.afterEach(async ({ request }) => {
    await request.post('/api/v1/control/emergency-stop/clear');
  });

  test('a client without the header can stop, but not clear or change anything', async ({
    playwright,
    baseURL,
    request,
  }) => {
    const bare = await playwright.request.newContext({ baseURL, extraHTTPHeaders: {} });
    try {
      const stop = await bare.post('/api/v1/control/emergency-stop');
      expect(stop.status()).toBe(200);
      expect(await stop.json()).toEqual({ active: true });

      const clear = await bare.post('/api/v1/control/emergency-stop/clear');
      expect(clear.status()).toBe(403);
      expect(await clear.json()).toEqual({ detail: 'api.error.request_header_missing' });

      const replan = await bare.post('/api/v1/plan/replan');
      expect(replan.status()).toBe(403);

      const read = await bare.get('/api/v1/control/state');
      expect((await read.json()).emergency_stop).toBe(true);
    } finally {
      await bare.dispose();
    }
    const clear = await request.post('/api/v1/control/emergency-stop/clear');
    expect(await clear.json()).toEqual({ active: false });
  });

  test('the SPA sends the header on its writes', async ({ browser, baseURL }) => {
    // A context without the config's header: only the SPA's own fetch helper can add it.
    const context = await browser.newContext({ baseURL, extraHTTPHeaders: {} });
    const page = await context.newPage();
    const sent: (string | undefined)[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && r.url().includes('/api/v1/control/emergency-stop')) {
        sent.push(r.headers()['x-suncore-request']);
      }
    });
    await page.goto('/');
    await page.getByTestId('estop').click();
    await expect(page.getByTestId('estop-banner')).toBeVisible();
    await page.getByTestId('estop-clear').click();
    await page.getByTestId('estop-dialog').getByTestId('estop-dialog-confirm').click();
    await expect(page.getByTestId('estop-banner')).toHaveCount(0);
    expect(sent).toEqual(['1', '1']);
    await context.close();
  });
});
