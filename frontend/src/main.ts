// SPDX-License-Identifier: Apache-2.0
import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
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

export default mount(App, { target: document.getElementById('app')! });
