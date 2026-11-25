// SPDX-License-Identifier: Apache-2.0
// Splits the built files into the standard user path and the expert-only extras, from the Vite
// manifest (`dist/.vite/manifest.json`, `build.manifest: true`), never from file names or hashes.

/** Source files whose dynamic-import entries (and what only they pull in) are expert extras. */
export const EXPERT_SOURCES = [
    /(^|\/)lib\/expert\/ExpertArea\.svelte$/,
    /(^|\/)lib\/views\/WhatIf\.svelte$/,
    /(^|\/)lib\/i18n\/locales\/params\.[a-z]+\.json$/,
];

/**
 * @param {Record<string, {file: string, src?: string, isEntry?: boolean, isDynamicEntry?: boolean,
 *   imports?: string[], css?: string[], assets?: string[]}>} manifest
 * @returns {Set<string>} output files (relative to dist) used by the expert extras only
 */
export function expertFiles(manifest) {
    const isExpertRoot = (v) =>
        v.isDynamicEntry && EXPERT_SOURCES.some((re) => re.test(v.src ?? ''));
    const closure = (roots) => {
        const files = new Set();
        const seen = new Set();
        const visit = (key) => {
            if (seen.has(key) || !manifest[key]) return;
            seen.add(key);
            const v = manifest[key];
            files.add(v.file);
            for (const f of [...(v.css ?? []), ...(v.assets ?? [])]) files.add(f);
            for (const i of v.imports ?? []) visit(i);
        };
        roots.forEach(visit);
        return files;
    };
    const entries = Object.entries(manifest);
    const expert = closure(entries.filter(([, v]) => isExpertRoot(v)).map(([k]) => k));
    const standard = closure(
        entries
            .filter(([, v]) => (v.isEntry || v.isDynamicEntry) && !isExpertRoot(v))
            .map(([k]) => k),
    );
    for (const f of standard) expert.delete(f);
    return expert;
}
