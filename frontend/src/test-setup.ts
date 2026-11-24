// SPDX-License-Identifier: Apache-2.0
// Tests may switch to German at any time: have the lazy catalogue loaded up front.
import { loadLocale } from './lib/i18n/index.svelte';

await loadLocale('de');
