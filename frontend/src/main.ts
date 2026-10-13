// SPDX-License-Identifier: Apache-2.0
import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';

// `?theme=light|dark` forces a scheme (used by the E2E tests and for screenshots); without it
// prefers-color-scheme decides. A reading of HA's own theme from the ingress iframe is not
// implemented (see frontend/README.md).
try {
  const theme = new URLSearchParams(location.search).get('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
} catch {
  // ignore malformed URLs
}

export default mount(App, { target: document.getElementById('app')! });
