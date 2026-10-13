// SPDX-License-Identifier: Apache-2.0
// The demo starts unconfigured (no `wizard` section), so every `/` would redirect to the wizard.
// Specs that are not about the wizard start from a finished setup.
import { request } from '@playwright/test';
import { resetDemo } from './helpers';

export default async function globalSetup(): Promise<void> {
  const port = Number(process.env.SUNCORE_E2E_PORT ?? 8099);
  const ctx = await request.newContext({ baseURL: `http://127.0.0.1:${port}` });
  await resetDemo(ctx);
  await ctx.dispose();
}
