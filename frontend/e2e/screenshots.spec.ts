// SPDX-License-Identifier: Apache-2.0
// Documentation screenshots from the real demo mode (simulator, scenario summer_day, demo
// started at 09:30 by playwright.config.ts). Nothing is mocked.
// Run: SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots
import { expect, test } from '@playwright/test';
import { resetDemo, STEP_IDS } from './helpers';

const dir = process.env.SCREENSHOTS_DIR;
test.skip(!dir, 'set SCREENSHOTS_DIR to take screenshots');

const shots = [
  { view: 'now', path: '/', scheme: 'light', locale: 'en-US' },
  { view: 'now', path: '/', scheme: 'dark', locale: 'en-US' },
  { view: 'now', path: '/', scheme: 'light', locale: 'de-DE' },
  { view: 'now', path: '/', scheme: 'dark', locale: 'de-DE' },
  { view: 'settings', path: '/#/settings', scheme: 'light', locale: 'en-US' },
  { view: 'settings', path: '/#/settings', scheme: 'light', locale: 'de-DE' },
  { view: 'plan', path: '/#/plan', scheme: 'light', locale: 'en-US' },
  { view: 'plan', path: '/#/plan', scheme: 'dark', locale: 'en-US' },
  { view: 'plan', path: '/#/plan', scheme: 'light', locale: 'de-DE' },
  { view: 'plan', path: '/#/plan', scheme: 'dark', locale: 'de-DE' },
] as const;

