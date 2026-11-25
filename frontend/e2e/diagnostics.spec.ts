// SPDX-License-Identifier: Apache-2.0
import { expect, test, type Page } from '@playwright/test';
import { expectNoAxeViolations, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('Settings > Advanced > Diagnostics (7-I)', () => {
  test.beforeEach(async ({ page, request }) => {
    await resetDemo(request);
    await page.goto('/#/settings/advanced');
    await expect(page.getByTestId('diagnostics')).toBeVisible();
  });
  test.afterAll(async ({ request }) => resetDemo(request));

  test('explains the pseudonymisation and loads nothing until asked', async ({ page }) => {
    await expect(page.getByTestId('diag-privacy')).toContainText('pseudonymised');
    await expect(page.getByTestId('diag-privacy')).toContainText(
      'Read it before you post it publicly',
    );
    await expect(page.getByTestId('diag-facts')).toHaveCount(0);
  });

  test('the preview is collapsible, readable and free of real ids', async ({ page }) => {
    const preview = page.getByTestId('diag-preview');
    await preview.locator('summary').first().click();
    await expect(page.getByTestId('diag-facts')).toBeVisible();
    await expect(page.getByTestId('diag-facts')).toContainText('SunCore version');
    const section = page.getByTestId('diag-section-configuration');
    await section.locator('summary').click();
    const json = section.locator('pre');
    await expect(json).toBeVisible();
    await expect(json).toHaveAttribute('tabindex', '0');
    // The demo's real entity ids are replaced by pseudonyms in the export.
    await expect(json).not.toContainText('sensor.sim_grid_power');
    await expect(json).toContainText('sensor.e_');
    await expectNoAxeViolations(page);
    // Collapsing hides it again.
    await section.locator('summary').click();
    await expect(json).toHaveCount(0);
  });

  test('downloads a JSON file with the previewed export', async ({ page }) => {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('diag-download').click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^suncore-diagnostics-\d{4}-\d{2}-\d{2}\.json$/);
    const path = await download.path();
    const { readFileSync } = await import('node:fs');
    const body = JSON.parse(readFileSync(path, 'utf-8')) as Record<string, unknown>;
    expect(body.schema_version).toBe(1);
    expect(JSON.stringify(body)).not.toContain('sensor.sim_grid_power');
    await expect(page.getByTestId('diag-saved')).toContainText('suncore-diagnostics-');
  });

  test('shows an error when the export cannot be created', async ({ page }) => {
    await page.route('**/api/v1/diagnostics', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: '{}' }),
    );
    await page.getByTestId('diag-download').click();
    await expect(page.getByTestId('diag-error')).toBeVisible();
    await expectNoAxeViolations(page);
  });
});

async function openNow(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
  await expect(page.getByTestId('device-cards')).toBeVisible();
}

test.describe('Heat pump card: live values (7-I)', () => {
  test('the demo shows the COP at the outdoor temperature, labelled assumed', async ({ page }) => {
    await openNow(page);
    const pump = page.getByTestId('device-heat_pump');
    await expect(pump.getByTestId('hp-cop')).toContainText('outdoors');
    await expect(pump.getByTestId('hp-cop-assumed')).toHaveText('assumed');
    await expect(pump.getByTestId('hp-model')).toHaveText('Not learned yet');
    await expectNoAxeViolations(page);
  });

  test('a fitted model shows its error and whether the planner uses it', async ({ page }) => {
    // The control state arrives on the WebSocket: patch the heat pump in every message.
    type Msg = { control?: { devices: { device_id: string; heat_pump?: unknown }[] } };
    const patch = (msg: Msg): Msg => {
      for (const d of msg.control?.devices ?? []) {
        if (d.device_id !== 'heat_pump') continue;
        d.heat_pump = {
          outdoor_c: 6.5,
          cop: 3.4,
          cop_assumed: false,
          model: {
            fit_error: 0.082,
            history_days: 21,
            fitted_at: '2026-11-24T03:00:00+00:00',
            used_by_planner: false,
          },
        };
      }
      return msg;
    };
    await page.routeWebSocket(/\/api\/v1\/live/, (ws) => {
      const server = ws.connectToServer();
      ws.onMessage((m) => server.send(m));
      server.onMessage((m) => {
        try {
          ws.send(JSON.stringify(patch(JSON.parse(String(m)) as Msg)));
        } catch {
          ws.send(m);
        }
      });
    });
    await openNow(page);
    const pump = page.getByTestId('device-heat_pump');
    await expect(pump.getByTestId('hp-cop')).toContainText('3.4');
    await expect(pump.getByTestId('hp-cop-assumed')).toHaveCount(0);
    await expect(pump.getByTestId('hp-model')).toContainText('error 8');
    await expect(pump.getByTestId('hp-model-use')).toContainText('Does not use the model');
    await expectNoAxeViolations(page);
  });
});
