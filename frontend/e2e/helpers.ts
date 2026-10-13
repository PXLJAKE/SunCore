// SPDX-License-Identifier: Apache-2.0
import AxeBuilder from '@axe-core/playwright';
import { expect, type APIRequestContext, type Page } from '@playwright/test';

const ref = (entity_id: string) => ({ entity_id, invert_sign: false });

export const DEMO_SITE = {
  schema_version: 2,
  grid: { power: ref('sensor.sim_grid_power'), import_power: null, export_power: null },
  pv: [{ name: 'PV', power: ref('sensor.sim_pv_power') }],
  battery: { power: ref('sensor.sim_battery_power'), soc: ref('sensor.sim_battery_soc') },
  house: ref('sensor.sim_house_power'),
};

async function put(request: APIRequestContext, section: string, data: unknown): Promise<void> {
  const res = await request.put(`/api/v1/config/${section}`, { data });
  expect(res.ok(), `PUT ${section}: ${res.status()}`).toBe(true);
}

/** The demo as the other specs expect it: configured, wizard finished, no devices. */
export async function resetDemo(
  request: APIRequestContext,
  opts: { wizardDone: boolean } = { wizardDone: true },
): Promise<void> {
  await put(request, 'site', DEMO_SITE);
  await put(request, 'devices', { schema_version: 1, devices: [] });
  await put(request, 'goals', { schema_version: 1 });
  await put(request, 'operation', { schema_version: 1, mode: 'shadow', device_modes: {} });
  await put(request, 'wizard', {
    schema_version: 1,
    completed: opts.wizardDone,
    completed_steps: opts.wizardDone ? ['region', 'grid', 'battery'] : [],
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
