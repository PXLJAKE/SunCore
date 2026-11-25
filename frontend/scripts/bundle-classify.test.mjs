// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { expertFiles } from './bundle-classify.mjs';

const manifest = {
    'index.html': { file: 'assets/index.js', isEntry: true, imports: ['_shared.js'] },
    '_shared.js': { file: 'assets/shared.js' },
    '_onlyExpert.js': { file: 'assets/only-expert.js', imports: ['_shared.js'] },
    '_catalogue-params.js': { file: 'assets/catalogue-params.js' },
    'src/lib/views/Plan.svelte': {
        file: 'assets/Plan.js',
        src: 'src/lib/views/Plan.svelte',
        isDynamicEntry: true,
        imports: ['_shared.js', '_sharedWithExpert.js'],
    },
    '_sharedWithExpert.js': { file: 'assets/shared-with-expert.js' },
    'src/lib/expert/ExpertArea.svelte': {
        file: 'assets/ExpertArea.js',
        src: 'src/lib/expert/ExpertArea.svelte',
        isDynamicEntry: true,
        imports: ['_shared.js', '_onlyExpert.js', '_sharedWithExpert.js'],
        css: ['assets/ExpertArea.css'],
    },
    'src/lib/i18n/locales/params.de.json': {
        file: 'assets/params.de.js',
        src: 'src/lib/i18n/locales/params.de.json',
        isDynamicEntry: true,
        imports: ['_catalogue-params.js'],
    },
};

describe('expertFiles', () => {
    it('takes the expert entries and what only they import', () => {
        expect([...expertFiles(manifest)].sort()).toEqual([
            'assets/ExpertArea.css',
            'assets/ExpertArea.js',
            'assets/catalogue-params.js',
            'assets/only-expert.js',
            'assets/params.de.js',
        ]);
    });

    it('keeps chunks that a standard view also imports on the standard path', () => {
        const files = expertFiles(manifest);
        expect(files.has('assets/shared-with-expert.js')).toBe(false);
        expect(files.has('assets/shared.js')).toBe(false);
    });
});
