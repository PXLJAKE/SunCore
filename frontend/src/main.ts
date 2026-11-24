// SPDX-License-Identifier: Apache-2.0
import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import { preloadInitialLocale } from './lib/i18n/index.svelte';
import { startThemeBridge } from './lib/theme/bridge';

// `?theme=light|dark` forces a scheme (used by the E2E tests and for screenshots); without it
// prefers-color-scheme decides. Under ingress the theme bridge copies HA's theme variables from
// the parent document when it is reachable (see frontend/README.md).
try {
  const theme = new URLSearchParams(location.search).get('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
} catch {
  // ignore malformed URLs
}

startThemeBridge();

// The browser's language catalogue (German is a separate chunk) is there before the first render,
// so nothing flashes in English first; a failed fetch falls back to English.
await preloadInitialLocale().catch(() => undefined);

export default mount(App, { target: document.getElementById('app')! });
