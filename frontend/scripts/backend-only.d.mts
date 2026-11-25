// SPDX-License-Identifier: Apache-2.0
// Types of backend-only.mjs for vite.config.ts.
import type { Plugin } from 'vite';

export const BACKEND_ONLY: RegExp;
export function stripBackendOnly(text: string): string;
export function backendOnlyKeys(): Plugin;
