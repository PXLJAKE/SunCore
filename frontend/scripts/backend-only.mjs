// SPDX-License-Identifier: Apache-2.0
// Messages only the backend renders (ADR-0016 decision 6: its catalogue is generated from the
// frontend catalogues): the names and states of the MQTT entities and the notification texts.
// The panel never shows them, so the build leaves them out of the browser chunks (7-G, about
// 2 kB gzip per language). `backend-only.test.mjs` checks that no frontend source uses one.

/** Key prefixes the frontend never renders. */
export const BACKEND_ONLY =
    /^(mqtt\.(entity|state)\.|notify\.(error|safety|proposal|program|ev_target|cheap_window|daily_summary)\.)/;

/** The catalogue JSON text without the backend-only keys. */
export function stripBackendOnly(text) {
    const catalogue = JSON.parse(text);
    return JSON.stringify(
        Object.fromEntries(Object.entries(catalogue).filter(([key]) => !BACKEND_ONLY.test(key))),
    );
}

/** Vite plugin: applies `stripBackendOnly` to the main catalogues before the JSON plugin. */
export function backendOnlyKeys() {
    return {
        name: 'suncore-backend-only-keys',
        enforce: 'pre',
        transform(code, id) {
            if (!/\/src\/lib\/i18n\/locales\/(en|de)\.json$/.test(id)) return null;
            return { code: stripBackendOnly(code), map: null };
        },
    };
}