for (const shot of shots) {
  test(`${shot.view} ${shot.scheme} ${shot.locale}`, async ({ browser }, info) => {
    test.skip(info.project.name === 'desktop', 'docs use phone and tablet');
    test.skip(shot.view === 'settings' && info.project.name === 'phone', 'tablet only');
    const ctx = await browser.newContext({
      colorScheme: shot.scheme,
      locale: shot.locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    const page = await ctx.newPage();
    await page.goto(shot.path);
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    if (shot.view === 'settings') {
      await expect(page.getByTestId('overview')).toBeVisible();
    }
    if (shot.view === 'plan') {
      await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
      await expect(page.getByTestId('fc-accuracy')).toBeVisible();
    }
    // Let the live feed deliver a few updates before the capture.
    await page.waitForTimeout(1500);
    const name = `${shot.view}-${info.project.name}-${shot.scheme}-${shot.locale.slice(0, 2)}`;
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    await ctx.close();
  });
}

// The battery card and the next planned actions of the Now view (the demo runs the generic
// battery adapter, see playwright.config.ts).
for (const shot of [
  { scheme: 'light', locale: 'en-US' },
  { scheme: 'dark', locale: 'en-US' },
  { scheme: 'light', locale: 'de-DE' },
  { scheme: 'dark', locale: 'de-DE' },
] as const) {
  test(`now-battery ${shot.scheme} ${shot.locale}`, async ({ browser }, info) => {
    test.skip(info.project.name === 'desktop', 'docs use phone and tablet');
    const ctx = await browser.newContext({
      colorScheme: shot.scheme,
      locale: shot.locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    const page = await ctx.newPage();
    await page.goto('/');
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    const card = page.getByTestId('battery-card');
    await expect(card).toBeVisible();
    await expect(
      page.getByTestId('now-next-actions').getByTestId('planned-action').first(),
    ).toBeVisible();
    await page.waitForTimeout(1500);
    const area = page.locator('main');
    const name = `now-battery-${info.project.name}-${shot.scheme}-${shot.locale.slice(0, 2)}`;
    // From the battery card to the end of the planned actions.
    const top = (await card.boundingBox())!;
    const next = (await page.getByTestId('now-next-actions').boundingBox())!;
    await page.screenshot({
      path: `${dir}/${name}.png`,
      fullPage: true,
      clip: {
        x: 0,
        y: Math.max(0, top.y - 8),
        width: (await area.boundingBox())!.x * 2 + (await area.boundingBox())!.width,
        height: next.y + next.height - top.y + 16,
      },
    });
    await ctx.close();
  });
}

// Decision log (rows appear a few ticks after the demo start) and the emergency stop: Now with
// the stop active (banner, "Back to normal"). The stop is cleared again afterwards.
const controlShots = [
  { view: 'decisions', scheme: 'light', locale: 'en-US' },
  { view: 'decisions', scheme: 'dark', locale: 'en-US' },
  { view: 'decisions', scheme: 'light', locale: 'de-DE' },
  { view: 'estop', scheme: 'light', locale: 'en-US' },
  { view: 'estop', scheme: 'dark', locale: 'en-US' },
  { view: 'estop', scheme: 'light', locale: 'de-DE' },
] as const;

for (const shot of controlShots) {
  test(`${shot.view} ${shot.scheme} ${shot.locale}`, async ({ browser, request }, info) => {
    test.skip(info.project.name === 'desktop', 'docs use phone and tablet');
    const ctx = await browser.newContext({
      colorScheme: shot.scheme,
      locale: shot.locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    try {
      const page = await ctx.newPage();
      await page.goto(shot.view === 'decisions' ? '/#/decisions' : '/');
      await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
      if (shot.view === 'decisions') {
        await expect(page.getByTestId('log-list').locator('li').first()).toBeVisible({
          timeout: 40_000,
        });
      } else {
        await expect(page.getByTestId('device-cards')).toBeVisible();
        await page.getByTestId('estop').click();
        await expect(page.getByTestId('estop-banner')).toBeVisible();
      }
      await page.waitForTimeout(1500);
      const name = `${shot.view}-${info.project.name}-${shot.scheme}-${shot.locale.slice(0, 2)}`;
      await page.screenshot({
        path: `${dir}/${name}.png`,
        fullPage: info.project.name === 'tablet',
      }); // phone: first screen only (size)
    } finally {
      await ctx.close();
      await request.post('/api/v1/control/emergency-stop/clear');
    }
  });
}

// Wizard steps of the fully configured demo for the docs. Long pages, so tablet only
// (skill demo-screenshots: keep each PNG under 300 kB); light en/de for every step,
// dark en/de for the device gallery and the summary.
const wizardShots = [
  ...(['en-US', 'de-DE'] as const).flatMap((locale) =>
    STEP_IDS.map((step) => ({ step, scheme: 'light' as const, locale })),
  ),
  ...(['en-US', 'de-DE'] as const).flatMap((locale) =>
    (['devices', 'summary', 'battery'] as const).map((step) => ({
      step,
      scheme: 'dark' as const,
      locale,
    })),
  ),
];

for (const { step, scheme, locale } of wizardShots) {
  test(`wizard ${step} ${scheme} ${locale}`, async ({ browser }, info) => {
    test.skip(
      info.project.name === 'desktop' || (info.project.name === 'phone' && step !== 'battery'),
      'wizard pages are long: tablet only (the battery step also on the phone)',
    );
    const ctx = await browser.newContext({
      colorScheme: scheme,
      locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    const page = await ctx.newPage();
    await page.goto(`/#/setup/${step}`);
    await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', step);
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    // The action bar is sticky; a full-page capture would freeze it mid-page.
    await page.addStyleTag({ content: '.actions { position: static !important; }' });
    await page.waitForTimeout(1500);
    const name = `wizard-${step}-${info.project.name}-${scheme}-${locale.slice(0, 2)}`;
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    await ctx.close();
  });
}

// The same air conditioner configured twice (lg_thinq and smartthinq_sensors): the hint in the
// device editor and the warning of the system check. A dedicated document state, set up through
// the API and reset afterwards; the default demo configuration stays untouched.
for (const locale of ['en-US', 'de-DE'] as const) {
  test(`duplicate unit ${locale}`, async ({ browser, request }, info) => {
    test.skip(info.project.name !== 'tablet', 'tablet only');
    const res = await request.get('/api/v1/config/devices');
    const section = await res.json();
    const ac = section.devices.find((d: { id: string }) => d.id === 'living_room_ac');
    const twin = {
      ...ac,
      id: 'living_room_ac_st',
      name: 'Living room AC (SmartThinQ)',
      template_id: 'generic.climate',
      entities: { climate: { entity_id: 'climate.sim_living_room_ac_smartthinq' } },
      power_model: { kind: 'nominal', nominal_w: 900 },
      priority: section.devices.length,
    };
    const put = await request.put('/api/v1/config/devices', {
      data: { ...section, devices: [...section.devices, twin] },
    });
    expect(put.ok(), `PUT devices: ${put.status()}`).toBe(true);
    const ctx = await browser.newContext({
      colorScheme: 'light',
      locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    try {
      const page = await ctx.newPage();
      const lang = locale.slice(0, 2);
      await page.goto('/#/setup/devices');
      await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', 'devices');
      await page
        .getByRole('listitem')
        .filter({ hasText: 'Living room AC (SmartThinQ)' })
        .getByRole('button')
        .first()
        .click();
      await expect(page.getByTestId('duplicate-unit-hint')).toBeVisible();
      await page.addStyleTag({ content: '.actions { position: static !important; }' });
      await page.waitForTimeout(1000);
      await page.getByTestId('device-editor').scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `${dir}/device-duplicate-hint-${info.project.name}-light-${lang}.png`,
        fullPage: true,
      });

      await page.goto('/#/setup/summary');
      await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', 'summary');
      await expect(page.getByText(/Living room AC \(SmartThinQ\)/).first()).toBeVisible();
      await page.addStyleTag({ content: '.actions { position: static !important; }' });
      await page.waitForTimeout(1500);
      await page.screenshot({
        path: `${dir}/wizard-summary-duplicate-${info.project.name}-light-${lang}.png`,
        fullPage: true,
      });
    } finally {
      await ctx.close();
      await resetDemo(request);
    }
  });
}
