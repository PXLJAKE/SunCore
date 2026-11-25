// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { BACKEND_ONLY, stripBackendOnly } from './backend-only.mjs';

const src = new URL('../src/', import.meta.url).pathname;
const locales = join(src, 'lib/i18n/locales');

function* sources(dir) {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
            if (name !== 'locales') yield* sources(p);
        } else if (/\.(svelte|ts)$/.test(name) && !/\.(test|d)\.ts$/.test(name)) yield p;
    }
}

describe('backend-only catalogue keys', () => {
    const en = JSON.parse(readFileSync(join(locales, 'en.json'), 'utf8'));
    const stripped = Object.keys(en).filter((k) => BACKEND_ONLY.test(k));

    it('strips the MQTT entity texts and the notification texts, nothing else', () => {
        expect(stripped.length).toBeGreaterThan(50);
        const kept = JSON.parse(stripBackendOnly(JSON.stringify(en)));
        expect(Object.keys(kept)).toHaveLength(Object.keys(en).length - stripped.length);
        expect(kept['notify.title']).toBe(en['notify.title']);
        expect(kept['mqtt.reason.unknown']).toBe(en['mqtt.reason.unknown']);
    });

    it('no frontend source uses a stripped key, literally or as a template prefix', () => {
        const offenders = [];
        for (const file of sources(src)) {
            const code = readFileSync(file, 'utf8');
            for (const key of stripped) if (code.includes(key)) offenders.push(`${file}: ${key}`);
            for (const m of code.matchAll(/`((?:mqtt|notify)\.[a-z0-9_.]*)\$\{/g)) {
                const prefix = m[1];
                if (stripped.some((k) => k.startsWith(prefix)))
                    offenders.push(`${file}: ${prefix}`);
            }
        }
        expect(offenders).toEqual([]);
    });
});
