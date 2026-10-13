// SPDX-License-Identifier: Apache-2.0
// The demo starts fully configured. Specs change the configuration, so the initial export is kept
// once and `resetDemo` imports it again.
import { request } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { SNAPSHOT_FILE } from './helpers';

export default async function globalSetup(): Promise<void> {
  const port = Number(process.env.SUNCORE_E2E_PORT ?? 8099);
  const ctx = await request.newContext({ baseURL: `http://127.0.0.1:${port}` });
  const res = await ctx.get('/api/v1/config/export');
  if (!res.ok()) throw new Error(`demo export failed: ${res.status()}`);
  writeFileSync(SNAPSHOT_FILE, JSON.stringify(await res.json()));
  await ctx.dispose();
}
