// SPDX-License-Identifier: Apache-2.0
import AxeBuilder from '@axe-core/playwright';
import { expect, type APIRequestContext, type Page } from '@playwright/test';

import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/** Where global setup keeps the demo's initial export (the demo starts fully configured). */
export const SNAPSHOT_FILE = join(
  tmpdir(),
  `suncore-e2e-demo-${process.env.SUNCORE_E2E_PORT ?? 8099}.json`,
);

async function put(request: APIRequestContext, section: string, data: unknown): Promise<void> {
  const res = await request.put(`/api/v1/config/${section}`, { data });
  expect(res.ok(), `PUT ${section}: ${res.status()}`).toBe(true);
}

/**
 * Back to the demo as it starts: fully configured, wizard finished. With `wizardDone: false` the
 * wizard is fresh: no devices, default goals and mode, no step done (region, tariff, site and
 * forecast keep the demo values; steps overwrite them).
 */
export async function resetDemo(
  request: APIRequestContext,
  opts: { wizardDone: boolean } = { wizardDone: true },
): Promise<void> {
  const snapshot = JSON.parse(readFileSync(SNAPSHOT_FILE, 'utf-8')) as unknown;
  const res = await request.post('/api/v1/config/import', { data: snapshot });
  expect(res.ok(), `import: ${res.status()}`).toBe(true);
  if (opts.wizardDone) return;
  await put(request, 'devices', { schema_version: 1, devices: [] });
  await put(request, 'goals', { schema_version: 2 });
  await put(request, 'operation', { schema_version: 1, mode: 'shadow', device_modes: {} });
  await put(request, 'wizard', {
    schema_version: 1,
    completed: false,
    completed_steps: [],
    version: 1,
  });
}

export const STEP_IDS = [
  'region',
  'energy',
  'grid',
  'pv',
  'battery',
  'tariff',
  'devices',
  'priorities',
  'mode',
  'summary',
] as const;

export async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target}`)).toEqual([]);
}

/** Open a wizard step and wait until its content (not the loading text) is there. */
export async function openStep(page: Page, step: (typeof STEP_IDS)[number]): Promise<void> {
  await page.goto(`/#/setup/${step}`);
  await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', step);
  await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
}
